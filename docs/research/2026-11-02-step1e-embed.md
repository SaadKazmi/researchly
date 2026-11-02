# Research 1e: embedder + reranker via ONNX/CPU (2026-11-02)

Recommendation up front: use fastembed 0.9.1 as-is for both models. It supports Qwen3-Embedding-0.6B and bge-reranker-v2-m3, with no torch in the dependency tree. Ship fp32 or the int8 variants (see section 3). The main pitfall is that fastembed does NOT add the Qwen3 query instruction for you. The real macOS-x64 risk is onnxruntime wheels (section 4).

## 1. fastembed today (verified locally)
- fastembed 0.9.1 (PyPI, 2026-10-24; 0.9.0 2026-10-07, 0.8.1 2026-09-22). Apache-2.0. Requires-Python >=3.10.
- Installed into a fresh `uv --python 3.12` project, the full tree is: anyio, certifi, charset-normalizer, click, colorama, filelock, flatbuffers, fsspec, h11, hf-xet, httpcore, httpx, huggingface-hub 1.33.0, idna, loguru, mmh3, numpy 2.5.4, onnxruntime 1.31.1, packaging, pillow, protobuf, py-rust-stemmers, pyyaml, requests, tokenizers 0.23.3, tqdm, typing-extensions, urllib3, win32-setctime. **No torch, no transformers.** `.venv` is 140 MB.
- Its onnxruntime pin for 3.12 is `>=1.17.0,!=1.20.0,!=1.24.0,!=1.24.1` (no upper cap). It declares `huggingface-hub>=0.20,<2.0` and uses `requests` (it does not use PoliteClient).
- `TextEmbedding.list_supported_models()` entries:

| fastembed id | dim | size (GB) | licence | HF repo, file |
|---|---|---|---|---|
| Qwen/Qwen3-Embedding-0.6B | 1024 | 2.38 | apache-2.0 | Qdrant/Qwen3-Embedding-0.6B-onnx, onnx/model.onnx + model.onnx_data |
| Qwen/Qwen3-Embedding-0.6B-Q | 1024 | 1.12 | apache-2.0 | same repo, onnx/model_quantized.onnx (needs onnxruntime>=1.23) |
| nomic-ai/nomic-embed-text-v1.5 (+ -Q 0.13 GB) | 768 | 0.52 | apache-2.0 | nomic-ai/nomic-embed-text-v1.5 |
| jinaai/jina-embeddings-v3 | 1024 | 2.29 | **cc-by-nc-4.0 (non-commercial; avoid)** | jinaai/jina-embeddings-v3 |
| google/embeddinggemma-300m | 768 | 1.24 | **gemma terms (custom licence; HF repo gated)**; fastembed pulls onnx-community/embeddinggemma-300m-ONNX | |
| snowflake-arctic-embed-xs/s/m/m-long/l | 384 to 1024 | | apache-2.0 | Only v1; **arctic-embed-m-v2.0 is not in 0.9.1's list** |
| bge-m3 | not in fastembed's list | | | |

- `TextCrossEncoder.list_supported_models()`:

| fastembed id | size (GB) | licence | repo |
|---|---|---|---|
| BAAI/bge-reranker-v2-m3 | 2.27 | apache-2.0 | onnx-community/bge-reranker-v2-m3-ONNX, onnx/model.onnx |
| BAAI/bge-reranker-v2-m3-int8 | 0.57 | apache-2.0 | same repo, onnx/model_int8.onnx |
| Xenova/ms-marco-MiniLM-L-6-v2 | 0.08 | apache-2.0 | |
| Xenova/ms-marco-MiniLM-L-12-v2 | 0.12 | apache-2.0 | |
| BAAI/bge-reranker-base | 1.04 | mit | |
| jinaai/jina-reranker-v1-tiny-en and -turbo-en | 0.13 and 0.15 | apache-2.0 | |
| jinaai/jina-reranker-v2-base-multilingual | 1.11 | **cc-by-nc-4.0** | |

