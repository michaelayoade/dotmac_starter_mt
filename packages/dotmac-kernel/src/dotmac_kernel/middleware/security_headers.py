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

- `script-src 'self'`: every script is a local /static file (htmx, the Alpine
  CSP build, components.js, csrf.js). No inline handler or evaluated string is
  needed, so neither unsafe script grant is present.
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

Operators may tighten this baseline through the `CONTENT_SECURITY_POLICY` env
compatibility knob: every baseline directive must remain and sources may only
be removed. Partial or wider policies fail application construction, even when
no typed capability happens to be active. `SECURITY_HEADERS_ENABLED=false`
disables the middleware when a fronting proxy owns these headers instead.
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dotmac_kernel.web_surfaces import BrowserSecurityRequirement

_CSP_BASELINE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("default-src", ("'self'",)),
    ("script-src", ("'self'",)),
    ("style-src", ("'self'", "'unsafe-inline'")),
    ("img-src", ("'self'", "data:", "https:")),
    ("font-src", ("'self'",)),
    ("connect-src", ("'self'",)),
    ("object-src", ("'none'",)),
    ("frame-ancestors", ("'none'",)),
    ("base-uri", ("'self'",)),
    ("form-action", ("'self'",)),
)

_CSP_REQUIREMENT_SOURCES: dict[BrowserSecurityRequirement, tuple[str, str]] = {
    BrowserSecurityRequirement.WORKER_SELF: ("worker-src", "'self'"),
    BrowserSecurityRequirement.WORKER_BLOB: ("worker-src", "blob:"),
    BrowserSecurityRequirement.MEDIA_SELF: ("media-src", "'self'"),
    BrowserSecurityRequirement.MEDIA_BLOB: ("media-src", "blob:"),
    BrowserSecurityRequirement.FRAME_SELF: ("frame-src", "'self'"),
}


def compose_content_security_policy(
    requirements: Iterable[BrowserSecurityRequirement] = (),
) -> str:
    """Resolve the deterministic CSP for active, typed browser capabilities."""

    directives = {name: list(sources) for name, sources in _CSP_BASELINE}
    for requirement in sorted(
        (BrowserSecurityRequirement(value) for value in requirements),
        key=str,
    ):
        directive, source = _CSP_REQUIREMENT_SOURCES[requirement]
        values = directives.setdefault(directive, [])
        if source not in values:
            values.append(source)
    return "; ".join(
        f"{directive} {' '.join(sources)}" for directive, sources in directives.items()
    )


_STRICT_CSP = compose_content_security_policy()

_HSTS = "max-age=63072000; includeSubDomains"


def _parse_content_security_policy(policy: str) -> dict[str, tuple[str, ...]]:
    directives: dict[str, tuple[str, ...]] = {}
    if "\r" in policy or "\n" in policy:
        raise ValueError("CSP override must be one HTTP header value")
    for raw_directive in policy.split(";"):
        parts = raw_directive.split()
        if not parts:
            continue
        name = parts[0].lower()
        sources = tuple(parts[1:])
        if not sources:
            raise ValueError(f"CSP override directive {name!r} has no sources")
        if name in directives:
            raise ValueError(f"CSP override repeats directive {name!r}")
        if len(set(sources)) != len(sources):
            raise ValueError(f"CSP override repeats a source in {name!r}")
        if "'none'" in sources and sources != ("'none'",):
            raise ValueError(f"CSP override mixes 'none' with sources in {name!r}")
        directives[name] = sources
    if not directives:
        raise ValueError("CSP override must declare a policy")
    return directives


def _validate_content_security_policy_override(
    policy: str,
    requirements: Iterable[BrowserSecurityRequirement] = (),
) -> None:
    """Allow a raw compatibility policy only when it tightens the baseline.

    Raw policy is intentionally a bounded compatibility seam, not a second CSP
    composition language.  It must retain every baseline directive, may remove
    allowed sources (or replace them with ``'none'``), and may add neither a
    directive nor a source.  Browser capabilities that need new mechanics use
    the typed requirement vocabulary instead.
    """

    if not policy:
        return
    typed_requirements = frozenset(
        BrowserSecurityRequirement(value) for value in requirements
    )
    if typed_requirements:
        raise ValueError(
            "a raw CSP override cannot replace active typed browser-security "
            "requirements"
        )
    baseline = _parse_content_security_policy(_STRICT_CSP)
    candidate = _parse_content_security_policy(policy)
    missing = sorted(set(baseline) - set(candidate))
    if missing:
        raise ValueError(
            "raw CSP override is missing required directives: " + ", ".join(missing)
        )
    extra = sorted(set(candidate) - set(baseline))
    if extra:
        raise ValueError(
            "raw CSP override adds untyped directives: " + ", ".join(extra)
        )
    for directive, sources in candidate.items():
        if sources == ("'none'",):
            continue
        unexpected = sorted(set(sources) - set(baseline[directive]))
        if unexpected:
            raise ValueError(
                "raw CSP override would weaken the computed baseline at "
                f"{directive!r}: {', '.join(unexpected)}"
            )


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
        browser_security_requirements: Iterable[BrowserSecurityRequirement] = (),
        cross_origin_opener_policy: str = "",
        cross_origin_resource_policy: str = "",
    ) -> None:
        self.app = app
        self.enabled = enabled
        requirements = frozenset(browser_security_requirements)
        _validate_content_security_policy_override(
            content_security_policy, requirements
        )
        self.csp = content_security_policy or compose_content_security_policy(
            requirements
        )
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
    "compose_content_security_policy",
]
