import importlib.util
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from huggingface_hub import constants

from researchly.ingest.pdf import convert_pdf

from .conftest import report

pytestmark = pytest.mark.platform

FIXTURE = Path(__file__).parent / "fixtures" / "table-page.pdf"
FRAGMENT = "runtime characteristics of docling"


@pytest.fixture(scope="session", autouse=True)
def _hf_home(models_dir: Path) -> Iterator[None]:
    # monkeypatch is function-scoped, so set and restore the environment by hand; CI caches this dir
    old = os.environ.get("HF_HOME")
    os.environ["HF_HOME"] = str(models_dir / "hf")
    # huggingface_hub read HF_HOME when first imported (the module imports above), so repoint its cache too
    old_cache = constants.HF_HUB_CACHE
    constants.HF_HUB_CACHE = str(models_dir / "hf" / "hub")
    yield
    constants.HF_HUB_CACHE = old_cache
    if old is None:
        del os.environ["HF_HOME"]
    else:
        os.environ["HF_HOME"] = old


def test_text_layout_and_table_region() -> None:
    t = time.perf_counter()
    result = convert_pdf(FIXTURE)
    report("pdf convert, cold (incl. model download if uncached)", time.perf_counter() - t)
    t = time.perf_counter()
    convert_pdf(FIXTURE)
    report("pdf convert, warm", time.perf_counter() - t)
    assert result.pages == 1
    assert "Table 1" in result.markdown
    assert FRAGMENT in result.markdown.lower()
    assert result.tables >= 1


def test_ocr_reads_an_image_only_page(tmp_path: Path) -> None:
    image = pdfium.PdfDocument(FIXTURE)[0].render(scale=2).to_pil().convert("RGB")
    scan = tmp_path / "scan.pdf"
    image.save(scan)
    t = time.perf_counter()
    result = convert_pdf(scan, ocr=True)
    report("pdf OCR, image-only page", time.perf_counter() - t)
    assert "table 1" in result.markdown.lower()


def test_no_torch_was_imported() -> None:
    assert "torch" not in sys.modules
    assert importlib.util.find_spec("torch") is None
