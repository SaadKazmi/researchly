# ADR 0005: Torch-free PDF conversion with Docling on onnxruntime

- Status: accepted
- Date: 2026-11-02
- Spec sections: §4 (install table), §11 item 2, §26 risk "torch bloat"

## Context
Spec §11 wants Docling with ONNX models and no torch for body text, tables and formulas. torch would add 1–3 GB to every install. Research is in `docs/research/2026-11-02-step1e-docling.md`, summarised in `2026-11-02-step1e-platform-spikes.md`, and found:
- `docling-slim` runs text, reading order (pypdfium2), layout (Heron ONNX) and OCR (RapidOCR on onnxruntime) with no torch.
- TableFormer and the formula model are torch-only (`docling-ibm-models` hard-requires torch), and there is no official ONNX export.
- Docling 2.14x still imports torch in three code paths that torch-free runs never need (upstream docling#3997).
- `docling-parse` 7.x has no macOS x86_64 wheel.

## Decision
- **Package:** `docling-slim[format-pdf-pypdfium2,convert-core,feat-ocr-rapidocr-onnx]==2.142.1`, pinned exactly because we patch private behaviour. We never use the `[models-onnxruntime]` extra (it pulls onnxruntime-gpu), nor `[models-local]` or `[standard]` (they pull torch).
- **Backend:** pypdfium2 on every OS (Apache-2.0/BSD; wheels for all five targets). It is the faster backend with less memory: 2.18 against 1.27 pages/s, and 2.6 against 6.2 GB, on an M3 Max in the Docling report (arXiv 2408.09869, Table 1). On the dev box it ran 1.9 s against 2.4–4.4 s for 3 pages, with a peak RSS of 708 against 797 MB. `docling-parse` 7.x also has no macOS x86_64 wheel.
- **Tables:** `do_table_structure=False`. Table regions come out as flattened text. GROBID's TEI tables are used where GROBID runs (Docker mode). TableFormer-in-ONNX is re-evaluated at step 2/4.
- **OCR:** RapidOCR on onnxruntime, full-page, used only when a page has no text layer.
- **Torch leaks:** `researchly.ingest.pdf` works around the three leaks:
  - stub modules while the table factory is built;
  - a CPU-only `decide_device`;
  - a duplicated CPU provider entry, so the engine's torch-based device probe is skipped.

  The setup runs once per process under a lock.
- **CI guard:** a CI test asserts that torch, torchvision, torchaudio, triton, `nvidia-*`, `onnxruntime-gpu` and `tensorflow*` are absent from both lock files (`backend/tests/test_lock.py`).

## Consequences
- Every install stays torch-free and keyless. The layout model (164 MB) and the OCR models (~31 MB) download on first use.
- Native mode gets no structured tables; tables are text only. Docker mode with GROBID gets TEI tables for scholarly PDFs.
- Docling upgrades are deliberate: the workarounds touch private APIs, so every bump re-runs the platform PDF tests on all five OS. Remove the workarounds once docling#3997 ships.
- OCR is slow on CPU (5–26 s per image-only page on CI), so ingestion runs OCR in the background queue and reports progress.

## Evidence
- **Local, Windows 10, i7-8700:** first convert 15 s cold (including the layout model download), 0.7 s warm per page; OCR about 11 s for an image-only page.
- **CI** (platform job summary for PR #5, run 41495194267):

| Runner | Convert cold (s) | Convert warm (s) | OCR, image-only page (s) |
|---|---|---|---|
| ubuntu-24.04 | 6.43 | 1.69 | 13.58 |
| ubuntu-24.04-arm | 5.34 | 2.11 | 13.36 |
| windows-2025 | 9.04 | 3.57 | 26.36 |
| macos-15 (arm64) | 3.97 | 0.53 | 5.54 |
| macos-15-intel | 8.20 | 1.17 | 24.94 |

- All five runners pass three platform tests, and `torch` is absent from `sys.modules` after conversion. The three tests:
  - a text page with a table (its fixture is a page of arXiv 2408.09869, CC BY 4.0);
  - an image-only page via OCR;
  - the no-torch check.
- Licences: docling and docling-slim are MIT; the Heron model, RapidOCR and the PP-OCR models are Apache-2.0; pypdfium2 is Apache-2.0/BSD.
