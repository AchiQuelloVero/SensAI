import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np

from rag.errors import RagError

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    source TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL,
    UNIQUE (source, chunk_index)
);
"""


@dataclass
class StoredDocument:
    source: str
    sha256: str
    size: int
    mtime_ns: int
    chunk_count: int = 0


@dataclass
class StoredChunk:
    source: str
    chunk_index: int
    text: str


class VectorStore:
    """Chunks and their embeddings in a SQLite file; vectors are float32 blobs read back with numpy."""

    def __init__(self, path: str):
        self.path = path
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        # one short-lived connection per operation: safe to call from any thread
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=10)
        except (sqlite3.Error, OSError) as e:
            raise RagError(f"cannot open the index {self.path}: {e}") from e

        try:
            with db:  # commits on success, rolls back on error
                yield db
        except sqlite3.Error as e:
            raise RagError(
                f"cannot use the index {self.path}: {e} (delete the file and ingest again to rebuild it)"
            ) from e
        finally:
            db.close()

    def check_settings(self, settings: dict[str, str]) -> bool:
        """Remember the settings the vectors were built with. If they differ from the stored ones the
        old vectors are useless (another model or chunking), so the index is emptied. Returns True then."""
        with self.connect() as db:
            stored = dict(db.execute("SELECT key, value FROM meta WHERE key != 'dim'"))
            has_documents = db.execute("SELECT 1 FROM documents LIMIT 1").fetchone() is not None

            changed = stored != settings
            if changed:
                db.execute("DELETE FROM chunks")
                db.execute("DELETE FROM documents")
                db.execute("DELETE FROM meta")  # also forgets the vector size
                db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", settings.items())
        return changed and has_documents

    def get_settings(self) -> dict[str, str]:
        with self.connect() as db:
            return dict(db.execute("SELECT key, value FROM meta WHERE key != 'dim'"))

    def get_document(self, source: str) -> StoredDocument | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT source, sha256, size, mtime_ns FROM documents WHERE source = ?", (source,)
            ).fetchone()
        return StoredDocument(*row) if row else None

    def list_documents(self) -> list[StoredDocument]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT d.source, d.sha256, d.size, d.mtime_ns, COUNT(c.id)
                FROM documents d LEFT JOIN chunks c ON c.source = d.source
                GROUP BY d.source ORDER BY d.source
                """
            ).fetchall()
        return [StoredDocument(*row) for row in rows]

    def chunk_count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def get_chunks(self, source: str) -> list[tuple[StoredChunk, int]]:
        """A document's chunks in order, each with its embedding's dimension (not the vector
        itself: it is only numbers, of no use to a person looking at the ingestion pipeline)."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT chunk_index, text, embedding FROM chunks WHERE source = ? ORDER BY chunk_index",
                (source,),
            ).fetchall()
        return [(StoredChunk(source, index, text), len(blob) // 4) for index, text, blob in rows]

    def replace_document(
        self,
        source: str,
        sha256: str,
        size: int,
        mtime_ns: int,
        chunks: list[str],
        vectors: list[list[float]],
    ) -> None:
        """Swap a document's chunks for new ones in one transaction: an interrupted ingest
        leaves either the old version or the new one, never half of each."""
        if not chunks or len(chunks) != len(vectors):
            raise RagError(f"{source}: got {len(vectors)} vector(s) for {len(chunks)} chunk(s)")

        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or not np.isfinite(matrix).all():
            raise RagError(f"{source}: the embeddings are not a clean list of same-size vectors")

        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key = 'dim'").fetchone()
            if row is None:
                db.execute("INSERT INTO meta (key, value) VALUES ('dim', ?)", (str(matrix.shape[1]),))
            elif int(row[0]) != matrix.shape[1]:
                raise RagError(
                    f"{source}: vectors have {matrix.shape[1]} dimensions but the index holds {row[0]} "
                    "(delete the index file and ingest again)"
                )

            db.execute("DELETE FROM chunks WHERE source = ?", (source,))
            db.execute(
                "INSERT OR REPLACE INTO documents (source, sha256, size, mtime_ns) VALUES (?, ?, ?, ?)",
                (source, sha256, size, mtime_ns),
            )
            db.executemany(
                "INSERT INTO chunks (source, chunk_index, text, embedding) VALUES (?, ?, ?, ?)",
                [(source, i, text, matrix[i].tobytes()) for i, text in enumerate(chunks)],
            )

    def touch_document(self, source: str, size: int, mtime_ns: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET size = ?, mtime_ns = ? WHERE source = ?", (size, mtime_ns, source)
            )

    def remove_document(self, source: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM chunks WHERE source = ?", (source,))
            db.execute("DELETE FROM documents WHERE source = ?", (source,))

    def load_all(self) -> tuple[list[StoredChunk], np.ndarray]:
        """Every chunk with its vector scaled to length 1, so a dot product is the cosine similarity."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT source, chunk_index, text, embedding FROM chunks ORDER BY source, chunk_index"
            ).fetchall()

        if not rows:
            return [], np.empty((0, 0), dtype=np.float32)

        chunks = [StoredChunk(source, index, text) for source, index, text, _ in rows]
        matrix = np.vstack([np.frombuffer(blob, dtype=np.float32) for *_, blob in rows])
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0  # a zero vector stays zero and scores 0 against everything
        return chunks, matrix / norms
