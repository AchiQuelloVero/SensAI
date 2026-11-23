from typing import Callable

from call_tracking.call_log import log_call
from core.config import AppConfig
from core.contracts import RetrievedContext
from rag.collections import Collection, chunk_settings_of, embed_model_of, get_collection
from rag.context import excerpt, frame_question
from rag.embeddings import Embedder
from rag.ingest import IngestReport, ingest_folder
from rag.retriever import Retriever
from rag.store import VectorStore


class RagService:
    """Builds the RAG pieces from the live config on each call, so changes to it
    (RAG switched on or off, another collection or model) apply to the next question."""

    def __init__(self, config: AppConfig):
        self.config = config

    def active_collection(self) -> Collection:
        return get_collection(self.config, self.config.rag_collection)

    def store(self, collection: Collection | None = None) -> VectorStore:
        collection = collection or self.active_collection()
        return VectorStore(collection.index_path)

    def embedder(self, collection: Collection | None = None) -> Embedder:
        collection = collection or self.active_collection()
        return Embedder(embed_model_of(self.config, collection), self.config.host)

    def ingest(
        self,
        collection: Collection | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> IngestReport:
        collection = collection or self.active_collection()
        size, overlap = chunk_settings_of(self.config, collection)
        return ingest_folder(
            collection.docs_dir,
            self.store(collection),
            self.embedder(collection),
            size,
            overlap,
            on_progress,
        )

    def build_context(self, question: str) -> RetrievedContext | None:
        """The question framed with excerpts from the active knowledge base, or None when RAG is off."""
        cfg = self.config
        if not cfg.rag_enabled:
            return None

        collection = self.active_collection()
        retriever = Retriever(self.store(collection), self.embedder(collection), cfg.rag_top_k, cfg.rag_min_score)
        chunks = retriever.search(question)

        # only where the answer came from, never the question or the text
        log_call(cfg.log_file, {
            "rag": "retrieval",
            "chunks": [
                {"source": c.source, "chunk": c.chunk_index + 1, "score": round(c.score, 3)}
                for c in chunks
            ],
        })
        return RetrievedContext(
            prompt=frame_question(question, chunks),
            excerpts=[excerpt(chunk) for chunk in chunks],
        )

    def explain_retrieval(self, question: str, collection: Collection | None = None, show: int = 10) -> dict:
        """Every candidate chunk ranked for `question`, marked with whether it would pass the
        active top_k / min_score threshold, and the prompt that would be injected from the ones
        that do. Does not require rag_enabled and does not log to calls.jsonl: it is a dry run
        for the pipeline view, not a real turn."""
        cfg = self.config
        collection = collection or self.active_collection()
        retriever = Retriever(self.store(collection), self.embedder(collection), cfg.rag_top_k, cfg.rag_min_score)
        candidates = retriever.rank(question, max(show, cfg.rag_top_k))

        results = []
        used = []
        for rank, chunk in enumerate(candidates, start=1):
            passed = rank <= cfg.rag_top_k and chunk.score >= cfg.rag_min_score
            results.append({
                "rank": rank,
                "source": chunk.source,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "score": round(chunk.score, 4),
                "passed": passed,
            })
            if passed:
                used.append(chunk)

        return {
            "question": question,
            "top_k": cfg.rag_top_k,
            "min_score": cfg.rag_min_score,
            "results": results,
            "injected_prompt": frame_question(question, used),
            "excerpts": [excerpt(chunk) for chunk in used],
        }
