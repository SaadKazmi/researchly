import asyncio
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest

from researchly.connectors import http
from researchly.connectors.base import QuotaExhausted, ResponseTooLarge, SourceUnavailable, UnwantedContent
from researchly.connectors.http import Breaker, HostLimit, PoliteClient, retry_after

pytestmark = pytest.mark.anyio


def client_for(handler, slept):
    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return PoliteClient(
        transport=httpx.MockTransport(handler), sleep=sleep, rand=lambda: 0.5, limits={"default": (0.0, 1)}
    )


def test_retry_after_parses_seconds_and_http_dates():
    assert retry_after("7") == 7.0
    soon = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    assert 25 <= (retry_after(soon) or 0) <= 30
    assert retry_after(None) is None and retry_after("soon") is None


async def test_user_agent_and_optional_mailto():
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={})

    async with client_for(handler, []) as client:
        await client.get("https://api.example.org/x", source="t")
    async with PoliteClient(transport=httpx.MockTransport(handler), mailto="me@example.org") as client:
        await client.get("https://api.example.org/x", source="t")
    assert seen[0].startswith("Researchly/") and "github.com/SaadKazmi/researchly" in seen[0]
    assert "mailto" not in seen[0]
    assert seen[1].endswith("; mailto:me@example.org)")


async def test_retries_5xx_with_jitter_then_succeeds():
    calls = iter([503, 502, 200])
    slept: list[float] = []
    async with client_for(lambda r: httpx.Response(next(calls), json={"ok": 1}), slept) as client:
        fetched = await client.get("https://api.example.org/x", source="t")
    assert fetched.status == 200 and fetched.json() == {"ok": 1}
    assert slept == [0.5, 1.0]  # rand 0.5 * min(60, 2**attempt)


async def test_404_and_400_are_answers_not_retries():
    slept: list[float] = []
    async with client_for(lambda r: httpx.Response(404, text="<html>"), slept) as client:
        fetched = await client.get("https://api.example.org/x", source="t")
    assert fetched.status == 404 and slept == []


async def test_gives_up_after_four_attempts():
    slept: list[float] = []
    async with client_for(lambda r: httpx.Response(500), slept) as client:
        with pytest.raises(SourceUnavailable):
            await client.get("https://api.example.org/x", source="t")
    assert len(slept) == 3


async def test_long_retry_after_raises_quota_exhausted_without_waiting():
    slept: list[float] = []
    async with client_for(lambda r: httpx.Response(429, headers={"retry-after": "3600"}), slept) as client:
        with pytest.raises(QuotaExhausted):
            await client.get("https://api.example.org/x", source="t")
    assert slept == []


async def test_transport_errors_are_retried():
    calls = iter([httpx.ConnectError("boom"), httpx.Response(200, json={})])

    def handler(request):
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    async with client_for(handler, []) as client:
        assert (await client.get("https://api.example.org/x", source="t")).status == 200


async def test_body_over_the_cap_is_refused():
    async with client_for(lambda r: httpx.Response(200, content=b"x" * 2048), []) as client:
        with pytest.raises(ResponseTooLarge):
            await client.get("https://api.example.org/x", source="t", max_bytes=1024)


async def test_redirects_are_followed_manually_and_capped():
    def handler(request):
        n = int(request.url.path.strip("/") or 0)
        return httpx.Response(302, headers={"location": f"/{n + 1}"})

    async with client_for(handler, []) as client:
        with pytest.raises(SourceUnavailable, match="redirects"):
            await client.get("https://api.example.org/0", source="t")


async def test_redirect_to_a_non_http_scheme_is_refused():
    async with client_for(lambda r: httpx.Response(302, headers={"location": "file:///etc/passwd"}), []) as client:
        with pytest.raises(SourceUnavailable, match="scheme"):
            await client.get("https://api.example.org/x", source="t")


