# ADR 0007: Local embedder and reranker on onnxruntime via fastembed

- Status: accepted
- Date: 2026-11-02
- Spec sections: §10 (embeddings, rerank), §4 (models row), §26

## Context
Spec §10 wants Qwen3-Embedding-0.6B as the embedder (via fastembed if supported) and bge-reranker-v2-m3 on onnxruntime, both local, keyless and torch-free. Research is in `docs/research/2026-11-02-step1e-embed.md` and found:
- fastembed 0.9.x serves `Qwen/Qwen3-Embedding-0.6B`, as fp32 (2.38 GB) or int8 `-Q` (1.12 GB), from `Qdrant/Qwen3-Embedding-0.6B-onnx` (Apache-2.0).
- fastembed does **not** add Qwen3's query instruction. Without it, query cosine against the reference is 0.81.
- int8 against fp32 cosine is 0.9996–0.9998.
- The last onnxruntime wheel for macOS x86_64 is 1.23.2.

## Decision
- **Embedder:** `Qwen/Qwen3-Embedding-0.6B-Q` (int8) through `fastembed==0.9.1`, with 1024 dims, L2-normalised and stored as `halfvec(1024)`. `researchly.embeddings.Embedder.query` prepends the Qwen3 query instruction; documents get none.
- **Reranker:** `BAAI/bge-reranker-v2-m3-int8` through fastembed's `TextCrossEncoder`. Scores are raw logits.
- **Downloads:** both models download on first use into the shared model cache. They are about 1.7 GB together, against 4.7 GB for fp32.
- **onnxruntime pin:** `onnxruntime==1.23.2` on `darwin`/`x86_64` and `>=1.23` elsewhere. Intel Macs are frozen on 1.23.x until they are dropped.
- **Rerank depth:** depth is no longer a fixed "top 50". Cross-encoding is the slow step on CPU (see Evidence), so step 4 sets the depth and passage truncation from a relevance-vs-latency benchmark on scholarly queries. That benchmark compares bge-reranker-v2-m3-int8 with smaller Apache/MIT cross-encoders that fastembed serves. Until step 4, callers pass the candidates they want reranked.

## Consequences
- No torch and no key. One shared onnxruntime with the PDF pipeline.
- **Correctness test:** a committed fp32 sentence-transformers reference (`backend/tests/platform/fixtures/embeddings_ref.json`, made by `backend/scripts/embedding_reference.py`) must match at cosine ≥0.99. This catches a dropped query instruction or a changed export.
- **First-run download:** the first ingest downloads about 1.7 GB, so the UI must show progress. On Windows, one first download hit WinError 1314 (symlink privilege) in the HF cache; the step 2 download path retries and falls back to copying.
- **Watch:** rerank latency. With 50 pairs it is 11–51 s on CI runners, which is too slow for an interactive turn.

## Evidence
- **Local, Windows 10, i7-8700:**
  - Embedder load: 48 s cold (including download), 3.5 s warm.
  - Rerank 50 passages of about 120 words: about 13 s.
- **CI** (platform job summary for PR #5, run 41495194267; models restored from the Actions cache):

| Runner | Embedder load (s) | Embed 4 docs (s) | Reranker load (s) | Rerank 50 passages (s) |
|---|---|---|---|---|
| ubuntu-24.04 | 1.88 | 1.19 | 1.45 | 11.28 |
| ubuntu-24.04-arm | 1.40 | 0.98 | 1.19 | 18.42 |
| windows-2025 | 2.50 | 4.06 | 4.18 | 47.45 |
| macos-15 (arm64) | 1.01 | 0.70 | 0.99 | 12.77 |
| macos-15-intel | 16.19 | 1.76 | 6.61 | 50.82 |

- All five runners pass four checks: the reference cosine ≥0.99 for documents and queries; the three relevant passages ranked top among 50; no torch imported; and the lock guard.
- Licences: fastembed and the Qdrant Qwen3 ONNX export are Apache-2.0; bge-reranker-v2-m3 is Apache-2.0; onnxruntime is MIT. Excluded models: jina v3 and jina-reranker-v2 (CC-BY-NC), and EmbeddingGemma (Gemma terms, gated repo).
