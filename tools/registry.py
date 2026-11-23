from typing import Any, Callable


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, dict[str, Any]] = {}

    @property
    def tools(self):
        return self._tools

    def register(
        self,
        name: str, #mcp+name jsut to make sure no overlap
        description: str,
        parameters: dict[str, Any],
        function: Callable,
        tool_type: str = "builtin",
        requires_confirmation: bool = False,
        server_id: str | None = None,
        original_name: str | None = None,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")

        self._tools[name] = {
            "description": description,
            "parameters": parameters,
            "type": tool_type,
            "function": function,
            "requires_confirmation": requires_confirmation,
            "server_id": server_id,
            "original_name": original_name,
        }

    def unregister_server(self, server_id: str) -> list[str]:
        names = [name for name, tool in self._tools.items() if tool["server_id"] == server_id]
        for name in names:
            del self._tools[name]
        return names

    def get(self, name: str):
        return self._tools.get(name)

    def to_ollama_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            }
            for name, tool in self._tools.items()
        ]