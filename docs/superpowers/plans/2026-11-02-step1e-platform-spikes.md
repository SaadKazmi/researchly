# Step 1e — Platform Spikes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close spec §24 step 1's remaining platform risks with production-shaped first slices that run on all five CI targets:
- the local ONNX embedder and reranker;
- torch-free PDF conversion;
- Pyodide-in-Deno light cells, with security tests;
- per-OS kernel-sandbox probes;
- native SearXNG started by `researchly start`.

Each spike ends in an ADR.

**Architecture:**
- **Backend (`researchly` package).** Three small modules, each tested on all five OS by a new `platform` CI job:
  - `embeddings.py`: fastembed (Qwen3-Embedding-0.6B int8 + bge-reranker-v2-m3 int8);
  - `ingest/pdf.py`: Docling with the pypdfium2 backend, Heron ONNX layout and RapidOCR ONNX, with no torch;
  - `sandbox/light.py` + `sandbox/pyodide_runner.ts`: Deno from the `deno` wheel, Pyodide from npm.
- **CLI.** `researchly_cli/searxng.py` installs a pinned SearXNG commit into an app-private venv. Native `start` then supervises granian next to the app. A failure turns web search off, and `doctor` says why.
- **Probes and guard.**
  - A stdlib probe script records the kernel-sandbox facts per OS in a non-blocking CI job.
  - A lock test guarantees torch never enters the backend or CLI locks.
- **Out of scope.** Step 2 wires these modules into ingestion and search; step 7/10 builds the kernel sandbox.

**Tech Stack:**
- fastembed 0.9.1;
- onnxruntime (1.23.2 on macOS x86_64, latest ≥1.23 elsewhere);
- docling-slim (exact pin), transformers 4.x, pypdfium2, rapidocr;
- deno 2.9.x (PyPI wheel), Pyodide 314.0.7 (npm);
- SearXNG at a pinned commit, served by granian 2.8.x;
- Python 3.12, uv 0.12.10.

**Spec:** `docs/superpowers/specs/2026-10-28-researchly-design.md`: §4 table (lines 216–218), §4.2 supervision, §10 (lines 528–535), §11 item 2, §19.4, §24 step 1, §26 risks 2–3 and "torch bloat".

**Research (read before your task):**
- `docs/research/2026-11-02-step1e-platform-spikes.md` (synthesis and decisions)
- the matching detailed report:
  - `…-step1e-embed.md` for Task 1;
  - `…-step1e-docling.md` for Task 2;
  - `…-step1e-sandbox.md` for Tasks 3–4;
  - `…-step1e-searxng.md` for Task 5.

## Global Constraints

- **Commands.** Python only through `uv` (`uv run --directory backend …`, `uv run --directory cli …`); the system `python` is 3.8. Lint/type gates per project: `ruff check`, `ruff format --check`, `pyright` (standard) all clean.
- **No torch, ever, in `backend/uv.lock` or `cli/uv.lock`.** The same applies to torchvision, torchaudio, triton, `nvidia-*`, `onnxruntime-gpu` and `tensorflow*` (spec §26). Never use `docling-slim[models-onnxruntime]`, which pulls `onnxruntime-gpu`, or `[models-local]` / `[standard]`, which pull torch.
- **Supply chain.** `[tool.uv] exclude-newer = "7 days"` stays. Never add a package version published less than 7 days ago. Pin exactly any library whose private behaviour we patch (`docling-slim`) or whose runtime we embed (`deno`, `pyodide`, the SearXNG commit). The SearXNG download is verified by sha256 before extraction, and its Python deps install with `--require-hashes`.
- **onnxruntime on Intel Macs.** `onnxruntime==1.23.2; sys_platform == 'darwin' and platform_machine == 'x86_64'` plus `onnxruntime>=1.23; not (sys_platform == 'darwin' and platform_machine == 'x86_64')`. This is the last x86_64 macOS wheel.
- **Offline default tests.** Plain `pytest -q` in backend never downloads a model or a runtime. Anything that does is marked `platform` and is deselected by default (`addopts = "-m 'not live and not platform'"`). The CLI's real SearXNG test is marked `searxng`; it runs in the CLI CI job like the `native` pg0 tests.
- **Licences.** Only Apache-2.0, MIT, BSD, MPL-2.0 (Pyodide, fetched at runtime) or CDLA-Permissive models. SearXNG (AGPL-3.0) is downloaded from upstream at install time, run unmodified as a separate process, and talked to over HTTP only. Never import `searx`, and never vendor its source into this repo. The Windows `pwd.py` shim is our own file in the venv, not a patch to SearXNG.
- **Secrets.** No secret in argv, state.json, logs or committed files. The SearXNG secret reaches granian through the `SEARXNG_SECRET` env var. The Deno process gets a minimal env (no inherited environment variables).
- **Paths for Deno on Windows.** Always pass `Path.resolve().as_posix()`. 8.3 short names (`SAAD~1.KAZ`) make Deno deny access.
- **Windows asyncio / processes.** Native children run under the existing supervisor (`researchly_cli.supervise`). Never call `pg0.Pg0.drop()`. Never install system packages, and never use sudo outside CI.
- **Style.** Match the surrounding code: short module docstrings citing spec sections, comments only where the why is non-obvious, no speculative options or config.

---

## File Structure

| File | Task | Responsibility |
|---|---|---|
| `backend/pyproject.toml`, `backend/uv.lock`, `cli/uv.lock` | 1, 2, 3 | new runtime deps; `platform` marker |
| `backend/src/researchly/embeddings.py` | 1 | `Embedder`, `Reranker` (fastembed; query instruction) |
| `backend/scripts/embedding_reference.py` | 1 | one-off PEP 723 script (torch, outside any lock) that writes the reference vectors |
| `backend/tests/test_lock.py` | 1 | torch-free lock guard |
| `backend/tests/platform/conftest.py` | 1 | `models_dir` fixture, `report()` timing helper |
| `backend/tests/platform/test_embeddings.py`, `backend/tests/platform/fixtures/embeddings_ref.json` | 1 | reference-match + rerank tests |
| `.github/workflows/ci.yml` | 1, 4, 5 | `platform` job (5 OS); `sandbox-probes` job; CLI job unchanged except time |
| `.gitignore` | 1 | `backend/.models/` |
| `backend/src/researchly/ingest/__init__.py`, `ingest/pdf.py` | 2 | `convert_pdf()` (Docling, no torch) |
| `backend/tests/platform/test_pdf.py`, `backend/tests/platform/fixtures/table-page.pdf` + `fixtures/README.md` | 2 | conversion + OCR + torch-absent tests |
| `backend/src/researchly/sandbox/__init__.py`, `sandbox/light.py`, `sandbox/pyodide_runner.ts` | 3 | light-cell runner |
| `backend/tests/platform/test_light_cells.py` | 3 | positive + escape tests |
| `scripts/sandbox_probe.py`, `scripts/tests/test_sandbox_probe.py` | 4 | per-OS kernel-sandbox facts as JSON |
| `cli/src/researchly_cli/searxng.py`, `cli/src/researchly_cli/searxng-requirements.txt` | 5 | install, settings, command, status |
| `cli/src/researchly_cli/native.py`, `state.py`, `main.py` | 5 | supervise granian; state fields; `doctor` line |
| `cli/tests/test_searxng.py` | 5 | unit tests + real install/run test (`searxng` marker) |
| `docs/adr/0004…0007`, spec, AGENTS.md, progress | 6 (controller) | decisions with CI numbers |

