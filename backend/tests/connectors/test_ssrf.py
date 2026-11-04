from ipaddress import ip_address

import anyio
import httpcore
import pytest

from researchly.connectors.base import SourceUnavailable
from researchly.connectors.http import PoliteClient
from researchly.connectors.ssrf import Blocked, GuardedBackend, GuardedTransport, check

pytestmark = pytest.mark.anyio

BLOCKED = [
    "0.0.0.0",  # noqa: S104 - an SSRF test vector, nothing binds
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.0.1", "100.64.0.1", "169.254.169.254",
    "198.18.0.1", "224.0.0.1", "::", "::1", "::ffff:127.0.0.1", "::ffff:7f00:1", "64:ff9b::a9fe:a9fe",
    "2002:a9fe:a9fe::", "2001:0:4136:e378:8000:63bf:3fff:fdd2", "fc00::1", "fe80::1", "fe80::1%eth0", "ff02::1",
    "64:ff9b:1::a9fe:a9fe", "64:ff9b:1::7f00:1", "fec0::1", "fec0:0:0:ffff::1", "::7f00:1", "::a9fe:a9fe",
    "::ffff:0:7f00:1", "5f00::1",
]  # fmt: skip


@pytest.mark.parametrize("raw", BLOCKED)
def test_non_global_addresses_are_blocked(raw):
    with pytest.raises(Blocked):
        check(ip_address(raw))


@pytest.mark.parametrize("raw", ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"])
def test_global_addresses_pass(raw):
    check(ip_address(raw))


@pytest.mark.parametrize("raw", ["64:ff9b::5db8:d70e", "::ffff:93.184.215.14"])
def test_dns64_and_mapped_forms_are_judged_by_their_inner_address(raw):
    check(ip_address(raw))


def resolver(*answers):
    calls = iter(answers)

    async def resolve(host, port):
        return next(calls)

    return resolve


async def test_any_private_answer_blocks_the_connection():
    backend = GuardedBackend(resolver(["93.184.215.14", "10.0.0.1"]))
    with pytest.raises(Blocked):
        await backend.connect_tcp("mixed.example", 443)


async def test_dns_rebinding_cannot_swap_the_address():
    # first answer public, a second lookup would be private: the backend must connect to the checked IP only
    connected = []

    class Inner:
        async def connect_tcp(self, host, port, **kwargs):
            connected.append(host)
            raise httpcore.ConnectError("stop here")

    backend = GuardedBackend(resolver(["93.184.215.14"], ["127.0.0.1"]))
    backend._inner = Inner()  # type: ignore[assignment]
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("rebind.example", 443)
    assert connected == ["93.184.215.14"]


async def test_only_ports_80_and_443():
    with pytest.raises(Blocked):
        await GuardedBackend(resolver(["93.184.215.14"])).connect_tcp("example.com", 5432)


async def test_unix_sockets_are_refused():
    with pytest.raises(Blocked):
        await GuardedBackend().connect_unix_socket("/var/run/docker.sock")


async def test_every_checked_address_is_tried_in_order():
    tried = []

    class Inner:
        async def connect_tcp(self, host, port, **kwargs):
            tried.append(host)
            if host == "93.184.215.14":
                raise httpcore.ConnectError("down")
            return "stream"

    backend = GuardedBackend(resolver(["93.184.215.14", "93.184.215.15"]))
    backend._inner = Inner()  # type: ignore[assignment]
    assert await backend.connect_tcp("two.example", 443) == "stream"
    assert tried == ["93.184.215.14", "93.184.215.15"]


async def test_all_addresses_failing_reraises_the_last_error():
    class Inner:
        async def connect_tcp(self, host, port, **kwargs):
            raise httpcore.ConnectError(f"down {host}")

    backend = GuardedBackend(resolver(["93.184.215.14", "93.184.215.15"]))
    backend._inner = Inner()  # type: ignore[assignment]
    with pytest.raises(httpcore.ConnectError, match="93.184.215.15"):
        await backend.connect_tcp("two.example", 443)


async def test_a_slow_resolver_hits_the_connect_timeout():
    async def slow(host, port):
        await anyio.sleep(5)
        return ["93.184.215.14"]

    with pytest.raises(httpcore.ConnectTimeout):
        await GuardedBackend(slow).connect_tcp("slow.example", 443, timeout=0.05)


class RecordingBackend:
    """Stands in for the real network: records hosts, serves a canned redirect."""

    def __init__(self, location):
        self.hosts = []
        self._location = location

    async def connect_tcp(self, host, port, **kwargs):
        self.hosts.append(host)
        return await httpcore.AsyncMockBackend(
            [b"HTTP/1.1 302 Found\r\n", f"Location: {self._location}\r\n".encode(), b"Content-Length: 0\r\n\r\n"]
        ).connect_tcp(host, port)


@pytest.mark.parametrize("location", ["http://127.0.0.1/admin", "http://public.example:8080/"])
async def test_a_redirect_to_an_internal_target_is_refused(location):
    async def resolve(host, port):
        return {"public.example": ["93.184.215.14"], "127.0.0.1": ["127.0.0.1"]}[host]

    transport = GuardedTransport(resolve)
    inner = RecordingBackend(location)
    transport._pool._network_backend._inner = inner  # type: ignore[attr-defined]

    async def sleep(seconds: float) -> None:
        pass

    client = PoliteClient(transport=transport, limits={"default": (0.0, 1)}, sleep=sleep, rand=lambda: 0.5)
    async with client:
        with pytest.raises(SourceUnavailable, match="refused"):
            await client.get("http://public.example/", source="t")
    assert inner.hosts == ["93.184.215.14"]


@pytest.mark.parametrize(
    "url", ["http://2130706433/", "http://127.1/", "http://0x7f.1/", "http://017700000001/", "http://localhost./"]
)
async def test_alternative_loopback_spellings_never_connect(url):
    async with guarded_client([]) as client:
        with pytest.raises(SourceUnavailable):  # "refused" where it resolves, "no answer" where it does not
            await client.get(url, source="t")


def guarded_client(slept):
    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return PoliteClient(transport=GuardedTransport(), sleep=sleep, rand=lambda: 0.5, limits={"default": (0.0, 1)})


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://[::1]/", "http://169.254.169.254/"])
async def test_client_refuses_internal_targets(url):
    slept: list[float] = []
    async with guarded_client(slept) as client:
        with pytest.raises(SourceUnavailable, match="refused"):
            await client.get(url, source="t")
    assert slept == []  # a refusal is not retried


async def test_refusals_do_not_open_the_breaker():
    async with guarded_client([]) as client:
        for _ in range(6):
            with pytest.raises(SourceUnavailable, match="refused"):
                await client.get("http://127.0.0.1/", source="t")


def test_the_default_client_uses_the_guarded_backend():
    client = PoliteClient()
    transport = client._http._transport  # contract test: httpx upgrades must keep the private hook working
    assert isinstance(transport, GuardedTransport)
    assert isinstance(transport._pool._network_backend, GuardedBackend)
