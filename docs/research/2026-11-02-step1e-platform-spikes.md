# Step 1e platform spikes: research synthesis (2026-11-02)

The detailed reports, each with sources and a local Windows 10 experiment log:

- [SearXNG native](2026-11-02-step1e-searxng.md)
- [Code sandboxes](2026-11-02-step1e-sandbox.md)
- [Docling without torch](2026-11-02-step1e-docling.md)
- [ONNX embedder and reranker](2026-11-02-step1e-embed.md)

All four were tried for real only on Windows x64. The other four CI targets were checked only by dependency resolution, so the CI matrix is the proof for them.

## Findings that change or sharpen the spec

| Area | Spec today | Finding | Decision for 1e |
|---|---|---|---|
| Native SearXNG (§4 table, risk 2) | Windows "made to work in the step-1 spike" | Runs on Windows with granian (WSGI) once a stub `pwd` module is added. `searx/valkeydb.py:22` imports `pwd`; upstream declines Windows support (#5411, PR #5412 closed). No PyPI or GitHub releases exist, and PyPI `searxng` is an unrelated package. | Install a pinned commit tarball (sha256-verified) into an app-private venv with hash-locked requirements. On Windows, write a `pwd.py` shim we author, leaving AGPL files unmodified. Run it under the existing supervisor. If install or start fails, web search is off and `doctor` says why. |
| PDF (§4 table, §11 item 2, risk "torch bloat") | Docling, ONNX models, no torch, "body text, tables and formulas" | `docling-slim` exists. Heron layout and RapidOCR run on onnxruntime with no torch. TableFormer and CodeFormula are torch-only (`docling-ibm-models` hard-requires torch), and there is no official ONNX export. Docling 2.14x still imports torch in three places; the workarounds are verified (upstream #3997). `docling-parse` 7.x and onnxruntime ≥1.24 have no macOS x64 wheels. | Use torch-free text, layout and OCR with the pypdfium2 backend on every OS, no TableFormer, and tables kept as flattened region text. GROBID's TEI tables are used where GROBID runs. Re-evaluate TableFormer ONNX at step 2/4. Pin `docling-slim` exactly, because the workarounds touch private behaviour. A CI test asserts torch is absent from the backend and CLI locks. |
| Embedder (§10, line 528) | Qwen3-Embedding-0.6B via fastembed if supported | fastembed 0.9.x supports `Qwen/Qwen3-Embedding-0.6B` (fp32 2.38 GB, int8 `-Q` 1.12 GB) from `Qdrant/Qwen3-Embedding-0.6B-onnx` (Apache-2.0). There is no torch in its tree. It does **not** add the Qwen3 query instruction; without it, query cosine against the reference is 0.81. int8 vs fp32 cosine is 0.9996 to 0.9998. Avoid the onnx-community export, a KV-cache LM. | fastembed with `Qwen/Qwen3-Embedding-0.6B-Q`. Researchly adds the query instruction itself, truncates to 1024 dims and normalises after truncation. A committed fp32 sentence-transformers reference must match at cosine ≥0.99. |
| Reranker (line 535) | bge-reranker-v2-m3 via onnxruntime | fastembed `BAAI/bge-reranker-v2-m3-int8` (0.57 GB) took 2.2 s for 51 short pairs; fp32 (2.27 GB) took 3.75 s. | int8. Both int8 models together are about 1.7 GB to download, against 4.7 GB for fp32. |
| onnxruntime on Intel Macs | not covered | The last macOS x86_64 wheel is 1.23.2 (2025-10-27). | Use the marker pin `onnxruntime==1.23.2; sys_platform=='darwin' and platform_machine=='x86_64'`. Intel Macs are frozen on 1.23.x; record it in the ADR. |
| Light cells (§19.4) | Pyodide in Deno, Deno permissions are the boundary | Deno 2.9.x installs from the `deno` PyPI wheel (MIT; win x64, mac x64/arm64, linux x64/arm64). Pyodide 314.0.x loads through `npm:pyodide` with `packageCacheDir`. With `--no-prompt`, scoped `--allow-read`/`--allow-write` and no net, run, ffi or env, the ctypes `system()`, `emscripten_run_script` and `fetch` escapes all return NotCapable. Warm start is about 3.5 to 4 s. Windows needs long forward-slash paths. | Build the light-cell runner now: a Python launcher plus a Deno runner, with security tests that must fail on every OS. Also escape-test the runner on each OS because its behaviour can change between Deno and Pyodide versions (Cyera, DEF CON 34). |
| Kernel sandbox (§19.4) | Windows: restricted token + ACLs + Job Object | A restricted token does not block network. An AppContainer with no capabilities, created through ctypes without admin, blocked network, writes and reads outside its folder. Ubuntu 24.04 blocks unprivileged userns for bwrap and needs root to lift, so Landlock is a first-class path. `sandbox-exec` still works on macOS 15 and 26. | Kernel build stays at step 7/10. In 1e, run per-OS probes (non-blocking CI) that record the mechanism, userns state, Landlock ABI and deny-test results for the ADR. Spec §19.4 is changed to AppContainer on Windows. |

## Licences checked

- SearXNG: AGPL-3.0. It runs as a separate, unmodified process reached over HTTP, which the FSF FAQ treats as separate programs. We download it at install time and do not vendor it.
- docling and docling-slim: MIT.
- Heron ONNX model: Apache-2.0.
- RapidOCR and its PP-OCR models: Apache-2.0.
- pypdfium2: Apache-2.0/BSD.
- fastembed: Apache-2.0.
- Qdrant Qwen3 ONNX export: Apache-2.0.
- bge-reranker-v2-m3: Apache-2.0.
- onnxruntime: MIT.
- deno wheel: MIT.
- Pyodide: MPL-2.0. It runs as a separate runtime and is fetched, not vendored.
- Excluded:
  - jina v3 and jina-reranker-v2: CC-BY-NC.
  - EmbeddingGemma: Gemma terms and a gated repo.
  - PyMuPDF: AGPL, in-process.
  - marker: GPL.
