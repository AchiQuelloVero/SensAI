import os
from pathlib import Path
from typing import Callable

from core.config import AppConfig, load_app_config, save_app_config
from rag.collections import (
    chunk_settings_of,
    create_collection,
    delete_collection,
    embed_model_of,
    list_collections,
    set_chunk_settings,
    set_embed_model,
)
from rag.errors import RagError
from rag.ingest import IngestReport
from rag.retriever import check_index_model
from rag.service import RagService

CHANGE_LABELS = ("added", "updated", "removed")


def error_result(command: str, error) -> dict:
    return {"ok": False, "command": command, "error": str(error), "output": f"error: {error}"}


def format_ingest_report(report: IngestReport, docs_dir: str, show_files: bool) -> str:
    lines = []

    if report.reset:
        lines.append("the embedding model or chunk settings changed: the whole index was rebuilt")

    if show_files:
        for label in CHANGE_LABELS:
            lines.extend(f"  {label:<9} {source}" for source in getattr(report, label))

    lines.extend(f"  failed: {message}" for _, message in report.failed)

    counts = [
        (len(report.added), "added"),
        (len(report.updated), "updated"),
        (len(report.unchanged), "unchanged"),
        (len(report.removed), "removed"),
        (len(report.failed), "failed"),
    ]
    summary = ", ".join(f"{n} {label}" for n, label in counts if n)
    if summary:
        lines.append(f"{docs_dir}: {summary}")
    else:
        lines.append(f"no .txt, .md or .pdf files found in {docs_dir}/")

    return "\n".join(lines)


def run_ingest_command(
    config: AppConfig,
    on_progress: Callable[[str, str], None] | None = None,
) -> dict:
    # with live progress the caller already printed each file, so the summary skips them
    service = RagService(config)
    try:
        docs_dir = service.active_collection().docs_dir
        report = service.ingest(on_progress=on_progress)
    except RagError as error:
        return error_result("ingest", error)

    return {
        "ok": not report.failed,
        "command": "ingest",
        "report": {
            "added": report.added,
            "updated": report.updated,
            "unchanged": report.unchanged,
            "removed": report.removed,
            "failed": [{"source": source, "error": message} for source, message in report.failed],
            "reset": report.reset,
        },
        "output": format_ingest_report(report, docs_dir, show_files=on_progress is None),
    }


def indexed_documents(config: AppConfig):
    service = RagService(config)
    collection = service.active_collection()
    # looking must not create an empty index file
    if not os.path.exists(collection.index_path):
        return []
    return service.store(collection).list_documents()


def run_docs_command(config: AppConfig) -> dict:
    try:
        documents = indexed_documents(config)
        docs_dir = RagService(config).active_collection().docs_dir
    except RagError as error:
        return error_result("docs", error)

    state = "on" if config.rag_enabled else "off"

    if not documents:
        output = (
            f"(no documents indexed yet in '{config.rag_collection}': put .txt, .md or .pdf files in "
            f"{docs_dir}/ and run /ingest)\nRAG is {state}"
        )
    else:
        total = sum(d.chunk_count for d in documents)
        width = max(len(d.source) for d in documents)
        active = RagService(config).active_collection()
        model = embed_model_of(config, active)
        size, overlap = chunk_settings_of(config, active)
        lines = [
            f"{len(documents)} document(s), {total} chunk(s), embedded with {model}, "
            f"chunked at {size} chars with {overlap} overlap"
        ]
        lines.extend(f"  {d.source:<{width}}  {d.chunk_count} chunk(s)" for d in documents)
        lines.append(f"RAG is {state}")
        output = "\n".join(lines)

    return {
        "ok": True,
        "command": "docs",
        "documents": [{"source": d.source, "chunks": d.chunk_count} for d in documents],
        "rag_enabled": config.rag_enabled,
        "output": output,
    }


def remember_rag_enabled(value: bool) -> str:
    """Keep the choice for the next start, in config.json only: the running config may
    carry command-line options that should not be saved. Returns a warning if it failed."""
    try:
        saved = load_app_config()
        saved.rag_enabled = value
        save_app_config(saved)
    except OSError as e:
        return f" (could not save the choice to config.json: {e.strerror or e})"
    return ""


