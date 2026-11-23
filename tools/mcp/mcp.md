# MCP servers

Tools from MCP servers are added to the tool registry, using FastMCP.

| File | Role |
|---|---|
| `tools/mcp/config/mcp_servers.json` | Server list |
| `tools/mcp/client.py` | `MCPClient`: `fastmcp.Client` subclass with a `server_id` |
| `tools/mcp/MCP_manager.py` | `MCPManager`: builds, connects and calls the clients |

## Config

```json
{
  "mcpServers": {
    "filesystem": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "./safe_test/"]
    }
  }
}
```

| `transport` | Fields | Connection |
|---|---|---|
| `stdio` | `command`, `args` (optional), `env` (optional) | `StdioTransport` |
| `streamable-http` | `url` | URL passed to the client |

Any other transport raises `ValueError: Unsupported transport`.

stdio servers run from the project root, so relative paths in `args` work wherever Sensai is launched from. A `command` of `python` or `python3` is replaced by the interpreter running Sensai (`sys.executable`), so Python servers get the venv's packages even when the venv isn't activated.

Configs copied from Claude Code or Claude Desktop also work: without `transport`, a `command` means `stdio` and a `url` means `streamable-http`, and `"type": "stdio"` / `"type": "http"` are read the same way.

Any server can also take `"disabled": true`: it is kept in the list but not connected at startup, and its tools are not registered.

## MCPManager

| Method | Does |
|---|---|
| `start()` | Reads the config, creates one `MCPClient` per server |
| `connect_all()` | Connects each client except disabled ones; a failure prints `[mcp] <id> failed to connect: <error>` and continues |
| `get_tools()` | `list_tools()` on each connected client |
| `register_tools(registry)` | Registers each tool as `<server_id>_<tool_name>`, type `mcp`, with its `input_schema` |
| `execute(server_id, name, args)` | Calls the tool, returns its `TextContent` blocks joined by `\n` |
| `restart(id)` | Closes the server, removes its tools, then reconnects a fresh client and registers them again (refused if disabled) |
| `disable(id)` | Removes its tools, closes it and marks it disabled |
| `enable(id)` | Clears the mark, connects a fresh client and registers its tools |
| `add_servers(servers)` | Writes new servers to the config, then `reload()`; refuses names already used |
| `remove_server(id)` | Deletes it from the config, then `reload()` |
| `reload()` | Re-reads the config: starts new servers, stops removed ones, restarts changed ones; unchanged ones keep their state |
| `disconnect_all()` | Closes connected clients |

`execute` raises `ValueError` for an unknown server and `RuntimeError` if it is not connected; `ToolExecutor` turns these into `{"error": ...}` for the model.

## Web controls

| Endpoint | Does |
|---|---|
| `POST /mcp/{id}/restart` | `restart(id)` |
| `POST /mcp/{id}/disable` | `disable(id)` |
| `POST /mcp/{id}/enable` | `enable(id)` |
| `POST /mcp/servers` | `add_servers`; body `{"config": <pasted JSON>, "id": <name, only for a single server's settings>}` |
| `DELETE /mcp/servers/{id}` | `remove_server(id)` |
| `POST /mcp/reload` | `reload()`, returns what was added, removed or restarted |
| `GET /tools/calls?limit=` | latest logged tool calls, newest first |

Each returns the same data as `GET /tools` and is refused (409) while a reply is being generated. The Tools panel has a button for each, and an "Add server" box to paste a config into.

## Adding a server: example

The calculator server has one tool, `calculate`, taking one `expression`, so it is easy for a small model. In the Tools panel, open "Add server", paste this and click Add:

```json
{"mcpServers": {"calculator": {"command": "uvx", "args": ["mcp-server-calculator"]}}}
```

It connects and its tool shows up as `calculator_calculate`. In a new chat, ask "Use the calculator to work out 1234 * 5678": the tool call appears in the chat and the answer is 7006652. Remove it with its Remove button.

## Third-party server demo: Blender