- HF repos (HF API, `?blobs=true`):
  - Qdrant/Qwen3-Embedding-0.6B-onnx: author Qdrant, apache-2.0, modified 2026-08-18. model.onnx is 4 MB plus a 2383 MB external-data file (fp32); model_quantized.onnx is 1121 MB. The export has only the inputs input_ids and attention_mask, and one output `last_hidden_state` of shape [batch, seq, 1024]. It is a clean encoder-style export.
  - onnx-community/Qwen3-Embedding-0.6B-ONNX: modified 2026-04-03, 24k downloads, **licence not set in the HF metadata** (it derives from Apache-2.0). Variants: fp32 2.4 GB, fp16 1.2 GB, int8/uint8/quantized 614 MB, q4 914 MB, q4f16 568 MB, bnb4 887 MB. It is a causal-LM export with KV-cache: the inputs include position_ids and 56 `past_key_values.N.key/value` tensors, all required. It is awkward to use directly (I hit "Required inputs (...past_key_values.0.key...) are missing"). Prefer the Qdrant export.
  - onnx-community/bge-reranker-v2-m3-ONNX: licence not set in the metadata (base model is Apache-2.0), modified 2025-09-01, 72k downloads. fp32 2271 MB, fp16 1136 MB, int8/uint8/quantized 571 MB, q4f16 702 MB.
  - Qwen/Qwen3-Embedding-0.6B (official): apache-2.0, safetensors 1192 MB, 8.9M downloads. The official repo has no ONNX; the Qdrant repo is the de facto source.
- **Query prefix caveat:** fastembed 0.9.1 `query_embed` does not add the instruction. The model description says "Prefixes: `Instruct: {task_description}\nQuery:{query}` for queries, none for documents". The Researchly code must build the string itself. Evidence: `query_embed("what is self-attention?")` gave cosine 0.8132 against the sentence-transformers query vector, which was built with the prompt. With a manual prefix the cosine is 1.0000.
- Pooling in fastembed: `last_token_normalized_embedding.py` uses `last_token_pooling` on the attention mask, then `normalize`. It gives 1024 dims and no truncation option, so Matryoshka truncation is done by us.

## 2. Direct onnxruntime sketch (works, verified: cosine 1.0 vs fp32 reference)
Use the Qdrant fp32 export (or `model_quantized.onnx`, onnxruntime>=1.23).
```python
import numpy as np, onnxruntime as ort
from huggingface_hub import snapshot_download
from tokenizers import Tokenizer
p = snapshot_download("Qdrant/Qwen3-Embedding-0.6B-onnx",
                      allow_patterns=["onnx/model.onnx","onnx/model.onnx_data","tokenizer.json"])
tok = Tokenizer.from_file(p+"/tokenizer.json")
tok.enable_padding(pad_id=151643, pad_token="<|endoftext|>", direction="left")  # left pad -> last token = last column
tok.enable_truncation(512)
sess = ort.InferenceSession(p+"/onnx/model.onnx", providers=["CPUExecutionProvider"])
def embed(texts, dim=1024):
    e = tok.encode_batch(texts)
    ids = np.array([x.ids for x in e], np.int64); am = np.array([x.attention_mask for x in e], np.int64)
    h = sess.run(None, {"input_ids": ids, "attention_mask": am})[0]   # [B,T,1024]
    v = h[:, -1, :][:, :dim]                                           # last-token pool, then Matryoshka cut
    return v / np.linalg.norm(v, axis=1, keepdims=True)                # normalise AFTER truncation
Q = lambda q: "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:" + q
```
Documents get no prefix. Notes:
- The tokenizer.json from the Qwen repo does not append EOS by itself. The sentence-transformers reference used the same tokens and matched at cosine 1.0, so none is needed here. Check this in the spike if you change tokenizers.
- With right padding you would have to index by `attention_mask.sum(1)-1`. fastembed handles that itself.
- Storing as halfvec(1024) loses nothing visible. Truncating to 512 and re-normalising gave cosine 1.0000 vs the reference truncated the same way; for the Q variant it was 0.9995 to 0.9998.