---

### Task 1: Local embedder and reranker, torch-free lock guard, `platform` CI job

**Files:**
- Modify: `backend/pyproject.toml` (deps, marker, addopts), `backend/uv.lock`, `cli/uv.lock` (`uv lock` in both projects), `.gitignore`, `.github/workflows/ci.yml`
- Create: `backend/src/researchly/embeddings.py`, `backend/scripts/embedding_reference.py`, `backend/tests/test_lock.py`, `backend/tests/platform/__init__.py`, `backend/tests/platform/conftest.py`, `backend/tests/platform/test_embeddings.py`, `backend/tests/platform/fixtures/embeddings_ref.json`

**Interfaces:**
- Produces:
  - `researchly.embeddings.Embedder(cache_dir: Path)` with `.documents(texts: Sequence[str]) -> np.ndarray` (shape `(n, 1024)`, L2-normalised) and `.query(text: str) -> np.ndarray` (shape `(1024,)`);
  - `researchly.embeddings.Reranker(cache_dir: Path)` with `.scores(query: str, passages: Sequence[str]) -> list[float]` (raw logits, higher = more relevant, same order as `passages`);
  - constants `EMBED_MODEL`, `RERANK_MODEL`, `QUERY_INSTRUCTION`;
  - test fixture `models_dir` (session) and helper `report(label: str, seconds: float)` in `backend/tests/platform/conftest.py`.

- [ ] **Step 1: Dependencies and marker.**
  - In `backend/pyproject.toml` `dependencies`, add:
    ```toml
      "fastembed==0.9.1",  # ONNX embedder + reranker on CPU, no torch (ADR 0007)
      "onnxruntime==1.23.2; sys_platform == 'darwin' and platform_machine == 'x86_64'",  # last Intel-Mac wheel
      "onnxruntime>=1.23; not (sys_platform == 'darwin' and platform_machine == 'x86_64')",
    ```
  - Change pytest config:
    ```toml
    addopts = "-m 'not live and not platform'"
    markers = [
      "db: needs Postgres 18 + pgvector",
      "live: calls a real model; opt-in, never in CI",
      "platform: downloads real models or runtimes; runs in the CI platform job (all five OS)",
    ]
    ```
  - Run `uv lock` in `backend/` then in `cli/`, which depends on backend by path. Check both locks have `onnxruntime` 1.23.2 under the darwin/x86_64 marker and no forbidden package. Run `uv sync` in both.
  - Add `backend/.models/` to `.gitignore`.

- [ ] **Step 2: Write the failing lock guard** — `backend/tests/test_lock.py`:
  ```python
  """Spec §26: torch/CUDA must never enter the native locks (Docling and fastembed run on onnxruntime)."""

  import re
  import tomllib
  from pathlib import Path

  import pytest

  ROOT = Path(__file__).resolve().parents[2]
  FORBIDDEN = re.compile(r"^(torch|torchvision|torchaudio|triton|nvidia-.+|onnxruntime-gpu|tensorflow.*)$")


  def forbidden(lock_text: str) -> list[str]:
      return sorted({p["name"] for p in tomllib.loads(lock_text)["package"] if FORBIDDEN.match(p["name"])})


  def test_the_guard_catches_torch() -> None:
      lock = '[[package]]\nname = "torch"\n[[package]]\nname = "nvidia-cudnn-cu12"\n[[package]]\nname = "numpy"\n'
      assert forbidden(lock) == ["nvidia-cudnn-cu12", "torch"]


  @pytest.mark.parametrize("lock", ["backend/uv.lock", "cli/uv.lock"])
  def test_native_locks_have_no_torch(lock: str) -> None:
      assert forbidden((ROOT / lock).read_text("utf-8")) == []
  ```
  Run `uv run --directory backend pytest tests/test_lock.py -q`. Expected: it errors until the file exists, then passes. The guard test proves it bites.

- [ ] **Step 3: Reference vectors (one-off, torch outside every lock).**
  - Create `backend/scripts/embedding_reference.py`, a PEP 723 script:
    - header: `# /// script` / `requires-python = "==3.12.*"` / `dependencies = ["sentence-transformers==<latest ≥7 days old>", "torch==<same rule>"]` / `# ///`.
    - It loads `SentenceTransformer("Qwen/Qwen3-Embedding-0.6B", model_kwargs={"torch_dtype": torch.float32})`. **fp32 is required**: the default bf16 load makes the reference noisy (cosines > 1.001 seen in research).
    - It encodes four fixed documents with `normalize_embeddings=True` and one query with `prompt_name="query"`.
    - It writes `{"model", "documents": [...], "query": "...", "document_vectors": [[...]], "query_vector": [...]}` to `backend/tests/platform/fixtures/embeddings_ref.json`, with floats rounded to 6 decimals.
  - Documents (use verbatim):
    1. "Self-attention lets every token in a sequence weigh every other token when computing its representation."
    2. "Photosynthesis converts light energy into chemical energy stored in glucose."
    3. "The transformer architecture replaced recurrence with stacked attention and feed-forward layers."
    4. "Paris is the capital and most populous city of France."
  - Query: "how does attention work in transformers?"
  - Run it once: `uv run --no-project --python 3.12 backend/scripts/embedding_reference.py`. It downloads ~1.2 GB into the default HF cache. Commit the JSON (~100 KB).
  - The script's docstring says when to re-run it: only when the model changes.

- [ ] **Step 4: Platform test scaffolding** — `backend/tests/platform/conftest.py`:
  ```python
  """Platform tests download real models and runtimes; CI runs them on all five OS (`pytest -m platform`)."""

  import os
  from pathlib import Path

  import pytest


  @pytest.fixture(scope="session")
  def models_dir() -> Path:
      # CI caches this dir between runs; locally it is the gitignored backend/.models
      return Path(os.environ.get("RESEARCHLY_TEST_MODELS_DIR", Path(__file__).resolve().parents[2] / ".models"))


  def report(label: str, seconds: float) -> None:
      """Timings for the ADRs: a row in the GitHub job summary, or stdout locally."""
      line = f"| {label} | {seconds:.2f} s |"
      if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
          with open(summary, "a", encoding="utf-8") as f:
              f.write(line + "\n")
      print(line)
  ```
  Also add an empty `backend/tests/platform/__init__.py`.

