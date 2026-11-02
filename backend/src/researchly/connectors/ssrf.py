"""SSRF-safe connections (spec §19.3): resolve once, check every address, connect to the checked IP.

URL, Host header, SNI and certificate checks stay those of the original host, because httpcore passes the origin host to
`start_tls`. `GuardedTransport` subclasses `httpx.AsyncHTTPTransport` so vcrpy cassettes still intercept it.
"""

import socket
from collections.abc import Awaitable, Callable, Iterable
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

import anyio
import httpcore
import httpx

NAT64 = ip_network("64:ff9b::/96")
Resolver = Callable[[str, int], Awaitable[list[str]]]


class Blocked(httpcore.ConnectError):
    pass


def check(ip: IPv4Address | IPv6Address) -> None:
    """`is_private` alone misses CGNAT and NAT64, and 3.12.0's `ipaddress` misreads 6to4, so unwrap explicitly."""
    if isinstance(ip, IPv6Address):
        if ip.scope_id:
            raise Blocked(f"zoned address {ip}")
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is None and ip in NAT64:
            inner = IPv4Address(int(ip) & 0xFFFFFFFF)
        if inner is not None:
            check(inner)
    if not ip.is_global or ip.is_multicast:
        raise Blocked(f"non-public address {ip}")


async def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpcore.ConnectError(f"cannot resolve {host}: {exc}") from exc
    return [str(info[4][0]) for info in infos]


class GuardedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, resolve: Resolver | None = None) -> None:
        self._resolve = resolve or _resolve
        # pyright sees httpcore's no-anyio fallback class; at runtime this is the real AnyIOBackend
        self._inner: httpcore.AsyncNetworkBackend = httpcore.AnyIOBackend()  # type: ignore[assignment]

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - signature fixed by httpcore.AsyncNetworkBackend
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if port not in (80, 443):
            raise Blocked(f"port {port} is not allowed")
        addresses = [ip_address(a) for a in await self._resolve(host, port)]
        if not addresses:
            raise httpcore.ConnectError(f"no address for {host}")
        for address in addresses:
            check(address)
        return await self._inner.connect_tcp(
            str(addresses[0]), port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - signature fixed by httpcore.AsyncNetworkBackend
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise Blocked("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, resolve: Resolver | None = None) -> None:
        super().__init__(trust_env=False)
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)  # httpx's defaults, HTTP/1.1 only
        # ponytail: replaces httpx's private pool to inject the backend; httpx is pinned and a test checks the hook
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=GuardedBackend(resolve),
        )
