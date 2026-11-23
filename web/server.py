import asyncio
import copy
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import create_agent
from cache.semantic_cache import CacheError
from cache.service import AnswerCache
from call_tracking.call_log import compute_stats, log_call, log_tool_call, recent_tool_calls
from core.commands import (
    active_persona,
    create_persona,
    persona_rows,
    remove_persona,
    update_persona,
    dispatch_command,
    run_edit_command,
    run_guarded_json_command,
    run_permissions_command,
    run_persona_command,
    run_prompt_command,
    set_system_message,
)
from core.config import (
    LOG_FILE,
    load_app_config,
    save_app_config,
)
from export.publish import load_share, publish_session, render_share
from export.service import export_saved_session
from guardrails.commands import guardrails_view
from guardrails.config import CATEGORIES, GuardrailError, load_config, save_config
from guardrails.guard import guard_input, guard_output
from guardrails.output import StreamMasker, mask_messages, turn_answer, types_to_hide, withdraw_answer
from guardrails.pii import mask_pii
from hallucination.check import check_answer, log_record, regenerate, result_view
from hallucination.commands import hallucination_view
from hallucination.config import HallucinationError, judge_model
from hallucination.config import load_config as load_hallucination_config
from hallucination.config import save_config as save_hallucination_config
from hallucination.grounding import grounding_prompt
from model.ollama import OllamaClient, OllamaError
from model.openrouter import make_model_client
from prompting.persona import PersonaError, guard_config_for, load_persona
from prompting.prompt_builder import PromptError, add_profile, build_system_prompt, load_profile, load_system_prompt
from rag.collections import (
    DEFAULT_NAME,
    create_collection,
    delete_collection,
    get_collection,
    set_chunk_settings,
    set_embed_model,
)
from rag.commands import (
    add_document,
    collection_documents,
    document_chunks,
    list_collections_view,
    remove_document,
    run_kb_try,
)
from rag.errors import RagError
from storage.branching import BranchError, fork_before_user_turn, list_leaves, path_forks, switch_branch, turn_tree
from storage.session_store import (
    SessionError,
    get_session_summaries,
    delete_session,
    initialize_session,
    rename_session,
    save_session,
)
from structured import StructuredOutputError, delete_schema, list_schemas, load_schema, save_schema

STATIC_DIR = Path(__file__).parent / "static"
INDEX_PAGE = STATIC_DIR / "index.html"
SESSION_ID = re.compile(r"\d{8}_\d{6}")
DONE = object()


async def web_confirm_tool(name, arguments):
    state = app.state
    cfg = getattr(state, "config", None)
    log_path = cfg.log_file if cfg else LOG_FILE
    if cfg and cfg.auto_approve:
        log_call(log_path, {"tool": name, "arguments": arguments, "decision": "auto-approved (web)"})
        return "y", arguments

    emit = state.emit
    if emit is None:
        # no page is listening for this turn, so nobody can answer
        log_call(log_path, {"tool": name, "arguments": arguments, "decision": "refused (web, no open stream)"})
        return "n", arguments

    confirm_id = uuid.uuid4().hex
    answer = asyncio.get_running_loop().create_future()
    state.pending_confirms[confirm_id] = answer
    emit(sse("confirm", {"id": confirm_id, "tool": name, "arguments": arguments}))
    try:
        # the turn is cancelled while waiting if the user stops it or the page goes away
        decision, edited = await answer
    finally:
        state.pending_confirms.pop(confirm_id, None)

    if decision == "refuse":
        log_call(log_path, {"tool": name, "arguments": arguments, "decision": "refused (web)"})
        return "n", arguments
    if decision == "edit" and edited != arguments:
        log_call(log_path, {"tool": name, "arguments": edited, "decision": "edited (web)"})
        return "y", edited
    log_call(log_path, {"tool": name, "arguments": arguments, "decision": "approved (web)"})
    return "y", arguments


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_app_config()
    app.state.config = cfg
    (
        app.state.session,
        app.state.messages,
        app.state.model,
        app.state.session_path,
        app.state.prompt_version,
    ) = initialize_session(cfg)
    app.state.hallucination_config = load_hallucination_config(cfg.hallucination_config_path)
    app.state.agent, mcp_manager = await create_agent(
        app.state.model, on_confirm=web_confirm_tool, config=cfg, hallucination_config=app.state.hallucination_config,
    )
    app.state.mcp = mcp_manager
    app.state.guard_config = None if cfg.no_guardrails else load_config(cfg.guardrails_config_path)
    app.state.lock = asyncio.Lock()
    app.state.emit = None  # pushes an SSE event to the page of the turn being generated
    app.state.pending_confirms = {}  # confirm id -> future resolved by POST /confirm/{id}
    try:
        yield
    finally:
        await mcp_manager.disconnect_all()


app = FastAPI(title="Sensai", lifespan=lifespan)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(INDEX_PAGE)


def tool_arguments(call):
    arguments = call.get("function", {}).get("arguments", {})
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            pass
    return arguments


def visible_messages(messages):
    # what the page shows: user text, final assistant text and tool calls, no system prompt
    shown = []
    asked = []  # tool calls of the last assistant message that have no result yet
    for message in messages:
        role = message["role"]
        if role in ("user", "assistant") and message.get("content"):
            shown.append({"role": role, "content": message["content"]})
            if message.get("guardrail"):
                shown[-1]["guardrail"] = message["guardrail"]
            if message.get("check"):
                shown[-1]["check"] = message["check"]
        if role == "assistant":
            asked = list(message.get("tool_calls") or [])
        elif role == "tool":
            call = asked.pop(0) if asked else {}
            ran = message.get("call") or {}  # absent in sessions saved before tool calls were shown
            shown.append({
                "role": "tool",
                "name": message.get("tool_name") or call.get("function", {}).get("name", ""),
                "arguments": ran.get("arguments", tool_arguments(call)),
                "decision": ran.get("decision"),
                "attempt": ran.get("attempt"),
                "max_attempts": ran.get("max_attempts"),
                "server": ran.get("server"),
                "duration_s": ran.get("duration_s"),
                "round": ran.get("round"),
                "result": message.get("content", ""),
            })
    return shown


