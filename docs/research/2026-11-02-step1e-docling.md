# Research 1e: Docling without torch (as of 2026-11-02)

Scratch project: `C:\Users\SAAD~1.KAZ\AppData\Local\Temp\claude\c--Users-saad-kazmi-Desktop-Researchly\cf262916-4f85-4150-ba91-25fd70803812\scratchpad\spike-docling` (uv, Python 3.12; scripts spike.py, spike_pdfium.py, spike_ocr.py, check_no_torch.py, plumb.py).
Test box: Windows 10, i7-8700 (6C/12T), CPU only.

## TL;DR / recommendation

1. `docling-slim` exists (PyPI, 2.145.0, 2026-10-29). Spec wording "docling-slim + ONNX-runtime models extra" is only PARTLY true today:
   - Layout (Heron) runs on onnxruntime, no torch. OCR (RapidOCR) runs on onnxruntime. Text/backends are torch-free. I ran all of this with zero torch in the venv.
   - TableFormer (table structure) and CodeFormula (code/formula enrichment) have NO official ONNX path. Both are torch-only (`docling-ibm-models` hard-requires `torch`, `torchvision`, `transformers`).
   - Docling 2.145.0 also has three torch leaks that need workarounds even when you never use torch features (see 1.3). Upstream issue #3997 (open) is the closest tracking issue.
   - The `models-onnxruntime` extra installs `onnxruntime-gpu` on Windows/Linux (not CPU `onnxruntime`); do not use it, install plain `onnxruntime`.
2. macOS x64 (Intel): `onnxruntime` last wheel 1.23.2 (2025-10-27); `docling-parse` last x64 wheel 4.7.2 (2025-12-02) but docling-slim needs >=7.22.2, so docling-parse is sdist-only there. Use the pypdfium2 backend (`format-pdf-pypdfium2` extra, drops docling-parse). That resolves (uv, `x86_64-apple-darwin`) and ran fine on Windows (faster than docling-parse: 1.9 s vs 2.4-4.4 s for 3 pages).
3. Recommendation: do NOT bet the spec on "Docling torch-free incl. tables". Make Docling a layered, optional-feature design:
   - Tier 1 (always, torch-free, verified): pypdfium2 text + Docling Heron ONNX layout + reading order (+ RapidOCR ONNX for scans). Gives body text, headings, captions, figure/table regions.
   - Tables: either (a) pdfplumber (MIT) for ruled tables + keep table region as text/image, or (b) the community TableFormer ONNX export `welcomyou/docling-tableformer-v1-onnx-stepcache` (MIT-tagged, base model docling-models; unofficial, single maintainer, needs your own decode loop, unverified here), or (c) an opt-in "docling[torch]" extra for users who accept the 1-6 GB hit. Treat TableFormer as a post-v1 spike.
   - Formulas: no torch-free path from Docling; keep formula as image/LaTeX-less region, or opt-in extra.
   - Alternatively, skip Docling's pipeline entirely: pypdfium2 + pdfplumber (+ optional GROBID container) is simpler and fully torch-free; use Docling's Heron ONNX only if layout quality matters.
   - Pin docling-slim exactly (spec already requires exact pins for core libs); the workarounds below patch private import behaviour and will break on upgrades. Needs an ADR.
   - Fix the spec text in §6/§11/§26 accordingly (the CI torch-absent check is valid and cheap, see section 4).

## 1. What exists, what is installed

### 1.1 Packages (PyPI JSON, fetched 2026-11-02)
| Package | Version | Date | Notes |
|---|---|---|---|
| docling | 2.145.0 | 2026-10-29 | meta package = `docling-slim[standard]` (standard includes `torch`, `torchvision`, `accelerate`, `rapidocr`, `docling-ibm-models`, `docling-parse`...) |
| docling-slim | 2.145.0 | 2026-10-29 | base deps only: certifi, docling-core, filetype, langcodes, pluggy, pydantic(-settings), requests, tqdm (~50 MB per its README) |
| docling-core | 2.105.0 | 2026-10-27 | |
| docling-parse | 7.24.2 | 2026-10-29 | deps: pillow, pydantic, docling-core, pywin32 (win) |
| docling-ibm-models | 4.0.4 | 2026-10-21 | HARD deps: `torch>=2.2.2`, `torchvision`, `transformers>=4.42`, `safetensors[torch]` |
| docling-onnx-models | 0.1.3 | 2025-09-09 | THIRD-PARTY (github.com/asmud/docling-onnx-models, MIT), claims drop-in ONNX replacement; stale (>1 yr), bundles `opencv-python`; not used by docling itself. Do not rely on it. |

