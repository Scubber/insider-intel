"""Tests for local hashing embeddings."""

from __future__ import annotations

from shared.utils.embeddings import HashingEmbedder, cosine_similarity


def test_embed_is_deterministic_and_normalized() -> None:
    emb = HashingEmbedder(dims=64)
    a = emb.embed("ransomware phishing campaign")
    b = emb.embed("ransomware phishing campaign")
    assert a == b
    norm = sum(x * x for x in a) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_similar_text_scores_higher_than_unrelated() -> None:
    emb = HashingEmbedder(dims=128)
    query = emb.embed("ransomware zero-day exploit")
    related = emb.embed("Critical ransomware uses zero-day exploit against servers")
    unrelated = emb.embed("Quarterly earnings beat analyst expectations for retail")
    assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)
