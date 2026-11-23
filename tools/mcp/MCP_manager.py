import asyncio
import json
import os
import re
import sys
from pathlib import Path


from fastmcp.client.transports import StdioTransport
from mcp_types import TextContent

from core.config import ROOT_DIR, MCP_CONFIG_PATH, MCP_LOG_FILE as DEFAULT_MCP_LOG_FILE
from tools.mcp.client import MCPClient
from tools.registry import ToolRegistry

MCP_LOG_FILE = Path(DEFAULT_MCP_LOG_FILE)
SERVER_ID = re.compile(r"[A-Za-z0-9_-]+")
# "type" is how Claude Code and Claude Desktop configs name the transport
TYPES = {"stdio": "stdio", "http": "streamable-http", "streamable-http": "streamable-http"}
# run python servers with the app's interpreter so they see the venv packages
PYTHON_COMMANDS = {"python", "python3"}


def normalize_server(server_id: str, settings) -> dict:
    if not SERVER_ID.fullmatch(server_id):
        raise ValueError(f"Invalid server name {server_id!r}: use letters, digits, - and _")
    if not isinstance(settings, dict):
        raise ValueError(f"{server_id}: the server config must be a JSON object")
    settings = dict(settings)
    kind = settings.pop("type", None)
    transport = settings.get("transport") or TYPES.get(kind)
    if transport is None and kind is None:
        transport = "stdio" if "command" in settings else "streamable-http" if "url" in settings else None
    if transport == "stdio":
        if not isinstance(settings.get("command"), str) or not settings["command"]:
            raise ValueError(f"{server_id}: a stdio server needs a \"command\"")
        if not all(isinstance(arg, str) for arg in settings.get("args", [])):
            raise ValueError(f"{server_id}: \"args\" must be a list of strings")
    elif transport == "streamable-http":
        if not str(settings.get("url", "")).startswith(("http://", "https://")):
            raise ValueError(f"{server_id}: an http server needs an http(s) \"url\"")
    elif transport is None and kind is None:
        raise ValueError(f"{server_id}: needs a \"command\" (stdio) or a \"url\" (http)")
    else:
        raise ValueError(f"Unsupported transport for {server_id}: {transport or kind}")
    return {"transport": transport, **settings}


