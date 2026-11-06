"""Section text → overlapping, sentence-aware chunks (spec §9.2) with semchunk and the embedder's tokenizer."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from semchunk.semchunk import chunk

# semchunk keeps chunk + overlap within chunk_size: 512 gives ~330-token means and ~400 maxima (research §6),
# the spec's "~350", and leaves room for a short query in the reranker's 512-token pair window.
CHUNK_TOKENS = 512
OVERLAP = 0.15


@dataclass(frozen=True)
class Piece:
    text: str
    tokens: int


def chunk_text(text: str, count_tokens: Callable[[str], int]) -> list[Piece]:
    if not text.strip():
        return []
    # no offsets=True, so semchunk returns plain strings
    chunks = cast(list[str], chunk(text, chunk_size=CHUNK_TOKENS, token_counter=count_tokens, overlap=OVERLAP))
    return [Piece(c, count_tokens(c)) for c in chunks if c.strip()]