docling-slim extras relevant here (from its metadata):
- `format-pdf` = docling-parse + pypdfium2 ; `format-pdf-pypdfium2` = pypdfium2 only ; `format-pdf-docling` = docling-parse only.
- `convert-core` = numpy, pillow, rtree, scipy.
- `feat-ocr-rapidocr-onnx` = rapidocr>=3.9.1 + onnxruntime (CPU wheel; no torch). `feat-ocr-rapidocr` = rapidocr only.
- `models-onnxruntime` = `onnxruntime-gpu` on win32/linux, `onnxruntime` on darwin (for py 3.11-3.13). Avoid on win/linux.
- `models-local` = accelerate, docling-ibm-models, torch, torchvision (TableFormer, CodeFormula, picture classifier on transformers).
- `models-vlm-inline` = transformers, torch-ish stack, peft, mlx-vlm on arm64 mac.
- `standard` = everything incl. torch, torchvision.
- There is NO `docling[onnx]` extra on `docling`; `docling[onnxruntime]` maps to `docling-slim[models-onnxruntime]`, `docling[rapidocr]` maps to `feat-ocr-rapidocr-onnx`.

### 1.2 Which components run without torch (docling 2.145.0, verified by running)
| Component | Torch-free? | Evidence |
|---|---|---|
| PDF parsing (docling-parse or pypdfium2 backend) | yes | ran both |
| Layout, Heron | yes, via `OnnxRuntimeObjectDetectionEngineOptions` + HF repo `docling-project/docling-layout-heron-onnx` (164 MB, Apache-2.0) | ran; needs `transformers<5` for the HF image processor (see 1.3) |
| Layout, Heron-101 / Egret medium/large/xlarge | NO (source comment: "Only Heron has an ONNX export") | stage_model_specs.py |
| OCR RapidOCR (onnxruntime, PP-OCRv4/v5) | yes (after workaround 2) | ran forced full-page OCR on 3 pages |
| TableFormer v1 (`do_table_structure=True`) | NO: needs `docling_ibm_models` (torch). With docling-ibm-models absent: `ModuleNotFoundError: docling_ibm_models` at `table_structure_model.py:117` | ran |
| TableFormer V2 (`docling-project/TableFormerV2`, Apache-2.0) | NO (imports torch, torchvision at module top) | source |
| Granite-Vision table model | NO | source |
| Code/formula enrichment (CodeFormula, CodeFormulaV2) | NO (transformers + torch) | docs/source; not run |
| Picture classifier | ONNX engine exists (`DocumentFigureClassifier-v2.0/2.5` onnx, Apache/MIT); not tested | HF listing |
| SmolDocling/Granite-Docling VLM | torch/transformers or mlx or API; SmolDocling-256M-preview has an `onnx` tag on HF (not wired in docling 2.145 as far as I found) | HF listing |
| Official TableFormer ONNX | does not exist in docling-project HF org (listing of 32 repos). Community: `welcomyou/docling-tableformer-v1-onnx-stepcache` (license: mit tag, encoder + decoder-step + bbox-head ONNX, you write the decode loop) | HF API |

### 1.3 The torch leaks in docling 2.145.0 and workarounds (all needed; all verified)
1. Default layout engine is `TRANSFORMERS` -> `import torch`. Fix: pass `LayoutObjectDetectionOptions.from_preset("layout_heron_default", engine_options=OnnxRuntimeObjectDetectionEngineOptions(...))`.
2. `docling.utils.accelerator_utils.decide_device()` does `import torch` unconditionally (line 23). Called by the ONNX layout engine's provider resolution and by `rapid_ocr_model`. Workaround A (layout): set `providers=["CPUExecutionProvider","CPUExecutionProvider"]` in the engine options, which makes `_resolve_providers` return early (onnxruntime warns "Duplicate provider", harmless). Workaround B (OCR): monkeypatch `decide_device` to return `"cpu"` in `docling.utils.accelerator_utils` and `docling.models.stages.ocr.rapid_ocr_model`.
3. Even with `do_table_structure=False`, the pipeline builds the table-structure factory, which imports `table_structure_model_v2` and `..._granite_vision` that `import torch` at module level. Workaround: before the first conversion, put temporary stub `torch`/`torchvision`/`torchvision.transforms` modules into `sys.modules`, call `docling.models.factories.get_table_structure_factory()` (lru_cached), then delete the stubs. (Importing the stubs earlier breaks `transformers`, which probes torch via `find_spec`.)
4. `transformers` is required (HF `AutoImageProcessor` for the layout model). It must be `>=4.50,<5`: transformers 5.22.1 refuses to load `RTDetrImageProcessor(Pil)` without torchvision ("Missing optional dependencies: torchvision"). With transformers 4.57.6 + huggingface-hub 0.36.2 it works with no torch (warning "None of PyTorch, TensorFlow, Flax found" is expected). Do not add your own `huggingface-hub>=2` pin: transformers 4.x needs `huggingface-hub<1`.
These are exactly what upstream issue #3997 (open, 2026-08-14, "Lazy-load transformers/torch ... so models-onnxruntime-only installs don't import torch at module import") is about; not fixed in 2.145.0. Also relevant: issue #4783 (open) "Document CPU-only torch install" - upstream's own answer for CPU users is currently "install CPU torch", i.e. torch-free is not a documented supported mode.