def persona_view(key):
    if not key:
        return None
    try:
        persona = load_persona(key, app.state.config.personas_dir)
    except PersonaError as error:
        return {"key": key, "name": key, "problem": str(error)}
    return {"key": key, "name": persona["name"]}


def current_session():
    return {
        "id": app.state.session["id"],
        "model": app.state.model,
        "persona": persona_view(app.state.session.get("persona")),
        "rag_collection": app.state.config.rag_collection,
        "forks": path_forks(app.state.session),
        "messages": visible_messages(app.state.messages),
        "context": context_view(app.state.messages),
    }


def context_view(messages):
    used = next((m["context_tokens"] for m in reversed(messages) if m.get("context_tokens")), 0)
    return {"used": used, "window": getattr(app.state.agent.model, "context_window", 0) or 0}


def require_idle():
    if app.state.lock.locked():
        raise HTTPException(status_code=409, detail="a reply is being generated, wait for it to finish")


@app.get("/session")
async def get_current_session():
    return current_session()


def config_view():
    cfg = app.state.config
    return {
        "model": app.state.model,
        "auto_approve": cfg.auto_approve,
        "no_guardrails": cfg.no_guardrails,
        "rag_enabled": cfg.rag_enabled,
        "cache_enabled": cfg.cache_enabled,
        "cache_threshold": cfg.cache_threshold,
        "cache_verify": cfg.cache_verify,
        "cache_mid_chat": cfg.cache_mid_chat,
        "context": context_view(app.state.messages),
    }


class ConfigUpdate(BaseModel):
    model: str | None = None
    auto_approve: bool | None = None
    no_guardrails: bool | None = None
    cache_enabled: bool | None = None
    cache_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    cache_verify: bool | None = None
    cache_mid_chat: bool | None = None


@app.get("/config")
async def get_config():
    return config_view()


@app.get("/models")
async def get_models():
    models, errors = [], []
    try:
        models += await asyncio.to_thread(OllamaClient(app.state.model, app.state.config.host).list_chat_models)
    except OllamaError as error:
        errors.append(str(error))
    if os.environ.get("OPENROUTER_API_KEY"):
        models += app.state.config.openrouter_models
    if not models and errors:
        raise HTTPException(status_code=502, detail="; ".join(errors))
    return models


@app.patch("/config")
async def patch_config(update: ConfigUpdate):
    require_idle()
    cfg = app.state.config

    if update.model is not None and update.model != app.state.model:
        client = make_model_client(update.model, cfg.host, cfg.num_ctx)
        try:
            await asyncio.to_thread(client.check_model_available)
        except OllamaError as error:
            raise HTTPException(status_code=400, detail=str(error))
        app.state.agent.model = client
        app.state.model = update.model
        cfg.model = update.model
        # resuming this chat brings back the model it was last used with, not the one it started with
        app.state.session["model"] = update.model
        if os.path.exists(app.state.session_path):
            try:
                save_session(app.state.session_path, app.state.session, app.state.messages)
            except SessionError as error:
                raise HTTPException(status_code=500, detail=str(error))

    if update.auto_approve is not None:
        cfg.auto_approve = update.auto_approve

    if update.no_guardrails is not None:
        cfg.no_guardrails = update.no_guardrails
        app.state.guard_config = None if cfg.no_guardrails else load_config(cfg.guardrails_config_path)

    for name in ("cache_enabled", "cache_threshold", "cache_verify", "cache_mid_chat"):
        value = getattr(update, name)
        if value is not None:
            setattr(cfg, name, value)

    save_app_config(cfg)
    return config_view()


class GuardrailsUpdate(BaseModel):
    pii: dict[str, Literal["allow", "mask", "block"]] | None = None
    blocked_categories: list[Literal[tuple(CATEGORIES)]] | None = None
    blocked_message: str | None = Field(default=None, min_length=1)
    output_check: bool | None = None


def current_rules():
    if app.state.guard_config is not None:
        return app.state.guard_config
    try:
        return load_config(app.state.config.guardrails_config_path)
    except GuardrailError as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.get("/guardrails")
async def get_guardrails():
    return guardrails_view(app.state.config, current_rules())


@app.patch("/guardrails")
async def patch_guardrails(update: GuardrailsUpdate):
    require_idle()
    rules = copy.deepcopy(current_rules())
    if update.pii is not None:
        rules["pii"].update(update.pii)
    if update.blocked_categories is not None:
        rules["blocked_categories"] = [c for c in CATEGORIES if c in update.blocked_categories]
    if update.blocked_message is not None:
        rules["blocked_message"] = update.blocked_message.strip()
    if update.output_check is not None:
        rules["output_check"] = update.output_check
    try:
        save_config(rules, app.state.config.guardrails_config_path)
    except GuardrailError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if app.state.guard_config is not None:
        app.state.guard_config.clear()
        app.state.guard_config.update(rules)
    return guardrails_view(app.state.config, rules)


class HallucinationUpdate(BaseModel):
    enabled: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    action: Literal["warn", "regenerate", "refuse"] | None = None


@app.get("/hallucination")
async def get_hallucination():
    return hallucination_view(app.state.hallucination_config)