[blender-mcp](https://github.com/ahujasid/blender-mcp) drives a running Blender through a socket that a Blender add-on opens (port 9876).

Setup, once:

1. Install Blender 4.2 or newer (`sudo dnf install blender` or the Flatpak).
2. Download `addon.py` from the blender-mcp repo. In Blender: Edit > Preferences > Add-ons > Install from Disk, pick `addon.py`, tick "Interface: Blender MCP".
3. In the 3D viewport press `N`, open the BlenderMCP tab, click "Connect to MCP server".

Demo, all from the web UI:

1. Tools > Add server, paste and click Add:

   ```json
   {"mcpServers": {"blender": {"command": "uvx", "args": ["blender-mcp"], "env": {"DISABLE_TELEMETRY": "true"}, "confirm": ["execute_blender_code"]}}}
   ```

   It connects with 9 tools (`blender_get_scene_info`, `blender_execute_blender_code`, ...). The server starts even when Blender is closed; its tools then answer "Could not connect to Blender".
2. New chat, "Create a cube in Blender". The model calls `blender_execute_blender_code` with `bpy` code, the approval dialog shows the code, approve it and the cube appears in Blender.
3. Back in Tools: Disable, Enable, Restart and Remove the server.

With `qwen3:8b` the call takes about 30 s end to end, the 9 tools add about 4k tokens to every request.

## Seeing the tool loop

In the chat, each call shows up as a block when it starts ("running…", also while it waits for approval) and is filled in when it returns: step number in the turn, tool, MCP server, run time (approval wait not counted), decision, arguments and result. The model's text after it goes in a new bubble below, so a multi-step loop reads top to bottom. Reopened sessions show the same blocks.

Each call is also written to the call log (`calls.jsonl`, both web and CLI):

```json
{"tool_call": "blender_execute_blender_code", "server": "blender", "session": "20261110_171749", "round": 1,
 "arguments": {"code": "import bpy\nbpy.ops.mesh.primitive_cube_add()"}, "decision": "approved", "ok": true,
 "duration_s": 0.02, "result": "{\"result\": \"...\"}", "result_chars": 105, "time": "2026-11-10T17:18:16"}
```

`result` keeps the first 4000 characters, `result_chars` the full length. `GET /tools/calls?limit=50` returns the latest ones, newest first; the Tools panel lists them under "Recent tool calls". `ok` comes from the result's shape: blender-mcp reports Blender errors as plain text, so those calls still show as ok and the model reads the error.

## Lifecycle

`app.create_agent()` calls `start()`, `connect_all()`, `register_tools()`. `sensai.py` calls `disconnect_all()` in a `finally` block on exit.

## File permissions

The `filesystem` server only reaches `safe_test/`. Inside it, `tools/permissions.py` decides which paths the agent may read or write. `ToolExecutor` checks every `filesystem` tool call before it runs (and again if the user edits the arguments at the confirmation prompt); a denied call returns `{"error": "<tool> denied: ..."}` to the model.

`tools/permissions.json`, paths relative to `safe_test/`:

```json
{
  "default": "read",
  "rules": [
    {"path": "notes", "access": "write"},
    {"path": "private", "access": "none"}
  ]
}
```

| Access | Allows |
|---|---|
| `none` | nothing |
| `read` | `read_*`, `list_directory*`, `directory_tree`, `search_files`, `get_file_info` |
| `write` | read, plus `write_file`, `edit_file`, `create_directory`, `move_file` |

- The rule with the longest matching path wins; paths with no rule get `default`. Use `.` for the workspace root.
- Every path in a call must pass: `move_file` needs write on both `source` and `destination`.
- Paths are resolved like the server does (relative to `safe_test/`), then through `realpath`: `..`, `~` and symlinks leading outside the workspace are denied, and a symlink inside it gets the rule of its real location.
- No file means everything is writable. A broken file denies every filesystem call with the error.
- The rules are listed in the system prompt, so the model knows what it may touch.
- Known limit: `search_files` and `directory_tree` on a readable folder also walk into `none` folders below it.

### /permissions

| Command | Does |
|---|---|
| `/permissions` | Lists the rules |
| `/permissions set <path> <none\|read\|write>` | Adds or changes a rule |
| `/permissions remove <path>` | Deletes a rule |
| `/permissions default <none\|read\|write>` | Access for paths with no rule |

Changes are saved to `tools/permissions.json`, apply to the next tool call and rebuild the system prompt. Works in the CLI and the web UI.

In the web UI, **File access** in the sidebar (or `/permissions` in the chat) opens an editor: a None / Read / Write switch per path, a remove button, and a field to add a rule. It uses `GET /permissions` and `POST /permissions` (`{"action": "set" | "remove" | "default", "path", "access"}`).

Tests: `python -m unittest tests.test_permissions`
