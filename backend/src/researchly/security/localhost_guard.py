"""Localhost guard (spec §4.3): Host allowlist, same-origin only, per-install token."""

import hmac
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

TOKEN_HEADER = "x-researchly-token"  # noqa: S105 (header name, not a secret)
_HOSTS = {"localhost", "127.0.0.1", "::1"}
_OPEN_PATHS = {"/health"}


class LocalhostGuard:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            error = self._check(scope)
            if error is not None:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse({"detail": error}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _check(self, scope: Scope) -> str | None:
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        if urlsplit(f"//{host}").hostname not in _HOSTS:
            return "host not allowed"
        origin = headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != host:
            return "cross-origin request rejected"
        if scope["path"] in _OPEN_PATHS:
            return None
        if not hmac.compare_digest(headers.get(TOKEN_HEADER, "").encode(), self.token.encode()):
            return "missing or invalid token"
        return None
