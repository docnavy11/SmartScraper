"""Authentication for the web UI, and the guard that makes it mandatory.

The UI is an admin panel, not a dashboard. It can start scrapers, approve a
script version, and promote a repair whose script may contain a `custom_python`
step, which runs unsandboxed in the run subprocess. Reaching it is equivalent to
running code on this host, so it may not be served to a network unauthenticated.

This mirrors `smartscraper.mcp.server.ensure_bind_allowed`: same rule, same
refusal, stated rather than silently downgraded.
"""

from __future__ import annotations

import ipaddress
import secrets

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware

from smartscraper.config import get_settings

# Paths that must stay reachable without credentials.
OPEN_PATHS: frozenset[str] = frozenset({"/healthz"})


class InsecureWebBindError(RuntimeError):
    """Raised at startup, never at request time, so it cannot be missed."""


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def ensure_web_bind_allowed(host: str, *, password: str | None = None, allow_insecure: bool = False) -> None:
    """Refuse to serve an unauthenticated admin UI to anything but loopback."""
    if is_loopback(host) or password or allow_insecure:
        return
    raise InsecureWebBindError(
        f"refusing to bind the web UI to {host!r} with no password: it can run "
        f"scrapers and approve scripts containing unsandboxed Python. "
        f"Set SS_WEB_PASSWORD, bind to 127.0.0.1, or set SS_ALLOW_INSECURE_BIND=1 "
        f"if an authenticating reverse proxy sits in front."
    )


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """HTTP Basic, applied to everything except the open paths.

    Basic over plain HTTP sends the password in reachable form on every request,
    so this is a lock on the door, not transport security. Put TLS in front of it
    on any untrusted network.
    """

    def __init__(self, app, *, user: str, password: str) -> None:
        super().__init__(app)
        self._user = user
        self._password = password

    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        if not self._authorised(request):
            from starlette.responses import Response

            return Response(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": 'Basic realm="smartscraper"'},
                content="authentication required",
            )
        return await call_next(request)

    def _authorised(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        scheme, _, encoded = header.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            return False
        import base64
        import binascii

        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        user, _, password = decoded.partition(":")
        # compare_digest on both halves, so neither length nor prefix leaks by timing
        ok_user = secrets.compare_digest(user, self._user)
        ok_pass = secrets.compare_digest(password, self._password)
        return ok_user and ok_pass


def install(app) -> bool:
    """Add Basic auth when a password is configured. Returns whether it was added."""
    s = get_settings()
    if not s.web_password:
        return False
    app.add_middleware(BasicAuthMiddleware, user=s.web_user, password=s.web_password)
    return True


def raise_for_insecure_bind() -> None:
    s = get_settings()
    ensure_web_bind_allowed(s.host, password=s.web_password, allow_insecure=s.allow_insecure_bind)