- [ ] **Step 5: Write the failing platform tests** — `backend/tests/platform/test_embeddings.py`:
  - `pytestmark = pytest.mark.platform`.
  - Session fixtures build `Embedder(models_dir / "fastembed")` and `Reranker(models_dir / "fastembed")`, each timed with `report("embedder load (cold if first run)", …)`.
  - Load `REF` from `fixtures/embeddings_ref.json`.
  - Tests:
    - `test_documents_match_the_fp32_reference`: shape `(4, 1024)`; norms within 1e-3 of 1; per-row cosine against `document_vectors` ≥ 0.99. Time `embedder.documents` → `report`.
    - `test_query_uses_the_instruction_and_matches_the_reference`: cosine ≥ 0.99 against `query_vector`, and the ranking of documents by dot product equals the reference ranking (doc 3 or 1 first, Paris last). Also assert `embed_without_instruction` scores lower cosine against the reference: compute it via `embedder.documents([REF["query"]])[0]`, which proves the prefix matters (research: 0.81).
    - `test_reranker_puts_the_relevant_passages_first`:
      - 3 relevant passages about attention/transformers and 47 off-topic ones. Each is ~120–200 words of **original** text: build them from a few hand-written paragraphs, varied per index by topic words. Never copy third-party text.
      - Assert the top-3 indices by score are exactly the relevant ones and all scores are finite.
      - `report("rerank 50 passages", …)`.
    - `test_no_torch_was_imported`: after the fixtures ran, `"torch" not in sys.modules` and `importlib.util.find_spec("torch") is None`.
  - Run `uv run --directory backend pytest -m platform tests/platform/test_embeddings.py -q`. Expected: ImportError (`researchly.embeddings` missing).

- [ ] **Step 6: Implement** `backend/src/researchly/embeddings.py`:
  ```python
  """Local embeddings and reranking on CPU through onnxruntime (spec §10, ADR 0007): no torch, no key, no account."""

  from collections.abc import Sequence
  from pathlib import Path

  import numpy as np
  from fastembed import TextEmbedding
  from fastembed.rerank.cross_encoder import TextCrossEncoder

  EMBED_MODEL = "Qwen/Qwen3-Embedding-0.6B-Q"  # int8 ONNX: cosine ≥0.9996 vs fp32 at half the download
  RERANK_MODEL = "BAAI/bge-reranker-v2-m3-int8"
  # Qwen3-Embedding expects this prefix on queries only; fastembed does not add it (query cosine 0.81 without it)
  QUERY_INSTRUCTION = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"


  class Embedder:
      """1024-dim, L2-normalised vectors: the model's full size, stored as halfvec(1024)."""

      def __init__(self, cache_dir: Path) -> None:
          self._model = TextEmbedding(EMBED_MODEL, cache_dir=str(cache_dir))

      def documents(self, texts: Sequence[str]) -> np.ndarray:
          return np.array(list(self._model.embed(list(texts))))

      def query(self, text: str) -> np.ndarray:
          return self.documents([QUERY_INSTRUCTION + text])[0]


  class Reranker:
      def __init__(self, cache_dir: Path) -> None:
          self._model = TextCrossEncoder(RERANK_MODEL, cache_dir=str(cache_dir))

      def scores(self, query: str, passages: Sequence[str]) -> list[float]:
          return [float(s) for s in self._model.rerank(query, list(passages))]
  ```
  Model downloads go through huggingface_hub, which spec §10 sanctions, not PoliteClient. Step 2 passes `data_dir / "models"`.

- [ ] **Step 7: Run the tests.**
  - `uv run --directory backend pytest -m platform tests/platform/test_embeddings.py -q -s`. Expected: PASS. The first run downloads ~1.7 GB into `backend/.models`.
  - Then run `uv run --directory backend pytest -q`. Expected: the platform tests are deselected; all others pass, including `test_lock.py`.
  - Run `ruff check`, `ruff format --check` and `pyright` in backend and cli, plus `uv run --directory cli pytest -q` (the CLI lock changed).

- [ ] **Step 8: CI `platform` job.** Add after the `backend` job in `.github/workflows/ci.yml`. Pin `actions/cache` to the full commit SHA of its latest v4/v5 release (`gh api repos/actions/cache/releases/latest`), with the version in a trailing comment like the other actions:
  ```yaml
    platform:
      name: platform (${{ matrix.os }})
      runs-on: ${{ matrix.os }}
      continue-on-error: ${{ matrix.os == 'macos-15-intel' }} # Intel macOS runner images are being retired
      timeout-minutes: 45
      strategy:
        fail-fast: false
        matrix:
          os: [ubuntu-24.04, ubuntu-24.04-arm, windows-2025, macos-15, macos-15-intel]
      defaults:
        run:
          working-directory: backend
      env:
        RESEARCHLY_TEST_MODELS_DIR: ${{ github.workspace }}/backend/.models
        HF_HUB_DISABLE_PROGRESS_BARS: "1"
      steps:
        - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
          with:
            persist-credentials: false
        - uses: astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2
          with:
            version: "0.12.10"
            enable-cache: true
            cache-dependency-glob: backend/uv.lock
        - uses: actions/cache@<sha> # <version>
          with:
            path: backend/.models
            # model files are OS-independent, but HF caches use symlinks that Windows restores differently
            key: models-${{ runner.os == 'Windows' && 'win' || 'posix' }}-${{ hashFiles('backend/src/researchly/embeddings.py', 'backend/src/researchly/ingest/pdf.py') }}
        - run: uv sync --locked
        - name: Platform tests (real models and runtimes)
          run: |
            echo "### ${{ matrix.os }}" >> "$GITHUB_STEP_SUMMARY"
            uv run pytest -q -rA -m platform
          shell: bash
  ```

- [ ] **Step 9: Commit** (as soon as the tests pass):
  ```bash
  git add backend/pyproject.toml backend/uv.lock cli/uv.lock .gitignore .github/workflows/ci.yml backend/src/researchly/embeddings.py backend/scripts/embedding_reference.py backend/tests/test_lock.py backend/tests/platform
  git commit -m "feat: local ONNX embedder and reranker, torch-free lock guard, platform CI job"
  ```

---

### Task 2: Torch-free PDF conversion (Docling, pypdfium2, Heron ONNX, RapidOCR)

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock`, `cli/uv.lock`
- Create: `backend/src/researchly/ingest/__init__.py` (empty), `backend/src/researchly/ingest/pdf.py`, `backend/tests/platform/test_pdf.py`, `backend/tests/platform/fixtures/table-page.pdf`, `backend/tests/platform/fixtures/README.md`

**Interfaces:**
- Consumes: `models_dir`, `report` from `backend/tests/platform/conftest.py` (Task 1).
- Produces: `researchly.ingest.pdf.convert_pdf(path: Path, *, ocr: bool = False) -> ConvertedPdf`, where `ConvertedPdf` is a frozen dataclass with `markdown: str`, `pages: int` and `tables: int` (count of table regions). A module-level cached `DocumentConverter` per `ocr` value.

- [ ] **Step 1: Dependencies.** Add to backend `dependencies`:
  ```toml
    # PDF text, layout and OCR on onnxruntime; pypdfium2 because docling-parse 7.x has no Intel-Mac wheel (ADR 0005)
    "docling-slim[format-pdf-pypdfium2,convert-core,feat-ocr-rapidocr-onnx]==<X>",
    "transformers>=4.50,<5",  # Heron's image processor; 5.x refuses it without torchvision
  ```
  - `<X>` is the newest `docling-slim` uv resolves under `exclude-newer = "7 days"`: run `uv lock` with `docling-slim[...]` unpinned, read the version, then pin it exactly.
  - Run `uv lock` in backend and cli. Confirm `huggingface-hub` resolved below 1.0, which transformers 4.x requires and fastembed accepts. Confirm `test_lock.py` still passes, `onnxruntime-gpu` is absent and `docling-parse` is absent.

- [ ] **Step 2: Test fixture PDF.**
  - Create `fixtures/table-page.pdf`: page 5 of arXiv 2408.09869v5, "Docling Technical Report" (CC BY 4.0), which contains "Table 1".
    - Fetch the PDF once (`https://arxiv.org/pdf/2408.09869v5`) into the scratch dir.
    - Keep only that page with pypdfium2: `PdfDocument.new()` then `import_pages(src, [4])`. Save it.
    - Check the size is < 500 KB. If the page index differs, find the page whose text contains "Table 1:".
  - `fixtures/README.md` records the source URL, page, licence (CC BY 4.0, attribution "Auer et al., Docling Technical Report, arXiv:2408.09869") and why it is there.