class MCPManager:
    def __init__(self, config_path: str | Path = MCP_CONFIG_PATH, log_file: str | Path = MCP_LOG_FILE):
        self.clients: dict[str, MCPClient] = {}
        # per server: list of tool names that need user confirmation, or "*" for all
        self.confirm: dict[str, list[str] | str] = {}
        # per server: transport and where it runs (never the env), and why it failed to connect
        self.info: dict[str, dict] = {}
        self.errors: dict[str, str] = {}
        self.settings: dict[str, dict] = {}
        self.disabled: set[str] = set()
        self.registry: ToolRegistry | None = None
        self.lock = asyncio.Lock()
        self.config_path = Path(config_path)
        self.log_file = Path(log_file)

    def _read_config(self) -> dict[str, dict]:
        with self.config_path.open(encoding="utf-8") as file:
            servers = json.load(file).get("mcpServers", {})
        return {server_id: normalize_server(server_id, settings) for server_id, settings in servers.items()}

    def _write_config(self, change) -> None:
        with self.config_path.open(encoding="utf-8") as file:
            config = json.load(file)
        change(config.setdefault("mcpServers", {}))
        temp = self.config_path.with_suffix(".tmp")
        temp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.config_path)

    def start(self) -> None:
        for server_id, settings in self._read_config().items():
            self.settings[server_id] = settings
            self.clients[server_id] = self._make_client(server_id)
            self.confirm[server_id] = settings.get("confirm", [])
            if settings.get("disabled"):
                self.disabled.add(server_id)

    def _make_client(self, server_id: str) -> MCPClient:
        settings = self.settings[server_id]
        transport = settings["transport"]

        if transport == "stdio":
            self.info[server_id] = {
                "transport": transport,
                "target": " ".join([settings["command"], *settings.get("args", [])]),
            }
            command = settings["command"]
            connection = StdioTransport(
                command=sys.executable if command in PYTHON_COMMANDS else command,
                args=settings.get("args", []),
                env=settings.get("env"),
                cwd=str(ROOT_DIR),
                log_file=self.log_file,
            )

        elif transport == "streamable-http":
            self.info[server_id] = {"transport": transport, "target": settings["url"]}
            connection = settings["url"]

        else:
            raise ValueError(
                f"Unsupported transport: {transport}"
            )

        return MCPClient(server_id=server_id, connection=connection)

    async def connect(self, server_id: str) -> None:
        client = self.clients[server_id]
        if not client.is_connected():
            await client.__aenter__()

    async def disconnect(self, server_id: str) -> None:
        client = self.clients[server_id]
        if client.is_connected():
            await client.close()

    async def connect_all(self) -> None:
        for server_id in self.clients:
            if server_id not in self.disabled:
                await self._try_connect(server_id)

    async def _try_connect(self, server_id: str) -> bool:
        try:
            await self.connect(server_id)
        except Exception as exc:
            self.errors[server_id] = str(exc)
            with self.log_file.open("a", encoding="utf-8") as log:
                log.write(f"[mcp] {server_id} failed to connect: {exc}\n")
            return False
        self.errors.pop(server_id, None)
        return True

    async def disconnect_all(self) -> None:
        for server_id in self.clients:
            await self.disconnect(server_id)

    async def get_tools(self) -> dict[str, list]:
        tools = {}
        for server_id, client in self.clients.items():
            if not client.is_connected():
                continue
            tools[server_id] = await client.list_tools()
        return tools

    async def execute(self, server_id: str, name: str, args: dict):
        client = self.clients.get(server_id)
        if client is None:
            raise ValueError(f"Unknown MCP server: {server_id}")
        if not client.is_connected():
            raise RuntimeError(f"MCP server not connected: {server_id}")

        result = await client.call_tool(name, args)
        return "\n".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )

    async def register_tools(self, registry: ToolRegistry) -> None:
        self.registry = registry
        for server_id, client in self.clients.items():
            if client.is_connected():
                await self._register_server(server_id)

    async def _register_server(self, server_id: str) -> None:
        confirm = self.confirm.get(server_id, [])
        for tool in await self.clients[server_id].list_tools():
            self.registry.register(
                # models handle "-" in tool names badly, original_name keeps the real one
                name=re.sub(r"\W", "_", f"{server_id}_{tool.name}"),
                description=tool.description or "",
                parameters=tool.input_schema,
                function=None,
                tool_type="mcp",
                requires_confirmation=confirm == "*" or tool.name in confirm,
                server_id=server_id,
                original_name=tool.name,
            )

    def _known(self, server_id: str) -> None:
        if server_id not in self.clients:
            raise ValueError(f"Unknown MCP server: {server_id}")

    async def _stop(self, server_id: str) -> None:
        if self.registry is not None:
            self.registry.unregister_server(server_id)
        try:
            await self.disconnect(server_id)
        except Exception as exc:
            with self.log_file.open("a", encoding="utf-8") as log:
                log.write(f"[mcp] {server_id} failed to close cleanly: {exc}\n")

    async def _start(self, server_id: str) -> bool:
        # a closed client cannot reconnect, its transport is gone
        self.clients[server_id] = self._make_client(server_id)
        if not await self._try_connect(server_id):
            return False
        if self.registry is not None:
            await self._register_server(server_id)
        return True

    async def restart(self, server_id: str) -> bool:
        async with self.lock:
            self._known(server_id)
            await self._stop(server_id)
            if server_id in self.disabled:
                return False
            return await self._start(server_id)

    async def disable(self, server_id: str) -> None:
        async with self.lock:
            self._known(server_id)
            self.disabled.add(server_id)
            await self._stop(server_id)
            self.errors.pop(server_id, None)

    async def enable(self, server_id: str) -> bool:
        async with self.lock:
            self._known(server_id)
            self.disabled.discard(server_id)
            if self.clients[server_id].is_connected():
                return True
            return await self._start(server_id)

    async def reload(self) -> dict[str, list[str]]:
        # unchanged servers are left alone, so a runtime disable survives a reload
        async with self.lock:
            return await self._reload()

    async def add_servers(self, servers: dict) -> dict[str, list[str]]:
        async with self.lock:
            if not servers:
                raise ValueError("No server found in the pasted config")
            new = {server_id: normalize_server(server_id, settings) for server_id, settings in servers.items()}
            taken = [server_id for server_id in new if server_id in self._read_config()]
            if taken:
                raise ValueError(f"Already configured: {', '.join(taken)}")
            self._write_config(lambda current: current.update(new))
            return await self._reload()

    async def remove_server(self, server_id: str) -> dict[str, list[str]]:
        async with self.lock:
            if server_id not in self._read_config():
                raise ValueError(f"Unknown MCP server: {server_id}")
            self._write_config(lambda current: current.pop(server_id))
            return await self._reload()

    async def _reload(self) -> dict[str, list[str]]:
        servers = self._read_config()
        changes = {"added": [], "removed": [], "restarted": []}

        for server_id in [sid for sid in self.clients if sid not in servers]:
            await self._stop(server_id)
            for table in (self.clients, self.settings, self.confirm, self.info, self.errors):
                table.pop(server_id, None)
            self.disabled.discard(server_id)
            changes["removed"].append(server_id)

        for server_id, settings in servers.items():
            if self.settings.get(server_id) == settings:
                continue
            added = server_id not in self.settings
            if not added:
                await self._stop(server_id)
            self.settings[server_id] = settings
            self.confirm[server_id] = settings.get("confirm", [])
            self.errors.pop(server_id, None)
            if settings.get("disabled"):
                self.disabled.add(server_id)
                self.clients[server_id] = self._make_client(server_id)
            else:
                self.disabled.discard(server_id)
                await self._start(server_id)
            changes["added" if added else "restarted"].append(server_id)

        return changes
