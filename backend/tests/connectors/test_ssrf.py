from ipaddress import ip_address

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
]  # fmt: skip


@pytest.mark.parametrize("raw", BLOCKED)
def test_non_global_addresses_are_blocked(raw):
    with pytest.raises(Blocked):
        check(ip_address(raw))


@pytest.mark.parametrize("raw", ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"])
def test_global_addresses_pass(raw):
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