- [ ] **Step 3: Write the failing tests** — `backend/tests/platform/test_pdf.py`, `pytestmark = pytest.mark.platform`. Set `HF_HOME` to `models_dir / "hf"` via a session-scoped autouse fixture (`monkeypatch` is function-scoped, so set and restore `os.environ` directly) so CI caches Docling's models.
  - `test_text_layout_and_table_region`:
    - `convert_pdf(FIXTURE)` → `pages == 1`, `"Table 1" in markdown`, `tables >= 1`, and a known sentence fragment from that page is present (pick one while making the fixture).
    - Time cold and warm (second call) with `report`.
  - `test_ocr_reads_an_image_only_page`:
    - Render the fixture page to an image (pypdfium2 `render(scale=2).to_pil()`) and save it as a one-page image-only PDF with PIL (`image.save(tmp/"scan.pdf")`).
    - `convert_pdf(scan, ocr=True)` → `"Table 1"` (or the same known fragment, case-insensitive) appears.
    - `report` the time.
  - `test_no_torch_was_imported`: same asserts as Task 1.
  - Run: ImportError expected.

- [ ] **Step 4: Implement** `backend/src/researchly/ingest/pdf.py`. Each workaround was verified on Windows in research (`docs/research/2026-11-02-step1e-docling.md` §1.3) and is tracked upstream in docling#3997. Re-verify them against the pinned version, keep them in this one place, and comment each one:
  ```python
  """PDF → Markdown with Docling, torch-free (spec §11, ADR 0005).

  Text and reading order come from pypdfium2, layout from the Heron ONNX model, and OCR from RapidOCR on onnxruntime.
  Docling's table-structure and formula models are torch-only, so table regions come out as flattened text; GROBID
  adds structured tables where it runs.
  """

  import importlib.machinery
  import sys
  import types
  from dataclasses import dataclass
  from functools import cache
  from pathlib import Path
  from unittest.mock import MagicMock

  from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
  from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
  from docling.datamodel.base_models import InputFormat
  from docling.datamodel.object_detection_engine_options import OnnxRuntimeObjectDetectionEngineOptions
  from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions, PdfPipelineOptions, RapidOcrOptions
  from docling.document_converter import DocumentConverter, PdfFormatOption


  @dataclass(frozen=True)
  class ConvertedPdf:
      markdown: str
      pages: int
      tables: int


  def _without_torch_imports() -> None:
      """Docling <pinned> imports torch where torch-free runs never need it (docling#3997)."""
      # 1. The table-structure plugin modules import torch at module level even with tables off: build the cached
      #    factory once with stand-in modules, then remove them (installed earlier, they confuse transformers).
      names = ("torch", "torchvision", "torchvision.transforms")
      for name in names:
          stub = types.ModuleType(name)
          stub.__spec__ = importlib.machinery.ModuleSpec(name, None)
          stub.__getattr__ = lambda _attr: MagicMock()  # type: ignore[method-assign]
          sys.modules[name] = stub
      try:
          from docling.models.factories import get_table_structure_factory

          get_table_structure_factory()
      finally:
          for name in names:
              del sys.modules[name]
      # 2. decide_device() imports torch unconditionally; this CPU-only path has nothing to decide.
      import docling.models.stages.ocr.rapid_ocr_model as rapid_ocr
      import docling.utils.accelerator_utils as accelerator

      accelerator.decide_device = rapid_ocr.decide_device = lambda *_a, **_k: "cpu"  # type: ignore[assignment]


  @cache
  def _converter(ocr: bool) -> DocumentConverter:
      _without_torch_imports()
      layout = LayoutObjectDetectionOptions.from_preset(
          "layout_heron_default",
          # an explicit provider list skips the torch-based device probe (3. of docling#3997)
          engine_options=OnnxRuntimeObjectDetectionEngineOptions(providers=["CPUExecutionProvider"]),
      )
      options = PdfPipelineOptions(
          do_table_structure=False,
          do_ocr=ocr,
          ocr_options=RapidOcrOptions(backend="onnxruntime", force_full_page_ocr=True),
          layout_options=layout,
          accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
      )
      return DocumentConverter(
          format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options, backend=PyPdfiumDocumentBackend)}
      )


  def convert_pdf(path: Path, *, ocr: bool = False) -> ConvertedPdf:
      doc = _converter(ocr).convert(path).document
      return ConvertedPdf(markdown=doc.export_to_markdown(), pages=doc.num_pages(), tables=len(doc.tables))
  ```
  Adjust these to what the pinned version actually needs, verified by running the tests:
  - In research, a single `CPUExecutionProvider` entry may still reach the torch probe. If it does, use the duplicated `["CPUExecutionProvider", "CPUExecutionProvider"]` that research verified, and say so in the comment.
  - Check whether workaround 2 is needed when `ocr=False`.

  Keep the workarounds minimal, and delete any the pinned version does not need.

- [ ] **Step 5: Run** the platform tests (PASS), plain `pytest -q`, and the ruff/pyright gates in backend and cli. If pyright cannot see the types of docling's dynamic attributes, use the narrowest `# type: ignore[...]` with a reason.

- [ ] **Step 6: Commit**: `git commit -m "feat: torch-free PDF conversion with Docling on onnxruntime"`

---

### Task 3: Light code cells — Pyodide inside Deno

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock`, `cli/uv.lock`
- Create: `backend/src/researchly/sandbox/__init__.py` (empty), `backend/src/researchly/sandbox/light.py`, `backend/src/researchly/sandbox/pyodide_runner.ts`, `backend/tests/platform/test_light_cells.py`

**Interfaces:**
- Consumes: `report` from the platform conftest.
- Produces:
  - `researchly.sandbox.light.warm_up(cache: Path) -> None`;
  - `researchly.sandbox.light.run_cell(code: str, workdir: Path, cache: Path, *, timeout: float = 60.0) -> CellResult`;
  - `CellResult`, a frozen dataclass: `ok: bool`, `stdout: str`, `error: str | None`;
  - constants `PYODIDE_VERSION = "314.0.7"`, `PACKAGES = ("numpy", "pandas", "matplotlib")`.

- [ ] **Step 1: Dependency.** Add `"deno==2.9.8",  # Deno runtime as a wheel: light code cells (ADR 0006)` (MIT; wheels for all five targets) and run `uv lock` in backend and cli. It must be ≥7 days old; 2.9.8 was published 2026-10-21.

