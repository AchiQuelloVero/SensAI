import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from core.config import AppConfig
from rag.chunker import check_chunk_settings
from rag.errors import RagError

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
DEFAULT_NAME = "default"


@dataclass
class Collection:
    name: str
    docs_dir: str
    index_path: str
    embed_model: str = ""
    # None means "use rag_chunk_size / rag_chunk_overlap from config.json"
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    created_at: str = ""


def check_name(name: str) -> str:
    name = name.strip()
    if not NAME_RE.fullmatch(name):
        raise RagError(
            f"'{name}' is not a valid knowledge base name: use 1-64 letters, digits, '_' or '-', "
            "starting with a letter or digit"
        )
    return name


def _default_collection(config: AppConfig) -> Collection:
    # follows rag_embed_model / rag_chunk_size / rag_chunk_overlap live, like it followed
    # rag_docs_dir / rag_index_path before collections existed
    return Collection(
        DEFAULT_NAME, config.rag_docs_dir, config.rag_index_path, config.rag_embed_model,
        config.rag_chunk_size, config.rag_chunk_overlap,
    )


def list_collections(config: AppConfig) -> list[Collection]:
    """Every knowledge base, "default" first. "default" always exists, even with no
    registry file yet, and keeps using rag_docs_dir / rag_index_path from config.json."""
    path = Path(config.rag_collections_path)
    extra = []
    if path.is_file():
        try:
            with path.open(encoding="utf-8") as f:
                rows = json.load(f)
        except (OSError, ValueError) as e:
            raise RagError(f"cannot read {path}: {e}") from e
        extra = [Collection(**row) for row in rows if row.get("name") != DEFAULT_NAME]
    return [_default_collection(config), *extra]


def get_collection(config: AppConfig, name: str) -> Collection:
    for collection in list_collections(config):
        if collection.name == name:
            return collection
    raise RagError(f"no knowledge base named '{name}'")


def embed_model_of(config: AppConfig, collection: Collection) -> str:
    # a collection saved before per-collection models existed has no embed_model recorded
    return collection.embed_model or config.rag_embed_model


def chunk_settings_of(config: AppConfig, collection: Collection) -> tuple[int, int]:
    # a collection saved before per-collection chunking existed has neither value recorded
    size = collection.chunk_size if collection.chunk_size is not None else config.rag_chunk_size
    overlap = collection.chunk_overlap if collection.chunk_overlap is not None else config.rag_chunk_overlap
    return size, overlap


def _save_extra(config: AppConfig, collections: list[Collection]) -> None:
    path = Path(config.rag_collections_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(c) for c in collections if c.name != DEFAULT_NAME]
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except OSError as e:
        raise RagError(f"cannot write {path}: {e}") from e


def create_collection(
    config: AppConfig,
    name: str,
    embed_model: str | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> Collection:
    name = check_name(name)
    collections = list_collections(config)
    if any(c.name == name for c in collections):
        raise RagError(f"a knowledge base named '{name}' already exists")

    embed_model = (embed_model or config.rag_embed_model).strip()
    if not embed_model:
        raise RagError("an embedding model is required")

    size = config.rag_chunk_size if chunk_size is None else chunk_size
    overlap = config.rag_chunk_overlap if chunk_overlap is None else chunk_overlap
    check_chunk_settings(size, overlap)

    collection = Collection(
        name=name,
        docs_dir=str(Path(config.rag_collections_dir) / name),
        index_path=str(Path(config.rag_indexes_dir) / f"{name}.sqlite"),
        embed_model=embed_model,
        chunk_size=size,
        chunk_overlap=overlap,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    try:
        os.makedirs(collection.docs_dir, exist_ok=True)
    except OSError as e:
        raise RagError(f"cannot create {collection.docs_dir}: {e.strerror or e}") from e

    collections.append(collection)
    _save_extra(config, collections)
    return collection


def set_embed_model(config: AppConfig, name: str, embed_model: str) -> Collection:
    """Change which model a knowledge base embeds with. Its index still holds vectors
    from the old model until /ingest runs again, which rebuilds it from scratch."""
    if name == DEFAULT_NAME:
        raise RagError("the default knowledge base uses rag_embed_model from config.json")

    embed_model = embed_model.strip()
    if not embed_model:
        raise RagError("an embedding model is required")

    collections = list_collections(config)
    for collection in collections:
        if collection.name == name:
            collection.embed_model = embed_model
            _save_extra(config, collections)
            return collection
    raise RagError(f"no knowledge base named '{name}'")


def set_chunk_settings(config: AppConfig, name: str, chunk_size: int, chunk_overlap: int) -> Collection:
    """Change how a knowledge base's documents are split into chunks. Its index still holds
    chunks cut with the old settings until /ingest runs again, which rebuilds it from scratch."""
    if name == DEFAULT_NAME:
        raise RagError("the default knowledge base uses rag_chunk_size / rag_chunk_overlap from config.json")

    check_chunk_settings(chunk_size, chunk_overlap)

    collections = list_collections(config)
    for collection in collections:
        if collection.name == name:
            collection.chunk_size = chunk_size
            collection.chunk_overlap = chunk_overlap
            _save_extra(config, collections)
            return collection
    raise RagError(f"no knowledge base named '{name}'")


def delete_collection(config: AppConfig, name: str) -> None:
    if name == DEFAULT_NAME:
        raise RagError("the default knowledge base cannot be deleted")

    collections = list_collections(config)
    remaining = [c for c in collections if c.name != name]
    if len(remaining) == len(collections):
        raise RagError(f"no knowledge base named '{name}'")

    target = next(c for c in collections if c.name == name)
    _save_extra(config, remaining)
    shutil.rmtree(target.docs_dir, ignore_errors=True)
    try:
        os.remove(target.index_path)
    except OSError:
        pass
