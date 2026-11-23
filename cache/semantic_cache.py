import hashlib
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    question TEXT NOT NULL,
    embedding BLOB NOT NULL,
    answer TEXT NOT NULL,
    created REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    duration_s REAL NOT NULL DEFAULT 0,
    saved_s REAL NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS answers_key ON answers (key);
"""

ADDED_COLUMNS = {
    "duration_s": "REAL NOT NULL DEFAULT 0",
    "saved_s": "REAL NOT NULL DEFAULT 0",
    "model": "TEXT NOT NULL DEFAULT ''",
}
ENTRY_FIELDS = ("id", "question", "answer", "model", "created", "hits", "duration_s", "saved_s")


class CacheError(Exception):
    pass


@dataclass
class CacheHit:
    id: int
    question: str
    answer: str
    score: float
    duration_s: float = 0.0


def cache_key(*parts: str) -> str:
    """One short label for everything an answer depends on (model, system prompt, ...):
    answers saved under another key are never reused."""
    return hashlib.sha256("\n\0".join(parts).encode("utf-8")).hexdigest()


def unit(vector) -> np.ndarray:
    """The vector scaled to length 1, so a dot product of two of them is the cosine similarity."""
    v = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(v)
    return v / norm if norm else v


class SemanticCache:
    """Questions, their embeddings and their answers in a SQLite file."""

    def __init__(self, path: str):
        self.path = path
        with self.connect() as db:
            db.executescript(SCHEMA)
            existing = {row[1] for row in db.execute("PRAGMA table_info(answers)")}
            for name, definition in ADDED_COLUMNS.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE answers ADD COLUMN {name} {definition}")

    @contextmanager
    def connect(self):

        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=10)
        except (sqlite3.Error, OSError) as e:
            raise CacheError(f"cannot open the cache {self.path}: {e}") from e

        try:
            with db:
                yield db
        except sqlite3.Error as e:
            raise CacheError(f"cannot use the cache {self.path}: {e} (delete the file to start over)") from e
        finally:
            db.close()

    def closest(self, key: str, vector) -> CacheHit | None:
        """The saved answer whose question is the most similar to `vector`, or None if there is none under `key`."""
        wanted = unit(vector)
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, question, answer, embedding, duration_s FROM answers WHERE key = ?", (key,)
            ).fetchall()

        best, best_score = None, -1.0
        for row in rows:
            saved = np.frombuffer(row[3], dtype=np.float32)
            if saved.shape != wanted.shape:
                continue
            score = float(unit(saved) @ wanted)
            if score > best_score:
                best, best_score = row, score

        if best is None:
            return None
        return CacheHit(id=best[0], question=best[1], answer=best[2], score=best_score, duration_s=best[4])

    def record_hit(self, hit_id: int, saved_s: float = 0.0) -> None:
        with self.connect() as db:
            db.execute("UPDATE answers SET hits = hits + 1, saved_s = saved_s + ? WHERE id = ?", (saved_s, hit_id))

    def add(self, key: str, question: str, vector, answer: str, duration_s: float = 0.0, model: str = "") -> None:
        v = np.asarray(vector, dtype=np.float32)
        if v.ndim != 1 or not v.size or not np.isfinite(v).all():
            raise CacheError("the question's embedding is not a clean vector")
        with self.connect() as db:
            db.execute(
                "INSERT INTO answers (key, question, embedding, answer, created, duration_s, model)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, question, v.tobytes(), answer, time.time(), duration_s, model),
            )

    def entries(self) -> list[dict]:
        """Every saved answer, newest first."""
        with self.connect() as db:
            rows = db.execute(f"SELECT {', '.join(ENTRY_FIELDS)} FROM answers ORDER BY created DESC").fetchall()
        return [dict(zip(ENTRY_FIELDS, row)) for row in rows]

    def delete(self, entry_id: int) -> bool:
        with self.connect() as db:
            return db.execute("DELETE FROM answers WHERE id = ?", (entry_id,)).rowcount > 0

    def forget(self, question: str, answer: str) -> int:
        with self.connect() as db:
            return db.execute("DELETE FROM answers WHERE question = ? AND answer = ?", (question, answer)).rowcount

    def clear(self) -> int:
        """Forget every saved answer. Returns how many there were."""
        with self.connect() as db:
            return db.execute("DELETE FROM answers").rowcount

    def stats(self) -> dict:
        with self.connect() as db:
            entries, hits, saved_s = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(hits), 0), COALESCE(SUM(saved_s), 0) FROM answers"
            ).fetchone()
        return {"entries": entries, "hits": hits, "saved_s": saved_s}