@app.patch("/hallucination")
async def patch_hallucination(update: HallucinationUpdate):
    require_idle()
    rules = copy.deepcopy(app.state.hallucination_config)
    for name in ("enabled", "confidence_threshold", "action"):
        value = getattr(update, name)
        if value is not None:
            rules[name] = value
    try:
        save_hallucination_config(rules, app.state.config.hallucination_config_path)
    except HallucinationError as error:
        raise HTTPException(status_code=400, detail=str(error))
    app.state.hallucination_config.update(rules)
    # the grounding rules go with the check: without it the model is not asked to say "I don't know"
    app.state.agent.grounding_prompt = grounding_prompt(rules)
    return hallucination_view(rules)


def profile_view(profile):
    profile = profile or {}
    return {
        "name": str(profile.get("name") or ""),
        "preferences": {key: str(value) for key, value in (profile.get("preferences") or {}).items()},
        "custom_instructions": str(profile.get("custom_instructions") or ""),
        # what the profile adds to the system prompt
        "injected": add_profile("", profile).strip(),
    }


def read_profile():
    try:
        return load_profile(app.state.config.profile_path, required=False)
    except PromptError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.get("/profile")
async def get_profile():
    return profile_view(read_profile())


class ProfileUpdate(BaseModel):
    name: str = ""
    preferences: dict[str, str] = {}
    custom_instructions: str = ""
    apply_to_chat: bool = True


@app.put("/profile")
async def update_profile(update: ProfileUpdate):
    require_idle()
    state = app.state
    cfg = state.config
    profile = read_profile() or {}  # keeps any keys the page does not edit

    for key, value in (("name", update.name.strip()), ("custom_instructions", update.custom_instructions.strip())):
        if value:
            profile[key] = value
        else:
            profile.pop(key, None)
    preferences = {key.strip(): value.strip() for key, value in update.preferences.items() if key.strip()}
    if preferences:
        profile["preferences"] = preferences
    else:
        profile.pop("preferences", None)

    system_prompt = None
    if update.apply_to_chat:
        try:
            persona = active_persona(state.session, cfg)
            system_prompt = build_system_prompt(load_system_prompt(cfg.system_prompt_path), profile, persona)
        except (PersonaError, PromptError) as error:
            raise HTTPException(status_code=400, detail=str(error))

    tmp_path = f"{cfg.profile_path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, cfg.profile_path)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"cannot write {cfg.profile_path}: {error}")

    if system_prompt is not None:
        set_system_message(state.session, state.messages, system_prompt)
        # a chat that was never saved gets the profile with the first message
        if os.path.exists(state.session_path):
            try:
                save_session(state.session_path, state.session, state.messages)
            except SessionError as error:
                raise HTTPException(status_code=500, detail=str(error))
    return profile_view(profile)


@app.get("/tools")
async def get_tools():
    state = app.state
    registry = state.agent.tool_registry.tools
    mcp = state.mcp
    servers = []
    for server_id, client in mcp.clients.items():
        servers.append({
            "id": server_id,
            **mcp.info.get(server_id, {}),
            "connected": client.is_connected(),
            "disabled": server_id in mcp.disabled,
            "error": mcp.errors.get(server_id, ""),
            "tools": sum(1 for tool in registry.values() if tool["server_id"] == server_id),
        })
    tools = [
        {
            "name": name,
            "description": tool["description"],
            "type": tool["type"],
            "server": tool["server_id"],
            "original_name": tool["original_name"],
            "requires_confirmation": tool["requires_confirmation"],
            "parameters": tool["parameters"],
        }
        for name, tool in registry.items()
    ]
    return {
        "servers": servers,
        "tools": tools,
    }


@app.get("/tools/calls")
async def get_tool_calls(limit: int = 50):
    return {"calls": recent_tool_calls(app.state.config.log_file, max(1, min(limit, 500)))}


@app.post("/mcp/reload")
async def reload_mcp():
    require_idle()
    try:
        changes = await app.state.mcp.reload()
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"Could not reload the MCP config: {error}")
    return {"changes": changes, **await get_tools()}


class AddServersRequest(BaseModel):
    config: dict
    id: str | None = None


def pasted_servers(config: dict, server_id: str | None) -> dict:
    # accepts {"mcpServers": {...}}, {"name": {...}} or a single server's settings plus a name
    if "mcpServers" in config:
        return config["mcpServers"]
    if "command" in config or "url" in config:
        if not server_id:
            raise ValueError("Give the server a name")
        return {server_id: config}
    return config


@app.post("/mcp/servers")
async def add_mcp_servers(request: AddServersRequest):
    require_idle()
    try:
        changes = await app.state.mcp.add_servers(pasted_servers(request.config, request.id))
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"changes": changes, **await get_tools()}


@app.delete("/mcp/servers/{server_id}")
async def remove_mcp_server(server_id: str):
    require_idle()
    try:
        changes = await app.state.mcp.remove_server(server_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except OSError as error:
        raise HTTPException(status_code=500, detail=str(error))
    return {"changes": changes, **await get_tools()}


@app.post("/mcp/{server_id}/{action}")
async def control_mcp(server_id: str, action: Literal["restart", "disable", "enable"]):
    require_idle()
    mcp = app.state.mcp
    if server_id not in mcp.clients:
        raise HTTPException(status_code=404, detail=f"Unknown MCP server: {server_id}")
    await getattr(mcp, action)(server_id)
    return await get_tools()


@app.get("/stats")
async def get_stats():
    return await asyncio.to_thread(compute_stats, app.state.config.log_file)


def cache_store():
    try:
        return AnswerCache(app.state.config).store()
    except CacheError as error:
        raise HTTPException(status_code=500, detail=str(error))


def cache_view():
    store = cache_store()
    try:
        return {**store.stats(), "entries": store.entries()}
    except CacheError as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.get("/cache")
async def get_cache():
    return await asyncio.to_thread(cache_view)


@app.delete("/cache/entries/{entry_id}")
async def delete_cache_entry(entry_id: int):
    try:
        deleted = await asyncio.to_thread(cache_store().delete, entry_id)
    except CacheError as error:
        raise HTTPException(status_code=500, detail=str(error))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"no saved answer with id {entry_id}")
    return await asyncio.to_thread(cache_view)