## 2. Wheels and licences

### 2.1 Wheels for CPython 3.12 (PyPI)
| Package (version) | win x64 | mac arm64 | mac x64 | linux x64 | linux arm64 |
|---|---|---|---|---|---|
| onnxruntime 1.31.1 (2026-10-26) | yes | yes, but `macosx_14_0` (macOS 14+) since 1.24.1 | NO. Last x64 wheel 1.23.2 (2025-10-27, `macosx_13_0_x86_64`) | manylinux_2_28 | manylinux_2_28 |
| docling-parse 7.24.2 | yes | `macosx_14_0_arm64` only | NO. Last x64 wheel 4.7.2 (2025-12-02); 7.x is sdist-only for mac x64 | manylinux_2_27/2_28 | manylinux_2_26/2_28 |
| pypdfium2 5.14.0 | yes | yes | yes | yes | yes |
| rapidocr 3.10.0 | py3-none-any | any | any | any | any |
| opencv-python-headless 5.0.0.93 | yes | yes | yes | yes | yes |
| numpy 2.5.4, scipy 1.18.1, pandas 3.1.0, shapely 2.2.0, pyclipper 1.4.0, rtree 1.4.1, lxml 6.1.3, tokenizers 0.23.3 / 0.22.2, safetensors, regex, psutil | yes | yes | yes | yes | yes |
| pywin32 312 (docling-parse on win) | yes | n/a | n/a | n/a | n/a |
Verification by `uv pip compile --only-binary :all: --python-version 3.12`: win x64, linux x64, linux arm64 resolve with docling-parse (67/65/65 pkgs, no torch). aarch64-apple-darwin and x86_64-apple-darwin FAIL with `Wheels are required for docling-parse` when using the `format-pdf` extra. With `format-pdf-pypdfium2` instead, both mac targets resolve (onnxruntime 1.23.2 because uv's default macOS target is 13; real macOS 14+ arm64 gets 1.31.1). Caveat: macOS 13 arm64 and older cannot get docling-parse or onnxruntime >=1.24; macOS 14+ needed for current wheels.
macOS runner note: `macos-15-intel` x64 is the one that exposes this; it is the last Intel GitHub-hosted image (GitHub has announced Intel macOS runner retirement, check current date before relying on it).

### 2.2 Licences
| Item | Licence | Source |
|---|---|---|
| docling, docling-core, docling-parse, docling-ibm-models | MIT | PyPI/GitHub (docling-project) |
| onnxruntime | MIT | PyPI |
| pypdfium2 | Apache-2.0 / BSD-3 (PDFium) | PyPI |
| rapidocr + PP-OCR ONNX models | code Apache-2.0; PaddleOCR models Apache-2.0 | rapidocr repo; models downloaded at first use (~31 MB) |
| Heron layout `docling-layout-heron(-onnx)` | Apache-2.0 | HF tag |
| Egret layouts / Heron-101 | Apache-2.0 | HF tags |
| TableFormer v1 weights (`docling-project/docling-models`) | tags: Apache-2.0 + CDLA-Permissive-2.0 | HF tags |
| TableFormerV2 | Apache-2.0 | HF tag |
| CodeFormula | MIT; CodeFormulaV2 CDLA-Permissive-2.0 | HF tags |
| DocumentFigureClassifier v2.0 / v2.5 | Apache-2.0 / MIT | HF tags |
| SmolDocling-256M-preview | CDLA-Permissive-2.0 | HF tag |
| granite-docling-2stage-258m | Apache-2.0 (ibm-granite/granite-docling-258M tag lookup failed in my run; assume Apache-2.0 per IBM release, verify) | HF |
| community TableFormer ONNX (welcomyou) | MIT tag | HF README |
All are permissive, fit Apache/MIT policy (CDLA-Permissive-2.0 is a permissive data licence; treat as acceptable but note it is not Apache/MIT literally).

## 3. CPU performance

### 3.1 Upstream (Docling Technical Report, arXiv 2408.09869, Table 1; 225 pages, OCR off, TORCH-based layout/table; measured on PDF backends)
| CPU | threads | docling-parse backend | pypdfium backend |
|---|---|---|---|
| Apple M3 Max (16c) | 4 | 177 s, 1.27 pages/s, 6.20 GB | 103 s, 2.18 pages/s, 2.56 GB |
| Apple M3 Max | 16 | 167 s, 1.34 p/s | 92 s, 2.45 p/s |
| Xeon E5-2690 (16c) | 4 | 375 s, 0.60 p/s, 6.16 GB | 239 s, 0.94 p/s, 2.42 GB |
| Xeon E5-2690 | 16 | 244 s, 0.92 p/s | 143 s, 1.57 p/s |
(Those numbers include TableFormer; older layout model.)

### 3.2 This spike (i7-8700, 3 pages, scientific paper pages 5-7 of the Docling report; includes Heron ONNX layout; no TableFormer)
| Config | cold (model download+init+convert) | warm 3 pages | peak RSS |
|---|---|---|---|
| docling-parse backend, ONNX layout, no tables, no OCR | 5.0-6.0 s (model was already downloaded in some runs; the very first run also downloaded 164 MB) | 2.4-4.4 s (~0.7-1.2 pages/s) | 797 MB |
| pypdfium2 backend, same | 5.0 s | 1.9 s (~1.5 pages/s) | 708 MB |
| docling-parse + forced full-page RapidOCR ONNX | 26.2 s | 17.1 s (~0.18 pages/s) | 1.48 GB |
| pdfplumber text+table (for comparison) | n/a | 1.5 s for 3 pages | small |
Model downloads: Heron ONNX repo 164 MB (model.onnx); TableFormer v1 `docling-models` repo is 342 MB (the cold run with `do_table_structure=True` downloaded it before failing on docling_ibm_models; it is torch-only, not usable); RapidOCR ONNX models ~31 MB (stored inside the venv `rapidocr/models`, fetched on first use).

## 4. Verifying "no torch"

Installed size of the spike venv: 617 MB (cv2 113, scipy 89, transformers 57, pandas 50, onnxruntime 43, docling_parse 37, rapidocr 32). Largest cost drivers are non-torch; opencv-python-headless via rapidocr is big. Compare: `docling[standard]` with CUDA torch on Linux ~6.5 GB incl. models (issue #4783).

Commands run in the spike:
- `uv add "docling-slim[format-pdf,convert-core,feat-ocr-rapidocr-onnx]" onnxruntime huggingface-hub` (38 s) then `uv add "transformers>=4.50,<5" "huggingface-hub>=0.23,<1"`. Result: 69 packages in uv.lock, none torch/nvidia/triton.
- `uv pip list | grep -i -E "^(torch|nvidia|triton)"` -> nothing.
- Runtime: `importlib.util.find_spec("torch")` is None and `"torch" in sys.modules` False after conversion.

Important: a UNIVERSAL uv.lock includes torch if ANY extra reachable from your deps pulls it. Negative test: a project depending on `docling-slim[models-local]` produced a lock where `uv tree --invert --package torch` shows `torch v2.15.0 <- accelerate <- docling-slim (extra: models-local)`, plus 17 nvidia-*/triton packages (linux markers). So the CI check below catches it.

CI assertion (verified; `check_no_torch.py`):
```python
import sys, tomllib, re
BAD = re.compile(r"^(torch|torchvision|torchaudio|triton|nvidia-.*|pytorch-.*|onnxruntime-gpu|tensorflow.*)$")
lock = tomllib.load(open(sys.argv[1] if len(sys.argv) > 1 else "uv.lock", "rb"))
hits = sorted({p["name"] for p in lock["package"] if BAD.match(p["name"])})
sys.exit(f"forbidden packages in lock: {hits}" if hits else print("ok: no torch/CUDA in lock"))
```
Output on spike lock: `ok: no torch/CUDA in lock`; on the negative lock: lists torch, torchvision, triton, nvidia-* and exits 1. Complement with `uv lock --check` (lock current) and, per platform, `uv export --frozen --no-hashes --python-platform <p> | grep -i -E "^(torch|nvidia)"`. Gotcha: `models-onnxruntime` extra adds `onnxruntime-gpu`, which the BAD regex blocks (intentional).

## 5. Alternatives (Apache/MIT + keyless)
- pypdfium2 (Apache-2.0/BSD) text + pdfplumber 0.11.10 / pdfminer.six (MIT): torch-free, wheels everywhere (pure Python + pypdfium2). In spike, pdfplumber extracted 3376/3015/27573 chars and 2 tables (2x3, 4x4) from the 3 pages (ruled tables only; borderless tables are missed; page 3 text length 27573 is inflated by character-level artifacts of its table, needs checking). No reading order or multi-column logic.
- GROBID: Apache-2.0, strong for scholarly header/references/body structure; JVM service (needs Java or Docker; the models are CRF/DL in-JVM, no torch; the DL variant is heavy). Violates "install without admin" unless the CLI downloads a JRE + jar; good as optional runtime. Not tested.
- Docling Heron-ONNX layout + pypdfium2 (this spike) is the best torch-free layout-aware option.
- PyMuPDF/pymupdf4llm: AGPL-3.0 (or commercial) - incompatible with an Apache/MIT project; exclude.
- marker: GPL-3.0 code + restrictive model weights licence, needs torch; exclude.
- unstructured: Apache-2.0 but hi_res strategy needs torch/detectron or onnx models + system deps (poppler/tesseract); `fast` strategy is plain pdfminer. Not better than pdfplumber for us.
- Tesseract: Apache-2.0 but needs a system binary; RapidOCR ONNX is preferable (wheel-only).

## 6. CI spike design (GitHub Actions)

Matrix: windows-2025, ubuntu-24.04, ubuntu-24.04-arm, macos-15, macos-15-intel. All use Python 3.12 and `astral-sh/setup-uv`. Use `extras` per runner: pypdfium2 backend everywhere (one code path; avoids docling-parse mac x64 hole).

Test PDF: commit pages 5-7 of arXiv 2408.09869 "Docling Technical Report" (CC BY 4.0, https://arxiv.org/abs/2408.09869; page contains "Table 1: Runtime characteristics..." (borderless, spanning page 5 in v5 numbering is index 4)). Created with `pypdfium2.PdfDocument.new().import_pages(src,[4,5,6])` -> 3.2 MB (too big; strip images or choose one text/table page; target < 300 KB). Alternative: author a tiny table PDF with reportlab (MIT) for determinism and keep it < 20 KB; keep the arXiv page as a second, realism test.

Workflow sketch:
```yaml
name: docling-spike
on: [workflow_dispatch, pull_request]
jobs:
  spike:
    strategy:
      fail-fast: false
      matrix:
        os: [windows-2025, ubuntu-24.04, ubuntu-24.04-arm, macos-15, macos-15-intel]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --python 3.12 --frozen --directory spikes/docling   # pyproject below
      - run: uv run --directory spikes/docling python check_no_torch.py uv.lock
      - run: uv run --directory spikes/docling python spike.py ../../tests/fixtures/sample.pdf
```
spikes/docling/pyproject.toml dependencies:
```
docling-slim[format-pdf-pypdfium2,convert-core,feat-ocr-rapidocr-onnx]==2.145.0
onnxruntime            # NOT docling-slim[models-onnxruntime] (pulls onnxruntime-gpu on win/linux)
transformers>=4.50,<5
huggingface-hub>=0.23,<1
pdfplumber             # fallback table path
```
spike.py must (1) apply the four workarounds from 1.3 (copy spike_pdfium.py in the scratch dir), (2) `converter.convert(pdf)`, (3) assert `"Table 1" in md` and a known sentence, (4) assert `len(doc.tables) >= 1` and, as a tripwire for upstream behaviour change, record whether table cells are structured (currently NOT: `do_table_structure=False` yields one flattened table item), (5) `assert importlib.util.find_spec("torch") is None and "torch" not in sys.modules`, (6) log cold/warm seconds and peak RSS (psutil) as step summary, with soft timeout e.g. cold < 120 s, warm < 30 s on runners, (7) cache `~/.cache/huggingface` keyed on docling-slim version to avoid re-downloading the 164 MB Heron model each run (also tests first-use download path in one cold job).
Expected per-runner results (not yet run; only Windows x64 was run): win/linux/mac-arm64 should work; mac-15-intel resolves to onnxruntime 1.23.2 (no 1.24+ x64 wheel) and must not use docling-parse. Linux arm64 wheels exist for all deps listed above. Failure to watch: `onnxruntime` 1.23.2 on macOS x64 loading the Heron ONNX opset (not tested), and numpy 2.5 / scipy wheels on macOS 13 target.

## 7. Experiment log (verbatim highlights, Windows 10, uv 0.12.10)
```
uv init --bare --python 3.12 .
uv add "docling-slim[format-pdf,convert-core,feat-ocr-rapidocr-onnx]" onnxruntime huggingface-hub   # 38 s
 + onnxruntime==1.31.1  + opencv-python-headless==5.0.0.93  + rapidocr==3.10.0 + pypdfium2==5.14.0 + numpy==2.5.4 ...  (no torch)
python spike.py (default options)            -> ModuleNotFoundError: No module named 'torch'  (transformers_engine.py:121)
python spike.py (ONNX layout engine)         -> RuntimeError: Failed to load image processor ... No module named 'transformers'
uv add transformers (5.22.1)                 -> "Missing optional dependencies: torchvision"
uv add "transformers>=4.50,<5" "huggingface-hub>=0.23,<1"  -> transformers 4.57.6, huggingface-hub 0.36.2
                                             -> ModuleNotFoundError torch at accelerator_utils.py:23 (decide_device)
providers=["CPUExecutionProvider"]*2         -> ModuleNotFoundError torch at table_structure_model_granite_vision.py:11
stub torch while priming table factory       -> OK:
   convert s (incl. model download+init) 4.96 / 6.03
   second convert s (warm) 4.38 / 2.41   (run-to-run noise)
   8401 chars markdown, tables: 1 (unstructured, flattened into one cell)
   torch in sys.modules: False | find_spec(torch): None | peak RSS MB 796.5
do_table_structure=True                      -> downloads docling-models (342 MB), then ModuleNotFoundError: docling_ibm_models
RapidOCR forced full page + decide_device patch -> convert 26.2 s cold / 17.05 s warm, peak RSS 1480.9 MB, no torch
pypdfium2 backend (spike_pdfium.py)          -> cold 4.97 s, warm 1.94 s, 9012 chars, peak RSS 707.9 MB, no torch
du -sh .venv -> 617M
```
Cleanup: deleted the two HF cache dirs this run created (`models--docling-project--docling-layout-heron-onnx`, `models--docling-project--docling-models`) from `~/.cache/huggingface/hub`; nothing else outside the scratch dir was touched (pdfplumber/psutil were installed into the scratch venv only). Pre-existing Qwen3-Embedding caches there were not mine.

## 8. Open items / not verified
- Markdown text quality vs ground truth not scored (only length + presence checks); table cell structure unavailable without TableFormer.
- Linux/macOS actually executing the spike: not run (no machines); only `uv pip compile --only-binary` resolution for those targets (win/linux ok, mac only with pypdfium2 extra).
- Community TableFormer ONNX decode loop effort/accuracy unmeasured; licence tag MIT is the repo owner's claim over weights derived from CDLA-Permissive/Apache upstream.
- granite-docling exact licence tag not retrieved; docling 2.145 granite/Smol ONNX wiring not confirmed.
- GitHub Intel-macOS runner future availability should be re-checked.

## Sources
- https://pypi.org/project/docling/ ; https://pypi.org/project/docling-slim/ ; https://pypi.org/project/docling-parse/ ; https://pypi.org/project/docling-ibm-models/ ; https://pypi.org/project/docling-core/ ; https://pypi.org/project/onnxruntime/ ; https://pypi.org/project/docling-onnx-models/ (all fetched via pypi.org/pypi/<name>/json 2026-11-02)
- https://github.com/docling-project/docling (releases v2.145.0 2026-10-29; source files docling/utils/accelerator_utils.py, docling/models/plugins/defaults.py, docling/models/stages/table_structure/*, docling/datamodel/stage_model_specs.py)
- https://github.com/docling-project/docling/issues/3997 ; https://github.com/docling-project/docling/issues/4783
- https://github.com/docling-project/docling-ibm-models (v4.0.4 2026-10-21; v4.0.0 2026-08-28 "remove unused models")
- https://huggingface.co/docling-project (org listing via HF API), docling-layout-heron-onnx, docling-models, TableFormerV2, welcomyou/docling-tableformer-v1-onnx-stepcache
- https://arxiv.org/abs/2408.09869 (Docling Technical Report, CC BY 4.0; Table 1 benchmarks)
