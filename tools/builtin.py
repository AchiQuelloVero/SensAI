from tools.registry import ToolRegistry
from tools.sandbox import run_python


def add(a: int, b: int) -> int:
    return a + b


def multiply(a: int, b: int) -> int:
    return a * b


def register_builtin_tools(registry: ToolRegistry) -> None:

    number_parameters = {
        "type": "object",
        "properties": {
            "a": {
                "type": "integer",
                "description": "First number.",
            },
            "b": {
                "type": "integer",
                "description": "Second number.",
            },
        },
        "required": ["a", "b"],
    }

    registry.register(
        name="add",
        description="Add two numbers together.",
        parameters=number_parameters,
        function=add,
    )

    registry.register(
        name="multiply",
        description="Multiply two numbers together.",
        parameters=number_parameters,
        function=multiply,
    )

    registry.register(
        name="run_python",
        description=(
            "Run Python code in a sandbox with 5 s and 128 MB limits (a Docker container with no "
            "network and no access to the user's files, when Docker is available) and return its "
            "stdout, stderr and exit code. Use it to execute "
            "code you wrote, check that it works, or compute something precisely instead of guessing. "
            "If it fails, read the error, fix the code and run it again."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The Python source code to run.",
                },
            },
            "required": ["code"],
        },
        function=run_python,
        requires_confirmation=True,
    )