@app.delete("/cache/entries")
async def clear_cache():
    try:
        await asyncio.to_thread(cache_store().clear)
    except CacheError as error:
        raise HTTPException(status_code=500, detail=str(error))
    return await asyncio.to_thread(cache_view)


@app.get("/schemas")
async def get_schemas():
    return list_schemas(app.state.config.schemas_dir)


@app.get("/schemas/{name}")
async def get_schema(name: str):
    try:
        return load_schema(name, app.state.config.schemas_dir)
    except StructuredOutputError as error:
        raise HTTPException(status_code=404, detail=str(error))


class NewSchema(BaseModel):
    name: str
    schema_: Any = Field(alias="schema")


@app.post("/schemas")
async def add_schema(new: NewSchema):
    try:
        save_schema(new.name, new.schema_, app.state.config.schemas_dir)
    except StructuredOutputError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return list_schemas(app.state.config.schemas_dir)


@app.delete("/schemas/{name}")
async def remove_schema(name: str):
    try:
        delete_schema(name, app.state.config.schemas_dir)
    except StructuredOutputError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return list_schemas(app.state.config.schemas_dir)


def save_active_session_if_any():
    state = app.state
    if os.path.exists(state.session_path):
        try:
            save_session(state.session_path, state.session, state.messages)
        except SessionError as error:
            raise HTTPException(status_code=500, detail=str(error))


@app.get("/kb")
async def get_knowledge_bases():
    cfg = app.state.config
    return {"collections": list_collections_view(cfg)["collections"], "suggested_models": cfg.rag_embed_models}


class KbCreateRequest(BaseModel):
    name: str
    embed_model: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None


@app.post("/kb")
async def create_knowledge_base(request: KbCreateRequest):
    require_idle()
    try:
        create_collection(
            app.state.config, request.name, request.embed_model, request.chunk_size, request.chunk_overlap
        )
    except RagError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return list_collections_view(app.state.config)["collections"]


class KbModelRequest(BaseModel):
    embed_model: str


@app.patch("/kb/{name}/model")
async def change_knowledge_base_model(name: str, request: KbModelRequest):
    require_idle()
    try:
        set_embed_model(app.state.config, name, request.embed_model)
    except RagError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return list_collections_view(app.state.config)["collections"]


class KbChunkingRequest(BaseModel):
    chunk_size: int
    chunk_overlap: int


@app.patch("/kb/{name}/chunking")
async def change_knowledge_base_chunking(name: str, request: KbChunkingRequest):
    require_idle()
    try:
        set_chunk_settings(app.state.config, name, request.chunk_size, request.chunk_overlap)
    except RagError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return list_collections_view(app.state.config)["collections"]


@app.delete("/kb/{name}")
async def delete_knowledge_base(name: str):
    require_idle()
    state = app.state
    try:
        delete_collection(state.config, name)
    except RagError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if state.config.rag_collection == name:
        state.config.rag_collection = DEFAULT_NAME
        state.session["rag_collection"] = DEFAULT_NAME
        save_active_session_if_any()
    return list_collections_view(state.config)["collections"]


@app.post("/kb/{name}/use")
async def use_knowledge_base(name: str):
    require_idle()
    state = app.state
    try:
        get_collection(state.config, name)
    except RagError as error:
        raise HTTPException(status_code=404, detail=str(error))
    state.config.rag_collection = name
    state.session["rag_collection"] = name
    save_active_session_if_any()
    return list_collections_view(state.config)["collections"]


@app.get("/kb/{name}/documents")
async def get_kb_documents(name: str):
    try:
        collection = get_collection(app.state.config, name)
    except RagError as error:
        raise HTTPException(status_code=404, detail=str(error))
    documents = await asyncio.to_thread(collection_documents, app.state.config, collection)
    return [{"source": d.source, "chunks": d.chunk_count} for d in documents]


@app.post("/kb/{name}/documents")
async def upload_kb_document(name: str, file: UploadFile = File(...)):
    require_idle()
    data = await file.read()
    result = await asyncio.to_thread(add_document, app.state.config, name, file.filename, data)
    if not result["ok"] and "error" in result:
        raise HTTPException(status_code=400, detail=result["output"])
    return result


@app.delete("/kb/{name}/documents/{source:path}")
async def delete_kb_document(name: str, source: str):
    require_idle()
    result = await asyncio.to_thread(remove_document, app.state.config, name, source)
    if not result["ok"] and "error" in result:
        raise HTTPException(status_code=400, detail=result["output"])
    return result


@app.get("/kb/{name}/chunks")
async def get_kb_document_chunks(name: str, source: str):
    try:
        collection = get_collection(app.state.config, name)
    except RagError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return await asyncio.to_thread(document_chunks, app.state.config, collection, source)


class KbTryRequest(BaseModel):
    question: str


@app.post("/kb/{name}/try")
async def try_knowledge_base(name: str, request: KbTryRequest):
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="empty question")
    result = await asyncio.to_thread(run_kb_try, app.state.config, name, question)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["output"])
    return result["result"]


