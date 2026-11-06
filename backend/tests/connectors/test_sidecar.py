import asyncio
import contextlib
import re
from pathlib import Path

import httpx
import pytest

from researchly.connectors.base import ResponseTooLarge, SourceUnavailable
from researchly.connectors.http import HostLimit
from researchly.connectors.sidecar import SidecarClient

pytestmark = pytest.mark.anyio

ORIGIN = "http://127.0.0.1:8766"


def client_for(handler, **kw) -> SidecarClient:
    kw.setdefault("limit", HostLimit(0.0, 2))
    return SidecarClient(ORIGIN, source="web", transport=httpx.MockTransport(handler), **kw)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8766/search",
        "http://user:pw@127.0.0.1:8766",
        "http://127.0.0.1:8766?x=1",
        "file:///etc/passwd",
        "searxng:8080",
        "http://",
    ],
)
def test_only_a_bare_origin_is_accepted(url):
    with pytest.raises(ValueError, match="origin"):
        SidecarClient(url, source="web")


def test_a_trailing_slash_and_a_service_name_are_fine():
    SidecarClient("http://searxng:8080/", source="web")
    SidecarClient("https://127.0.0.1:9", source="web")


@pytest.mark.parametrize("path", ["https://evil.example/x", "//evil.example/x", "search", ""])
async def test_paths_must_be_relative_to_the_origin(path):
    seen = []
    client = client_for(lambda r: seen.append(r) or httpx.Response(200))
    with pytest.raises(ValueError, match="relative"):
        await client.get(path)
    assert seen == []
    await client.aclose()


async def test_get_reaches_the_origin_with_params_and_the_real_ip_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1" and request.url.port == 8766
        assert request.url.path == "/search"
        assert request.url.params["q"] == "a b" and request.url.params["format"] == "json"
        assert request.headers["x-real-ip"] == "127.0.0.1"
        return httpx.Response(200, json={"results": []})

    client = client_for(handler)
    fetched = await client.get("/search", {"q": "a b", "format": "json"})
    assert fetched.status == 200 and fetched.json() == {"results": []}
    assert fetched.url.startswith(ORIGIN + "/search?")
    await client.aclose()


async def test_other_statuses_are_returned_for_the_caller():
    client = client_for(lambda r: httpx.Response(502, text="bad gateway"))
    assert (await client.get("/healthz")).status == 502
    await client.aclose()


async def test_a_redirect_is_never_followed():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"})

    client = client_for(handler)
    with pytest.raises(SourceUnavailable, match="redirect"):
        await client.get("/search")
    assert len(seen) == 1
    await client.aclose()


async def test_an_oversized_body_is_refused():
    client = client_for(lambda r: httpx.Response(200, content=b"x" * 2048), max_bytes=1024)
    with pytest.raises(ResponseTooLarge):
        await client.get("/search")
    await client.aclose()


async def test_a_dead_sidecar_is_unavailable_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = client_for(handler)
    with pytest.raises(SourceUnavailable, match="unreachable: ConnectError"):
        await client.get("/healthz")
    await client.aclose()


async def test_a_slow_sidecar_times_out():
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    client = client_for(handler, timeout=0.05)
    with pytest.raises(SourceUnavailable, match="unreachable: TimeoutError"):
        await client.get("/search")
    await client.aclose()


async def test_requests_are_paced():
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    limit = HostLimit(1.0, 2, sleep=sleep, clock=lambda: 0.0)
    client = client_for(lambda r: httpx.Response(200), limit=limit)
    await client.get("/healthz")
    await client.get("/healthz")
    assert sleeps == [1.0]
    await client.aclose()


def test_only_the_http_modules_import_httpx():
    src = Path(__file__).resolve().parents[2] / "src" / "researchly"
    imports = re.compile(r"^\s*(?:import|from)\s+(?:httpx|httpcore)\b", re.M)
    found = {p.relative_to(src).as_posix() for p in src.rglob("*.py") if imports.search(p.read_text("utf-8"))}
    assert found == {"connectors/http.py", "connectors/sidecar.py", "connectors/ssrf.py"}


@pytest.mark.parametrize(
    "path",
    [
        "http://127.0.0.1:8766@evil.example/x",
        "http://evil.example:8766/x",
        "http://127.0.0.1:9999/x",
        "\\\\evil.example/x",
        "/\\evil.example/x",
        "/@evil.example/x",
        "/..//evil.example/x",
    ],
)
async def test_no_path_can_leave_the_origin(path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200)

    client = client_for(handler)
    with contextlib.suppress(ValueError):
        await client.get(path)
    assert all((u.scheme, u.host, u.port) == ("http", "127.0.0.1", 8766) for u in seen)
    await client.aclose()
