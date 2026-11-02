"""Serve the built SPA (spec §18): index.html for client-side routes, immutable hashed assets, security headers."""

import os
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

WEB_DIR = Path(__file__).parent / "web"  # `pnpm -C frontend build` writes the SPA here
BUILD_HINT = "run `pnpm -C frontend install` and `pnpm -C frontend build`"
_NO_FALLBACK = {"api", "mcp", "assets"}  # unknown API paths stay JSON 404s; a missing asset is a real 404
_HEADERS = {
    # 'unsafe-inline' styles: Base UI positions popups with style attributes
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path.split(os.sep, 1)[0] in _NO_FALLBACK:
                raise
            response = await super().get_response("index.html", scope)
        immutable = path.startswith("assets" + os.sep) and response.status_code in (200, 304)  # Vite hashes these names
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if immutable else "no-cache"
        response.headers.update(_HEADERS)
        return response
