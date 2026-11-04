"""robots.txt for crawled full-text pages (spec §19.3, RFC 9309). API endpoints are not crawled and skip it."""

import time
from collections.abc import Callable
from urllib.parse import urlsplit

from protego import Protego

from researchly.connectors.base import ConnectorError
from researchly.connectors.http import PoliteClient

AGENT = "Researchly"  # the product token of our User-Agent
TTL = 24 * 3600.0  # RFC 9309 §2.4: a cached copy is good for 24 h
RETRY_UNREACHABLE = 600.0  # an unreachable file disallows for a while, then we ask again
MAX_CRAWL_DELAY = 60.0  # a slower site is skipped rather than stalling a run


class Robots:
    def __init__(self, client: PoliteClient, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._client, self._clock = client, clock
        self._rules: dict[str, tuple[float, Protego | None]] = {}  # origin -> (expires at, rules or None)

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._rules.get(origin)
        if cached is None or cached[0] <= self._clock():
            rules = await self._fetch(origin, parts.hostname or "")
            cached = (self._clock() + (TTL if rules is not None else RETRY_UNREACHABLE), rules)
            self._rules[origin] = cached
        rules = cached[1]
        if rules is None:
            return False
        delay = rules.crawl_delay(AGENT)
        if delay is not None:
            if delay > MAX_CRAWL_DELAY:
                return False
            self._client.set_crawl_delay(parts.hostname or "", float(delay))
        return rules.can_fetch(url, AGENT)

    async def _fetch(self, origin: str, host: str) -> Protego | None:
        try:
            fetched = await self._client.get(f"{origin}/robots.txt", source=host, max_bytes=500_000)
        except ConnectorError:
            return None  # unreachable (5xx after retries, network): disallow, RFC 9309 §2.3.1.4
        if fetched.status >= 500:
            return None
        if fetched.status >= 400:
            return Protego.parse("")  # unavailable: everything is allowed, RFC 9309 §2.3.1.3
        return Protego.parse(fetched.text())