- [ ] **Step 2: Write the failing tests** — `backend/tests/platform/test_light_cells.py`, `pytestmark = pytest.mark.platform`.
  - Session fixture `cache = tmp_path_factory.mktemp("sandbox-cache")`; call `warm_up(cache)` once, timed with `report`.
  - Per test: a fresh `workdir`, and a separate `outside` tmp dir holding `secret.txt` ("decoy-secret").
  - Every escape test also asserts that the sentinel/decoy effect did **not** happen on the host. Each pairs with a positive control that proves the harness can observe success.

  | Test | Code run in the cell | Assert |
  |---|---|---|
  | `test_numpy_pandas_matplotlib_and_workdir_files` | `import numpy, pandas, matplotlib; matplotlib.use("agg")`; plot; `savefig("plot.png")`; `print(pandas.DataFrame({"a":[1,2]}).a.sum())` | `ok`, `"3" in stdout`, `(workdir/"plot.png").stat().st_size > 0`; report warm cell time |
  | `test_network_is_denied` | `import js; await js.fetch("https://example.com")` | not ok, `"NotCapable"` in error |
  | `test_ctypes_system_cannot_spawn` | `import ctypes; ctypes.CDLL(None).system(b"echo pwned > <outside>/pwn.txt")` | `not (outside/"pwn.txt").exists()` |
  | `test_deno_command_is_denied` | `import js; js.Deno.Command.new("deno", ["--version"]).outputSync()` | not ok |
  | `test_host_js_cannot_read_outside` (+ control) | `ctypes.CDLL(None).emscripten_run_script_string(b"Deno.readTextFileSync('<outside>/secret.txt')")` | not ok or `"decoy-secret" not in stdout`; error mentions NotCapable. **Control:** the same call on `<workdir>/mine.txt`, pre-written by the test, returns its content |
  | `test_host_js_cannot_write_outside` | `…emscripten_run_script(b"Deno.writeTextFileSync('<outside>/w.txt','x')")` | `not (outside/"w.txt").exists()` |
  | `test_env_and_ffi_are_denied` | `Deno.env.get('PATH')` and `Deno.dlopen(...)` through `emscripten_run_script_string` | NotCapable for both |
  | `test_symlink_out_of_the_workdir_is_denied` (skip on win32: symlinks need privilege there) | `import os; os.symlink("<outside>/secret.txt", "/work/l"); print(open("/work/l").read())` | `"decoy-secret" not in stdout` |
  | `test_runaway_cell_is_killed` | `while True: pass` with `timeout=5` | not ok, `"timed out"` in error, returns in < 15 s |

  Embed host paths into cell code as `Path.resolve().as_posix()`. Run the tests: ImportError expected.

- [ ] **Step 3: Implement the runner** `backend/src/researchly/sandbox/pyodide_runner.ts`:
  ```ts
  // Light-cell runner (spec §19.4, ADR 0006). Deno's permission flags, set by light.py, are the sandbox.
  //   deno run … pyodide_runner.ts --warm PKG_DIR    fill the package cache (network allowed to the Pyodide CDN)
  //   deno run … pyodide_runner.ts PKG_DIR WORK_DIR  run Python from stdin with WORK_DIR mounted at /work;
  //                                                  the last stdout line is {"ok", "stdout", "error"}
  import { loadPyodide } from "npm:pyodide@314.0.7";

  const warm = Deno.args[0] === "--warm";
  const [pkgDir, workDir] = warm ? Deno.args.slice(1) : Deno.args;
  const out: string[] = [];
  const py = await loadPyodide({
    packageCacheDir: pkgDir,
    stdout: (s) => out.push(s),
    stderr: (s) => out.push(s),
  });
  const quiet = { messageCallback: () => {} };
  if (warm) {
    await py.loadPackage(["numpy", "pandas", "matplotlib"], quiet);
    Deno.exit(0);
  }
  py.mountNodeFS("/work", workDir);
  py.FS.chdir("/work");
  const code = await new Response(Deno.stdin.readable).text();
  let error: string | null = null;
  try {
    await py.loadPackagesFromImports(code, quiet);
    await py.runPythonAsync(code);
  } catch (e) {
    error = String(e);
  }
  console.log(JSON.stringify({ ok: error === null, stdout: out.join("\n"), error }));
  ```
  - If `mountNodeFS` is unavailable in this Pyodide build, use `py.FS.mount(py.FS.filesystems.NODEFS, { root: workDir }, "/work")` after `py.FS.mkdir("/work")`.
  - If a NotCapable error kills the Pyodide instance before the `catch` (as `system()` did in research), the process exits non-zero with no JSON. `light.py` maps that to `ok=False` with stderr as the error.

- [ ] **Step 4: Implement** `backend/src/researchly/sandbox/light.py`:
  ```python
  """Light code cells: Pyodide inside Deno, with Deno's permissions as the boundary (spec §19.4, ADR 0006).

  No network, no subprocesses, no FFI, no environment; reads only Pyodide itself, the package cache and the cell's
  workdir; writes only the workdir. Pyodide-level escapes (ctypes, emscripten_run_script) reach Deno's JS, where
  these flags still apply, so the platform tests attempt each escape on every OS.
  """

  import json
  import os
  import subprocess
  import sys
  import sysconfig
  from dataclasses import dataclass
  from pathlib import Path

  PYODIDE_VERSION = "314.0.7"  # also in pyodide_runner.ts
  PACKAGES = ("numpy", "pandas", "matplotlib")  # also in pyodide_runner.ts
  RUNNER = Path(__file__).with_name("pyodide_runner.ts")


  @dataclass(frozen=True)
  class CellResult:
      ok: bool
      stdout: str
      error: str | None


  def _deno() -> str:
      return str(Path(sysconfig.get_path("scripts")) / ("deno.exe" if sys.platform == "win32" else "deno"))


  def _p(path: Path) -> str:
      return path.resolve().as_posix()  # Deno denies 8.3 short paths on Windows


  def _env(cache: Path) -> dict[str, str]:
      env = {"DENO_DIR": _p(cache / "deno"), "DENO_NO_UPDATE_CHECK": "1", "NO_COLOR": "1"}
      if sys.platform == "win32":
          env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]  # Windows DLL loading needs it
      return env


  def _pyodide_dir(cache: Path) -> Path:
      return cache / "deno" / "npm" / "registry.npmjs.org" / "pyodide" / PYODIDE_VERSION


  def warm_up(cache: Path) -> None:
      """Download Pyodide and the default packages once; network only to the Pyodide CDN."""
      pkgs = cache / "pyodide"
      pkgs.mkdir(parents=True, exist_ok=True)
      subprocess.run(  # noqa: S603 - fixed argv
          [_deno(), "run", "--no-prompt", "--allow-net=cdn.jsdelivr.net",
           f"--allow-read={_p(cache)}", f"--allow-write={_p(pkgs)}", str(RUNNER), "--warm", _p(pkgs)],
          env=_env(cache), check=True, capture_output=True, timeout=600,
      )


  def run_cell(code: str, workdir: Path, cache: Path, *, timeout: float = 60.0) -> CellResult:
      read = ",".join(_p(p) for p in (_pyodide_dir(cache), cache / "pyodide", workdir))
      args = [_deno(), "run", "--no-prompt", "--cached-only", f"--allow-read={read}",
              f"--allow-write={_p(workdir)}", str(RUNNER), _p(cache / "pyodide"), _p(workdir)]
      try:
          proc = subprocess.run(  # noqa: S603 - fixed argv
              args, input=code, capture_output=True, text=True, encoding="utf-8", env=_env(cache), timeout=timeout
          )
      except subprocess.TimeoutExpired:
          return CellResult(ok=False, stdout="", error=f"timed out after {timeout:g} s")
      lines = proc.stdout.strip().splitlines()
      try:
          result = json.loads(lines[-1])
      except (IndexError, ValueError):
          return CellResult(ok=False, stdout=proc.stdout, error=proc.stderr.strip() or f"exit code {proc.returncode}")
      return CellResult(ok=bool(result["ok"]), stdout=result["stdout"], error=result["error"])
  ```
  The warm-up's `--allow-read` must cover Deno's npm cache and the package dir; widen it only to what the run proves it needs. Format the argv lists with `ruff format`.

  Then make sure the runner ships: `pyodide_runner.ts` is in the wheel (hatch includes non-Python files under `src/researchly` by default; verify with `uv build --wheel` and a zip listing, then delete `dist/`).