@app.get("/personas")
async def get_personas():
    return persona_rows(app.state.config, app.state.session.get("persona"))


class NewPersona(BaseModel):
    key: str
    persona: dict


def save_current_session():
    state = app.state
    if os.path.exists(state.session_path):
        try:
            save_session(state.session_path, state.session, state.messages)
        except SessionError as error:
            raise HTTPException(status_code=500, detail=str(error))


@app.post("/personas")
async def add_persona(new: NewPersona):
    try:
        create_persona(app.state.config, new.key.strip(), new.persona)
    except PersonaError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return await get_personas()


@app.patch("/personas/{key}")
async def edit_persona(key: str, changes: dict):
    require_idle()
    state = app.state
    try:
        await asyncio.to_thread(update_persona, state.config, state.session, state.messages, key, changes)
    except (PersonaError, PromptError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    if state.session.get("persona") == key:
        save_current_session()
    return {"personas": await get_personas(), "current": persona_view(state.session.get("persona"))}


@app.delete("/personas/{key}")
async def delete_persona_file(key: str):
    try:
        remove_persona(app.state.config, app.state.session, key)
    except PersonaError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return await get_personas()


class PersonaRequest(BaseModel):
    key: str


@app.post("/persona")
async def switch_persona(request: PersonaRequest):
    require_idle()
    state = app.state
    result = run_persona_command(state.config, state.session, state.messages, request.key)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    # a chat that was never saved gets its persona with the first message
    if os.path.exists(state.session_path):
        try:
            save_session(state.session_path, state.session, state.messages)
        except SessionError as error:
            raise HTTPException(status_code=500, detail=str(error))
    return persona_view(result["persona"])


class PermissionsRequest(BaseModel):
    action: Literal["set", "remove", "default"]
    path: str = ""
    access: str = ""


@app.get("/permissions")
async def get_permissions():
    state = app.state
    result = run_permissions_command(state.config, state.session, state.messages, "")
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["error"])
    return result["permissions"]


@app.post("/permissions")
async def change_permissions(request: PermissionsRequest):
    require_idle()
    state = app.state
    text = " ".join(part for part in (request.action, request.path, request.access) if part)
    result = run_permissions_command(state.config, state.session, state.messages, text)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error") or result["output"])
    if os.path.exists(state.session_path):
        try:
            save_session(state.session_path, state.session, state.messages)
        except SessionError as error:
            raise HTTPException(status_code=500, detail=str(error))
    return result["permissions"]


def prompt_result(result, status=400):
    if not result["ok"]:
        raise HTTPException(status_code=status, detail=result.get("error") or result["output"])
    return result


@app.get("/prompt/versions")
async def get_prompt_versions():
    state = app.state
    result = run_prompt_command(state.model, state.config, state.session, state.messages, {}, "versions")
    # `chat` is the version this chat was started with, unknown for a resumed one
    return {"versions": result["versions"], "chat": state.prompt_version}


@app.get("/prompt/versions/{version}")
async def get_prompt_version(version: int):
    state = app.state
    result = run_prompt_command(state.model, state.config, state.session, state.messages, {}, f"show {version}")
    return {"version": version, "content": prompt_result(result, 404)["content"]}


class RollbackRequest(BaseModel):
    version: int


@app.post("/prompt/rollback")
async def rollback_prompt(request: RollbackRequest):
    require_idle()
    state = app.state
    prompt_state = {"version": state.prompt_version}
    result = prompt_result(
        run_prompt_command(
            state.model, state.config, state.session, state.messages, prompt_state, f"rollback {request.version}"
        )
    )
    state.prompt_version = prompt_state["version"]
    # a chat that was never saved gets the prompt with the first message
    if os.path.exists(state.session_path):
        try:
            save_session(state.session_path, state.session, state.messages)
        except SessionError as error:
            raise HTTPException(status_code=500, detail=str(error))
    return {"version": result["version"]}


class CompareRequest(BaseModel):
    version_a: int
    version_b: int
    question: str


@app.post("/prompt/compare")
async def compare_prompts(request: CompareRequest):
    require_idle()
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="empty question")
    state = app.state
    async with state.lock:  # two model calls, keep the chat from competing with them
        result = await asyncio.to_thread(
            run_prompt_command,
            state.model,
            state.config,
            state.session,
            state.messages,
            {},
            f"compare {request.version_a} {request.version_b} {question}",
        )
    return {"comparisons": prompt_result(result)["comparisons"]}


@app.get("/sessions")
async def list_sessions():
    try:
        rows = get_session_summaries(app.state.config.sessions_dir)
    except SessionError as error:
        raise HTTPException(status_code=500, detail=str(error))
    rows.sort(key=lambda row: row[1], reverse=True)
    return [
        {
            "id": id_, "updated_at": updated, "model": model, "turns": turns, "title": title,
            "persona": persona, "prompt_version": prompt_version,
        }
        for id_, updated, model, turns, title, persona, prompt_version in rows
    ]


def begin_new_session():
    (
        app.state.session,
        app.state.messages,
        app.state.model,
        app.state.session_path,
        app.state.prompt_version,
    ) = initialize_session(app.state.config, model_override=app.state.model)


@app.post("/sessions/new")
async def start_new_session():
    require_idle()
    begin_new_session()
    return current_session()