## 3. Quality and speed
Benchmarks (as reported, with source):
- Qwen3-Embedding-0.6B model card: MTEB Multilingual 64.33 (task mean) / 56.00 (type); MTEB Eng v2 70.70 / 64.88; C-MTEB 66.33. MRL 32 to 1024 dims, last-token pooling, max length 32k, Apache-2.0, 0.6B params. In its own table BGE-M3 (0.6B) scores 59.56 on MTEB Multilingual, multilingual-e5-large-instruct 63.22 (Eng v2 65.53), and gte-Qwen2-1.5B 59.45 (Eng v2 67.20). Sources: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B and the paper https://arxiv.org/abs/2506.05176. These are the authors' own numbers, so they are somewhat favourable; the live MTEB leaderboard is the check.
- EmbeddingGemma-300m model card: MTEB Multilingual v2 61.15, Eng v2 69.67, Code 68.76 (768d). Licence is the Gemma Terms of Use, and the HF repo is gated (login and accept needed). That conflicts with "no account" and "Apache/MIT only", so do not use it as a default. Fine-tuned derivatives inherit the Gemma terms. https://huggingface.co/google/embeddinggemma-300m
- jina-embeddings-v3 and jina-reranker-v2-base-multilingual are cc-by-nc-4.0 (visible in fastembed's own registry). Excluded.
- snowflake-arctic-embed-m-v2.0: Apache-2.0, ONNX available, 74 languages. I could not fetch its scores from the card (they are truncated), so I give none. It is not in fastembed 0.9.1's list.
- nomic-embed-text-v1.5: Apache-2.0, 768d, 0.52 GB (0.13 GB Q). I did not verify an MTEB figure. It is older and English-centric.
- Rerankers: BAAI/bge-reranker-v2-m3 is Apache-2.0, 0.6B, built on bge-m3, and the card recommends it for multilingual use and efficiency. The card gives its BEIR/MIRACL results only as charts, with no numbers (BEIR: reranks the top 100 of bge-en-v1.5 large and of e5-mistral-7b). The scores are raw logits, so apply a sigmoid for [0,1]. https://huggingface.co/BAAI/bge-reranker-v2-m3. ms-marco-MiniLM is much smaller (80 to 120 MB) and clearly weaker on out-of-domain and multilingual text; I did not obtain numbers.
- Gap: I did not find an independent CPU throughput benchmark for these models. Only my local numbers below are measured.

Measured locally (Windows 10, i7-8700 6C/12T, onnxruntime 1.31.1, CPU, short ~10-token texts, so these are optimistic):

| Run | Result |
|---|---|
| Qwen3 fp32 embed, 32 short texts | 1.34 s (about 24 texts/s); load 74.7 s including the 2.4 GB first download |
| Qwen3 fp32 direct onnxruntime, 32 short texts | 1.14 s |
| Qwen3 int8 (Q) embed, 32 texts | 3.29 s on the first run (includes session warm-up); load 50.5 s with download of 1.12 GB |
| bge-reranker-v2-m3 fp32, 51 pairs | 3.75 s; load 100.3 s with download |
| bge-reranker-v2-m3 int8, 51 pairs | 2.16 s; load 39.8 s with download |

The int8 Q embedder cosine vs fp32 is 0.9996 to 0.9998. Real passages (about 300 tokens) will be roughly 10 to 30 times slower per text, so budget top-50 reranking at roughly 20 to 60 s per query on a similar CPU for fp32 (unmeasured; verify in the spike with realistic abstracts). That reinforces choosing int8 for the reranker.
- Disk: Qwen3 fp32 2.4 GB + reranker fp32 2.3 GB = 4.7 GB; int8 for both = about 1.7 GB.
- Recommendation: embedder fp32 (best fidelity, drop-in with fastembed id `Qwen/Qwen3-Embedding-0.6B`; the Q variant at 1.1 GB is the fallback for low disk). Reranker `BAAI/bge-reranker-v2-m3-int8` (0.57 GB). Re-check ranking agreement of int8 vs fp32 on a fixed set.

## 4. onnxruntime wheels (PyPI JSON, cp312)
- onnxruntime 1.31.1 (2026-10-26) cp312 wheels: macosx_14_0_arm64, manylinux_2_28_aarch64, manylinux_2_28_x86_64, win_amd64, win_arm64. **No macOS x86_64 wheel.**
- macOS x64 history: 1.21 and 1.22 had macosx_13_0_universal2. 1.23.0, 1.23.1 and 1.23.2 (2025-10-27) had separate macosx_13_0_x86_64 and arm64. From 1.24.1 (2026-02-05) on, arm64 only (macosx_14_0). **The last release with an Intel Mac wheel is 1.23.2** (there is no 1.24.0 in the cp312 listing).
- Note that arm64 wheels need macOS 14+ from 1.24 on. The macos-15 runner is fine.
- Consequences: (a) on Intel Macs `onnxruntime>=1.24` fails to install. fastembed's own pin (`>=1.17,!=1.20.0,!=1.24.0,!=1.24.1`) would let a resolver pick 1.24.2+ and then fail. Researchly must add `onnxruntime>=1.23,<1.24 ; sys_platform=='darwin' and platform_machine=='x86_64'` and a plain `onnxruntime>=1.23` for the other platforms. (I did not test uv's universal lock with this. Do it in the spike.) (b) The Qwen3 Q model needs onnxruntime>=1.23, so 1.23.2 satisfies it. (c) Intel Mac stays supported but frozen on 1.23.x, with no security or feature updates; in the longer run, plan to declare macOS x64 a best-effort or Docker-only platform. (d) Windows arm64 is out of scope, but a wheel exists.
- Linux wheels need glibc >= 2.28. ubuntu-24.04 and ubuntu-24.04-arm are fine.

