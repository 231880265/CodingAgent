"""Pluggable, lazily initialized embedding providers."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input string."""


class HashingEmbeddingProvider:
    """Dependency-free lexical baseline with stable hashed token features.

    It is intentionally deterministic, so ranking tests and offline use do not
    depend on a model download.  A real local encoder can be selected through
    configuration without changing the retriever.
    """

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = max(32, int(dimensions))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            return [value / norm for value in vector]
        return vector


class SentenceTransformerEmbeddingProvider:
    """Optional local encoder; model import and loading happen on first search."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

            self._model = SentenceTransformer(self.model_name)
        values = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, value)) for value in values]


def _tokens(text: str) -> list[str]:
    lowered = text.lower()
    tokens = re.findall(r"[a-z0-9_.:/\\-]+", lowered)
    for block in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(block) <= 2:
            tokens.append(block)
        else:
            tokens.extend(block[index : index + 2] for index in range(len(block) - 1))
    return tokens
