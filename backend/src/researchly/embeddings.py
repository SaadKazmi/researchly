"""Local embeddings and reranking on CPU through onnxruntime (spec §10, ADR 0007): no torch, no key, no account."""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder
from tokenizers import Tokenizer

EMBED_MODEL = "Qwen/Qwen3-Embedding-0.6B-Q"  # int8 ONNX: cosine ≥0.9996 vs fp32 at half the download
RERANK_MODEL = "BAAI/bge-reranker-v2-m3-int8"
# Qwen3-Embedding expects this prefix on queries only; fastembed does not add it (query cosine 0.81 without it)
QUERY_INSTRUCTION = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"


class Embedder:
    """1024-dim, L2-normalised vectors: the model's full size, stored as halfvec(1024)."""

    model_id = EMBED_MODEL

    def __init__(self, cache_dir: Path) -> None:
        self._model = TextEmbedding(EMBED_MODEL, cache_dir=str(cache_dir))
        loaded: Tokenizer = self._model.model.tokenizer  # pyright: ignore[reportAttributeAccessIssue]
        # fastembed's copy truncates at 512 and pads; counting needs the whole text
        self._counter = Tokenizer.from_str(loaded.to_str())
        self._counter.no_truncation()
        self._counter.no_padding()

    def count_tokens(self, text: str) -> int:
        return len(self._counter.encode(text, add_special_tokens=False).ids)

    def documents(self, texts: Sequence[str]) -> np.ndarray:
        return np.array(list(self._model.embed(list(texts))))

    def query(self, text: str) -> np.ndarray:
        return self.documents([QUERY_INSTRUCTION + text])[0]


class Reranker:
    def __init__(self, cache_dir: Path) -> None:
        self._model = TextCrossEncoder(RERANK_MODEL, cache_dir=str(cache_dir))

    def scores(self, query: str, passages: Sequence[str]) -> list[float]:
        return [float(s) for s in self._model.rerank(query, list(passages))]
