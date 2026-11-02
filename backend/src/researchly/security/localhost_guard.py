"""Localhost guard (spec §4.3, ADR 0003): Host allowlist, same-origin only, per-install token.

The token arrives in the `x-researchly-token` header (CLI, MCP, tests) or in an HttpOnly cookie that the one-time
login link `/?token=<t>` printed by `researchly start` sets (browsers). GET/HEAD outside /api and /mcp is public:
the SPA's HTML and assets carry no data.
"""

import hmac
from urllib.parse import parse_qsl, urlencode, urlsplit

from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

TOKEN_HEADER = "x-researchly-token"  # noqa: S105 (header name, not a secret)
TOKEN_COOKIE = "researchly_token"  # noqa: S105 (cookie name, not a secret)
COOKIE_MAX_AGE = 400 * 24 * 3600  # Chrome's cap; the token never rotates
_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PRIVATE = {"api", "mcp"}  # first path segments that always need the token
_FETCH_SITES = {"same-origin", "none"}  # "none": the user typed or bookmarked the URL


def _private(path: str) -> bool:
    return path.lstrip("/").split("/", 1)[0] in _PRIVATE


def _cookies(header: str, name: str) -> list[str]:
    """Every value of the named cookie: another server on 127.0.0.1 can plant a same-named one that sorts first."""
    pairs = (part.strip().partition("=") for part in header.split(";"))
    return [value for key, _, value in pairs if key == name]


class LocalhostGuard:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        origin = headers.get("origin")
        try:
            host_ok = urlsplit(f"//{host}").hostname in _HOSTS
            origin_ok = origin is None or urlsplit(origin).netloc == host
        except ValueError:  # a malformed Host or Origin (e.g. "[") fails closed
            host_ok = origin_ok = False
        if not host_ok:
            await _deny(scope, receive, send, "host not allowed", 403)
            return
        if not origin_ok:
            await _deny(scope, receive, send, "cross-origin request rejected", 403)
            return
        public = scope["type"] == "http" and scope["method"] in ("GET", "HEAD") and not _private(scope["path"])
        query = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        if public and scope["method"] == "GET" and any(key == "token" for key, _ in query):
            raw = scope.get("raw_path")
            path = raw.decode("latin-1") if raw else scope["path"]
            await self._login(path, query)(scope, receive, send)
            return
        if not public and not self._authenticated(headers):
            await _deny(scope, receive, send, "missing or invalid token", 401)
            return
        await self.app(scope, receive, send)

    def _valid(self, value: str) -> bool:
        return hmac.compare_digest(value.encode(), self.token.encode())

    def _authenticated(self, headers: dict[str, str]) -> bool:
        if self._valid(headers.get(TOKEN_HEADER, "")):
            return True
        # a cookie rides along on every request to 127.0.0.1, whatever the port: only trust it from our own pages
        same_origin = headers.get("sec-fetch-site") in _FETCH_SITES
        return same_origin and any(self._valid(v) for v in _cookies(headers.get("cookie", ""), TOKEN_COOKIE))

    def _login(self, path: str, query: list[tuple[str, str]]) -> Response:
        """Swap the one-time `?token=` link for the cookie, then reload the same page without the token."""
        given = next(value for key, value in query if key == "token")
        if not self._valid(given):
            return JSONResponse({"detail": "missing or invalid token"}, status_code=401)
        rest = urlencode([(k, v) for k, v in query if k != "token"])
        target = "/" + path.lstrip("/")  # "//evil.example" would be a protocol-relative redirect
        response = RedirectResponse(
            target + (f"?{rest}" if rest else ""),
            status_code=303,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
        response.set_cookie(TOKEN_COOKIE, given, max_age=COOKIE_MAX_AGE, httponly=True, samesite="strict")
        return response


async def _deny(scope: Scope, receive: Receive, send: Send, detail: str, status: int) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
    else:
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
