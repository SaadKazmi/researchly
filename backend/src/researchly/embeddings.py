"""Local embeddings and reranking on CPU through onnxruntime (spec §10, ADR 0007): no torch, no key, no account.

Both load their model on first use, not on construction: a run that calls no tool downloads nothing. Every caller
already runs them through `asyncio.to_thread`, so the first load never blocks the event loop.
"""

import threading
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
        self._dir = cache_dir
        self._lock = threading.Lock()  # two tools' threads may ask first at once
        self._loaded: tuple[TextEmbedding, Tokenizer] | None = None

    def _load(self) -> tuple[TextEmbedding, Tokenizer]:
        with self._lock:
            if self._loaded is None:
                model = TextEmbedding(EMBED_MODEL, cache_dir=str(self._dir))
                loaded: Tokenizer = model.model.tokenizer  # pyright: ignore[reportAttributeAccessIssue]
                # fastembed's copy truncates at 512 and pads; counting needs the whole text
                counter = Tokenizer.from_str(loaded.to_str())
                counter.no_truncation()
                counter.no_padding()
                self._loaded = model, counter
            return self._loaded

    def count_tokens(self, text: str) -> int:
        return len(self._load()[1].encode(text, add_special_tokens=False).ids)

    def documents(self, texts: Sequence[str]) -> np.ndarray:
        return np.array(list(self._load()[0].embed(list(texts))))

    def query(self, text: str) -> np.ndarray:
        return self.documents([QUERY_INSTRUCTION + text])[0]


class Reranker:
    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir
        self._lock = threading.Lock()
        self._model: TextCrossEncoder | None = None

    def scores(self, query: str, passages: Sequence[str]) -> list[float]:
        with self._lock:
            if self._model is None:
                self._model = TextCrossEncoder(RERANK_MODEL, cache_dir=str(self._dir))
        return [float(s) for s in self._model.rerank(query, list(passages))]
