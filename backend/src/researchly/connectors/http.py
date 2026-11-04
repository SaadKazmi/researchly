"""The only way Researchly talks to the internet (AGENTS.md; spec §8.2, §19.3).

Per-host pacing, retries with Retry-After, a circuit breaker per source, a body size cap, manual redirects and an
SSRF-guarded transport. Limits are in-process: one app process does all outbound HTTP (spec §4.2).
"""

import asyncio
import contextlib
import email.utils
import json
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
import yaml

from researchly.connectors.base import QuotaExhausted, ResponseTooLarge, SourceUnavailable, UnwantedContent
from researchly.connectors.ssrf import Blocked

if TYPE_CHECKING:
    from researchly.connectors.cache import HttpCache

TTL_METADATA = timedelta(days=60)
TTL_COUNTS = timedelta(days=7)
TTL_SEARCH = timedelta(hours=24)

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
LONG_WAIT = 120.0  # a longer Retry-After is a quota, not a hiccup
MAX_BLOCK = 3600.0  # cap on how long a server's Retry-After can lock a host out
TOTAL_TIMEOUT = 120.0  # per hop of network time (send + body), whatever the server drips
MAX_REDIRECTS = 5
LIMITS_FILE = Path(__file__).with_name("limits.yaml")


@dataclass(frozen=True)
class Fetched:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str

    def json(self) -> Any:
        return json.loads(self.body)

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")  # explicit: Windows defaults to cp1252 (HF emoji)


