import requests

from rag.errors import RagError

# texts sent to /api/embed in one request: keeps each call short enough for the timeout
BATCH_SIZE = 32


class Embedder:
    def __init__(self, model: str, host: str = "http://localhost:11434"):
        if not isinstance(model, str) or not model.strip():
            raise RagError(f"rag_embed_model must be a model name, got {model!r}")
        self.model = model.strip()
        self.host = host.rstrip("/")

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for start in range(0, len(texts), BATCH_SIZE):
            vectors.extend(self.embed_batch(texts[start:start + BATCH_SIZE]))
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            response = requests.post(
                f"{self.host}/api/embed",
                json={"model": self.model, "input": texts},
                # the first call loads the model into memory, which can be slow on CPU
                timeout=(5, 120),
            )
        except requests.ConnectionError as exc:
            raise RagError(f"Cannot reach Ollama at {self.host}. Is it running?") from exc
        except requests.Timeout as exc:
            raise RagError("Ollama took too long to compute the embeddings.") from exc
        except requests.RequestException as exc:
            raise RagError(f"Embedding request to Ollama failed: {exc}") from exc

        if response.status_code != 200:
            raise RagError(self.http_error_message(response))

        try:
            embeddings = response.json()["embeddings"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RagError("Ollama sent back an unexpected answer to the embedding request") from exc

        self.check_embeddings(embeddings, len(texts))
        return embeddings

    def http_error_message(self, response) -> str:
        try:
            detail = response.json().get("error", response.text)
        except ValueError:
            detail = response.text

        if response.status_code == 404:
            return (
                f"Embedding model '{self.model}' is not installed. "
                f"Run: ollama pull {self.model} (or change rag_embed_model in config.json)"
            )
        if response.status_code == 501:
            return (
                f"'{self.model}' cannot produce embeddings. Set rag_embed_model in config.json "
                "to an embedding model such as nomic-embed-text"
            )
        return f"Ollama returned HTTP {response.status_code} for the embedding request: {detail}"

    def check_embeddings(self, embeddings, expected: int) -> None:
        valid = (
            isinstance(embeddings, list)
            and len(embeddings) == expected
            and all(isinstance(v, list) and v for v in embeddings)
            and len({len(v) for v in embeddings}) <= 1
        )
        if not valid:
            raise RagError(
                f"Ollama sent back malformed embeddings from '{self.model}' "
                f"(expected {expected} vector(s) of the same size)"
            )
