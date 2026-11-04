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
GLOBAL_UNICAST_V6 = ip_network("2000::/3")
Resolver = Callable[[str, int], Awaitable[list[str]]]


class Blocked(httpcore.ConnectError):
    pass


def check(ip: IPv4Address | IPv6Address) -> None:
    """IPv6: zoned is blocked; IPv4-mapped and 64:ff9b::/96 (DNS64) are judged by their inner IPv4 only;
    otherwise a 6to4/Teredo inner IPv4 must pass and the address must sit in 2000::/3 (`is_global` varies by patch)."""
    if isinstance(ip, IPv6Address):
        if ip.scope_id:
            raise Blocked(f"zoned address {ip}")
        if ip.ipv4_mapped:
            return check(ip.ipv4_mapped)
        if ip in NAT64:
            return check(IPv4Address(int(ip) & 0xFFFFFFFF))
        inner = ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is not None:
            check(inner)
        if ip not in GLOBAL_UNICAST_V6:
            raise Blocked(f"non-global-unicast address {ip}")
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
        try:
            with anyio.fail_after(timeout):  # DNS counts against the connect timeout
                answers = await self._resolve(host, port)
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout(f"resolving {host} timed out") from exc
        addresses = [ip_address(a) for a in answers]
        if not addresses:
            raise httpcore.ConnectError(f"no address for {host}")
        for address in addresses:
            check(address)
        last: Exception | None = None
        for address in addresses:  # all checked above; connect to each checked IP in order
            try:
                return await self._inner.connect_tcp(
                    str(address), port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        raise last  # type: ignore[misc]  # addresses is non-empty, so last is set

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
        # trust_env here only means SSL_CERT_FILE/SSL_CERT_DIR (corporate CAs); proxies stay off at the client
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)  # httpx's defaults, HTTP/1.1 only
        # ponytail: replaces httpx's private pool to inject the backend; httpx is pinned and a test checks the hook
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=True),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=GuardedBackend(resolve),
        )
