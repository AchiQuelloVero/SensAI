import asyncio
import json
import time
from typing import Callable

from core.contracts import (
    AnswerStore,
    ContextProvider,
    Message,
    ModelClient,
    ModelResponse,
    ToolCatalog,
    ToolRunner,
)


REPEATED_CALL = (
    "You already made this exact call in this turn and it failed. Do not repeat it: "
    "fix the arguments, or tell the user what went wrong."
)

TOO_MANY_ATTEMPTS = (
    "{name} failed {count} times in a row in this turn, the most allowed. Do not call it again: "
    "tell the user what went wrong and what you tried."
)

OUT_OF_ROUNDS = (
    "You have used all your tool calls for this message. Do not call any more tools. "
    "Answer now with the information you already have, and say what you could not find."
)


SUMMED_STATS = ("prompt_tokens", "response_tokens", "total_duration_s", "eval_duration_s")


class AgentRuntime:
    # main loop for agent calling tools

    def __init__(
        self,
        model: ModelClient,
        tool_registry: ToolCatalog,
        tool_executor: ToolRunner,
        max_tool_rounds: int = 5,
        context_provider: ContextProvider | None = None,
        grounding_prompt: str | None = None,
        answer_store: AnswerStore | None = None,
        max_tool_attempts: int = 3,
    ):
        self.model = model
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.max_tool_rounds = max_tool_rounds
        self.context_provider = context_provider
        self.grounding_prompt = grounding_prompt
        self.answer_store = answer_store
        self.max_tool_attempts = max_tool_attempts

    # on each msg from user it runs the loop checking for tool calling might need to do a few tool calls in a row
    async def run_turn(
        self,
        messages: list[Message],
        user_input: str,
        on_text_chunk: Callable[[str], None] | None = None, #NB if it comes in stream or all at once
        on_tool_call: Callable[[str, dict, str], None] | None = None, # (name, trace, result text) once a tool has run
        on_tool_event: Callable[[dict], None] | None = None, # live start/end of each tool
    ) -> ModelResponse:
        turn_start = len(messages)

        messages.append({
            "role": "user",
            "content": user_input,
        })

        totals = {key: 0 for key in SUMMED_STATS}

        tool_rounds = 0
        failed_calls = set()
        failures = {}  # tool name -> failed calls in a row, so a retry shows as "attempt 2 of 3"
        model_name = getattr(self.model, "model", "")
        history = messages[:turn_start]
        try:
            cache_note = ""
            if self.answer_store is not None:
                cached = await asyncio.to_thread(self.answer_store.lookup, model_name, history, user_input)
                if cached.answer is not None:
                    if on_text_chunk is not None:
                        on_text_chunk(cached.answer)
                    messages.append({"role": "assistant", "content": cached.answer})
                    totals["total_duration_s"] = cached.lookup_s
                    return ModelResponse(content=cached.answer, stats=totals, cached=True, saved_s=cached.saved_s)
                cache_note = cached.note
            answer_start = time.perf_counter()

            # retrieved excerpts go to the model in place of the bare question for this turn only;
            # `messages` (what is saved and shown) keeps what the user actually typed
            context = None
            if self.context_provider is not None:
                context = await asyncio.to_thread(
                    self.context_provider.build_context,
                    user_input,
                )
            framed_input = context.prompt if context is not None else None

            while True:
                out_of_rounds = tool_rounds >= self.max_tool_rounds
                request = self._request_messages(messages, turn_start, framed_input, self.grounding_prompt)
                if out_of_rounds:
                    request = [*request, {"role": "system", "content": OUT_OF_ROUNDS}]

                # model.chat is blocking, run it in a thread so the event loop stays free
                response = await asyncio.to_thread(
                    self.model.chat,
                    messages=request,
                    tools=self.tool_registry.to_ollama_tools() if not out_of_rounds else [],

                    on_text_chunk=on_text_chunk,
                )

                for key in SUMMED_STATS:
                    totals[key] += response.stats.get(key, 0) or 0
                # the last call holds the whole conversation, so it is what fills the window
                totals["context_tokens"] = (
                    (response.stats.get("prompt_tokens", 0) or 0)
                    + (response.stats.get("response_tokens", 0) or 0)
                )
                totals["context_window"] = getattr(self.model, "context_window", 0) or 0

                messages.append(response.to_message())

                if not response.tool_calls:
                
                    if self.answer_store is not None:
                        saved_note = await asyncio.to_thread(
                            self.answer_store.save, model_name, history, user_input, response.content,
                            time.perf_counter() - answer_start, tool_rounds > 0,
                        )
                        cache_note = " · ".join(note for note in (cache_note, saved_note) if note)
                    return ModelResponse(
                        content=response.content,
                        stats=totals,
                        excerpts=context.excerpts if context is not None else [],
                        request=request,
                        cache_note=cache_note,
                    )

                if out_of_rounds:
                    raise RuntimeError(
                        "Maximum tool-call rounds exceeded"
                    )

                tool_rounds += 1

                for call in response.tool_calls:
                    function = call.get("function", {})
                    name = function.get("name", "")
                    arguments = function.get("arguments", {})

                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            pass

                    trace = {"arguments": arguments, "decision": None}
                    key = (name, json.dumps(arguments, sort_keys=True, default=str))
                    failed_before = failures.get(name, 0)
                    repeated = key in failed_calls
                    too_many = failed_before >= self.max_tool_attempts
                    if failed_before and not repeated and not too_many:
                        trace["attempt"] = failed_before + 1
                        trace["max_attempts"] = self.max_tool_attempts

                    if on_tool_event is not None:
                        on_tool_event({
                            "phase": "start",
                            "name": name,
                            "round": tool_rounds,
                            "arguments": arguments,
                            "attempt": trace.get("attempt"),
                            "max_attempts": trace.get("max_attempts"),
                        })
                    started = time.perf_counter()

                    if repeated:
                        trace["decision"] = "skipped"
                        result = {"error": REPEATED_CALL}
                    elif too_many:
                        trace["decision"] = "skipped"
                        result = {"error": TOO_MANY_ATTEMPTS.format(name=name, count=failed_before)}
                    else:
                        result = await self.tool_executor.execute(
                            name,
                            arguments,
                            trace,
                        )
                        if describe_result(result)[0]:
                            failures[name] = 0
                        else:
                            failed_calls.add(key)
                            failures[name] = failed_before + 1
                    content = json.dumps(result, ensure_ascii=False)
                    ok, summary = describe_result(result)
                    trace["ok"] = ok
                    trace["round"] = tool_rounds
                    trace.setdefault("duration_s", round(time.perf_counter() - started, 2))

                    if on_tool_event is not None:
                        on_tool_event({
                            "phase": "end",
                            "name": name,
                            "arguments": trace["arguments"],  # what ran: the user may have edited them
                            "decision": trace["decision"],
                            "ok": ok,
                            "summary": summary,
                            "duration_s": trace["duration_s"],
                        })

                    # give the tool result back to the model; "call" is only for display
                    messages.append({
                        "role": "tool",
                        "tool_name": name,
                        "content": content,
                        "call": trace,
                    })
                    if on_tool_call is not None:
                        on_tool_call(name, trace, content)

        except Exception:

            del messages[turn_start:]
            raise

    @staticmethod
    def _request_messages(
        messages: list[Message],
        turn_start: int,
        framed_input: str | None,
        grounding_prompt: str | None = None,
    ) -> list[Message]:
        request = list(messages)
        if framed_input is not None:
            request[turn_start] = {**messages[turn_start], "content": framed_input}
        # added to each request rather than saved, so the session keeps the prompt the user wrote
        if grounding_prompt and request and request[0]["role"] == "system":
            request[0] = {**request[0], "content": request[0]["content"] + "\n\n" + grounding_prompt}
        return request



def describe_result(result: dict) -> tuple[bool, str]:
    if "error" in result:
        return False, str(result["error"])
    value = result.get("result")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return True, f"{len(value):,} chars"
    if isinstance(value, dict) and "returncode" in value:
        return describe_run(value)
    if isinstance(value, dict) and "error" in value:
        return False, str(value["error"])
    if isinstance(value, list):
        return True, f"{len(value)} result" + ("" if len(value) == 1 else "s")
    return True, f"{len(json.dumps(value, ensure_ascii=False)):,} chars"


def describe_run(run: dict) -> tuple[bool, str]:
    """A run_python result: it failed unless the code exited with 0, so the model gets to fix it."""
    where = run.get("sandbox", "subprocess")
    if run.get("timed_out"):
        return False, f"timed out · {where}"
    if run.get("returncode") == 0:
        return True, f"exit 0 · {where}"
    lines = [line.strip() for line in (run.get("stderr") or "").splitlines() if line.strip()]
    detail = lines[-1] if lines else f"exit {run.get('returncode')}"
    return False, f"{detail[:80]} · {where}"
