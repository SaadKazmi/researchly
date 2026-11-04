import time
from pathlib import Path

import pytest

from researchly.embeddings import Embedder
from researchly.ingest.chunking import CHUNK_TOKENS, chunk_text

from .conftest import report

pytestmark = pytest.mark.platform


@pytest.fixture(scope="module")
def embedder(models_dir: Path) -> Embedder:
    t = time.perf_counter()
    e = Embedder(models_dir / "fastembed")
    report("embedder load (chunking tokens)", time.perf_counter() - t)
    return e


def test_qwen3_token_counts_are_untruncated_and_chunks_fit(embedder: Embedder) -> None:
    assert 3 <= embedder.count_tokens("Attention is all you need") <= 12
    long = "Self-attention relates positions of a single sequence. " * 200
    assert embedder.count_tokens(long) > 1000  # fastembed's own tokenizer truncates at 512; ours must not
    pieces = chunk_text(long, embedder.count_tokens)
    assert all(p.tokens <= CHUNK_TOKENS for p in pieces)
    mean = sum(p.tokens for p in pieces) / len(pieces)
    print(f"chunk tokens: mean={mean:.1f} max={max(p.tokens for p in pieces)} n={len(pieces)}")
    assert 250 <= mean <= 512
    assert embedder.model_id == "Qwen/Qwen3-Embedding-0.6B-Q"