- [ ] **Step 5: Run** the platform tests and fix until green.
  - Any escape test that **passes the escape** is a security finding. Do not weaken the test: stop and report it as BLOCKED with the output.
  - Then run plain `pytest -q` and the ruff/pyright gates in backend and cli.

- [ ] **Step 6: Commit**: `git commit -m "feat: light code cells in Pyodide inside Deno with escape tests"`

---

### Task 4: Kernel-sandbox feasibility probes (non-blocking CI)

**Files:**
- Create: `scripts/sandbox_probe.py`, `scripts/tests/test_sandbox_probe.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `python scripts/sandbox_probe.py`, which prints one JSON object: `{"os", "arch", "mechanism", "checks": {...}, "deny_tests": {"write_outside", "read_decoy", "network", "spawn"}: "denied" | "allowed" | "skipped", "notes": [...]}`. The script uses the stdlib only and needs no third-party imports.

- [ ] **Step 1: Write the failing test** `scripts/tests/test_sandbox_probe.py`. It runs the script with `subprocess.run([sys.executable, "scripts/sandbox_probe.py"], ...)` from the repo root, parses stdout as JSON, and asserts:
  - the keys above exist;
  - `os == platform.system()`;
  - every `deny_tests` value is one of the three strings.

  It does **not** assert "denied": these are probes, and the ADR records the values. Run: `uv run --no-project --python 3.12 --with pytest==9.1.1 pytest scripts/tests/test_sandbox_probe.py -q` → fails (no script).

- [ ] **Step 2: Implement** `scripts/sandbox_probe.py`. It has a docstring stating its purpose (spec §19.4 / §26 risk 3, ADR 0006) and these per-OS functions:
  - **Every deny test** runs a child `python -c` that tries one operation and exits 0 on success. The harness sets up three things:
    - a temp `work` dir (allowed);
    - a temp `outside/decoy.txt`;
    - a loopback TCP listener on `127.0.0.1:<free port>`, so the network test is hermetic.

    A **positive control** runs the same child unsandboxed and must succeed. A failed control is reported as `"skipped"` with a note.
  - **Linux:**
    - Record:
      - `uname -r`;
      - `/sys/kernel/security/lsm`;
      - `/proc/sys/kernel/apparmor_restrict_unprivileged_userns` (if present);
      - `/proc/sys/kernel/unprivileged_userns_clone` (if present).
    - Landlock ABI via `ctypes.CDLL(None, use_errno=True).syscall(444, None, 0, 1)` (`LANDLOCK_CREATE_RULESET_VERSION`; 444 on x86_64 and aarch64). A negative result records the errno name.
    - `bwrap --version` if on PATH, then `bwrap --ro-bind / / --dev /dev --unshare-all --die-with-parent true`, recording whether it worked and its stderr.
    - If the Landlock ABI is ≥ 1, apply a ruleset in a child process and run the four deny tests under it:
      - `prctl(PR_SET_NO_NEW_PRIVS)` first;
      - handled FS access = all FS rights for the ABI;
      - a read-only beneath rule for `/`, a full-access beneath rule for `work`, and no rule for `outside`. So, to see `read_decoy` denied, grant read on `/usr`, `/lib*`, `/etc`, the Python prefix and `work` only, not `/`;
      - handled net = connect TCP when ABI ≥ 4.

      The child is `python -c` that applies the ruleset via ctypes and then `os.execv`s the test. The mechanism is `landlock`.
  - **macOS:**
    - Record `sw_vers -productVersion`.
    - Run each deny test as `sandbox-exec -p PROFILE python -c …` with:
      ```
      (version 1)(allow default)(deny network*)(deny file-write* (subpath "<outside>"))(deny file-read* (subpath "<outside>"))(deny process-exec (literal "/bin/sh"))
      ```
      `(allow default)` keeps the probe small. The kernel step will invert it.
    - Mechanism: `seatbelt`.
  - **Windows:**
    - Port the research AppContainer probe (the ctypes code in `docs/research/2026-11-02-step1e-sandbox.md` §5): `CreateAppContainerProfile` / `DeriveAppContainerSidFromAppContainerName`, `SECURITY_CAPABILITIES` with zero capabilities, `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x20009`, `CreateProcessW` with `EXTENDED_STARTUPINFO_PRESENT`.
    - The child needs read+execute on the Python install: `icacls <base_prefix> /grant *S-1-15-2-1:(OI)(CI)(RX)` (ALL APPLICATION PACKAGES). If that fails (not the owner), record the failure and skip.
    - The child's exit code is the result. Always `DeleteAppContainerProfile` in a `finally`.
    - Also record `ctypes.windll.shell32.IsUserAnAdmin()`. CI runners are admin, which the ADR must note.
    - Mechanism: `appcontainer`.
  - Print the JSON with `indent=2`. Keep the whole script under ~250 lines. Each section is plain functions, with no classes beyond the ctypes structures.

- [ ] **Step 3: Run** the test locally on Windows (PASS). Also run the script and keep its output for the ADR. Expected on this box: AppContainer denies all four, as in research.

- [ ] **Step 4: CI job**, non-blocking:
  ```yaml
    sandbox-probes:
      name: sandbox probes (${{ matrix.os }})
      runs-on: ${{ matrix.os }}
      continue-on-error: true # facts for ADR 0006, not a gate; the kernel sandbox is built at step 7/10
      timeout-minutes: 10
      strategy:
        fail-fast: false
        matrix:
          os: [ubuntu-24.04, ubuntu-24.04-arm, windows-2025, macos-15, macos-15-intel]
      steps:
        - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
          with:
            persist-credentials: false
        - uses: astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2
          with:
            version: "0.12.10"
        - if: runner.os == 'Linux'
          run: sudo apt-get install -y bubblewrap # CI only: to see whether userns lets bwrap run here
        - name: Probe
          shell: bash
          run: |
            uv run --no-project --python 3.12 scripts/sandbox_probe.py | tee probe.json
            { echo "### ${{ matrix.os }}"; echo '```json'; cat probe.json; echo '```'; } >> "$GITHUB_STEP_SUMMARY"
  ```
  The existing `repo-checks` job already runs `scripts/tests`, so the new test runs there on Linux.

- [ ] **Step 5: Commit**: `git commit -m "test: per-OS kernel sandbox feasibility probes"`

---

### Task 5: Native SearXNG in `researchly start`

**Files:**
- Create: `cli/src/researchly_cli/searxng.py`, `cli/src/researchly_cli/searxng-requirements.txt`, `cli/tests/test_searxng.py`
- Modify:
  - `cli/src/researchly_cli/native.py` (start/stop a second supervisor);
  - `cli/src/researchly_cli/state.py` (State fields + validation);
  - `cli/src/researchly_cli/main.py` (install before native start; health warning; `doctor` line; drop the web-search entry from `DEGRADED_NATIVE`);
  - `cli/pyproject.toml` (marker `searxng`);
  - existing CLI tests that construct `State` or assert `DEGRADED_NATIVE`/doctor output.

**Interfaces:**
- Consumes:
  - `state.data_dir()`, `state.log_dir()`, `state.free_port()`, `state.wait_until()`, `state.Secrets.searxng` (exists);
  - `supervise.spawn`, `supervisor_cmd`, `terminate_tree`.
- Produces (in `researchly_cli.searxng`):
  - `COMMIT: str` (full 40-char SHA), `TARBALL_SHA256: str`;
  - `home() -> Path` (= `state.data_dir() / "searxng" / COMMIT[:12]`);
  - `install(home: Path) -> None` (idempotent via `home / "ready"`; raises `SearxngError`, and on failure writes the reason to `home.parent / "error.txt"`);
  - `settings_text() -> str`;
  - `command(home: Path, port: int) -> list[str]`;
  - `env(home: Path, settings: Path, secret: str) -> dict[str, str]`;
  - `healthy(port: int) -> bool` (GET `/healthz` = 200);
  - `status() -> str` (for `doctor`);
  - `PORT = 8766`.

  New `State` fields, all `int | float | None = None`: `searxng_port`, `searxng_pid`, `searxng_created`.

- [ ] **Step 1: Pin the commit.**
  - Use the commit of the Docker image already in `infra/compose.yaml` (`searxng/searxng:2026.10.25-54ca1d9cf`), so both modes run the same SearXNG.
  - Resolve the full SHA with `gh api repos/searxng/searxng/commits/54ca1d9cf --jq .sha`.
  - Download `https://codeload.github.com/searxng/searxng/tar.gz/<SHA>` into the scratch dir and record its sha256.
  - From the extracted `requirements.txt` + `requirements-server.txt`, generate the hash-locked file:
    ```
    uv pip compile --universal --python-version 3.12 --generate-hashes --no-header <extracted>/requirements.txt <extracted>/requirements-server.txt -o cli/src/researchly_cli/searxng-requirements.txt
    ```
    Prepend a comment line naming the commit and the regeneration command.
  - Read `<extracted>/searx/version.py` to learn what `searx/version_frozen.py` must define (research: `VERSION_STRING`, `VERSION_TAG`, `DOCKER_TAG`, `GIT_URL`, `GIT_BRANCH`). Re-check at this commit.

