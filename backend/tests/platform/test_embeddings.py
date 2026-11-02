import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from researchly.embeddings import Embedder, Reranker

from .conftest import report

pytestmark = pytest.mark.platform

REF = json.loads((Path(__file__).parent / "fixtures" / "embeddings_ref.json").read_text("utf-8"))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


@pytest.fixture(scope="session")
def embedder(models_dir: Path) -> Embedder:
    t = time.perf_counter()
    e = Embedder(models_dir / "fastembed")
    report("embedder load (cold if first run)", time.perf_counter() - t)
    return e


@pytest.fixture(scope="session")
def reranker(models_dir: Path) -> Reranker:
    t = time.perf_counter()
    r = Reranker(models_dir / "fastembed")
    report("reranker load (cold if first run)", time.perf_counter() - t)
    return r


def test_documents_match_the_fp32_reference(embedder: Embedder) -> None:
    t = time.perf_counter()
    vecs = embedder.documents(REF["documents"])
    report("embed 4 documents", time.perf_counter() - t)
    assert vecs.shape == (4, 1024)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1, atol=1e-3)
    for got, want in zip(vecs, REF["document_vectors"], strict=True):
        assert cosine(got, np.array(want)) >= 0.99


def test_query_uses_the_instruction_and_matches_the_reference(embedder: Embedder) -> None:
    ref_q = np.array(REF["query_vector"])
    q = embedder.query(REF["query"])
    assert q.shape == (1024,)
    assert cosine(q, ref_q) >= 0.99
    docs = embedder.documents(REF["documents"])
    ranking = list(np.argsort(-(docs @ q)))
    assert ranking == list(np.argsort(-(np.array(REF["document_vectors"]) @ ref_q)))
    assert ranking[0] in (0, 2)
    assert ranking[-1] == 3  # Paris
    bare = embedder.documents([REF["query"]])[0]
    assert cosine(bare, ref_q) < cosine(q, ref_q)  # the instruction prefix matters


RELEVANT = [
    "Self-attention computes, for every token in a sequence, a weighted average over all the other tokens. "
    "Each token is projected into a query, a key and a value vector. The dot product of a query with every key "
    "gives a score, a softmax turns the scores into weights, and the weights mix the value vectors into a new "
    "representation. Because every token attends to every other one in a single step, a transformer captures "
    "long-range dependencies without the sequential bottleneck of a recurrent network, and the whole computation "
    "parallelises well on modern accelerators. Multiple heads let the model attend to different relations at once.",
    "A transformer block stacks a multi-head attention layer and a position-wise feed-forward network, each wrapped "
    "in a residual connection and layer normalisation. Attention is how information moves between positions: the "
    "attention weights say how much each position reads from every other. Positional encodings are added to the "
    "embeddings because attention itself is order-agnostic. Stacking many such blocks lets early layers capture "
    "local syntax while deeper layers combine distant context into abstract features useful for the task at hand.",
    "In scaled dot-product attention the scores are divided by the square root of the key dimension before the "
    "softmax, which keeps the gradients stable. Causal masking stops a decoder from attending to future tokens, "
    "so language models can be trained to predict the next word in parallel across a whole sequence. Cross "
    "attention reuses the same mechanism so that a decoder can look at encoder outputs, which is how translation "
    "models align a target word with the source words it depends on.",
]
TOPICS = [
    ("sourdough bread", "flour, water, salt and a long fermentation with wild yeast", "crumb and crust"),
    ("coral reefs", "polyps, algae and calcium carbonate skeletons in warm shallow seas", "bleaching events"),
    ("medieval castles", "stone keeps, curtain walls, moats and murder holes", "siege warfare"),
    ("volcanic islands", "magma plumes, basalt flows and black sand beaches", "island formation"),
    ("jazz improvisation", "chord changes, swing rhythm and call-and-response solos", "club culture"),
    ("beekeeping", "hives, frames, smokers and the seasonal rhythm of the colony", "honey harvests"),
    ("marathon training", "long runs, tempo sessions, tapering and carbohydrate loading", "race day pacing"),
]


def off_topic(i: int) -> str:
    name, detail, aspect = TOPICS[i % len(TOPICS)]
    return (
        f"This passage is about {name}, number {i} in a long catalogue of unrelated hobbies and landscapes. "
        f"It describes {detail}, and it spends most of its length on {aspect}. Practitioners and enthusiasts "
        f"often swap stories about their first attempts, the mistakes they made, and the small habits that "
        f"improved their results over the years. Seasonal changes matter a great deal, as do patience, good "
        f"equipment and a willingness to learn from older hands. Variation {i} adds that local traditions "
        f"differ from region to region, and that newcomers are usually welcomed warmly by the community. "
        f"None of this has anything to do with neural networks or sequence modelling; it is simply everyday "
        f"knowledge gathered by people who care about {name} and want to pass it on to the next generation."
    )


def test_reranker_puts_the_relevant_passages_first(reranker: Reranker) -> None:
    passages = [off_topic(i) for i in range(50)]
    relevant_idx = [4, 21, 37]
    for idx, text in zip(relevant_idx, RELEVANT, strict=True):
        passages[idx] = text
    t = time.perf_counter()
    scores = reranker.scores("how does attention work in transformers?", passages)
    report("rerank 50 passages", time.perf_counter() - t)
    assert len(scores) == 50
    assert all(math.isfinite(s) for s in scores)
    assert sorted(np.argsort(scores)[-3:].tolist()) == relevant_idx


def test_no_torch_was_imported(embedder: Embedder, reranker: Reranker) -> None:
    assert "torch" not in sys.modules
    assert importlib.util.find_spec("torch") is None
