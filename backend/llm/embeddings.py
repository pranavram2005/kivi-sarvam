"""Embedding providers.

The default provider is `hashing`: a deterministic, dependency-free embedder
built from hashed word, bigram and character n-gram features. It is not a
neural model, but it gives genuine vector similarity, runs offline, costs
nothing, and produces byte-identical results on every machine - which is what
makes the evaluation in this repo reproducible.

`openai`, `gemini` and `sentence-transformers` are drop-in alternatives for
anyone who wants true distributional semantics; set KIVI_EMBEDDING_PROVIDER.
"""

from __future__ import annotations

import hashlib
import math
import os
from abc import ABC, abstractmethod
from functools import lru_cache

from backend.config import get_settings
from backend.memory.text import bigrams, char_ngrams, content_tokens


class EmbeddingProvider(ABC):
    """Turns text into an L2-normalised vector."""

    name: str = "base"
    dim: int = 512
    model: str = "unknown"

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        ...

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def describe(self) -> dict[str, object]:
        return {"provider": self.name, "model": self.model, "dim": self.dim}


# ---------------------------------------------------------------------------
# Default: hashed feature embedding
# ---------------------------------------------------------------------------
class HashingEmbedder(EmbeddingProvider):
    """Signed feature hashing over multi-resolution text features.

    Three feature families, each with its own weight:

      * unigrams      - the words themselves, the dominant signal
      * bigrams       - short phrases, so "project atlas" is not just two words
      * char 4-grams  - sub-word overlap, which survives ASR mangling

    Features are hashed into a fixed number of buckets with a *signed* hash, so
    collisions cancel out on average instead of always adding. The result is L2
    normalised, which makes the dot product a cosine similarity.

    Hashing uses blake2b rather than Python's built-in `hash()`, because
    `hash()` on strings is salted per process and would make stored vectors
    meaningless after a restart.
    """

    name = "hashing"

    UNIGRAM_WEIGHT = 1.0
    BIGRAM_WEIGHT = 0.6
    CHARGRAM_WEIGHT = 0.30

    def __init__(self, dim: int = 512, model: str = "kivi-hash-v1") -> None:
        self.dim = max(64, dim)
        self.model = model

    def _bucket(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        index = value % self.dim
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return index, sign

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = content_tokens(text)
        if not tokens:
            return vector

        def add(feature: str, weight: float) -> None:
            index, sign = self._bucket(feature)
            vector[index] += sign * weight

        # Sublinear term frequency: a word repeated five times is not five
        # times as important as a word said once.
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            tf = 1.0 + math.log(count)
            add(f"w:{token}", self.UNIGRAM_WEIGHT * tf)
            for gram in char_ngrams(token):
                add(f"c:{gram}", self.CHARGRAM_WEIGHT * tf)

        for gram in bigrams(tokens):
            add(f"b:{gram}", self.BIGRAM_WEIGHT)

        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]
        return vector


# ---------------------------------------------------------------------------
# Optional remote providers
# ---------------------------------------------------------------------------
class OpenAIEmbedder(EmbeddingProvider):
    name = "openai"

    def __init__(self, model: str = "text-embedding-3-small", dim: int = 512) -> None:
        from openai import OpenAI  # imported lazily; optional dependency

        self.model = model
        self.dim = dim
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        cleaned = [t if t.strip() else " " for t in texts]
        response = self._client.embeddings.create(
            model=self.model, input=cleaned, dimensions=self.dim
        )
        return [item.embedding for item in response.data]


class GeminiEmbedder(EmbeddingProvider):
    name = "gemini"

    def __init__(self, model: str = "text-embedding-004", dim: int = 768) -> None:
        from google import genai  # imported lazily; optional dependency

        self.model = model
        self.dim = dim
        self._client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

    def embed(self, text: str) -> list[float]:
        result = self._client.models.embed_content(
            model=self.model, contents=text or " "
        )
        values = list(result.embeddings[0].values)
        self.dim = len(values)
        return values


class SentenceTransformerEmbedder(EmbeddingProvider):
    name = "sentence-transformers"

    def __init__(self, model: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.model = model
        self._model = SentenceTransformer(model)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            [t if t.strip() else " " for t in texts], normalize_embeddings=True
        )
        return [list(map(float, v)) for v in vectors]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_embedder(
    provider: str | None = None, model: str | None = None, dim: int | None = None
) -> EmbeddingProvider:
    settings = get_settings()
    provider = (provider or settings.embedding_provider).lower()
    model = model or settings.embedding_model
    dim = dim or settings.embedding_dim

    try:
        if provider == "openai":
            return OpenAIEmbedder(model=model or "text-embedding-3-small", dim=dim)
        if provider == "gemini":
            return GeminiEmbedder(model=model or "text-embedding-004")
        if provider == "sentence-transformers":
            return SentenceTransformerEmbedder(model=model or "all-MiniLM-L6-v2")
    except Exception as exc:  # pragma: no cover - depends on optional packages
        print(
            f"[kivi] embedding provider '{provider}' unavailable ({exc}); "
            f"falling back to 'hashing'."
        )

    return HashingEmbedder(dim=dim, model="kivi-hash-v1")


@lru_cache(maxsize=1)
def get_embedder() -> EmbeddingProvider:
    """Process-wide embedder singleton."""
    return build_embedder()


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Vectors from `embed` are already unit length, but we
    normalise defensively so mixed-provider databases still behave."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(x * x for x in a[:n]))
    nb = math.sqrt(sum(x * x for x in b[:n]))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
