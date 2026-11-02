from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from researchly.connectors.base import QuotaExhausted, ResponseTooLarge, SourceUnavailable
from researchly.connectors.http import Breaker, PoliteClient, retry_after

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


async def test_buckets_and_hosts_are_paced_separately():
    async with PoliteClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        limits={"api.example.org": (10.0, 1), "api.example.org/list": (0.0, 1), "default": (0.0, 1)},
    ) as client:
        assert client.limit_for("https://api.example.org/x", None).interval == 10.0
        assert client.limit_for("https://api.example.org/x", "api.example.org/list").interval == 0.0
        assert client.limit_for("https://other.example.org/x", None).interval == 0.0
