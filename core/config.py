import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = "config.json"
ENV_FILE = ROOT_DIR / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    # variables already set in the shell win over the file
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env()

HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:8b"
# shown in the Settings model list next to the Ollama models
OPENROUTER_MODELS = ["openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"]
# the guardrail, cache and hallucination checks run on Ollama: used when the chat model is an OpenRouter one
CHECK_MODEL = DEFAULT_MODEL
MAX_TOOL_ROUNDS = 5
# failed calls in a row to one tool in a turn (e.g. run_python fixing its own errors) before it is stopped
MAX_TOOL_ATTEMPTS = 3
# "docker" runs run_python in a container (a plain subprocess when Docker or the image is missing), "subprocess" never uses Docker
SANDBOX = "docker"
SANDBOX_IMAGE = "python:3.12-slim"
# Ollama's context window in tokens; without it Ollama cuts every request at 4096
NUM_CTX = 16384

SYSTEM_PROMPT_PATH = "system_prompt.txt"
PROFILE_PATH = "profile.json"
SESSIONS_DIR = "sessions"
LOG_FILE = "calls.jsonl"
SCHEMAS_DIR = "schemas"
INTERNAL_SCHEMAS_DIR = str(ROOT_DIR / "schemas" / "internal")
VERSIONS_DIR = "prompt_versions"
PERSONAS_DIR = "personas"
DEFAULT_PERSONA = "sensai"
GUARDRAILS_CONFIG_PATH = str(ROOT_DIR / "guardrails" / "config" / "guardrails.json")
MCP_CONFIG_PATH = str(ROOT_DIR / "tools" / "mcp" / "config" / "mcp_servers.json")
HALLUCINATION_CONFIG_PATH = str(ROOT_DIR / "hallucination" / "config" / "hallucination.json")
MCP_LOG_FILE = "mcp.log"
PERMISSIONS_PATH = str(ROOT_DIR / "tools" / "permissions.json")
WORKSPACE_DIR = str(ROOT_DIR / "safe_test")
RAG_DOCS_DIR = "knowledge"
# 800 chars (~150-200 words) is long enough to hold a full paragraph or policy clause so retrieval
# gets a complete thought, short enough that unrelated sentences rarely end up in the same chunk,
# and well inside nomic-embed-text's 2048-token input limit even for dense text. 100 (an eighth of
# that) stops a sentence that lands right on a cut from being split with no copy of it on either
# side, without re-embedding most of the previous chunk; see README section 22 for the per-knowledge-
# base override and section 20 for how embedding models were chosen.
RAG_CHUNK_SIZE = 800
RAG_CHUNK_OVERLAP = 100
RAG_EMBED_MODEL = "nomic-embed-text"
RAG_INDEX_PATH = "rag_index.sqlite"
RAG_TOP_K = 4
RAG_MIN_SCORE = 0.5
# every knowledge base besides "default" (which keeps using rag_docs_dir / rag_index_path above)
# gets its own folder under RAG_COLLECTIONS_DIR and its own index file under RAG_INDEXES_DIR
RAG_COLLECTIONS_DIR = "knowledge_bases"
RAG_INDEXES_DIR = "rag_indexes"
RAG_COLLECTIONS_PATH = "rag_collections.json"
RAG_DEFAULT_COLLECTION = "default"
# suggested in the web UI when creating a knowledge base or changing its model; see
# evals/eval_embeddings.py and README section 20 for how they compare on the same questions
RAG_EMBED_MODELS = ["nomic-embed-text", "mxbai-embed-large", "all-minilm"]
CACHE_PATH = "semantic_cache.sqlite"
CACHE_THRESHOLD = 0.7
CACHE_EMBED_MODEL = "nomic-embed-text"


@dataclass
class AppConfig:
    host: str = HOST
    model: str = DEFAULT_MODEL
    openrouter_models: list[str] = field(default_factory=lambda: list(OPENROUTER_MODELS))
    check_model: str = CHECK_MODEL
    persona: str = DEFAULT_PERSONA
    max_tool_rounds: int = MAX_TOOL_ROUNDS
    max_tool_attempts: int = MAX_TOOL_ATTEMPTS
    sandbox: str = SANDBOX
    sandbox_image: str = SANDBOX_IMAGE
    num_ctx: int = NUM_CTX
    system_prompt_path: str = SYSTEM_PROMPT_PATH
    profile_path: str = PROFILE_PATH
    sessions_dir: str = SESSIONS_DIR
    log_file: str = LOG_FILE
    schemas_dir: str = SCHEMAS_DIR
    versions_dir: str = VERSIONS_DIR
    personas_dir: str = PERSONAS_DIR
    guardrails_config_path: str = GUARDRAILS_CONFIG_PATH
    mcp_config_path: str = MCP_CONFIG_PATH
    hallucination_config_path: str = HALLUCINATION_CONFIG_PATH
    mcp_log_file: str = MCP_LOG_FILE
    auto_approve: bool = False
    no_guardrails: bool = False
    rag_enabled: bool = False
    rag_docs_dir: str = RAG_DOCS_DIR
    rag_chunk_size: int = RAG_CHUNK_SIZE
    rag_chunk_overlap: int = RAG_CHUNK_OVERLAP
    rag_embed_model: str = RAG_EMBED_MODEL
    rag_index_path: str = RAG_INDEX_PATH
    rag_top_k: int = RAG_TOP_K
    rag_min_score: float = RAG_MIN_SCORE
    rag_collections_dir: str = RAG_COLLECTIONS_DIR
    rag_indexes_dir: str = RAG_INDEXES_DIR
    rag_collections_path: str = RAG_COLLECTIONS_PATH
    rag_embed_models: list[str] = field(default_factory=lambda: list(RAG_EMBED_MODELS))
    # which knowledge base the current chat retrieves from
    rag_collection: str = RAG_DEFAULT_COLLECTION
    cache_enabled: bool = False
    cache_path: str = CACHE_PATH
    cache_threshold: float = CACHE_THRESHOLD
    cache_verify: bool = True
    cache_mid_chat: bool = True
    cache_embed_model: str = CACHE_EMBED_MODEL


def load_app_config(config_path: str = CONFIG_FILE, cli_args=None) -> AppConfig:
    config = AppConfig()
    valid_fields = {f.name for f in fields(AppConfig)}

    path = Path(config_path)
    if path.is_file():
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key, value in data.items():
                    if key in valid_fields and value is not None:
                        setattr(config, key, value)
        except (OSError, ValueError):
            pass

    if cli_args is not None:
        arg_map = {
            "host": "host",
            "model": "model",
            "persona": "persona",
            "system_prompt": "system_prompt_path",
            "profile": "profile_path",
            "sessions_dir": "sessions_dir",
            "log_file": "log_file",
            "auto_approve": "auto_approve",
            "no_guardrails": "no_guardrails",
        }
        for arg_name, field_name in arg_map.items():
            val = getattr(cli_args, arg_name, None)
            if val is not None:
                setattr(config, field_name, val)

    return config


def save_app_config(config: AppConfig, config_path: str = CONFIG_FILE) -> None:
    path = Path(config_path)
    with path.open("w", encoding="utf-8") as f:
        json.dump(asdict(config), f, indent=2, ensure_ascii=False)
