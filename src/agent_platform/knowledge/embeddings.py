"""Embedding provider port + adapters (knowledge-rag-spec.md section 8).

LangChain seam: the ONLY knowledge module file besides chunking.py
that may import LangChain (boundary-enforced). The OpenAI adapter is
constructed lazily on first use (E3): create_app() boots without an
API key and management routes stay functional.
"""

import hashlib
import math
import re
import threading
from typing import Protocol, runtime_checkable

from agent_platform.knowledge.domain import ProviderUnavailableError


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embedding port (E1). name identifies model + provider for E4."""

    name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class LangChainEmbeddingProvider:
    """Wraps any langchain_core Embeddings behind the platform port."""

    def __init__(self, embeddings, name: str) -> None:
        self._embeddings = embeddings
        self.name = name

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [list(map(float, vector)) for vector in self._embeddings.embed_documents(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [float(value) for value in self._embeddings.embed_query(text)]


class LazyOpenAIEmbeddingProvider:
    """OpenAI embeddings via langchain_openai, constructed on first call.

    Missing key, construction or call failures raise
    ProviderUnavailableError so the API maps to 503 (E3) and ingest
    marks the document FAILED (I7).
    """

    def __init__(self, model: str) -> None:
        self._model = model
        self.name = f"openai:{model}"
        self._inner: LangChainEmbeddingProvider | None = None
        self._lock = threading.Lock()

    def _provider(self) -> LangChainEmbeddingProvider:
        with self._lock:
            if self._inner is None:
                try:
                    from langchain_openai import OpenAIEmbeddings

                    self._inner = LangChainEmbeddingProvider(
                        OpenAIEmbeddings(model=self._model), self.name
                    )
                except Exception as exc:
                    raise ProviderUnavailableError(
                        f"openai embedding provider unavailable: {exc}"
                    ) from exc
            return self._inner

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return self._provider().embed_documents(texts)
        except ProviderUnavailableError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(f"openai embedding call failed: {exc}") from exc

    def embed_query(self, text: str) -> list[float]:
        try:
            return self._provider().embed_query(text)
        except ProviderUnavailableError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(f"openai embedding call failed: {exc}") from exc


class FakeEmbeddingProvider:
    """Deterministic token-hash embeddings for tests/dev (E2).

    dim=64: each token hashes to a bucket that accumulates +1, then the
    vector is L2-normalized, so cosine similarity roughly follows token
    overlap. CJK characters count as individual tokens so Chinese text
    ranks functionally too.
    """

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim
        self.name = "fake:token-hash"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text):
            bucket = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big") % self._dim
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def create_embedding_provider(settings) -> EmbeddingProvider:
    """Factory over Settings (raw strings; parsing lives here per house
    convention). openai -> lazy OpenAI, fake -> deterministic fake."""
    provider = (settings.knowledge_embedding_provider or "openai").strip().lower()
    if provider == "openai":
        return LazyOpenAIEmbeddingProvider(settings.knowledge_openai_embedding_model)
    if provider == "fake":
        return FakeEmbeddingProvider()
    raise ValueError(f"unknown knowledge embedding provider: {provider}")
