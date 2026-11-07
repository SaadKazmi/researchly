import gzip
import hashlib
import json

import httpx
import pytest
from sqlalchemy import func, select

from researchly.connectors.base import SourceUnavailable
from researchly.connectors.http import PoliteClient
from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import PaperCode
from researchly.services import paper_code as pc
from researchly.services.identity import normalize_title

pytestmark = pytest.mark.anyio

T2T = "https://github.com/tensorflow/tensor2tensor"
TORCH = "https://github.com/jadore801120/attention-is-all-you-need-pytorch"
GAT = "https://github.com/example/gat-mol"


def link(paper, title, arxiv_id, repo, official, framework="none", in_paper=False):
    return {
        "paper_url": f"https://paperswithcode.com/paper/{paper}",
        "paper_title": title,
        "paper_arxiv_id": arxiv_id,
        "paper_url_abs": None,
        "paper_url_pdf": None,
        "repo_url": repo,
        "is_official": official,
        "mentioned_in_paper": in_paper,
        "mentioned_in_github": False,
        "framework": framework,
    }


ITEMS = [
    link("attention-is-all-you-need", "Attention Is All You Need", "1706.03762", T2T, True, "tf", True),
    link("attention-is-all-you-need", "Attention Is All You Need", "1706.03762", TORCH, False, "pytorch"),
    link("gat-mol", "Graph Attention Networks for Molecules", None, GAT, True),
    link("no-repo", "A Paper Without Code", "1810.10559", None, False),
    link("weak-title", "Hi", None, "https://github.com/example/hi", True),  # no arXiv ID, title too weak
    link("bad-link", "A Paper With A Bad Link", "2001.00001", "javascript:alert(1)", True),
]


def gz(items=ITEMS) -> bytes:
    return gzip.compress(json.dumps(items, indent=1).encode())


def test_iter_links_reads_items_across_chunk_edges():
    assert list(pc.iter_links(gz(), chunk=7)) == ITEMS
    assert list(pc.iter_links(gzip.compress(b"[]"))) == []


@pytest.mark.parametrize(
    "data",
    [gzip.compress(json.dumps(ITEMS).encode()[:-40]), gz()[:-10], b"not gzip at all"],
)
def test_a_truncated_or_broken_file_fails(data):
    with pytest.raises(ValueError, match="links file"):
        list(pc.iter_links(data, chunk=64))


def test_to_row_keeps_linkable_rows_only():
    rows = [pc.to_row(item) for item in ITEMS]
    assert rows[0] == {
        "paper_url": "https://paperswithcode.com/paper/attention-is-all-you-need",
        "repo_url": T2T,
        "arxiv_id": "1706.03762",
        "title_norm": "attention is all you need",
        "is_official": True,
        "framework": "tf",
        "mentioned_in_paper": True,
    }
    assert rows[2] is not None and rows[2]["arxiv_id"] is None and rows[2]["framework"] is None
    assert rows[2]["title_norm"] == normalize_title("Graph Attention Networks for Molecules")
    assert rows[3:] == [None, None, None]
    assert pc.to_row("not a row") is None


async def test_download_follows_the_cdn_redirect_and_checks_sha256(monkeypatch):
    body = gz()
    monkeypatch.setattr(pc, "SHA256", hashlib.sha256(body).hexdigest())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "huggingface.co":
            assert request.url.path == f"/datasets/pwc-archive/files/resolve/{pc.COMMIT}/{pc.FILE}"
            return httpx.Response(302, headers={"Location": "https://us.aws.cdn.hf.co/xet-bridge-us/abc?sig=1"})
        assert request.url.host == "us.aws.cdn.hf.co"
        return httpx.Response(200, content=body, headers={"content-type": "application/gzip"})

    async with PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)}) as client:
        assert await pc.download(client) == body


@pytest.mark.parametrize(
    ("response", "message"), [(httpx.Response(200, content=b"x"), "sha256"), (httpx.Response(404), "HTTP 404")]
)
async def test_a_changed_or_missing_file_is_refused(response, message):
    transport = httpx.MockTransport(lambda r: response)
    async with PoliteClient(transport=transport, limits={"default": (0.0, 1)}) as client:
        with pytest.raises(SourceUnavailable, match=message):
            await pc.download(client)


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.mark.db
async def test_load_then_look_up_by_arxiv_id_or_title(sm):
    async with sm() as s:
        assert await pc.load(s, gz()) == 3
        assert await pc.load(s, gz()) == 3  # idempotent
        assert await s.scalar(select(func.count()).select_from(PaperCode)) == 3
        links = await pc.lookup(s, arxiv_id="arXiv:1706.03762v7")
        assert links == [pc.CodeLink(T2T, True, "tf", True), pc.CodeLink(TORCH, False, "pytorch", False)]
        assert [x.repo_url for x in await pc.lookup(s, title="Graph attention networks for molecules.")] == [GAT]
        fallback = await pc.lookup(s, arxiv_id="9999.99999", title="Graph Attention Networks for Molecules")
        assert [x.repo_url for x in fallback] == [GAT]  # no arXiv row: the title is tried next
        assert await pc.lookup(s, title="Hi") == []
        assert await pc.lookup(s) == []


@pytest.mark.db
async def test_ensure_loaded_downloads_once(sm, monkeypatch):
    calls = []

    async def fake_download(client):
        calls.append(client)
        return gz()

    monkeypatch.setattr(pc, "download", fake_download)
    async with sm() as s, PoliteClient() as client:
        assert await pc.ensure_loaded(s, client) == 3
        assert await pc.ensure_loaded(s, client) == 0
    assert len(calls) == 1
