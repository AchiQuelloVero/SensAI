# Architecture

`sensai.py` is the CLI entry point. `app.py` wires the other modules together.

| Module | Role |
|---|---|
| `sensai.py` | Parses args, runs the chat loop, handles `/sessions`, `/stats`, `/json`, `/exit`; saves the session and logs the call after each turn |
| `app.py` | `create_agent(model)`: builds the Ollama client, tool registry, MCP manager, tool executor and agent |
| `core/agent.py` | `AgentRuntime.run_turn()`: model ↔ tool loop for one user message |
| `core/contracts.py` | `ModelResponse` dataclass and the `ModelClient`, `ToolCatalog`, `ToolRunner`, `ToolLookup` protocols |
| `core/config.py` | `HOST` (`http://localhost:11434`), `DEFAULT_MODEL` (`qwen3:8b`), `MAX_TOOL_ROUNDS` (`5`) |
| `model/ollama.py` | `OllamaClient`: `check_model_available()` and streaming `chat()` on `/api/chat` |
| `prompting/prompt_builder.py` | Loads the system prompt and profile, builds the final system prompt |
| `storage/session_store.py` | Saves, loads and lists session files |
| `call_tracking/call_log.py` | Appends calls to the JSONL log, prints `/stats` |
| `tools/` | Tool registry, executor, built-in tools, MCP client and manager |
| `structured.py` | JSON-schema replies used by `/json` |
| `hallucination/` | Grounding rules, evidence, claim extraction, judge, self-consistency and actions on unsupported answers (`hallucination/config/hallucination.json`) |
| `evals/` | Evaluation scripts and their test cases |

## Startup (`app.create_agent`)

1. `OllamaClient(model, HOST).check_model_available()`
2. `ToolRegistry()` + `register_builtin_tools()`
3. `MCPManager()` → `start()` → `connect_all()` → `register_tools(registry)`
4. `ToolExecutor(registry, mcp_manager)`
5. `AgentRuntime(model, registry, executor, MAX_TOOL_ROUNDS)`

Returns `(agent, mcp_manager)`; `sensai.py` calls `mcp_manager.disconnect_all()` on exit.

## One turn

```
user input
  → AgentRuntime.run_turn
    → OllamaClient.chat (streamed to the terminal)
    → tool_calls? → ToolExecutor.execute → tool result appended → chat again
    → no tool_calls → return ModelResponse
  → check_answer (hallucination/check.py): extract claims → judge them against the evidence
    → show the verdict (terminal: sensai.py; web: SSE events fact_check*, web/server.py), then warn / regenerate once / refuse
  → save_session + log_call
```

On `OllamaError`, `RuntimeError` or Ctrl-C, the turn's messages are removed from history.