def test_breaker_opens_after_five_failures_and_allows_one_probe():
    now = [0.0]
    breaker = Breaker(clock=lambda: now[0])
    for _ in range(5):
        breaker.before("t")
        breaker.failure()
    with pytest.raises(SourceUnavailable):
        breaker.before("t")
    now[0] = 61.0
    breaker.before("t")  # the half-open probe
    with pytest.raises(SourceUnavailable):
        breaker.before("t")  # only one probe at a time
    breaker.failure()
    now[0] = 61.0 + 119.0
    with pytest.raises(SourceUnavailable):
        breaker.before("t")  # cool-down doubled to 120 s
    now[0] = 61.0 + 121.0
    breaker.before("t")
    breaker.success()
    breaker.before("t")  # closed again


def test_only_the_probe_releases_the_probe():
    now = [0.0]
    breaker = Breaker(clock=lambda: now[0])
    assert breaker.before("t") is False  # an ordinary call, started before the breaker opened
    for _ in range(5):
        breaker.failure()
    now[0] = 61.0
    assert breaker.before("t") is True  # the half-open probe
    breaker.release(False)  # the older call finishes
    with pytest.raises(SourceUnavailable):
        breaker.before("t")  # the probe is still held
    breaker.release(True)
    assert breaker.before("t") is True


async def test_buckets_and_hosts_are_paced_separately():
    async with PoliteClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        limits={"api.example.org": (10.0, 1), "api.example.org/list": (0.0, 1), "default": (0.0, 1)},
    ) as client:
        assert client.limit_for("https://api.example.org/x", None).interval == 10.0
        assert client.limit_for("https://api.example.org/x", "api.example.org/list").interval == 0.0
        assert client.limit_for("https://other.example.org/x", None).interval == 0.0


async def test_breaker_probe_is_released_when_the_probe_raises_a_non_transport_error():
    now = [0.0]
    big = [False]

    def handler(request):
        return httpx.Response(200, content=b"x" * 2048) if big[0] else httpx.Response(500)

    async def sleep(seconds: float) -> None:
        pass

    async with PoliteClient(
        transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: now[0], limits={"default": (0.0, 1)}
    ) as client:
        for _ in range(5):
            with pytest.raises(SourceUnavailable):
                await client.get("https://api.example.org/x", source="t")
        with pytest.raises(SourceUnavailable, match="circuit"):
            await client.get("https://api.example.org/x", source="t")
        now[0] = 61.0
        big[0] = True
        with pytest.raises(ResponseTooLarge):
            await client.get("https://api.example.org/x", source="t", max_bytes=1024)
        assert (await client.get("https://api.example.org/x", source="t")).status == 200


async def test_host_limit_releases_its_permit_when_cancelled_while_waiting():
    limit = HostLimit(10.0, 1, clock=lambda: 0.0)
    async with limit:
        pass  # next slot is 10 s away
    task = asyncio.create_task(limit.__aenter__())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not limit._sem.locked()


async def test_host_limit_spaces_slots_by_the_interval():
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    limit = HostLimit(2.0, 1, sleep=sleep, clock=lambda: 0.0)
    for _ in range(3):
        async with limit:
            pass
    assert slept == [2.0, 4.0]


async def test_long_retry_after_blocks_the_host_for_other_sources():
    slept: list[float] = []
    requests: list[str] = []

    def handler(request):
        requests.append("x")
        return httpx.Response(429, headers={"retry-after": "3600"})

    async with client_for(handler, slept) as client:
        with pytest.raises(QuotaExhausted):
            await client.get("https://api.example.org/x", source="t")
        with pytest.raises(QuotaExhausted):
            await client.get("https://api.example.org/y", source="u")
    assert slept == [] and len(requests) == 1


async def test_a_huge_retry_after_is_clamped():
    async with client_for(lambda r: httpx.Response(429, headers={"retry-after": "99999999"}), []) as client:
        with pytest.raises(QuotaExhausted):
            await client.get("https://api.example.org/x", source="t")
        limit = client.limit_for("https://api.example.org/x", None)
        assert limit.blocked_until <= time.monotonic() + http.MAX_BLOCK


async def test_short_retry_after_is_waited_out_instead_of_jitter():
    calls = iter([503, 200])
    slept: list[float] = []

    def handler(request):
        status = next(calls)
        return httpx.Response(status, headers={"retry-after": "5"} if status == 503 else {})

    async with client_for(handler, slept) as client:
        assert (await client.get("https://api.example.org/x", source="t")).status == 200
    assert len(slept) == 1 and 4.5 <= slept[0] <= 5.0


