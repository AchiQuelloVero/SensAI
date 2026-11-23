# Web MCP server

FastMCP server named `web` that gives the agent web search and page fetching, using `ddgs`.

## Tools

| Tool | Agent sees | Args | Returns |
|---|---|---|---|
| `search` | `web_search` | `search: str` | Up to 5 results: `{title, url, snippet}`, snippet cut to 300 chars |
| `fetch` | `web_fetch` | `url: str` | Page text (`text_markdown`), cut to 3000 chars |

The `web_` prefix is added by `MCPManager.register_tools` (`<server_id>_<tool_name>`).
Both `DDGS` calls are blocking, so they run in a thread with `anyio.to_thread.run_sync`.

## Limits

| Constant | Value |
|---|---|
| `MAX_RESULTS` | 5 |
| `MAX_SNIPPET_CHARS` | 300 |
| `MAX_PAGE_CHARS` | 3000 |

## Config

In `tools/mcp/config/mcp_servers.json`:

```json
"web": {
  "transport": "stdio",
  "command": "python3",
  "args": ["tools/mcp/servers/web_server.py"]
}
```

`sensai.py` starts it automatically at launch and stops it on exit. Its stderr goes to `mcp.log`.