def checked_session_id(session_id):
    if not SESSION_ID.fullmatch(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return session_id


class RenameRequest(BaseModel):
    title: str


@app.patch("/sessions/{session_id}")
async def rename_saved_session(session_id: str, request: RenameRequest):
    require_idle()
    checked_session_id(session_id)
    title = " ".join(request.title.split())[:80]
    try:
        rename_session(app.state.config.sessions_dir, session_id, title)
    except SessionError as error:
        raise HTTPException(status_code=404, detail=str(error))
    if session_id == app.state.session["id"]:
        # the next save writes this dict, it must not bring the old title back
        if title:
            app.state.session["title"] = title
        else:
            app.state.session.pop("title", None)
    return {"id": session_id, "title": title}


@app.delete("/sessions/{session_id}")
async def delete_saved_session(session_id: str):
    require_idle()
    checked_session_id(session_id)
    try:
        delete_session(app.state.config.sessions_dir, session_id)
    except SessionError as error:
        raise HTTPException(status_code=404, detail=str(error))
    if session_id == app.state.session["id"]:
        begin_new_session()  # the chat on screen is gone, move to a fresh one
        return {"session": current_session()}
    return {"session": None}


@app.post("/sessions/{session_id}/publish")
async def publish_saved_session(session_id: str):
    checked_session_id(session_id)
    try:
        token = publish_session(session_id, app.state.config.sessions_dir)
    except SessionError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return {"token": token, "url": f"/s/{token}"}


EXPORT_TYPES = {"json": "application/json", "markdown": "text/markdown", "pdf": "application/pdf"}


@app.get("/sessions/{session_id}/export")
async def export_session(session_id: str, format: Literal["json", "markdown", "pdf"] = "json"):
    checked_session_id(session_id)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path = export_saved_session(session_id, app.state.config.sessions_dir, format, os.path.join(tmp, "out"))
        except SessionError as error:
            raise HTTPException(status_code=404, detail=str(error))
        with open(path, "rb") as file:
            body = file.read()
    name = f"sensai_{session_id}.{'md' if format == 'markdown' else format}"
    return Response(body, media_type=EXPORT_TYPES[format], headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/s/{token}", response_class=HTMLResponse)
async def view_share(token: str):
    snapshot = load_share(token)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="share not found")
    return render_share(snapshot)


@app.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str):
    require_idle()
    cfg = app.state.config
    if not SESSION_ID.fullmatch(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    try:
        session, messages, model, session_path, prompt_version = initialize_session(
            cfg, resume_id=session_id
        )
    except SessionError as error:
        raise HTTPException(status_code=404, detail=str(error))

    # same rule as the CLI: a resumed session keeps the model it was started with
    if model != app.state.model:
        client = make_model_client(model, cfg.host, cfg.num_ctx)
        try:
            await asyncio.to_thread(client.check_model_available)
        except OllamaError as error:
            raise HTTPException(status_code=400, detail=str(error))
        app.state.agent.model = client
        app.state.model = model

    app.state.session = session
    app.state.messages = messages
    app.state.session_path = session_path
    app.state.prompt_version = prompt_version
    return current_session()


@app.get("/branches")
async def get_branches():
    rows = list_leaves(app.state.session)
    return [
        {"id": leaf_id, "current": is_current, "preview": preview}
        for leaf_id, is_current, preview in rows
    ]


@app.get("/branches/tree")
async def get_branch_tree():
    return turn_tree(app.state.session)


@app.post("/branches/{leaf_id}/switch")
async def switch_to_branch(leaf_id: str):
    require_idle()
    try:
        app.state.messages = switch_branch(app.state.session, leaf_id)
    except BranchError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return current_session()


class EditRequest(BaseModel):
    n: int
    text: str


@app.post("/edit")
async def edit_message(request: EditRequest):
    require_idle()
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="empty message")
    try:
        app.state.messages = fork_before_user_turn(app.state.session, request.n)
    except BranchError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return current_session()


class ConfirmAnswer(BaseModel):
    decision: Literal["approve", "refuse", "edit"]
    arguments: dict | None = None


@app.post("/confirm/{confirm_id}")
async def answer_confirm(confirm_id: str, answer: ConfirmAnswer):
    pending = app.state.pending_confirms.get(confirm_id)
    if pending is None or pending.done():
        raise HTTPException(status_code=404, detail="no tool call is waiting for this answer")
    if answer.decision == "edit" and answer.arguments is None:
        raise HTTPException(status_code=400, detail="edit needs the new arguments")
    pending.set_result((answer.decision, answer.arguments))
    return {"ok": True}


class ChatRequest(BaseModel):
    message: str
    json_schema: str | None = None  # JSON mode: answer once as JSON matching schemas/<name>.json


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def blocked_event(decision, guard_config) -> str:
    return sse("blocked", {
        "message": guard_config["blocked_message"],
        "category": decision["category"],
        "reason": decision.get("reason", ""),
        "pii": decision.get("pii") or {},
    })


