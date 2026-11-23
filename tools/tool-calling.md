# Tool calling

The model can call registered functions during a turn.

| File | Role |
|---|---|
| `tools/registry.py` | `ToolRegistry`: stores tools, exports them in Ollama format |
| `tools/builtin.py` | Built-in tools `add(a, b)` and `multiply(a, b)` |
| `tools/executor.py` | `ToolExecutor`: runs a tool by name |
| `core/agent.py` | `AgentRuntime.run_turn()`: the tool loop |

## Registry

```python
registry.register(
    name="add",
    description="Add two numbers together.",
    parameters=number_parameters,  # JSON schema
    function=add,
)
```

Each tool stores `description`, `parameters`, `type` (default `builtin`), `function`, `server_id`, `original_name`.
Registering an existing name raises `ValueError: Tool already registered`.
`to_ollama_tools()` returns the list sent to Ollama as `tools`.

## Loop (`run_turn`)

1. Append the user message.
2. Call `model.chat(messages, tools=registry.to_ollama_tools())` and append the assistant message.
3. No `tool_calls` → return the reply with summed token/duration stats.
4. Otherwise, for each call: parse arguments (JSON string → dict if needed), run `tool_executor.execute(name, arguments)`, append `{"role": "tool", "tool_name": name, "content": json.dumps(result)}`.
5. Go back to step 2.

After `max_tool_rounds` (5) rounds with more tool calls → `RuntimeError("Maximum tool-call rounds exceeded")`.
Any exception removes every message added during the turn, then is re-raised.

## Executor

Returns `{"result": ...}` on success, otherwise `{"error": ...}` (never raises):

| Case | Error |
|---|---|
| Name not registered | `Unknown tool: <name>` |
| Type not `builtin` / `api` / `mcp` | `Unknown tool type: <type>` |
| Arguments not a dict | `Arguments for <name> must be a JSON object, got: ...` |
| `mcp` tool with no manager | `No MCP manager for tool: <name>` |
| Tool raises | `<name> failed: <ExceptionType>: <message>` |

`mcp` tools go to `mcp_manager.execute(server_id, original_name, arguments)` (see [mcp/mcp.md](mcp/mcp.md)); others call `function(**arguments)`.
