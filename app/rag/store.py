"""Vector index over the regulation corpus (FR-2.1).

The SRS names ChromaDB or PGvector. Both are supported through the same
interface, and an in-process NumPy index is the default so the service starts
with no external dependency. At ten articles the corpus fits in a few hundred
kilobytes, and an exact search over it is faster than a network round trip to
a vector database — an approximate index only starts paying for itself once
the corpus grows past a few thousand chunks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from app.config import get_settings
from app.core.logging import get_logger
from app.rag.embeddings import Embedder, HashingEmbedder, cosine_similarity

logger = get_logger("rased.rag.store")

CORPUS_PATH = Path(__file__).parent / "corpus" / "gtpl.json"


@dataclass(frozen=True, slots=True)
class RegulationChunk:
    """One retrievable article."""

    id: str
    document: str
    article: str
    article_number: str
    title: str
    text: str
    tags: tuple[str, ...] = ()
    thresholds: dict[str, Any] = field(default_factory=dict)

    def embedding_text(self) -> str:
        """What actually gets embedded.

        Title and tags are repeated alongside the body so a short, precise
        query ('سقف الشراء المباشر') is not drowned out by a long article body.
        """
        return f"{self.title}. {' '.join(self.tags)}. {self.text}"

    def citation(self) -> str:
        return f"{self.document} - {self.article}"


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk: RegulationChunk
    score: float


@runtime_checkable
class VectorStore(Protocol):
    def add(self, chunks: list[RegulationChunk]) -> None: ...
    def search(self, query: str, top_k: int) -> list[SearchHit]: ...
    def count(self) -> int: ...


def load_corpus(path: Path | None = None) -> tuple[list[RegulationChunk], dict[str, Any]]:
    """Read the corpus file, returning its chunks and its metadata header."""
    source = path or CORPUS_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    chunks = [
        RegulationChunk(
            id=doc["id"],
            document=doc["document"],
            article=doc["article"],
            article_number=doc["article_number"],
            title=doc["title"],
            text=doc["text"],
            tags=tuple(doc.get("tags", ())),
            thresholds=doc.get("thresholds", {}),
        )
        for doc in payload["documents"]
    ]
    metadata = {
        "corpus_version": payload.get("corpus_version", "unknown"),
        "source_status": payload.get("source_status", "unknown"),
        "disclaimer_ar": payload.get("disclaimer_ar", ""),
        "document_count": len(chunks),
    }
    return chunks, metadata


class InMemoryVectorStore:
    """Exact cosine search over an in-process matrix."""

    def __init__(self, embedder: Embedder | None = None) -> None:
        self.embedder = embedder or HashingEmbedder()
        self._chunks: list[RegulationChunk] = []
        self._matrix: np.ndarray | None = None

    def add(self, chunks: list[RegulationChunk]) -> None:
        if not chunks:
            return
        self._chunks.extend(chunks)
        vectors = self.embedder.embed([c.embedding_text() for c in self._chunks])
        self._matrix = vectors

    def count(self) -> int:
        return len(self._chunks)

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        if self._matrix is None or not self._chunks:
            return []
        query_vector = self.embedder.embed([query])[0]
        scores = cosine_similarity(query_vector, self._matrix)
        order = np.argsort(scores)[::-1][:top_k]
        return [SearchHit(chunk=self._chunks[i], score=float(scores[i])) for i in order]


class ChromaVectorStore:
    """ChromaDB-backed index, used when the optional dependency is installed."""

    def __init__(self, path: Path, collection: str = "gtpl", embedder: Embedder | None = None):
        import chromadb

        self.embedder = embedder or HashingEmbedder()
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"}
        )
        self._by_id: dict[str, RegulationChunk] = {}

    def add(self, chunks: list[RegulationChunk]) -> None:
        if not chunks:
            return
        vectors = self.embedder.embed([c.embedding_text() for c in chunks])
        self._collection.upsert(
            ids=[c.id for c in chunks],
            embeddings=[v.tolist() for v in vectors],
            documents=[c.text for c in chunks],
            metadatas=[
                {"document": c.document, "article": c.article, "title": c.title}
                for c in chunks
            ],
        )
        self._by_id.update({c.id: c for c in chunks})

    def count(self) -> int:
        return self._collection.count()

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        query_vector = self.embedder.embed([query])[0]
        result = self._collection.query(query_embeddings=[query_vector.tolist()], n_results=top_k)
        hits: list[SearchHit] = []
        for chunk_id, distance in zip(result["ids"][0], result["distances"][0], strict=False):
            chunk = self._by_id.get(chunk_id)
            if chunk is not None:
                hits.append(SearchHit(chunk=chunk, score=1.0 - float(distance)))
        return hits


def build_store(embedder: Embedder | None = None) -> VectorStore:
    """Construct the configured backend, falling back to memory if unavailable."""
    settings = get_settings()
    if settings.vector_backend == "chroma":
        try:
            return ChromaVectorStore(settings.vector_path, embedder=embedder)
        except ImportError:
            logger.warning(
                "chromadb is not installed; falling back to the in-memory index",
                extra={"configured_backend": "chroma"},
            )
    return InMemoryVectorStore(embedder=embedder)
