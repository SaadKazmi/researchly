import httpx
import pytest

from researchly.connectors.http import PoliteClient
from researchly.connectors.robots import Robots

pytestmark = pytest.mark.anyio

ARXIV_ROBOTS = "User-agent: *\nCrawl-delay: 15\nAllow: /html\nAllow: /pdf\nDisallow: /e-print\nDisallow: /src\n"


def make(handler, seen=None, now=None):
    async def sleep(seconds: float) -> None:
        return None

    def logged(request):
        if seen is not None:
            seen.append(str(request.url))
        return handler(request)

    client = PoliteClient(
        transport=httpx.MockTransport(logged), sleep=sleep, rand=lambda: 0.0, limits={"default": (0.0, 1)}
    )
    clock = (lambda: now[0]) if now is not None else (lambda: 0.0)
    return client, Robots(client, clock=clock)


async def test_rules_and_crawl_delay_are_applied():
    client, robots = make(lambda r: httpx.Response(200, text=ARXIV_ROBOTS))
    async with client:
        assert await robots.allowed("https://arxiv.org/html/1706.03762")
        assert not await robots.allowed("https://arxiv.org/e-print/1706.03762")
        assert client.limit_for("https://arxiv.org/html/1", None).interval == 15.0


async def test_robots_is_fetched_once_per_origin_and_refreshed_after_24_hours():
    seen: list[str] = []
    now = [0.0]
    client, robots = make(lambda r: httpx.Response(200, text="User-agent: *\nAllow: /\n"), seen, now)
    async with client:
        await robots.allowed("https://pub.example.org/a.pdf")
        await robots.allowed("https://pub.example.org/b.pdf")
        assert seen == ["https://pub.example.org/robots.txt"]
        now[0] = 24 * 3600 + 1
        await robots.allowed("https://pub.example.org/c.pdf")
    assert len(seen) == 2


async def test_missing_robots_allows_everything():
    client, robots = make(lambda r: httpx.Response(404))
    async with client:
        assert await robots.allowed("https://pub.example.org/a.pdf")


async def test_unreachable_robots_disallows():
    client, robots = make(lambda r: httpx.Response(503))
    async with client:
        assert not await robots.allowed("https://pub.example.org/a.pdf")


async def test_a_crawl_delay_over_a_minute_skips_the_site():
    client, robots = make(lambda r: httpx.Response(200, text="User-agent: *\nCrawl-delay: 3600\nAllow: /\n"))
    async with client:
        assert not await robots.allowed("https://slow.example.org/a.pdf")


async def test_rules_for_our_agent_win_over_the_wildcard():
    text = "User-agent: Researchly\nDisallow: /private\n\nUser-agent: *\nDisallow: /\n"
    client, robots = make(lambda r: httpx.Response(200, text=text))
    async with client:
        assert await robots.allowed("https://pub.example.org/paper.pdf")
        assert not await robots.allowed("https://pub.example.org/private/x.pdf")


async def test_userinfo_is_never_sent_and_origins_ignore_case():
    seen: list[str] = []
    client, robots = make(lambda r: httpx.Response(200, text=ARXIV_ROBOTS), seen)
    async with client:
        await robots.allowed("https://user:secret@Pub.Example.org/a.pdf")
        await robots.allowed("https://pub.example.org/b.pdf")
    assert seen == ["https://pub.example.org/robots.txt"]