- [ ] **Step 2: Write the failing unit tests** in `cli/tests/test_searxng.py`. No network.
  - `test_native_settings_match_the_docker_settings`: `yaml.safe_load(settings_text())` equals `yaml.safe_load(infra/searxng/settings.yml)` (repo-root path via `Path(__file__).resolve().parents[2]`). Both rely on `SEARXNG_SECRET` from the env.
  - `test_command_binds_loopback_with_one_worker`: `command(home, 9999)` uses the venv's `granian` (`Scripts/granian.exe` on win32, `bin/granian` elsewhere) and contains `--interface wsgi`, `--host 127.0.0.1`, `--port 9999`, `--workers 1` and `searx.webapp:app`.
  - `test_env_carries_the_secret_outside_argv`: `env(...)` sets `SEARXNG_SECRET`, `SEARXNG_SETTINGS_PATH`, `PYTHONPATH` (the extracted source dir) and `PYTHONUTF8=1`. The secret is not in `command(...)`.
  - `test_install_rejects_a_tampered_download`: monkeypatch the downloader to write bytes with the wrong hash. `install` raises `SearxngError` mentioning sha256, leaves no `ready` marker, and writes `error.txt`.
  - `test_status_reports_ready_error_or_pending`: covers the three cases with tmp dirs (`ready` marker; `error.txt` content; neither).
  - Native wiring, with the same fakes `test_native.py` already uses (read it first and reuse its fixtures):
    - `native.start(secrets, searxng_home=home)` spawns a second supervisor whose command is `command(home, port)` and records `searxng_port/pid/created` in the returned `State`;
    - `native.start(secrets, searxng_home=None)` spawns none;
    - `native.stop` terminates both trees.
  - `main.start` in native mode:
    - installs (fake) and passes the home;
    - on `SearxngError`, warns `web search is off: <reason>` and still starts;
    - when `healthy` stays false for the wait, warns and still succeeds.
  - `doctor` in native mode prints a `web search:` line with `status()`; `DEGRADED_NATIVE` no longer mentions web search.
  - `read_state` round-trips the three new fields, and rejects a non-port `searxng_port` the same way it rejects a bad `pg_port`.

  Run: `uv run --directory cli pytest tests/test_searxng.py -q` → fails.

- [ ] **Step 3: Write the failing real test** in the same file:
  - `@pytest.mark.searxng def test_installs_and_answers_json_from_a_mock_engine(tmp_path)`.
  - Start a stdlib `http.server` mock on a free loopback port in a thread. It answers `{"items":[{"link":"https://example.org/p1","name":"Mock Paper One","abstract":"offline fixture"}]}`.
  - `install(tmp_path / "sx")`. Real download and real uv; CI has both.
  - Write a test settings file built from `yaml.safe_load(settings_text())` plus:
    ```yaml
    use_default_settings: {engines: {keep_only: []}}
    engines:
      - {name: mock, engine: json_engine, shortcut: mk, categories: general,
         search_url: "http://127.0.0.1:<port>/?q={query}", results_query: items, url_query: link,
         title_query: name, content_query: abstract, disabled: false,
         enable_http: true}  # plain http needs this
    ```
  - Launch `subprocess.Popen(command(home, free_port), env=env(home, settings, "test-secret-0123456789abcdef"))`.
  - Wait ≤ 120 s for `healthy`.
  - Then `GET /search?q=hello&format=json&engines=mock` → `results[0]["title"] == "Mock Paper One"`, `unresponsive_engines == []`.
  - Finally `supervise.terminate_tree(proc.pid)`.

  Register the marker in `cli/pyproject.toml`:
  `"searxng: installs and runs the pinned SearXNG (downloads ~7 MB + Python deps)"`.