## 5. Correctness check (verified)
1. Once, in a throwaway env with torch (`uv run --with sentence-transformers --with torch`), load the model in **float32** (`model_kwargs={"dtype": torch.float32}`). The default load is bfloat16 and makes the reference noisy: my first run gave cosines of 1.0017 against it. Encode with `normalize_embeddings=True`; for queries use `prompt_name="query"`. Docs get no prompt.
2. Save the vectors as JSON (4 docs + 1 query = 5 x 1024 floats, about 100 KB) and commit them as `refvec.json` along with the fixed sentences.
3. In CI, embed the same strings via ONNX and assert per-row cosine >= 0.99. Use the **manual prefixed** query string. My results: fastembed fp32 and direct ORT fp32 both give 1.0000 on docs and query; the int8 Q gives 0.9996 to 0.9998. The threshold of 0.99 is safe for all of these.
4. Also assert: shape (n,1024), |norm-1|<1e-3, the truncated 512 re-normalised cosine against the truncated reference >= 0.99, and the ranking of query-vs-docs equals the reference ranking.

## 6. GitHub Actions spike design
Matrix: windows-2025, ubuntu-24.04, ubuntu-24.04-arm, macos-15, macos-15-intel. All are standard GitHub-hosted labels (macos-15-intel is the last x64 mac image; check its end-of-life date on GitHub's runner-images announcements).
```yaml
jobs:
  spike:
    strategy: {fail-fast: false, matrix: {os: [windows-2025, ubuntu-24.04, ubuntu-24.04-arm, macos-15, macos-15-intel]}}
    runs-on: ${{ matrix.os }}
    env: {HF_HOME: ${{ github.workspace }}/.hf, HF_HUB_DISABLE_PROGRESS_BARS: "1"}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5   # caches uv
      - uses: actions/cache@v4
        with: {path: .hf, key: hf-qwen3-bge-v1}   # about 3 GB fp32+int8 combined; Actions cache limit is 10 GB per repo. The key is shared across OSes, so one OS saves.
      - run: uv sync --python 3.12   # deps: fastembed, numpy; on macos-15-intel the marker pins onnxruntime<1.24
      - run: uv run python spike/run.py   # prints t_cold or t_warm
      - run: uv run python spike/run.py   # second run is warm
```
`spike/run.py` should:
1. `assert importlib.util.find_spec("torch") is None` and the same for transformers.
2. Print `onnxruntime.__version__`, `platform.machine()`, and the providers.
3. Time `TextEmbedding("Qwen/Qwen3-Embedding-0.6B")` load (cold = download), embed 4 refs + query; assert shape, norms, cosine >= 0.99 against `refvec.json`, and the ranking.
4. Time `TextCrossEncoder("BAAI/bge-reranker-v2-m3-int8")`: rerank the query against about 50 passages (mix of 3 relevant and noise); assert the relevant ones come first (top-3 set) and scores are finite.
5. Write a timing JSON to `$GITHUB_STEP_SUMMARY`. Assert a loose time ceiling for warm runs (for example warm load under 60 s) to catch regressions only.
6. Make the model download resumable and retried; HF is unauthenticated here ("You are sending unauthenticated requests" warning appears), so use the cache and retry with backoff to avoid rate-limit flakes.
Also a negative job: on macos-15-intel run `uv pip install onnxruntime==1.31.1` and expect failure, so the pin is proven to be needed.

## Experiment log (commands and results, all in the scratch dir)
- `uv init --bare --python 3.12 .` then `uv add fastembed` gave `fastembed==0.9.1`, `onnxruntime==1.31.1`, `numpy==2.5.4`, `tokenizers==0.23.3`, `huggingface-hub==1.33.0`. No torch.
- `fe_spike.py Qwen/Qwen3-Embedding-0.6B-Q`: "Fetching 5 files 45 s; load embed 50.5; embed (32, 1024) 3.29 s; norms 1.0000002/0.99999994".
- `fe_spike.py Qwen/Qwen3-Embedding-0.6B`: "load embed 74.7; embed (32, 1024) 1.34 s; query top: 0".
- `rr.py BAAI/bge-reranker-v2-m3-int8`: "load rerank 39.8; rerank 51 docs 2.16 s". `rr.py BAAI/bge-reranker-v2-m3`: "load rerank 100.3; rerank 51 docs 3.75 s". Both put the relevant doc on top (score +2.9/+3.2 vs about -11).
- Reference: `uv run --no-project --python 3.12 --with sentence-transformers --with torch` (torch 2.15.0+cpu, sentence-transformers 6.2.0), 2m36s wall. The first run loaded in bf16 ("This model was loaded in torch.bfloat16 on CPU"), producing cosines of 1.0005 to 1.0017 (noisy). It was rerun in fp32.
- `cmp.py` (fastembed vs fp32 reference): Qwen3 fp32 doc cos [1. 1. 1. 1.], query with manual prefix [1.]; trunc512+renorm [1. 1. 1. 1.]. Q: doc [0.9998 0.9997 0.9996 0.9998], query [0.9997]. Without the prefix (`query_embed`): query cos 0.8132.
- `direct.py`, onnx-community export: ValueError "Required inputs (['past_key_values.0.key', ...]) are missing from input feed (['input_ids', 'attention_mask', 'position_ids'])", with 56 KV inputs. Download 61.7 s.
- `direct.py`, Qdrant export: "IN ['input_ids', 'attention_mask'] OUT ['last_hidden_state'] [['batch_size','sequence_length',1024]]", "direct doc cos [1. 1. 1. 1.] query cos [1.]", "32 texts 1.14 s".
- HF cache sizes afterwards: Qdrant 2.3 G, onnx-community 2.3 G, Qwen official 1.2 G (from the torch run). Windows warns about symlinks (non-admin, no Developer Mode); the cache still works but copies files, so budget about 2x disk on Windows if two repos are used. HF is unauthenticated and worked without issue.
- Not done: timing on realistic 300-token passages; uv universal-lock behaviour with the macOS-x64 marker; Linux/macOS runs (no hardware).

## Sources
- PyPI JSON for fastembed and onnxruntime (https://pypi.org/pypi/onnxruntime/json, https://pypi.org/pypi/fastembed/json), the installed fastembed 0.9.1 METADATA and `last_token_normalized_embedding.py`.
- https://huggingface.co/Qwen/Qwen3-Embedding-0.6B, https://huggingface.co/Qdrant/Qwen3-Embedding-0.6B-onnx, https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX, https://huggingface.co/onnx-community/bge-reranker-v2-m3-ONNX, https://huggingface.co/BAAI/bge-reranker-v2-m3, https://huggingface.co/google/embeddinggemma-300m, https://huggingface.co/Snowflake/snowflake-arctic-embed-m-v2.0 (HF API used for file sizes and licences).
- Qwen3 Embedding paper https://arxiv.org/abs/2506.05176 and blog https://qwenlm.github.io/blog/qwen3-embedding
