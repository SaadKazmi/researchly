"""HTTP client for local sidecars (SearXNG now, GROBID at step 4): the only HTTP outside PoliteClient (ADR 0010).

Every request goes to the one origin the CLI configured (`RESEARCHLY_SEARXNG_URL`), and callers pass relative paths,
so no caller, page or model can pick the target. No redirects, no env proxy, a size cap and short timeouts. It is not
loopback-only: in Docker mode `searxng` resolves to a private compose-network address, which the SSRF guard refuses.
"""

import asyncio
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

from researchly.connectors.base import ResponseTooLarge, SourceUnavailable
from researchly.connectors.http import Fetched, HostLimit


class SidecarClient:
    def __init__(
        self,
        base_url: str,
        *,
        source: str,
        timeout: float = 15.0,
        max_bytes: int = 2_000_000,
        limit: HostLimit | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError(f"a sidecar URL must be a bare http(s) origin, got {base_url!r}")
        self.source = source
        self._timeout, self._max_bytes = timeout, max_bytes
        self._limit = limit or HostLimit(1.0, 2)  # one query a second, two in flight: engines ban fast clients
        self._http = httpx.AsyncClient(
            base_url=f"{parts.scheme}://{parts.netloc}",
            transport=transport,
            headers={"X-Real-IP": "127.0.0.1"},  # SearXNG's bot detection logs an error per request without it
            follow_redirects=False,
            trust_env=False,  # an env proxy would send local traffic elsewhere
            timeout=httpx.Timeout(timeout, connect=2.0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Fetched:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError(f"sidecar paths are relative to the configured origin, got {path!r}")
        request = self._http.build_request("GET", path, params=params)
        base = self._http.base_url
        if (request.url.scheme, request.url.host, request.url.port) != (base.scheme, base.host, base.port):
            raise ValueError(f"{path!r} is not relative to the configured origin")
        body = bytearray()
        async with self._limit:
            try:
                async with asyncio.timeout(self._timeout):
                    response = await self._http.send(request, stream=True)
                    try:
                        if response.is_redirect:
                            raise SourceUnavailable(self.source, f"unexpected redirect (HTTP {response.status_code})")
                        async for chunk in response.aiter_bytes():
                            body += chunk
                            if len(body) > self._max_bytes:
                                raise ResponseTooLarge(self.source, f"over {self._max_bytes} bytes")
                    finally:
                        await response.aclose()
            except (httpx.HTTPError, TimeoutError) as exc:
                raise SourceUnavailable(self.source, f"unreachable: {type(exc).__name__}") from exc
        headers = {k.lower(): v for k, v in response.headers.items()}  # the same shape PoliteClient returns
        return Fetched(response.status_code, headers, bytes(body), str(request.url))