async def test_retry_after_on_the_final_attempt_keeps_the_host_deferred():
    async with client_for(lambda r: httpx.Response(503, headers={"retry-after": "30"}), []) as client:
        with pytest.raises(SourceUnavailable):
            await client.get("https://api.example.org/x", source="t")
        ahead = client.limit_for("https://api.example.org/x", None)._next_at - time.monotonic()
    assert 25 <= ahead <= 30


async def test_a_hung_request_times_out_and_is_retried(monkeypatch):
    monkeypatch.setattr(http, "TOTAL_TIMEOUT", 0.05)

    async def handler(request):
        await asyncio.sleep(3600)

    async with client_for(handler, []) as client:
        with pytest.raises(SourceUnavailable):
            await client.get("https://api.example.org/x", source="t")


async def test_if_none_match_is_dropped_on_redirect_hops():
    seen: list[str | None] = []

    class FakeCache:
        async def get(self, key):
            return SimpleNamespace(fresh=False, etag='"e"', fetched=http.Fetched(200, {}, b"old", "u"))

        async def refresh(self, key, ttl):
            pass

    def handler(request):
        seen.append(request.headers.get("if-none-match"))
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        return httpx.Response(304)

    async with PoliteClient(
        transport=httpx.MockTransport(handler),
        cache=FakeCache(),  # type: ignore[arg-type]
        limits={"default": (0.0, 1)},
    ) as client:
        fetched = await client.get("https://api.example.org/a", source="t", cache_key="k")
    assert seen == ['"e"', None] and fetched.body == b"old"


async def test_a_first_url_with_a_bad_scheme_is_refused():
    async with client_for(lambda r: httpx.Response(200), []) as client:
        with pytest.raises(SourceUnavailable, match="scheme"):
            await client.get("file:///etc/passwd", source="t")


async def test_accept_rejects_other_content_types_before_reading_the_body():
    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<html>landing</html>"
        )

    async with client_for(handler, []) as client:
        with pytest.raises(UnwantedContent, match="text/html"):
            await client.get("https://pub.example.org/paper.pdf", source="t", accept=frozenset({"application/pdf"}))


async def test_accept_matches_the_media_type_ignoring_parameters():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "Application/PDF; qs=0.9"}, content=b"%PDF-1.7")

    async with client_for(handler, []) as client:
        fetched = await client.get("https://pub.example.org/p.pdf", source="t", accept=frozenset({"application/pdf"}))
    assert fetched.body == b"%PDF-1.7"


async def test_accept_ignores_non_200_answers():
    async with client_for(lambda r: httpx.Response(404, headers={"content-type": "text/html"}), []) as client:
        fetched = await client.get("https://pub.example.org/p.pdf", source="t", accept=frozenset({"application/pdf"}))
    assert fetched.status == 404


def redirecting(hops: dict[str, str]):
    def handler(request):
        target = hops.get(str(request.url))
        if target:
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, content=b"ok")

    return handler


async def test_a_cross_host_hop_waits_for_its_own_host():
    slept: list[float] = []
    now = [0.0]

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    hops = {
        "https://doi.example.org/1": "https://pub.example.org/1",
        "https://doi.example.org/2": "https://pub.example.org/2",
    }
    client = PoliteClient(
        transport=httpx.MockTransport(redirecting(hops)),
        sleep=sleep,
        clock=lambda: now[0],
        limits={"doi.example.org": (0.0, 1), "pub.example.org": (5.0, 1), "default": (0.0, 1)},
    )
    async with client:
        await client.get("https://doi.example.org/1", source="t")
        await client.get("https://doi.example.org/2", source="t")
    assert slept == [5.0]  # the second hop to pub.example.org waited for its 5 s slot


async def test_a_redirect_back_to_a_held_host_does_not_deadlock():
    hops = {"https://a.example.org/1": "https://b.example.org/1", "https://b.example.org/1": "https://a.example.org/2"}
    client = PoliteClient(
        transport=httpx.MockTransport(redirecting(hops)),
        limits={"a.example.org": (0.0, 1), "b.example.org": (0.0, 1), "default": (0.0, 1)},
    )
    async with client:
        fetched = await asyncio.wait_for(client.get("https://a.example.org/1", source="t"), timeout=2)
    assert fetched.body == b"ok"


