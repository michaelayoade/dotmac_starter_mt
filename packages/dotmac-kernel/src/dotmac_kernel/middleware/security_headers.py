"""Security response headers on every response (control-plane security Task 5).

Pure ASGI middleware (same shape as `CSRFMiddleware`), mounted OUTERMOST in
`app/main.py` so even middleware short-circuit responses (tenant-resolver
404s, rate-limit 429s, error pages) carry the headers.

Known scope limit: Starlette's `ServerErrorMiddleware` wraps ALL user
middleware and is what invokes the app-level catch-all `Exception` handler,
so a 500 produced by an UNHANDLED exception (a bug condition) does not
carry these headers. Every normal path does: success responses,
`HTTPException`/domain-error responses (handled by `ExceptionMiddleware`,
which runs INSIDE this middleware), and middleware short-circuits
(tenant 404s, 429s, CSRF 403s).

The Content-Security-Policy default (`_STRICT_CSP`) is computed from this
codebase's ACTUAL asset inventory (Task 5 required the inventory first —
see docs/SECURITY.md "Content-Security-Policy rationale" for the audit):

- `script-src 'self' 'unsafe-eval'`: every script is a local /static file
  (htmx, Alpine, components.js, csrf.js — the old inline toast bridge in
  base.html was MOVED into components.js so 'unsafe-inline' is not needed).
  'unsafe-eval' is required by the standard Alpine.js build, which compiles
  `x-data`/`x-show` expressions with `new Function`.
- `style-src 'self' 'unsafe-inline'`: Tailwind + vendored fonts.css are
  local. 'unsafe-inline' is NOT for tenant CSS -- tenant-supplied `custom_css`
  was retired on 2026-08-13 (ADR-0006 D8) and no response carries a
  tenant-authored `<style>` block any more. What still needs it is the inline
  `style="..."` ATTRIBUTES in first-party templates (the platform screens set
  `var(--dmui-*)` that way). Removing it is a separate slice that has to
  convert those attributes first; until then, claiming a tighter policy in this
  comment than the header actually sends would be the more dangerous error.
- `font-src 'self'`: fonts are VENDORED (static/fonts/, no-CDN standard) —
  no fonts.googleapis.com / fonts.gstatic.com origins.
- `img-src 'self' data: https:`: tenants may set an external https
  `logo_url` in branding.
- everything else locked: `object-src 'none'`, `frame-ancestors 'none'`
  (mirrors X-Frame-Options DENY), `base-uri 'self'`, `form-action 'self'`,
  `connect-src 'self'` (htmx XHR is same-origin).

Operators override via the `CONTENT_SECURITY_POLICY` env knob (everything
by config); `SECURITY_HEADERS_ENABLED=false` disables the middleware when a
fronting proxy owns these headers instead.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_STRICT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_HSTS = "max-age=63072000; includeSubDomains"


def _is_secure_request(request: Request) -> bool:
    """Same signal `CSRFMiddleware`/auth cookies use: direct TLS or a
    trusted proxy's x-forwarded-proto."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


class SecurityHeadersMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        content_security_policy: str = "",
        cross_origin_opener_policy: str = "",
        cross_origin_resource_policy: str = "",
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.csp = content_security_policy or _STRICT_CSP
        self.cross_origin_opener_policy = cross_origin_opener_policy
        self.cross_origin_resource_policy = cross_origin_resource_policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        add_hsts = _is_secure_request(request)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (
                            b"referrer-policy",
                            b"strict-origin-when-cross-origin",
                        ),
                        (
                            b"permissions-policy",
                            b"camera=(), geolocation=(), microphone=()",
                        ),
                        (b"content-security-policy", self.csp.encode()),
                    ]
                )
                if self.cross_origin_opener_policy:
                    headers.append(
                        (
                            b"cross-origin-opener-policy",
                            self.cross_origin_opener_policy.encode("latin-1"),
                        )
                    )
                if self.cross_origin_resource_policy:
                    headers.append(
                        (
                            b"cross-origin-resource-policy",
                            self.cross_origin_resource_policy.encode("latin-1"),
                        )
                    )
                if add_hsts:
                    headers.append((b"strict-transport-security", _HSTS.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = [
    "SecurityHeadersMiddleware",
]
