from cache.service import AnswerCache
from core.agent import AgentRuntime
from core.config import AppConfig, load_app_config
from hallucination.config import load_config as load_hallucination_config
from hallucination.grounding import grounding_prompt
from model.openrouter import make_model_client
from rag.service import RagService
from tools.builtin import register_builtin_tools
from tools.executor import ToolExecutor
from tools.mcp.MCP_manager import MCPManager
from tools.registry import ToolRegistry


async def create_agent(
    model_name: str | None = None,
    on_confirm=None,
    config: AppConfig | None = None,
    hallucination_config: dict | None = None,
) -> tuple[AgentRuntime, MCPManager]:
    cfg = config or load_app_config()
    model_client = make_model_client(model_name or cfg.model, cfg.host, cfg.num_ctx)
    model_client.check_model_available()
    # before the MCP servers start, so a bad config does not leave them running
    if hallucination_config is None:
        hallucination_config = load_hallucination_config(cfg.hallucination_config_path)

    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)

    mcp_manager = MCPManager(
        config_path=cfg.mcp_config_path,
        log_file=cfg.mcp_log_file,
    )
    mcp_manager.start()
    await mcp_manager.connect_all()
    await mcp_manager.register_tools(tool_registry)

    tool_executor = ToolExecutor(tool_registry, mcp_manager, on_confirm=on_confirm)

    agent = AgentRuntime(
        model=model_client,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        max_tool_rounds=cfg.max_tool_rounds,
        context_provider=RagService(cfg),
        grounding_prompt=grounding_prompt(hallucination_config),
        answer_store=AnswerCache(cfg),
        max_tool_attempts=cfg.max_tool_attempts,
    )
    return agent, mcp_manager

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web.server:app", host="127.0.0.1", port=8000)
