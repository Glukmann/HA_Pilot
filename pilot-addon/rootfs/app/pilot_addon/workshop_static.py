"""Serving the built workshop SPA from the add-on.

Served at the app root: GET / returns index.html, hashed assets live under
/assets/ with long-lived caching, extensionless deep links fall back to
index.html (the SPA router is hash-based, so those are rare). /api/* and /ws
belong to their own routes — this handler answers 404 for them instead of
HTML, so a missing API path can never be masked by the SPA shell.

Ingress: the Supervisor proxy strips /api/hassio_ingress/<token> before
forwarding (supervisor/api/ingress.py forwards only the captured path tail),
so requests arrive prefix-free. If some other reverse proxy keeps a prefix,
X-Ingress-Path is honored when resolving files as a belt-and-braces.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger("pilot.addon")

# Populated at image build time (see pilot-addon/Dockerfile); absent in
# Supervisor source-builds without the workshop stage — the UI then 404s
# cleanly while the REST/WS contract keeps working.
WORKSHOP_DIR = Path(__file__).parent / "workshop_dist"

_INDEX_HEADERS = {"Cache-Control": "no-cache"}
_ASSET_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def _strip_ingress(tail: str, ingress_path: str | None) -> str:
    """Drop the ingress prefix from the path when a proxy left it in place."""
    rel = tail.lstrip("/")
    if ingress_path:
        prefix = ingress_path.strip("/")
        if prefix and (rel == prefix or rel.startswith(prefix + "/")):
            return rel[len(prefix) :].lstrip("/")
    return rel


def _safe_join(root: Path, rel: str) -> Path | None:
    """Resolve rel under root, rejecting anything that escapes it."""
    if not rel or rel.startswith("/") or "\x00" in rel:
        return None
    try:
        target = (root / rel).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return None
    return target


def attach_workshop(app: web.Application, workshop_dir: Path | None = None) -> None:
    """Register the SPA catch-all route; 404 cleanly when the dist is absent.

    Must be registered last: the /{tail:.*} pattern matches every GET, so
    the /api/* and /ws routes have to win by earlier registration.
    """
    root = (workshop_dir or WORKSHOP_DIR).resolve()
    has_index = (root / "index.html").is_file()
    if not has_index:
        logger.warning(
            "workshop UI not built (%s missing) — / serves 404", root / "index.html"
        )

    async def workshop(request: web.Request) -> web.StreamResponse:
        rel = _strip_ingress(
            request.match_info["tail"], request.headers.get("X-Ingress-Path")
        )
        if rel == "ws" or rel == "api" or rel.startswith("api/"):
            return web.json_response({"error": "not found"}, status=404)
        target = _safe_join(root, rel) if has_index else None
        if target is not None and target.is_file():
            headers = _INDEX_HEADERS if rel == "index.html" else _ASSET_HEADERS
            return web.FileResponse(target, headers=headers)
        if "." in Path(rel).name:
            # Looks like a missing file, not a route — don't answer with HTML.
            return web.json_response({"error": "not found"}, status=404)
        if has_index:
            return web.FileResponse(root / "index.html", headers=_INDEX_HEADERS)
        return web.json_response({"error": "workshop UI not built"}, status=404)

    app.router.add_get("/{tail:.*}", workshop)
