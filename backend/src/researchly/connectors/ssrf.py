"""SSRF-guarded transport; the real guard arrives in a later task."""

import httpx


class GuardedTransport(httpx.AsyncHTTPTransport):
    pass
