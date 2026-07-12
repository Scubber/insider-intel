"""Local hashing embedder for MVP semantic search (no external API).

Produces deterministic L2-normalized vectors so cosine similarity works offline.
Swap for a managed embedding model (e.g. xAI / OpenAI-compatible) later via the
same Embedder protocol.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+._-]{1,}", re.IGNORECASE)

DEFAULT_DIMS = 256


class Embedder(Protocol):
    dims: int

    def embed(self, text: str) -> list[float]:
        """Return an L2-normalized embedding vector."""
        ...


class HashingEmbedder:
    """Feature-hashing bag-of-tokens embedder (fast, dependency-free)."""

    def __init__(self, dims: int = DEFAULT_DIMS) -> None:
        if dims < 8:
            raise ValueError("dims must be >= 8")
        self.dims = dims

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        tokens = _TOKEN_RE.findall((text or "").lower())
        if not tokens:
            return vec

        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign

        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]


_DEFAULT: HashingEmbedder | None = None


def get_default_embedder() -> HashingEmbedder:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = HashingEmbedder()
    return _DEFAULT


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity for equal-length vectors (assumes L2-normalized)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b, strict=True)))
