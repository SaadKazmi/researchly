# /// script
# requires-python = "==3.12.*"
# dependencies = ["sentence-transformers==6.2.0", "torch==2.14.1"]
# ///
"""Writes fp32 reference vectors for tests/platform/test_embeddings.py.

Run with `uv run --no-project --python 3.12 backend/scripts/embedding_reference.py`. torch lives only in this
script's environment, never in a lock. Re-run only when EMBED_MODEL changes. fp32 is required: the default bf16
load is noisy (cosines above 1.001).
"""

# pyright: reportMissingImports=false
# (torch and sentence-transformers exist only in this script's own environment)
import json
from pathlib import Path

import torch
from sentence_transformers import SentenceTransformer

MODEL = "Qwen/Qwen3-Embedding-0.6B"
DOCUMENTS = [
    "Self-attention lets every token in a sequence weigh every other token when computing its representation.",
    "Photosynthesis converts light energy into chemical energy stored in glucose.",
    "The transformer architecture replaced recurrence with stacked attention and feed-forward layers.",
    "Paris is the capital and most populous city of France.",
]
QUERY = "how does attention work in transformers?"
OUT = Path(__file__).resolve().parents[1] / "tests" / "platform" / "fixtures" / "embeddings_ref.json"

model = SentenceTransformer(MODEL, model_kwargs={"torch_dtype": torch.float32})
docs = model.encode(DOCUMENTS, normalize_embeddings=True)
query = model.encode([QUERY], prompt_name="query", normalize_embeddings=True)[0]


def r6(v) -> list[float]:
    return [round(float(x), 6) for x in v]


OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(
    json.dumps(
        {
            "model": MODEL,
            "documents": DOCUMENTS,
            "query": QUERY,
            "document_vectors": [r6(v) for v in docs],
            "query_vector": r6(query),
        }
    ),
    encoding="utf-8",
)
print("wrote", OUT)
