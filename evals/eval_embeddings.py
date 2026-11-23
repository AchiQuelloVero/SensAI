import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from core.config import HOST, RAG_EMBED_MODELS
from rag.chunker import chunk_text
from rag.embeddings import Embedder
from rag.errors import RagError
from rag.retriever import Retriever
from rag.store import VectorStore

CASES_PATH = Path(__file__).parent / "embedding_cases.json"
TOP_K = 3


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare embedding models on the same documents and questions (recall@1, recall@3, speed)."
    )
    parser.add_argument(
        "-m", "--models",
        nargs="+",
        default=RAG_EMBED_MODELS,
        help=f"Ollama embedding models to compare (default: {' '.join(RAG_EMBED_MODELS)})",
    )
    parser.add_argument("-c", "--cases", default=CASES_PATH, help="test cases file (default: evals/embedding_cases.json)")
    parser.add_argument("-k", "--top-k", type=int, default=TOP_K, help=f"how many results count toward recall@k (default: {TOP_K})")
    parser.add_argument("--host", default=HOST, help=f"Ollama host (default: {HOST})")
    return parser.parse_args()


def load_cases(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        sys.exit(f"error: cannot read test cases {path}: {e}")


def rank_of(chunks, source):
    for rank, chunk in enumerate(chunks, start=1):
        if chunk.source == source:
            return rank
    return None


def run_model(model, cases, host, top_k):
    tmp_dir = tempfile.mkdtemp(prefix="eval_embeddings_")
    try:
        store = VectorStore(str(Path(tmp_dir) / "index.sqlite"))
        embedder = Embedder(model, host)
        store.check_settings({"embed_model": embedder.model, "chunk_size": "2000", "chunk_overlap": "0"})

        embedder.embed_one("warm up")  # the first call loads the model into memory; exclude that from the timing

        embed_start = time.perf_counter()
        for doc in cases["documents"]:
            chunks = chunk_text(doc["text"], size=2000, overlap=0)
            vectors = embedder.embed(chunks)
            digest = file_sha256_of_text(doc["text"])
            store.replace_document(doc["source"], digest, len(doc["text"]), 0, chunks, vectors)
        embed_seconds = time.perf_counter() - embed_start

        _, matrix = store.load_all()
        dimension = matrix.shape[1] if matrix.size else 0

        # min_score=-1 so a wrong top result still shows up instead of being filtered out
        retriever = Retriever(store, embedder, top_k=top_k, min_score=-1.0)

        hits_at_1 = hits_at_k = 0
        query_seconds = []
        for case in cases["questions"]:
            start = time.perf_counter()
            results = retriever.search(case["question"])
            query_seconds.append(time.perf_counter() - start)
            rank = rank_of(results, case["expected_source"])
            if rank == 1:
                hits_at_1 += 1
            if rank is not None and rank <= top_k:
                hits_at_k += 1

        total = len(cases["questions"])
        return {
            "model": model,
            "dimension": dimension,
            "recall_1": hits_at_1 / total,
            "recall_k": hits_at_k / total,
            "embed_seconds": embed_seconds,
            "avg_query_ms": sum(query_seconds) / len(query_seconds) * 1000,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def file_sha256_of_text(text: str) -> str:
    # the real ingest pipeline hashes file bytes; here the corpus lives in JSON, not on disk
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    args = parse_args()
    cases = load_cases(args.cases)
    print(f"{len(cases['documents'])} documents, {len(cases['questions'])} questions, top_k={args.top_k}\n")

    rows = []
    for model in args.models:
        print(f"{model}...")
        try:
            rows.append(run_model(model, cases, args.host, args.top_k))
        except RagError as e:
            print(f"  skipped: {e}")

    if not rows:
        sys.exit("error: no model could be evaluated")

    header = ["model", "dim", "recall@1", f"recall@{args.top_k}", "embed corpus", "avg query"]
    table = [header]
    for row in rows:
        table.append([
            row["model"],
            str(row["dimension"]),
            f"{row['recall_1']:.0%}",
            f"{row['recall_k']:.0%}",
            f"{row['embed_seconds']:.2f}s",
            f"{row['avg_query_ms']:.0f}ms",
        ])

    print()
    widths = [max(len(r[i]) for r in table) for i in range(len(header))]
    for i, row in enumerate(table):
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)))
        if i == 0:
            print("  ".join("-" * width for width in widths))

    print("\nMarkdown:\n")
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join(["---"] * len(header)) + "|")
    for row in table[1:]:
        print("| " + " | ".join(f"`{row[0]}`" if i == 0 else cell for i, cell in enumerate(row)) + " |")


if __name__ == "__main__":
    main()
