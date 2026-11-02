"""PDF → Markdown with Docling, torch-free (spec §11, ADR 0005).

Text and reading order come from pypdfium2, layout from the Heron ONNX model, and OCR from RapidOCR on onnxruntime.
Docling's table-structure and formula models are torch-only, so table regions come out as flattened text; GROBID
adds structured tables where it runs.
"""

import importlib.machinery
import sys
import threading
import types
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from unittest.mock import MagicMock

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.object_detection_engine_options import OnnxRuntimeObjectDetectionEngineOptions
from docling.datamodel.pipeline_options import (
    LayoutObjectDetectionOptions,
    OcrMode,
    PdfPipelineOptions,
    RapidOcrOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption


@dataclass(frozen=True)
class ConvertedPdf:
    markdown: str
    pages: int
    tables: int


def _without_torch_imports(ocr: bool) -> None:
    """Docling 2.142.1 imports torch where torch-free runs never need it (docling#3997)."""
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
    # 2. RapidOcrModel calls decide_device(), which imports torch unconditionally; CPU-only has nothing to decide.
    if ocr:
        import docling.models.stages.ocr.rapid_ocr_model as rapid_ocr

        rapid_ocr.decide_device = lambda *_a, **_k: "cpu"  # type: ignore[assignment]


_SETUP = threading.Lock()  # concurrent first calls must not interleave the sys.modules swap


@cache
def _converter(ocr: bool) -> DocumentConverter:
    with _SETUP:
        _without_torch_imports(ocr)
    layout = LayoutObjectDetectionOptions.from_preset(
        "layout_heron_default",
        # 3. the engine runs its torch-based device probe when the list is exactly ["CPUExecutionProvider"], so repeat
        #    the entry; onnxruntime accepts the duplicate
        engine_options=OnnxRuntimeObjectDetectionEngineOptions(
            providers=["CPUExecutionProvider", "CPUExecutionProvider"]
        ),
    )
    options = PdfPipelineOptions(
        do_table_structure=False,
        do_ocr=ocr,
        ocr_options=RapidOcrOptions(backend="onnxruntime", mode=OcrMode.DEFAULT),
        layout_options=layout,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options, backend=PyPdfiumDocumentBackend)}
    )


def convert_pdf(path: Path, *, ocr: bool = False) -> ConvertedPdf:
    doc = _converter(ocr).convert(path).document
    return ConvertedPdf(markdown=doc.export_to_markdown(), pages=doc.num_pages(), tables=len(doc.tables))