def retry_after(value: str | None) -> float | None:
    """RFC 9110 §10.2.3: delay-seconds or an HTTP-date."""
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, (email.utils.parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


class HostLimit:
    """One request per `interval` seconds, at most `concurrency` in flight; `defer` pushes the next slot."""

    def __init__(
        self,
        interval: float,
        concurrency: int,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interval = interval
        self.blocked_until = 0.0  # a quota-length Retry-After: callers fail fast until then
        self._sem = asyncio.Semaphore(concurrency)
        self._next_at = 0.0
        self._sleep, self._clock = sleep, clock

    def defer(self, seconds: float) -> None:
        self._next_at = max(self._next_at, self._clock() + min(seconds, LONG_WAIT))

    async def __aenter__(self) -> None:
        await self._sem.acquire()
        try:
            now = self._clock()
            wait = self._next_at - now
            self._next_at = max(now, self._next_at) + self.interval  # reserve the slot before sleeping
            if wait > 0:
                await self._sleep(wait)
        except BaseException:
            self._sem.release()  # cancelled while waiting: never leak the permit
            raise

    async def __aexit__(self, *exc: object) -> None:
        self._sem.release()


class Breaker:
    """Opens after `threshold` consecutive failed calls; one half-open probe after the cool-down."""

    def __init__(
        self,
        threshold: int = 5,
        cooldown: float = 60.0,
        max_cooldown: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold, self._base, self._max, self._clock = threshold, cooldown, max_cooldown, clock
        self._cooldown = cooldown
        self._failures = 0
        self._open_until = 0.0
        self._probing = False

    def before(self, source: str) -> bool:
        """Admit a call; True when it is the half-open probe."""
        if self._failures < self._threshold:
            return False
        if self._clock() < self._open_until or self._probing:
            raise SourceUnavailable(source, "circuit open after repeated failures")
        self._probing = True
        return True

    def release(self, probe: bool) -> None:
        """Give up a half-open probe without counting an outcome (cancellation, unexpected exit)."""
        if probe:
            self._probing = False

    def success(self) -> None:
        self._failures, self._cooldown, self._probing = 0, self._base, False

    def failure(self, until: float | None = None) -> None:
        self._failures += 1
        self._probing = False
        if self._failures >= self._threshold:
            self._open_until = max(until or 0.0, self._clock() + self._cooldown)
            self._cooldown = min(self._cooldown * 2, self._max)


def _load_limits() -> dict[str, tuple[float, int]]:
    raw = yaml.safe_load(LIMITS_FILE.read_text(encoding="utf-8"))
    return {key: (float(v[0]), int(v[1])) for key, v in raw.items()}


class PoliteClient:
    def __init__(
        self,
        *,
        mailto: str | None = None,
        cache: "HttpCache | None" = None,
        transport: httpx.AsyncBaseTransport | None = None,
        limits: dict[str, tuple[float, int]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        from researchly.connectors.ssrf import GuardedTransport

        agent = f"Researchly/{version('researchly')} (+https://github.com/SaadKazmi/researchly"
        agent += f"; mailto:{mailto})" if mailto else ")"  # only the user's own address; never a project email
        self._http = httpx.AsyncClient(
            transport=transport or GuardedTransport(),
            headers={"User-Agent": agent},
            follow_redirects=False,  # every hop is checked below
            trust_env=False,  # an env proxy would bypass the SSRF check
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self._cache = cache
        self._limit_specs = limits or _load_limits()
        self._limits: dict[str, HostLimit] = {}
        self._breakers: defaultdict[str, Breaker] = defaultdict(lambda: Breaker(clock=clock))
        self._sleep, self._rand, self._clock = sleep, rand, clock

    async def __aenter__(self) -> "PoliteClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _limit_key(self, url: str, bucket: str | None) -> str:
        key = bucket or urlsplit(url).hostname or ""
        return key if key in self._limit_specs else "default"

    def limit_for(self, url: str, bucket: str | None) -> HostLimit:
        key = self._limit_key(url, bucket)
        if key not in self._limits:
            self._limits[key] = HostLimit(*self._limit_specs[key], sleep=self._sleep, clock=self._clock)
        return self._limits[key]

    def set_crawl_delay(self, host: str, seconds: float) -> None:
        """Honour a robots.txt Crawl-delay: never faster than the site asks, never slower than we already are."""
        interval, _ = self._limit_specs.get(host, self._limit_specs["default"])
        if seconds > interval:
            old = self._limits.get(host) or self._limits.get("default")
            self._limit_specs[host] = (seconds, 1)
            new = self._limits[host] = HostLimit(seconds, 1, sleep=self._sleep, clock=self._clock)
            if old:  # keep a quota block and the pending slot
                new.blocked_until = old.blocked_until
                new._next_at = max(old._next_at, self._clock() + seconds)

    async def get(
        self,
        url: str,
        *,
        source: str,
        params: Mapping[str, str | int] | None = None,
        bucket: str | None = None,
        cache_key: str | None = None,
        ttl: timedelta | None = None,
        max_bytes: int = 5_000_000,
        accept: frozenset[str] | None = None,
    ) -> Fetched:
        if urlsplit(url).scheme not in ("http", "https"):
            raise SourceUnavailable(source, f"disallowed scheme: {urlsplit(url).scheme or url}")
        cached = await self._cache.get(cache_key) if self._cache and cache_key else None
        if cached and cached.fresh:
            return cached.fetched
        headers = {"If-None-Match": cached.etag} if cached and cached.etag else {}
        limit = self.limit_for(url, bucket)
        if limit.blocked_until > self._clock():
            raise QuotaExhausted(source, f"{urlsplit(url).hostname} is rate limited; try again later")
        breaker = self._breakers[source]
        probe = breaker.before(source)
        try:
            for attempt in range(MAX_ATTEMPTS):
                wait: float | None = None
                try:
                    fetched = await self._send(url, params, headers, max_bytes, source, accept, bucket)
                except (httpx.TransportError, TimeoutError) as exc:
                    if isinstance(exc.__cause__, Blocked):  # a refusal, not an outage: no retry, no breaker strike
                        raise SourceUnavailable(source, f"refused: {exc.__cause__}") from exc.__cause__
                    fetched = None
                except Exception:  # the server answered (too big, bad redirect, bad body): not an outage
                    breaker.success()
                    raise
                if fetched is not None and fetched.status not in RETRY_STATUS:
                    breaker.success()
                    if cached and fetched.status == 304:
                        if self._cache and cache_key and ttl:
                            await self._cache.refresh(cache_key, ttl)
                        return cached.fetched
                    if self._cache and cache_key and ttl and fetched.status == 200:
                        await self._cache.put(cache_key, fetched, ttl)
                    return fetched
                if fetched is not None:
                    wait = retry_after(fetched.headers.get("retry-after"))
                    if wait is not None and wait > LONG_WAIT:
                        block = min(wait, MAX_BLOCK)
                        limit.blocked_until = self._clock() + block  # fail fast, never sleep
                        breaker.failure(until=self._clock() + block)
                        raise QuotaExhausted(source, f"rate limited; retry after {wait:.0f} s")
                if wait is not None:
                    limit.defer(wait)  # the next `_send` waits it out
                if attempt == MAX_ATTEMPTS - 1:
                    break
                if wait is None:
                    await self._sleep(self._rand() * min(60.0, 2.0**attempt))
            breaker.failure()
            raise SourceUnavailable(source, f"no answer from {urlsplit(url).hostname} after {MAX_ATTEMPTS} attempts")
        finally:
            breaker.release(probe)  # a half-open probe is never left held (cancellation, unexpected exit)

    async def _send(
        self,
        url: str,
        params: Mapping[str, str | int] | None,
        headers: dict[str, str],
        max_bytes: int,
        source: str,
        accept: frozenset[str] | None,
        bucket: str | None,
    ) -> Fetched:
        """Follow redirects holding one host's permit at a time (two requests crossing hosts must not deadlock)."""
        request = self._http.build_request("GET", url, params=params, headers=headers)
        stack, current = contextlib.AsyncExitStack(), None
        try:
            for hop in range(MAX_REDIRECTS + 1):
                hop_bucket = bucket if hop == 0 else None
                key = self._limit_key(str(request.url), hop_bucket)
                if key != current:  # a new host: free the old permit, then wait for the new host's turn
                    await stack.aclose()
                    stack, current = contextlib.AsyncExitStack(), key
                    await stack.enter_async_context(self.limit_for(str(request.url), hop_bucket))
                async with asyncio.timeout(TOTAL_TIMEOUT):  # network time only, never the pacing wait
                    result = await self._hop(request, source, accept, max_bytes)
                if isinstance(result, Fetched):
                    return result
                request = result
            raise SourceUnavailable(source, f"more than {MAX_REDIRECTS} redirects")
        finally:
            await stack.aclose()

    async def _hop(
        self, request: httpx.Request, source: str, accept: frozenset[str] | None, max_bytes: int
    ) -> "Fetched | httpx.Request":
        """One request: the answer, or the next request when it redirects."""
        try:
            response = await self._http.send(request, stream=True)
        except httpx.UnsupportedProtocol as exc:  # a redirect to file://, ftp:// ... refused while building
            raise SourceUnavailable(source, "disallowed redirect scheme") from exc
        try:
            if response.next_request is not None:  # set for every redirect because follow_redirects=False
                nxt = response.next_request
                nxt.headers.pop("if-none-match", None)  # a validator for the first URL, not the hop
                if nxt.url.scheme not in ("http", "https"):  # a mock transport would not refuse it
                    raise SourceUnavailable(source, f"disallowed scheme: {nxt.url.scheme}")
                return nxt
            if accept is not None and response.status_code == 200:
                media = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if media not in accept:
                    raise UnwantedContent(source, f"content type {media or 'missing'}")
            try:
                declared = int(response.headers.get("content-length") or 0)
            except ValueError:
                declared = 0  # malformed: ignore it, the streamed cap still applies
            if declared > max_bytes:
                raise ResponseTooLarge(source, f"{declared} bytes > {max_bytes}")
            body = bytearray()
            async for chunk in response.aiter_bytes():  # decoded bytes, so a gzip bomb is capped too
                body += chunk
                if len(body) > max_bytes:
                    raise ResponseTooLarge(source, f"body > {max_bytes} bytes")
            return Fetched(
                response.status_code,
                {k.lower(): v for k, v in response.headers.items()},
                bytes(body),
                str(response.url),
            )
        finally:
            await response.aclose()
