import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

from core.agent import AgentRuntime
from core.config import DEFAULT_MODEL, DEFAULT_PERSONA, SYSTEM_PROMPT_PATH, load_app_config
from core.contracts import RetrievedContext
from hallucination.check import check_answer
from hallucination.config import HallucinationError, judge_model, load_config
from hallucination.grounding import grounding_prompt
from model.ollama import OllamaError
from model.openrouter import make_model_client
from prompting.persona import PersonaError, load_persona
from prompting.prompt_builder import PromptError, build_system_prompt, load_system_prompt
from prompting.versioning import VersionError, get_version
from rag.context import excerpt, frame_question
from rag.retriever import RetrievedChunk
from structured import StructuredOutputError
from tools.builtin import register_builtin_tools
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry

CASES_PATH = Path(__file__).parent / "hallucination_cases.json"
KINDS = ["known", "no_answer", "fake"]

# the answer says it cannot answer instead of making something up
DECLINES = re.compile(
    r"\b(?:i don't know|i do not know|i'm not sure|i am not sure|not aware of|no (?:information|record|evidence)"
    r"|don't have (?:any )?(?:information|access|details|a record)|do not have (?:any )?(?:information|access|details)"
    r"|(?:does|did) not exist|doesn't exist|didn't exist|there (?:is|was) no|no such"
    r"|(?:haven't|have not|didn't|did not) (?:told|tell|mention|mentioned|shared|share|said|say)"
    r"|not (?:mentioned|provided|specified|stated|covered)|(?:cannot|can't|couldn't|could not) find)",
    re.IGNORECASE,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Measure how often the assistant invents answers.")
    parser.add_argument(
        "-m", "--models",
        nargs="+",
        default=[DEFAULT_MODEL],
        help=f"chat models to test (default: {DEFAULT_MODEL}), also openrouter/<id>; "
             "the judge is the chat model, or the local check model for an OpenRouter one",
    )
    parser.add_argument(
        "-p", "--prompt-versions",
        nargs="+",
        type=int,
        default=[None],
        help="versions of system_prompt.txt to compare (see /prompt versions); default: the current file",
    )
    parser.add_argument(
        "-o", "--report",
        help="also write the scores as a markdown table to this file",
    )
    parser.add_argument(
        "-c", "--cases",
        default=CASES_PATH,
        help="test cases file (default: evals/hallucination_cases.json)",
    )
    parser.add_argument(
        "-k", "--kinds",
        nargs="+",
        choices=KINDS,
        default=KINDS,
        help="only run these kinds of cases",
    )
    return parser.parse_args()


def load_cases(path, kinds):
    try:
        with open(path, encoding="utf-8") as f:
            cases = json.load(f)
    except (OSError, ValueError) as e:
        sys.exit(f"error: cannot read test cases {path}: {e}")
    return [case for case in cases if case["kind"] in kinds]


class Documents:
    """Serves the case's documents as if the retriever had found them."""

    def __init__(self, documents):
        self.chunks = [RetrievedChunk(d["source"], 0, d["text"], 1.0) for d in documents]

    def build_context(self, question):
        if not self.chunks:
            return None
        return RetrievedContext(frame_question(question, self.chunks), [excerpt(c) for c in self.chunks])


def mentions(answer, expected):
    answer = re.sub(r"(?<=\d),(?=\d)", "", answer)
    return any(re.search(rf"(?<!\w){re.escape(e)}(?!\w)", answer, re.IGNORECASE) for e in expected)


def declines(answer):
    return bool(DECLINES.search(answer.replace("’", "'")))


def run_case(model, case, system, config, registry):
    app_config = load_app_config()
    agent = AgentRuntime(
        make_model_client(model, app_config.host, app_config.num_ctx),
        registry,
        ToolExecutor(registry),
        context_provider=Documents(case.get("documents", [])),
        grounding_prompt=grounding_prompt(config),
    )
    messages = [{"role": "system", "content": system}, *case.get("history", [])]
    turn_start = len(messages)
    response = asyncio.run(agent.run_turn(messages, case["question"]))
    check = check_answer(agent.model, judge_model(config, model), config, messages, turn_start, response)

    answer = response.content
    if case["kind"] == "known":
        passed = mentions(answer, case["expected"]) and not check.flagged
    else:
        passed = declines(answer) or bool(check.flagged)
    return answer, check, passed


def system_for(version):
    """The system prompt for a saved version of system_prompt.txt, or the current file when version is None."""
    base = load_system_prompt(SYSTEM_PROMPT_PATH) if version is None else get_version(SYSTEM_PROMPT_PATH, version).strip()
    return build_system_prompt(base, None, load_persona(DEFAULT_PERSONA))


def markdown_table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def main():
    args = parse_args()
    cases = load_cases(args.cases, args.kinds)
    try:
        config = {**load_config(), "enabled": True}
        systems = {version: system_for(version) for version in args.prompt_versions}
    except (HallucinationError, PromptError, PersonaError, VersionError) as e:
        sys.exit(f"error: {e}")
    registry = ToolRegistry()
    register_builtin_tools(registry)

    rows = []
    for model in args.models:
        try:
            make_model_client(model, load_app_config().host).check_model_available()
        except OllamaError as e:
            sys.exit(f"error: {e}")
        for version in args.prompt_versions:
            label = "current" if version is None else f"v{version}"
            print(f"{model}, prompt {label} ({len(cases)} cases)...")
            results = {kind: [] for kind in args.kinds}
            for case in cases:
                start = time.time()
                try:
                    answer, check, passed = run_case(model, case, systems[version], config, registry)
                except (OllamaError, StructuredOutputError, RuntimeError) as e:
                    print(f"    error  {case['question'][:60]!r}: {e}")
                    results[case["kind"]].append((False, time.time() - start))
                    continue
                results[case["kind"]].append((passed, time.time() - start))
                mark = "ok  " if passed else "FAIL"
                print(f"    {mark} {case['kind']:9} {len(check.flagged)}/{len(check.verdicts)} flagged  {case['question'][:60]!r}")
                if not passed:
                    print(f"           answer: {' '.join(answer.split())[:150]!r}")

            row = [model, label]
            for kind in args.kinds:
                passed = sum(ok for ok, _ in results[kind])
                row.append(f"{passed}/{len(results[kind])}")
            every = [r for kind in args.kinds for r in results[kind]]
            row.append(f"{sum(ok for ok, _ in every) / len(every):.0%}" if every else "-")
            row.append(f"{sum(s for _, s in every) / len(every):.0f}s" if every else "-")
            rows.append(row)

    table = markdown_table(["model", "prompt", *args.kinds, "score", "avg time"], rows)
    print("\n" + table)
    if args.report:
        try:
            Path(args.report).write_text(table + "\n", encoding="utf-8")
        except OSError as e:
            sys.exit(f"error: cannot write the report {args.report}: {e}")
        print(f"report written to {args.report}")


if __name__ == "__main__":
    main()
