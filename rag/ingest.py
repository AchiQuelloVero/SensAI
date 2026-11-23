import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rag.chunker import check_chunk_settings, chunk_text
from rag.embeddings import Embedder
from rag.errors import RagError
from rag.loader import find_documents, read_document, source_name
from rag.store import VectorStore


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    # True when the model or chunk settings changed and the whole index was rebuilt
    reset: bool = False


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ingest_folder(
    docs_dir: str,
    store: VectorStore,
    embedder: Embedder,
    chunk_size: int,
    chunk_overlap: int,
    on_progress: Callable[[str, str], None] | None = None,
) -> IngestReport:
    """Bring the index in line with the folder, embedding only files that are new or changed.

    A file that cannot be read is reported in `failed` and the others carry on. An embedding
    error (Ollama down, model missing) stops everything, since every other file would fail the same way."""
    check_chunk_settings(chunk_size, chunk_overlap)
    paths = find_documents(docs_dir)

    report = IngestReport()
    report.reset = store.check_settings({
        "embed_model": embedder.model,
        "chunk_size": str(chunk_size),
        "chunk_overlap": str(chunk_overlap),
    })

    def done(status: str, source: str) -> None:
        if on_progress is not None:
            on_progress(status, source)

    def fail(source: str, message: str) -> None:
        # the old version, if any, no longer matches what is on disk
        store.remove_document(source)
        report.failed.append((source, message))
        done("failed", source)

    present = set()
    for path in paths:
        source = source_name(path, docs_dir)
        present.add(source)

        try:
            stat = path.stat()
            known = store.get_document(source)

            # same size and modification time: assume unchanged without reading the file
            if known and known.size == stat.st_size and known.mtime_ns == stat.st_mtime_ns:
                report.unchanged.append(source)
                done("unchanged", source)
                continue

            digest = file_sha256(path)
        except OSError as e:
            fail(source, f"cannot read {path}: {e.strerror or e}")
            continue

        # touched or copied but the content is the same: nothing to embed
        if known and known.sha256 == digest:
            store.touch_document(source, stat.st_size, stat.st_mtime_ns)
            report.unchanged.append(source)
            done("unchanged", source)
            continue

        try:
            text = read_document(path)
        except RagError as e:
            fail(source, str(e))
            continue

        chunks = chunk_text(text, chunk_size, chunk_overlap)
        vectors = embedder.embed(chunks)
        store.replace_document(source, digest, stat.st_size, stat.st_mtime_ns, chunks, vectors)

        status = "updated" if known else "added"
        getattr(report, status).append(source)
        done(status, source)

    for document in store.list_documents():
        if document.source not in present:
            store.remove_document(document.source)
            report.removed.append(document.source)
            done("removed", document.source)

    return report