def run_rag_command(config: AppConfig, text: str) -> dict:
    choice = text.strip().lower()

    if choice not in ("", "status", "on", "off"):
        return {
            "ok": False,
            "command": "rag",
            "usage": True,
            "output": "usage: /rag [on|off]",
        }

    try:
        collection = RagService(config).active_collection()
        documents = indexed_documents(config)
        if choice == "on":
            if not documents:
                raise RagError(
                    f"cannot turn RAG on: the '{config.rag_collection}' knowledge base is empty. "
                    f"Put .txt, .md or .pdf files in {collection.docs_dir}/ and run /ingest first"
                )
            check_index_model(RagService(config).store(collection), embed_model_of(config, collection))
    except RagError as error:
        return error_result("rag", error)

    chunks = sum(d.chunk_count for d in documents)
    note = ""

    if choice in ("on", "off"):
        config.rag_enabled = choice == "on"
        note = remember_rag_enabled(config.rag_enabled)

    state = "on" if config.rag_enabled else "off"
    output = f"RAG is {state} ({len(documents)} document(s), {chunks} chunk(s) indexed){note}"
    return {
        "ok": True,
        "command": "rag",
        "enabled": config.rag_enabled,
        "output": output,
    }


def find_collection(config: AppConfig, name: str):
    return next((c for c in list_collections(config) if c.name == name), None)


def collection_documents(config: AppConfig, collection):
    # looking must not create an empty index file
    if not os.path.exists(collection.index_path):
        return []
    return RagService(config).store(collection).list_documents()


def document_chunks(config: AppConfig, collection, source: str) -> list[dict]:
    """A document's chunks in order, each with its embedding's dimension: the ingestion
    pipeline (documents -> chunks -> embeddings) for one document."""
    if not os.path.exists(collection.index_path):
        return []
    rows = RagService(config).store(collection).get_chunks(source)
    return [
        {"chunk_index": chunk.chunk_index, "text": chunk.text, "dimensions": dimensions}
        for chunk, dimensions in rows
    ]


def collection_view(config: AppConfig, collection) -> dict:
    documents = collection_documents(config, collection)
    chunk_size, chunk_overlap = chunk_settings_of(config, collection)
    return {
        "name": collection.name,
        "docs_dir": collection.docs_dir,
        "embed_model": embed_model_of(config, collection),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "created_at": collection.created_at,
        "active": collection.name == config.rag_collection,
        "documents": len(documents),
        "chunks": sum(d.chunk_count for d in documents),
    }


def list_collections_view(config: AppConfig) -> dict:
    return {
        "ok": True,
        "command": "kb",
        "collections": [collection_view(config, c) for c in list_collections(config)],
    }


KB_USAGE = (
    "usage: /kb [create <name> [embed_model [chunk_size chunk_overlap]] | delete <name> | use <name> "
    "| model <name> <embed_model> | chunk <name> <chunk_size> <chunk_overlap> | try <name> <question>]"
)


def format_try_result(result: dict) -> str:
    lines = [f"top_k={result['top_k']}  min_score={result['min_score']}"]
    width = max((len(r["source"]) for r in result["results"]), default=0)
    for r in result["results"]:
        mark = "pass" if r["passed"] else "    "
        preview = " ".join(r["text"].split())
        preview = preview if len(preview) <= 60 else preview[:57] + "..."
        lines.append(
            f"  {mark}  #{r['rank']:<2} {r['score']:.4f}  {r['source']:<{width}} chunk {r['chunk_index'] + 1}  {preview!r}"
        )
    if not result["results"]:
        lines.append("  (no chunks in the index)")
    lines.append("")
    lines.append("injected prompt:")
    lines.append(result["injected_prompt"])
    return "\n".join(lines)


def run_kb_try(config: AppConfig, collection_name: str, question: str) -> dict:
    """Rank every chunk of a knowledge base against `question`, mark which ones pass the
    top_k / min_score threshold, and show the prompt that would be injected from those."""
    collection = find_collection(config, collection_name)
    if collection is None:
        return error_result("kb", RagError(f"no knowledge base named '{collection_name}'"))

    try:
        result = RagService(config).explain_retrieval(question, collection)
    except RagError as error:
        return error_result("kb", error)

    return {"ok": True, "command": "kb", "result": result, "output": format_try_result(result)}