async def test_unknown_hosts_sharing_the_default_limit_do_not_deadlock_on_a_hop():
    hops = {"https://x.example.org/1": "https://y.example.org/1"}
    client = PoliteClient(transport=httpx.MockTransport(redirecting(hops)), limits={"default": (0.0, 1)})
    async with client:
        fetched = await asyncio.wait_for(client.get("https://x.example.org/1", source="t"), timeout=2)
    assert fetched.body == b"ok"


def test_crawl_delay_raises_but_never_lowers_a_host_interval():
    client = PoliteClient(limits={"arxiv.org": (15.0, 1), "default": (1.0, 1)})
    client.set_crawl_delay("pub.example.org", 10.0)
    assert client.limit_for("https://pub.example.org/x", None).interval == 10.0
    client.set_crawl_delay("arxiv.org", 2.0)
    assert client.limit_for("https://arxiv.org/html/1", None).interval == 15.0
    client.set_crawl_delay("quick.example.org", 0.5)  # below the default: the default still applies
    assert client.limit_for("https://quick.example.org/x", None).interval == 1.0


def test_limits_file_paces_arxiv_org_per_robots_crawl_delay():
    assert http._load_limits()["arxiv.org"] == (15.0, 1)


async def test_requests_crossing_hosts_in_opposite_directions_do_not_deadlock():
    arrived = asyncio.Event()
    first_hops = []

    async def handler(request):
        if request.url.path == "/start":  # both first hops must be in flight at once
            first_hops.append(request.url.host)
            if len(first_hops) == 2:
                arrived.set()
            await arrived.wait()
            other = "b.example.org" if request.url.host == "a.example.org" else "a.example.org"
            return httpx.Response(302, headers={"location": f"https://{other}/end"})
        return httpx.Response(200, content=b"ok")

    client = PoliteClient(
        transport=httpx.MockTransport(handler),
        limits={"a.example.org": (0.0, 1), "b.example.org": (0.0, 1), "default": (0.0, 1)},
    )
    async with client:
        first, second = await asyncio.wait_for(
            asyncio.gather(
                client.get("https://a.example.org/start", source="t"),
                client.get("https://b.example.org/start", source="t"),
            ),
            timeout=2,
        )
    assert first.body == second.body == b"ok"


async def test_a_quota_block_survives_a_crawl_delay():
    client = PoliteClient(limits={"default": (1.0, 1)})
    client.limit_for("https://pub.example.org/x", None).blocked_until = 99.0
    client.set_crawl_delay("pub.example.org", 10.0)
    limit = client.limit_for("https://pub.example.org/x", None)
    assert limit.interval == 10.0 and limit.blocked_until == 99.0


async def test_unwanted_content_does_not_open_the_breaker():
    kind = ["text/html"]

    def handler(request):
        return httpx.Response(200, headers={"content-type": kind[0]}, content=b"x")

    async with client_for(handler, []) as client:
        for _ in range(6):
            with pytest.raises(UnwantedContent):
                await client.get("https://pub.example.org/p.pdf", source="t", accept=frozenset({"application/pdf"}))
        kind[0] = "application/pdf"
        fetched = await client.get("https://pub.example.org/p.pdf", source="t", accept=frozenset({"application/pdf"}))
    assert fetched.status == 200


async def test_an_unwanted_content_hop_releases_that_hosts_permit():
    def handler(request):
        if request.url.host == "a.example.org":
            return httpx.Response(302, headers={"location": "https://b.example.org/landing"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")

    client = PoliteClient(
        transport=httpx.MockTransport(handler),
        limits={"a.example.org": (0.0, 1), "b.example.org": (0.0, 1), "default": (0.0, 1)},
    )
    async with client:
        with pytest.raises(UnwantedContent):
            await client.get("https://a.example.org/x", source="t", accept=frozenset({"application/pdf"}))
        fetched = await asyncio.wait_for(client.get("https://b.example.org/y", source="t"), 2)
    assert fetched.status == 200
