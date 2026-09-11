"""Embedding backends (FR-2.1).

Two implementations, chosen by configuration:

``SovereignEmbedder``
    Calls an embedding endpoint inside the VPC — normally the same vLLM
    deployment that serves generation. This is what production should use.

``HashingEmbedder``
    A dependency-free hashed n-gram vectoriser. It needs no model, no download
    and no network, which is what keeps the test suite and an air-gapped first
    boot working. It is lexical, not semantic: it matches on shared character
    n-grams, so it retrieves «سقف الشراء المباشر» for a query about a ceiling
    but will not connect two passages that share meaning without vocabulary.

That limitation is acceptable here and worth being explicit about. Article
retrieval over a small, highly technical Arabic corpus is largely a lexical
problem, and the citation gate in FR-2.3 means a retrieval miss produces a
refusal rather than a wrong answer.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol, runtime_checkable

import numpy as np

from app.pii.validators import normalize_arabic, normalize_digits

DEFAULT_DIMENSION = 512

_TOKEN = re.compile(r"[\wء-ۿ]+", re.UNICODE)


@runtime_checkable
class Embedder(Protocol):
    dimension: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _prepare(text: str) -> str:
    """Fold a string to its comparison form before features are extracted."""
    return normalize_arabic(normalize_digits(text)).lower()


def _features(text: str, *, min_n: int = 3, max_n: int = 5) -> list[str]:
    """Word unigrams plus character n-grams within each word.

    Character n-grams carry most of the weight for Arabic, where the same root
    surfaces with different affixes — تجاري / التجاري / تجارية share n-grams
    that a word-level model would treat as three unrelated tokens.
    """
    prepared = _prepare(text)
    tokens = _TOKEN.findall(prepared)
    features: list[str] = list(tokens)
    for token in tokens:
        padded = f" {token} "
        for n in range(min_n, max_n + 1):
            if len(padded) < n:
                break
            features.extend(padded[i : i + n] for i in range(len(padded) - n + 1))
    return features


class HashingEmbedder:
    """Signed hashing vectoriser with L2 normalisation."""

    def __init__(self, dimension: int = DEFAULT_DIMENSION) -> None:
        self.dimension = dimension

    def _hash(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        # The sign bit is taken from a separate bit of the digest so that
        # collisions cancel on average instead of always reinforcing.
        index = value % self.dimension
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return index, sign

    def embed(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature in _features(text):
                index, sign = self._hash(feature)
                matrix[row, index] += sign
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return matrix / norms


class SovereignEmbedder:
    """Embeddings from an OpenAI-compatible endpoint inside the VPC."""

    def __init__(self, base_url: str, model: str, api_key: str, dimension: int, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dimension = dimension
        self.timeout = timeout

    def embed(self, texts: list[str]) -> np.ndarray:
        import httpx

        response = httpx.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": texts},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        vectors = np.array([item["embedding"] for item in payload["data"]], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return vectors / norms


def cosine_similarity(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of one L2-normalised query against normalised rows."""
    return matrix @ query