def parse_int(value: str, label: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise RagError(f"{label} must be a whole number, got {value!r}") from None


def run_kb_command(config: AppConfig, session: dict, text: str) -> dict:
    """/kb [list | create <name> [embed_model [chunk_size chunk_overlap]] | delete <name> | use <name>
    | model <name> <embed_model> | chunk <name> <chunk_size> <chunk_overlap> | try <name> <question>]:
    manage the knowledge bases documents are ingested into, each with its own embedding model and
    chunking, pick which one this chat retrieves from, and preview retrieval for a question."""
    verb, _, rest = text.strip().partition(" ")
    rest = rest.strip()

    if not verb or verb == "list":
        result = list_collections_view(config)
        width = max((len(c["name"]) for c in result["collections"]), default=0)
        lines = [
            f'{"*" if c["active"] else " "} {c["name"]:<{width}}  {c["documents"]} document(s), '
            f'{c["chunks"]} chunk(s), embedded with {c["embed_model"]}, '
            f'chunked at {c["chunk_size"]}/{c["chunk_overlap"]}'
            for c in result["collections"]
        ]
        lines.append(KB_USAGE)
        result["output"] = "\n".join(lines)
        return result

    if verb == "try":
        name, _, question = rest.partition(" ")
        question = question.strip()
        if not name or not question:
            return {"ok": False, "command": "kb", "usage": True, "output": "usage: /kb try <name> <question>"}
        return run_kb_try(config, name, question)

    tokens = rest.split()
    name = tokens[0] if tokens else ""
    args = tokens[1:]

    try:
        if verb == "create":
            if not name:
                raise RagError("usage: /kb create <name> [embed_model [chunk_size chunk_overlap]]")
            if len(args) not in (0, 1, 3):
                raise RagError("usage: /kb create <name> [embed_model [chunk_size chunk_overlap]]")
            embed_model = args[0] if args and args[0] != "-" else None
            chunk_size = parse_int(args[1], "chunk_size") if len(args) == 3 else None
            chunk_overlap = parse_int(args[2], "chunk_overlap") if len(args) == 3 else None
            collection = create_collection(config, name, embed_model, chunk_size, chunk_overlap)
            output = (
                f"created knowledge base '{collection.name}' (embeddings: {collection.embed_model}, "
                f"chunked at {collection.chunk_size}/{collection.chunk_overlap})"
            )
        elif verb == "delete":
            if not name:
                raise RagError("usage: /kb delete <name>")
            delete_collection(config, name)
            if config.rag_collection == name:
                config.rag_collection = "default"
                session["rag_collection"] = "default"
            output = f"deleted knowledge base '{name}'"
        elif verb == "use":
            if not name:
                raise RagError("usage: /kb use <name>")
            if not any(c.name == name for c in list_collections(config)):
                raise RagError(f"no knowledge base named '{name}'")
            config.rag_collection = name
            session["rag_collection"] = name
            output = f"this chat now uses the '{name}' knowledge base"
        elif verb == "model":
            if not name or len(args) != 1:
                raise RagError("usage: /kb model <name> <embed_model>")
            collection = set_embed_model(config, name, args[0])
            output = (
                f"'{collection.name}' now embeds with {collection.embed_model}. "
                "Run /ingest after switching to it to rebuild the index with the new model."
            )
        elif verb == "chunk":
            if not name or len(args) != 2:
                raise RagError("usage: /kb chunk <name> <chunk_size> <chunk_overlap>")
            collection = set_chunk_settings(config, name, parse_int(args[0], "chunk_size"), parse_int(args[1], "chunk_overlap"))
            output = (
                f"'{collection.name}' now chunks at {collection.chunk_size} chars with "
                f"{collection.chunk_overlap} overlap. Run /ingest after switching to it to rebuild the index."
            )
        else:
            return {"ok": False, "command": "kb", "usage": True, "output": KB_USAGE}
    except RagError as error:
        return error_result("kb", error)

    return {**list_collections_view(config), "output": output}


def add_document(config: AppConfig, collection_name: str, filename: str, data: bytes) -> dict:
    """Write an uploaded file into the knowledge base's folder and (re)ingest it."""
    service = RagService(config)
    collection = find_collection(config, collection_name)
    if collection is None:
        return error_result("kb", RagError(f"no knowledge base named '{collection_name}'"))

    name = os.path.basename(filename)
    if not name or name.startswith("."):
        return error_result("kb", RagError(f"not a valid file name: {filename!r}"))

    target = Path(collection.docs_dir) / name
    try:
        os.makedirs(collection.docs_dir, exist_ok=True)
        target.write_bytes(data)
    except OSError as e:
        return error_result("kb", RagError(f"cannot write {target}: {e.strerror or e}"))

    try:
        report = service.ingest(collection=collection)
    except RagError as error:
        return error_result("kb", error)

    return {
        "ok": not report.failed,
        "command": "kb",
        "collection": collection_view(config, collection),
        "output": format_ingest_report(report, collection.docs_dir, show_files=True),
    }


def remove_document(config: AppConfig, collection_name: str, source: str) -> dict:
    """Delete a document from the knowledge base's folder and drop it from the index."""
    service = RagService(config)
    collection = find_collection(config, collection_name)
    if collection is None:
        return error_result("kb", RagError(f"no knowledge base named '{collection_name}'"))

    docs_root = Path(collection.docs_dir).resolve()
    path = (docs_root / source).resolve()
    if docs_root not in path.parents:
        return error_result("kb", RagError(f"not a valid document: {source!r}"))

    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        return error_result("kb", RagError(f"cannot delete {path}: {e.strerror or e}"))

    try:
        report = service.ingest(collection=collection)
    except RagError as error:
        return error_result("kb", error)

    return {
        "ok": True,
        "command": "kb",
        "collection": collection_view(config, collection),
        "output": f"removed {source} from '{collection.name}'",
    }
