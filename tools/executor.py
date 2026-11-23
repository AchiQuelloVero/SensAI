import inspect
import json
import time
from typing import Any, Awaitable, Callable

from core.contracts import ToolLookup
from tools.mcp.MCP_manager import MCPManager
from tools.permissions import check

# may be sync (CLI prompt) or async (web UI waiting for the user's answer)
Confirmer = Callable[[str, dict], tuple[str, dict] | Awaitable[tuple[str, dict]]]


class ToolExecutor:

    def __init__(
        self,
        registry: ToolLookup,
        mcp_manager: MCPManager | None = None,
        on_confirm: Confirmer | None = None,
    ):
        self.registry = registry
        self.mcp_manager = mcp_manager
        self.on_confirm = on_confirm

    # `trace`, when given, is filled with the arguments that actually ran and the
    # confirmation decision (None when the tool needed no confirmation)
    async def execute(self, name: str, arguments: Any, trace: dict | None = None) -> dict:
        tool = self.registry.get(name)

        if tool is None:
            return {"error": f"Unknown tool: {name}"}
        if trace is not None and tool.get("server_id"):
            trace["server"] = tool["server_id"]

        if tool.get("type") not in ("builtin", "api", "mcp"):
            return {
                "error": f"Unknown tool type: {tool.get('type')}"
            }

        if not isinstance(arguments, dict):
            return {
                "error": (
                    f"Arguments for {name} must be a JSON object, "
                    f"got: {arguments!r}"
                )
            }

        denied = self.check_permissions(tool, arguments, trace)
        if denied:
            return denied

        edited = False
        if tool.get("requires_confirmation") and self.on_confirm is not None:
            asked = arguments
            answer = self.on_confirm(name, arguments)
            if inspect.isawaitable(answer):
                answer = await answer
            decision, arguments = answer
            edited = arguments != asked
            if trace is not None:
                trace["arguments"] = arguments
                trace["decision"] = "refused" if decision == "n" else "edited" if edited else "approved"
            if decision == "n":
                return {"error": "user refused to run this tool"}
            # the user may have edited the paths
            denied = self.check_permissions(tool, arguments, trace)
            if denied:
                return denied

        started = time.perf_counter()
        try:
            result = await self._run(name, tool, arguments)
        finally:
            # only the run itself, not the time spent waiting for the user's approval
            if trace is not None:
                trace["duration_s"] = round(time.perf_counter() - started, 2)
        if edited and "result" in result:
            # the model only knows what it asked for, so it would report the original arguments
            result["note"] = (
                f"The user edited the arguments before approving. Asked for: {json.dumps(asked, ensure_ascii=False)}. "
                f"Ran with: {json.dumps(arguments, ensure_ascii=False)}."
            )
        return result

    async def _run(self, name: str, tool: dict, arguments: dict) -> dict:
        try:
            if tool["type"] == "mcp":
                if self.mcp_manager is None:
                    return {"error": f"No MCP manager for tool: {name}"}
                result = await self.mcp_manager.execute(
                    tool["server_id"], tool["original_name"], arguments
                )
            else:
                result = tool["function"](**arguments)
                if inspect.isawaitable(result):
                    result = await result
            return {"result": result}
        except Exception as exc:
            return {
                "error": (
                    f"{name} failed: "
                    f"{type(exc).__name__}: {exc}"
                )
            }

    def check_permissions(self, tool: dict, arguments: dict, trace: dict | None) -> dict | None:
        if tool.get("type") != "mcp" or tool.get("server_id") != "filesystem":
            return None
        reason = check(tool["original_name"], arguments)
        if reason is None:
            return None
        if trace is not None:
            trace["decision"] = "denied"
        return {"error": f"{reason}. Do not retry: tell the user, they can change it with /permissions."}