@app.post("/chat")
async def chat(request: ChatRequest):
    state = app.state
    cfg = state.config
    user_input = request.message.strip()
    if not user_input:
        raise HTTPException(status_code=400, detail="empty message")
    if request.json_schema and request.json_schema not in list_schemas(cfg.schemas_dir):
        raise HTTPException(status_code=400, detail=f"unknown schema: {request.json_schema}")
    if state.lock.locked():
        raise HTTPException(status_code=409, detail="a reply is already being generated")

    async def events():
        nonlocal user_input
        async with state.lock:
            messages = state.messages

            cmd_res = None
            if request.json_schema or user_input == "/json" or user_input.startswith("/json "):
                # the schema picker and a typed /json share the guarded path of the CLI
                if request.json_schema:
                    json_text = f"{request.json_schema} {user_input}"
                else:
                    json_text = user_input[len("/json"):].strip()
                cmd_res = await asyncio.to_thread(
                    run_guarded_json_command, state.model, messages, json_text, cfg, state.session, state.guard_config
                )
                if cmd_res.get("blocked"):
                    yield blocked_event(cmd_res["blocked"], state.guard_config)
                    return
                if cmd_res.get("input_pii"):
                    yield sse("masked", {"pii": cmd_res["input_pii"]})
                if cmd_res.get("output_pii"):
                    yield sse("output_masked", {"pii": cmd_res["output_pii"]})
            elif user_input == "/edit" or user_input.startswith("/edit "):
                edit_res = run_edit_command(
                    state.session,
                    messages,
                    user_input[len("/edit"):].strip(),
                )
                if not edit_res["ok"]:
                    yield sse("error", {"message": edit_res["output"]})
                    return
                user_input = edit_res["new_content"]
            elif user_input.startswith("/"):
                prompt_state = {"version": state.prompt_version}
                cmd_res = await asyncio.to_thread(
                    dispatch_command,
                    user_input,
                    config=cfg,
                    model=state.model,
                    messages=messages,
                    session=state.session,
                    prompt_state=prompt_state,
                    guard_config=state.guard_config,
                )
                state.prompt_version = prompt_state["version"]
                if user_input.split()[:2] in (["/guardrails", "on"], ["/guardrails", "off"]):
                    state.guard_config = None if cfg.no_guardrails else load_config(cfg.guardrails_config_path)

            if cmd_res is not None:
                if not cmd_res["ok"] and "error" in cmd_res:
                    yield sse("error", {"message": cmd_res["output"]})
                    return
                yield sse("token", {"text": cmd_res["output"]})
                yield sse("done", {"stats": {}, "command": cmd_res})
                return

            turn_start = len(messages)
            start = time.perf_counter()
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()

            # model.chat runs in a worker thread, so chunks are handed over thread-safely
            def push(item):
                loop.call_soon_threadsafe(queue.put_nowait, item)

            state.emit = push
            on_chunk = lambda text: push(sse("token", {"text": text}))
            masker = None
            if state.guard_config is not None:
                masker = StreamMasker(types_to_hide(state.guard_config), on_chunk)
                on_chunk = masker

            def on_tool_call(name, trace, content):
                if masker is not None:
                    masker.flush()  # text the model wrote before the call goes out first
                    content, counts = mask_pii(content, masker.types)
                    for pii_type, count in counts.items():
                        masker.counts[pii_type] = masker.counts.get(pii_type, 0) + count
                if trace["decision"] == "approved" and cfg.auto_approve:
                    trace["decision"] = "auto-approved"  # trace is the dict saved in the message too
                log_tool_call(cfg.log_file, name, trace, content, state.session["id"])
                push(sse("tool_call", {
                    "name": name,
                    "arguments": trace["arguments"],
                    "decision": trace["decision"],
                    "attempt": trace.get("attempt"),
                    "max_attempts": trace.get("max_attempts"),
                    "server": trace.get("server"),
                    "duration_s": trace.get("duration_s"),
                    "round": trace.get("round"),
                    "result": content,
                }))

            def on_tool_event(event):
                if event["phase"] != "start":
                    return
                if masker is not None:
                    masker.flush()
                tool = state.agent.tool_registry.get(event["name"]) or {}
                push(sse("tool_start", {
                    "name": event["name"],
                    "server": tool.get("server_id"),
                    "arguments": event["arguments"],
                    "round": event["round"],
                    "attempt": event.get("attempt"),
                    "max_attempts": event.get("max_attempts"),
                }))

            # the text that passed the guardrails (masked), or None while still unchecked
            checked = {"text": None if state.guard_config is not None else user_input}

            async def produce():
                prompt_text = user_input
                if state.guard_config is not None:
                    guard_config = guard_config_for(state.session, state.guard_config, cfg.personas_dir)
                    decision = await asyncio.to_thread(guard_input, state.model, user_input, guard_config)
                    log_call(cfg.log_file, {"guardrail": "input", **{k: v for k, v in decision.items() if k != "text"}})
                    if decision["action"] == "block":
                        push(blocked_event(decision, state.guard_config))
                        return None
                    if decision["pii"]:
                        push(sse("masked", {"pii": decision["pii"]}))
                    prompt_text = decision["text"]
                    checked["text"] = prompt_text
                try:
                    return await state.agent.run_turn(
                        messages=messages,
                        user_input=prompt_text,
                        on_text_chunk=on_chunk,
                        on_tool_call=on_tool_call,
                        on_tool_event=on_tool_event,
                    )
                finally:
                    if masker is not None:
                        masker.flush()

            task = asyncio.create_task(produce())
            task.add_done_callback(lambda _: push(DONE))

            try:
                try:
                    while (item := await queue.get()) is not DONE:
                        yield item
                except (GeneratorExit, asyncio.CancelledError):
                    task.cancel()
                    del messages[turn_start:]
                    # stopped before the guardrails answered: drop the message, like the CLI does
                    if checked["text"] is not None:
                        messages.append({"role": "user", "content": checked["text"], "interrupted": True})
                        save_session(state.session_path, state.session, messages)
                    raise

                try:
                    response = task.result()
                except (OllamaError, RagError, RuntimeError) as error:
                    del messages[turn_start:]
                    log_call(cfg.log_file, {
                        "model": state.model,
                        "prompt_version": state.prompt_version,
                        "error": str(error),
                        "total_duration_s": time.perf_counter() - start,
                    })
                    yield sse("error", {"message": str(error)})
                    return

                if response is None:
                    del messages[turn_start:]
                    return

                if masker is not None:
                    mask_messages(messages[turn_start:], masker.types)
                    if masker.counts:
                        log_call(cfg.log_file, {"guardrail": "output", "action": "mask", "pii": masker.counts})
                        yield sse("output_masked", {"pii": masker.counts})

                outcome = {"output_check": None, "withdrawn": False, "fact_check": None}

                async def answer_check():
                    """The guardrails' check of the answer: harmful or leaking answers are withdrawn."""
                    if state.guard_config is None:
                        return
                    guard_config = guard_config_for(state.session, state.guard_config, cfg.personas_dir)
                    yield sse("output_checking", {})
                    check_start = time.perf_counter()
                    output_check = await asyncio.to_thread(
                        guard_output, state.model, messages[turn_start]["content"], turn_answer(messages, turn_start), guard_config,
                    )
                    outcome["output_check"] = output_check
                    if output_check is not None:
                        output_check["duration_s"] = round(time.perf_counter() - check_start, 2)
                        log_call(cfg.log_file, {"guardrail": "output", **output_check})
                        if output_check["action"] == "block":
                            outcome["withdrawn"] = True
                            output_check["forgotten"] = await asyncio.to_thread(
                                withdraw_answer, messages, turn_start, state.guard_config, output_check, state.agent.answer_store,
                            )
                            yield sse("output_blocked", {"message": state.guard_config["blocked_message"], **output_check})

                async def drain(work):
                    """The events pushed while `work` runs (the answer written again streams through them)."""
                    work.add_done_callback(lambda _: push(DONE))
                    try:
                        while (item := await queue.get()) is not DONE:
                            yield item
                    except (GeneratorExit, asyncio.CancelledError):
                        work.cancel()
                        raise

                async def check_once(judge):
                    try:
                        result = await asyncio.to_thread(
                            check_answer, state.agent.model, judge, state.hallucination_config, messages, turn_start, response,
                        )
                        return result, None
                    except (StructuredOutputError, OllamaError) as error:
                        log_call(cfg.log_file, {"hallucination": "check", "judge_model": judge, "error": str(error)})
                        return None, str(error)

                async def fact_check():
                    """Checks the claims of the streamed answer and acts on the unsupported ones, like the terminal chat."""
                    hconfig = state.hallucination_config
                    judge = judge_model(hconfig, state.model)
                    yield sse("fact_checking", {"again": False})
                    first, reason = await check_once(judge)
                    if first is None:
                        yield sse("fact_unchecked", {"reason": reason})
                        return
                    action = hconfig["action"] if first.flagged else "none"
                    yield sse("fact_check", {"action": action, "again": False, **result_view(first)})
                    final, regenerated = first, None
                    answer = messages[-1]["content"]
                    question = messages[turn_start]["content"]

                    if action == "refuse":
                        messages[-1]["content"] = hconfig["refusal_message"]
                        await asyncio.to_thread(state.agent.answer_store.forget, question, answer)
                        yield sse("fact_refused", {"message": hconfig["refusal_message"]})
                    elif action == "regenerate":
                        yield sse("fact_regenerating", {})
                        write = lambda text: push(sse("token", {"text": text}))
                        again_masker = None
                        if state.guard_config is not None:
                            again_masker = StreamMasker(types_to_hide(state.guard_config), write)

                        def rewrite():
                            try:
                                return regenerate(state.agent.model, response, answer, first.flagged, again_masker or write)
                            finally:
                                if again_masker is not None:
                                    again_masker.flush()  # before the worker ends, while the stream is still read

                        work = asyncio.create_task(asyncio.to_thread(rewrite))
                        try:
                            async for item in drain(work):
                                yield item
                            new = work.result()
                        except OllamaError as error:
                            yield sse("fact_kept", {"message": answer, "reason": str(error)})
                            new = None
                        if new is not None:
                            messages[-1]["content"] = new.content
                            if again_masker is not None:
                                mask_messages(messages[-1:], again_masker.types)
                            await asyncio.to_thread(state.agent.answer_store.forget, question, answer)
                            yield sse("fact_checking", {"again": True})
                            regenerated, reason = await check_once(judge)
                            if regenerated is None:
                                yield sse("fact_unchecked", {"reason": reason, "again": True})
                            else:
                                final = regenerated
                                yield sse("fact_check", {"action": "none", "again": True, **result_view(regenerated)})

                    # kept in the session, so later checks do not take these claims as evidence
                    if final.flagged and action != "refuse":
                        messages[-1]["check"] = {"action": action, "flagged": [v["claim"] for v in final.flagged]}
                    outcome["fact_check"] = {"claims": len(final.verdicts), "flagged": len(final.flagged), "duration_s": final.duration_s}
                    log_call(cfg.log_file, log_record(first, judge, action, regenerated))
                    if action == "regenerate" and messages[-1]["content"] != answer:
                        # the new answer goes through the guardrails' answer check too
                        async for item in answer_check():
                            yield item

                try:
                    async for item in answer_check():
                        yield item
                    if state.hallucination_config["enabled"] and not response.cached and not outcome["withdrawn"]:
                        async for item in fact_check():
                            yield item
                except (GeneratorExit, asyncio.CancelledError):
                    # stopped while the answer was checked: the answer is complete, keep it
                    save_session(state.session_path, state.session, messages)
                    raise
                output_check = outcome["output_check"]

                if not response.cached and response.stats.get("context_tokens"):
                    messages[-1]["context_tokens"] = response.stats["context_tokens"]
                save_session(state.session_path, state.session, messages)

                if not response.cached:
                    log_call(cfg.log_file, {"model": state.model, "prompt_version": state.prompt_version, **response.stats})
                yield sse("done", {
                    "stats": response.stats,
                    "cached": response.cached,
                    "saved_s": response.saved_s,
                    "cache_note": response.cache_note,
                    "output_check": output_check,
                    "fact_check": outcome["fact_check"],
                    "context": context_view(messages),
                })
            finally:
                state.emit = None
                task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
