from dataclasses import dataclass

import numpy as np

from rag.embeddings import Embedder
from rag.errors import RagError
from rag.store import VectorStore


@dataclass
class RetrievedChunk:
    source: str
    chunk_index: int
    text: str
    score: float


def check_retrieval_settings(top_k, min_score) -> None:
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        raise RagError(f"rag_top_k must be a positive integer, got {top_k!r}")
    if isinstance(min_score, bool) or not isinstance(min_score, (int, float)) or not -1 <= min_score <= 1:
        raise RagError(f"rag_min_score must be a number between -1 and 1, got {min_score!r}")


def check_index_model(store: VectorStore, model: str) -> None:
    built_with = store.get_settings().get("embed_model")
    if built_with != model:
        raise RagError(
            f"The index was built with '{built_with}' but rag_embed_model is '{model}'. "
            "Run the ingest command to rebuild it."
        )


class Retriever:
    def __init__(self, store: VectorStore, embedder: Embedder, top_k: int, min_score: float):
        check_retrieval_settings(top_k, min_score)
        self.store = store
        self.embedder = embedder
        self.top_k = top_k
        self.min_score = min_score

    def rank(self, query: str, limit: int) -> list[RetrievedChunk]:
        """Every chunk by cosine similarity with the query, best first, `top_k` and `min_score`
        not applied. Used to show which chunks would pass the threshold and which would not."""
        chunks, matrix = self.store.load_all()
        if not chunks:
            raise RagError("The knowledge index is empty. Add documents to the folder and run the ingest command.")

        check_index_model(self.store, self.embedder.model)

        query_vector = np.asarray(self.embedder.embed_one(query), dtype=np.float32)
        if query_vector.shape != (matrix.shape[1],):
            raise RagError(
                f"The query embedding has {query_vector.size} dimensions but the index has {matrix.shape[1]}. "
                "Run the ingest command to rebuild the index."
            )

        norm = np.linalg.norm(query_vector)
        if not norm:
            return []
        scores = matrix @ (query_vector / norm)  # both sides have length 1: dot product = cosine

        best = np.argsort(-scores, kind="stable")[:limit]
        return [
            RetrievedChunk(chunks[i].source, chunks[i].chunk_index, chunks[i].text, float(scores[i]))
            for i in best
        ]

    def search(self, query: str) -> list[RetrievedChunk]:
        """The best `top_k` chunks by cosine similarity with the query, best first.
        Chunks scoring under `min_score` are dropped, so a question the documents do not
        cover gives an empty list instead of the least-bad chunks."""
        return [chunk for chunk in self.rank(query, self.top_k) if chunk.score >= self.min_score]
