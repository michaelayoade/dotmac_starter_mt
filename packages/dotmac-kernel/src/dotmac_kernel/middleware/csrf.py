"""Signed, session-bound CSRF protection for declared browser routes.

The middleware issues/rotates a token on safe browser requests. Applicability
is explicit: the browser-surface runtime adds :func:`require_csrf` to every
composed route, and that dependency validates every unsafe method whether or
not the request happened to carry another cookie. Bearer/API routes receive no
such dependency and are therefore not classified by URL-prefix guesses.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
CSRF_COOKIE = "csrf_token"
CSRF_HOST_COOKIE = "__Host-csrf_token"
CSRF_HEADER = "x-csrf-token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_PROTECTED_ATTR = "dotmac_csrf_protected"


class CSRFValidationError(HTTPException):
    """The request reached a CSRF-protected unsafe browser operation."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF check failed",
        )


@dataclass(frozen=True, slots=True)
class CSRFTokenSigner:
    secret: bytes
    max_age_seconds: int
    session_cookie_names: tuple[str, ...]

    @classmethod
    def build(
        cls,
        *,
        secret: str,
        max_age_seconds: int,
        session_cookie_names: Sequence[str],
    ) -> CSRFTokenSigner:
        if not secret:
            raise ValueError("CSRF signing secret must not be empty")
        if max_age_seconds < 1:
            raise ValueError("CSRF token max age must be positive")
        return cls(
            secret=secret.encode("utf-8"),
            max_age_seconds=max_age_seconds,
            session_cookie_names=tuple(session_cookie_names),
        )

    def _binding(self, request: Request) -> str:
        material = "\0".join(
            f"{name}={request.cookies.get(name, '')}"
            for name in self.session_cookie_names
        ).encode("utf-8")
        return hmac.new(
            self.secret, b"session\0" + material, hashlib.sha256
        ).hexdigest()

    def issue(self, request: Request, *, now: int | None = None) -> str:
        issued_at = int(time.time()) if now is None else now
        payload = f"{issued_at}.{secrets.token_urlsafe(32)}.{self._binding(request)}"
        signature = hmac.new(
            self.secret, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"{payload}.{signature}"

    def valid(self, request: Request, token: str | None) -> bool:
        if not token:
            return False
        try:
            issued_raw, nonce, binding, supplied = token.split(".", 3)
            issued_at = int(issued_raw)
        except (TypeError, ValueError):
            return False
        age = int(time.time()) - issued_at
        if age < 0 or age > self.max_age_seconds or not nonce:
            return False
        if not hmac.compare_digest(binding, self._binding(request)):
            return False
        payload = f"{issued_raw}.{nonce}.{binding}"
        expected = hmac.new(
            self.secret, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, supplied)


class CSRFMiddleware:
    """Issue signed double-submit tokens; validation belongs to dependencies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        secret: str | None = None,
        production: bool = False,
        max_age_seconds: int = 7200,
        session_cookie_names: Sequence[str] = (),
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.production = production
        if secret is None:
            # Compatibility for direct middleware consumers. The application
            # factory always passes the dedicated setting explicitly.
            from dotmac_kernel.config import settings

            secret = settings.csrf_secret
        self.signer = CSRFTokenSigner.build(
            secret=secret,
            max_age_seconds=max_age_seconds,
            session_cookie_names=session_cookie_names,
        )
        self.cookie_name = CSRF_HOST_COOKIE if production else CSRF_COOKIE

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        request.state.csrf_enabled = self.enabled
        request.state.csrf_signer = self.signer
        request.state.csrf_cookie_name = self.cookie_name
        existing = request.cookies.get(self.cookie_name)
        valid_existing = self.enabled and self.signer.valid(request, existing)
        token = existing if valid_existing else self.signer.issue(request)
        request.state.csrf_token = token

        async def send_with_csrf(message: Message) -> None:
            set_cookie = (
                self.enabled
                and request.method in SAFE_METHODS
                and getattr(request.state, "csrf_required", False)
                and not valid_existing
            )
            if set_cookie and message["type"] == "http.response.start":
                cookie = (
                    f"{self.cookie_name}={token}; Path=/; SameSite=lax; "
                    f"Max-Age={self.signer.max_age_seconds}"
                    + ("; Secure" if self.production else "")
                )
                headers = list(message.get("headers", []))
                headers.append((b"set-cookie", cookie.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_csrf)


def _origin_tuple(
    scheme: str, hostname: str, port: int | None
) -> tuple[str, str, int | None]:
    normalized_scheme = scheme.lower()
    if port == (443 if normalized_scheme == "https" else 80):
        port = None
    return normalized_scheme, hostname.lower(), port


def _request_origin(request: Request) -> tuple[str, str, int | None] | None:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    scheme = forwarded_proto.split(",", 1)[0].strip() or request.url.scheme
    try:
        host = urlsplit(f"//{request.headers.get('host', '')}")
        if not host.hostname:
            return None
        return _origin_tuple(scheme, host.hostname, host.port)
    except ValueError:
        return None


def _submitted_origin(value: str) -> tuple[str, str, int | None] | None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        return _origin_tuple(parsed.scheme, parsed.hostname, parsed.port)
    except ValueError:
        return None


def _valid_request_provenance(request: Request) -> bool:
    """Reject explicit cross-site evidence without treating absence as proof.

    Origin, Referer and Fetch Metadata are defence in depth over the signed
    token. Older browsers and non-browser contract clients may omit them, so
    missing headers remain acceptable; a present contradictory header does not.
    """

    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False
    supplied = request.headers.get("origin")
    if supplied is None:
        supplied = request.headers.get("referer")
    if supplied is None:
        return True
    origin = _submitted_origin(supplied)
    return origin is not None and origin == _request_origin(request)


async def require_csrf(request: Request) -> None:
    """Validate header or hidden-form proof on every unsafe browser request."""

    request.state.csrf_required = True
    if request.method in SAFE_METHODS or not getattr(
        request.state, "csrf_enabled", False
    ):
        return
    signer = getattr(request.state, "csrf_signer", None)
    cookie_name = getattr(request.state, "csrf_cookie_name", CSRF_COOKIE)
    cookie_token = request.cookies.get(cookie_name)
    supplied = request.headers.get(CSRF_HEADER)
    if supplied is None:
        form = await request.form()
        value = form.get(CSRF_FORM_FIELD)
        supplied = str(value) if value is not None else None
    if (
        signer is None
        or not cookie_token
        or not supplied
        or not _valid_request_provenance(request)
        or not hmac.compare_digest(cookie_token, supplied)
        or not signer.valid(request, supplied)
    ):
        raise CSRFValidationError()


setattr(require_csrf, CSRF_PROTECTED_ATTR, True)


__all__ = [
    "CSRF_COOKIE",
    "CSRF_FORM_FIELD",
    "CSRF_HEADER",
    "CSRF_HOST_COOKIE",
    "CSRF_PROTECTED_ATTR",
    "CSRFMiddleware",
    "CSRFTokenSigner",
    "CSRFValidationError",
    "require_csrf",
]