- [ ] **Step 4: Implement `searxng.py`.** Requirements, in order:
  - **`install`:**
    - Download with `urllib.request.urlopen(URL, timeout=60)`, streamed to `home.parent / "searxng.tar.gz"`. This is the CLI's installer, not a data connector, so PoliteClient does not apply; add `# noqa: S310` with that reason.
    - Verify sha256 against `TARBALL_SHA256` **before** extracting.
    - Extract with `tarfile` (`filter="data"`), taking only `searxng-<SHA>/searx/**`, `requirements*.txt` and `LICENSE`, and skipping symlinks/hardlinks. Upstream has a symlink that Windows cannot create.
    - `uv venv --python 3.12 <home>/venv`, then `uv pip install --python <venv python> --require-hashes -r <searxng-requirements.txt>`. Find uv as `os.environ.get("UV") or shutil.which("uv")`; uv sets `UV` for `uv run`. If uv is missing, raise `SearxngError("uv not found")`.
    - Write `searx/version_frozen.py` with the commit's date and short SHA.
    - On win32, write the `pwd.py` shim into `<venv>/Lib/site-packages`, with a docstring saying why:
      - SearXNG imports `pwd` at load time;
      - it is used only to log a Valkey connection failure, and Valkey is never configured here;
      - the shim avoids patching AGPL files (ADR 0004).
    - Touch `ready`; delete the tarball; delete a stale `error.txt`.
    - Any exception → write `error.txt` (one line: type + message) and raise `SearxngError` from it.
  - **`settings_text`:** the same YAML content as `infra/searxng/settings.yml`, as a constant, with a one-line comment that a test keeps the two equal. `install` writes it to `home / "settings.yml"`.
  - **`command`:** `[granian, "--interface", "wsgi", "--host", "127.0.0.1", "--port", str(port), "--workers", "1", "--blocking-threads", "4", "searx.webapp:app"]`.
  - **`env`:** `{**os.environ, "SEARXNG_SETTINGS_PATH": str(settings), "SEARXNG_SECRET": secret, "PYTHONPATH": str(src), "PYTHONUTF8": "1"}`.
  - **`status`:**
    - `"ready (SearXNG <COMMIT[:9]>)"` when `home()/ready` exists;
    - `f"off: {error}"` when `error.txt` exists;
    - otherwise `"not installed yet (installed on the next native start)"`.
  - **`native.start(secrets, searxng_home: Path | None = None)`:**
    - After the app supervisor, if `searxng_home`, spawn `supervisor_cmd(logs / "searxng.log", command(home, port))` with `env(home, home / "settings.yml", secrets.searxng)` and `port = state.free_port(PORT)`.
    - Record the fields.
    - Its failure cleanup terminates this tree too.
  - **`native.stop`:** terminates the SearXNG tree when its pid/created are recorded.
  - **`main.start` (native):**
    - After `_ensure_web_ui()`, call `_ensure_web_search()`. It returns the home or `None`, echoes `Installing web search (SearXNG)...` only when an install is needed, and on `SearxngError` warns `warning: web search is off: <reason>`.
    - Pass the result to `native.start`.
    - After a successful start, if `run.searxng_port` and not `state.wait_until(lambda: searxng.healthy(port), 60)`, warn `warning: web search is still starting or failed; see <logs>/searxng.log`.
  - **`doctor`:** in native mode, print `web search: <searxng.status()>`. Remove the web-search line from `DEGRADED_NATIVE`.

- [ ] **Step 5: Run.**
  - `uv run --directory cli pytest -q -rA`: everything, including the real `searxng` test (~1–2 min) and the `native` tests.
  - The ruff/pyright gates in cli.
  - The E2E smoke: `pnpm -C frontend build` then `pnpm -C e2e exec playwright test --grep @smoke`. Native start now installs SearXNG on first run; expect a longer cold start.
  - A manual check with an isolated data dir: `RESEARCHLY_DATA_DIR=<scratch>`, `RESEARCHLY_LOG_DIR=<scratch>/logs`, `PYTHONPATH`-free shell, `PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring`. Run `researchly config set runtime native`, `start`, `doctor`, curl `http://127.0.0.1:<searxng_port>/healthz`, then `stop`. Then confirm no granian process is left (`tasklist | findstr granian`).
  - Never use the real data dir.

- [ ] **Step 6: Commit**: `git commit -m "feat: native SearXNG installed and supervised by researchly start"`

---

### Task 6 (controller): ADRs, spec, docs

After CI is green on the branch, with the platform job's step summaries and the probe JSONs in hand:

- [ ] ADR 0004 native SearXNG: pinned commit + hashes, `pwd` shim, granian, AGPL reasoning, measured install/start times per OS, upstream non-support risk, and a bump procedure that re-greps for POSIX-only imports.
- [ ] ADR 0005 PDF parsing without torch:
  - Docling pin and workarounds, with docling#3997 tracked.
  - The tables/formulas gap and the decision: GROBID tables where available; TableFormer ONNX re-evaluated at step 2/4.
  - pypdfium2 vs docling-parse.
  - Timings per OS.
- [ ] ADR 0006 code sandboxes:
  - Pyodide-in-Deno flags and escape-test results per OS, plus warm/cold timings.
  - Probe table per OS: userns, Landlock ABI, bwrap, Seatbelt and AppContainer results.
  - Decision: Windows unelevated = AppContainer + Job Object, replacing the restricted token.
- [ ] ADR 0007 local embedder/reranker: fastembed, int8 models, query instruction, onnxruntime Intel-Mac pin and its consequence, reference-match numbers and timings per OS.
- [ ] Spec edits, citing the ADRs:
  - lines 216–218 (§4 table);
  - §11 item 2 (tables/formulas wording);
  - §10 lines 528/535 (fastembed int8, the instruction);
  - §19.4 Windows bullet;
  - §26 risks 2–3 (status).
- [ ] AGENTS.md: the `platform` and `searxng` test commands; gotchas (Deno paths, Intel-Mac onnxruntime pin, model cache dir). `docs/progress.md` entry. The plan's Deferred list.

## Deferred (recorded, not built here)

- Wiring into the product (step 2):
  - embedder/reranker into hybrid search;
  - `convert_pdf` into ingestion;
  - model dir under `data_dir/models`;
  - `RESEARCHLY_SEARXNG_URL` passed to the app in both modes;
  - first-use model download progress in the UI.
- TableFormer ONNX (community export) or upstream ONNX: step 2/4 bake-off against GROBID tables.
- Formula → LaTeX (opt-in, the user's vision model): step 4.
- Kernel sandbox, CPython full kernel (step 7/10): AppContainer + Job Object (Windows), bwrap+seccomp / Landlock (Linux), Seatbelt (macOS). Light-cell memory ceiling via the OS (Job Object / RLIMIT_AS) arrives with it.
- Windows arm64 (no `deno` wheel): out of the CI matrix; use the official zip if it is ever added.
- SearXNG engine selection for scholarly search (`engines=` per query): step 2 connectors.
- Rerank depth and passage truncation (step 4): 50 int8 pairs take 11–51 s on CI runners. Benchmark bge-reranker-v2-m3-int8 against smaller Apache/MIT cross-encoders on scholarly queries (relevance vs latency) and set the depth (ADR 0007).
- Warm Pyodide per notebook session (step 7/10): a fresh Deno + Pyodide per cell costs 6–22 s for a numpy/pandas/matplotlib cell on CI runners (ADR 0006).
- First HF model download on Windows hit WinError 1314 (symlink privilege) once: the step 2 download path retries and falls back to copying.
- Concurrent first `researchly start` calls can race on the SearXNG install (start lock, step 9).
