# Decision: ChromaDB vs. our own SQLite vector store

**Status:** Decided — keep the custom SQLite + numpy store (`rag/store.py`, `rag/retriever.py`), do not adopt ChromaDB.

## Context

R1 asks for a vector store that works like a database with collections (several independent knowledge bases, each with CRUD), and names ChromaDB as the advised choice. Sensai already has a working store built directly on SQLite and numpy, on top of which the knowledge-base features were built: several named knowledge bases with create/list/delete and add/remove documents (`rag/collections.py`), a chosen and compared embedding model per knowledge base, a chunk size/overlap per knowledge base, a retrieval threshold (`rag_min_score`), and a pipeline view (documents → chunks → embeddings, and retrieval with scores and the injected prompt). The question is whether to replace that store with ChromaDB or keep it.

## Decision

Keep the SQLite + numpy store. It already provides everything a vector store needs to give Sensai *for this project* — collections, CRUD, cosine similarity search, a threshold — without adding ChromaDB's dependency weight, moving parts, or loss of visibility into what is actually stored. Revisit this only if a concrete need shows up that the current store cannot meet (see "When this decision should be revisited" below).

## Why not ChromaDB

**Dependency weight, for a project that is meant to run fully local and light.** `pip install chromadb --dry-run` resolves **56 additional packages**, including `onnxruntime` (ChromaDB's default embedding path, unused here since Sensai always embeds through Ollama), `grpcio`, a `kubernetes` client, a full OpenTelemetry OTLP exporter stack, `huggingface_hub` + `tokenizers`, `pydantic` v2, `httpx`, `aiohttp`, and `bcrypt`. Sensai's own storage code depends on `numpy` and the standard library's `sqlite3` only — both already project dependencies for other reasons. [M1]/[EV2] of the project's use case (a developer working offline, sometimes under an NDA, who wants a small, auditable footprint) weighs against pulling in a stack this size for a feature that numpy and `sqlite3` already cover.

**Scale: brute-force cosine search is not the bottleneck here.** ChromaDB's main advantage over a naive store is approximate nearest-neighbor search (HNSW), which matters once a collection holds hundreds of thousands of vectors and exact search becomes too slow. The use case is one developer's project documentation and personal knowledge bases — tens to low thousands of chunks per knowledge base, not a web-scale corpus. `evals/eval_embeddings.py` measured 16-30ms per query against a small corpus on CPU; a 768-dimension matrix multiply against even 50,000 chunks is still single-digit-to-tens of milliseconds on the numpy `matrix @ query_vector` path `rag/retriever.py` already uses. There is no retrieval-speed problem to solve.

**Transparency the pipeline view (and debugging) depends on.** The pipeline view added for this project shows every candidate chunk's score, which ones passed `rag_top_k`/`rag_min_score`, and the exact prompt that gets injected — including chunks that *did not* pass the threshold, which a normal similarity-search API does not return. Built on raw SQL and numpy, that is a plain query plus a sort. Built on top of ChromaDB, it would mean either bypassing Chroma's query API to reach its internal storage anyway, or living with less visibility into exactly what is in the index. A `.sqlite` file per knowledge base can also be opened and inspected with any SQLite tool; ChromaDB's persistent client writes its own internal layout (SQLite metadata plus its own binary/Parquet index segments) that isn't meant to be read directly.

**ChromaDB's collection model does not add anything "database with collections" does not already have here.** Each Sensai knowledge base is already one self-contained unit: its own folder of documents, its own SQLite index file, its own embedding model and chunk settings, created/listed/deleted through `rag/collections.py` and `/kb`. That *is* the "several knowledge bases, each with CRUD" R1 asks for; ChromaDB's `Client.create_collection()` / `get_collection()` / `delete_collection()` would rename these operations, not add a capability Sensai is missing.

**Migration cost vs. benefit, at this point in the project.** Four knowledge-base features (CRUD, per-collection embedding model, per-collection chunking, the pipeline view) are already built and tested directly against `rag/store.py`'s schema. Replacing it now means rewriting `rag/store.py`, `rag/ingest.py`, `rag/retriever.py` and `rag/collections.py`, and re-verifying every one of those features against Chroma's API and persistence model, for a store that would behave the same way at this scale and remove the direct SQL access the pipeline view relies on. That cost is not justified by a benefit this project does not need yet.

## When this decision should be revisited

- A single knowledge base needs to hold on the order of **hundreds of thousands of chunks or more**, where brute-force cosine search over the full matrix becomes measurably slow and approximate search (HNSW) is worth its accuracy trade-off.
- Sensai needs to **share an index across multiple processes or machines** (a server-mode vector database), rather than one process reading its own local SQLite file.
- Retrieval needs **metadata filtering combined with similarity search** (e.g. "only chunks from documents tagged `confidential`") beyond what a `WHERE source = ?` on the existing `documents`/`chunks` tables can express.

None of these apply to the current use case: a single developer's local, offline assistant with a handful of project-sized knowledge bases.
