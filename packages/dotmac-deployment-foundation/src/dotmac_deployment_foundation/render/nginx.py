"""Deterministic Nginx site-configuration renderer.

Extraction source: `dotmac_sub/nginx/selfcare.dotmac.io.conf` (the canonical,
production-used edge configuration), with one deliberate correction —
`dotmac_sub/deploy/nginx/selfcare.dotmac.io` (the OLDER, divergent copy) still
carries a dedicated Server-Sent-Events location that the canonical file
regressed into the generic API block (inventory defect D13). That capability
is reinstated here as `IngressRoute.sse`, a first-class declaration rather
than a path convention.

Every value that varies across products — host, port, path, timeout, body
size — comes from `ProductDeploymentSpec`. Nothing here names a product. What
IS hardcoded is a short list of documented, product-independent operational
conventions (loopback address, filesystem roots, cache lifetimes, cipher
suites) — each lives in one `_UPPER_CASE` constant near the top of this file,
commented with why it is fixed rather than declared.

## The candidate-port derivation

`render_nginx`'s `candidate_port_base` (default 18000) is the base of a block
of ports reserved for warm deployment candidates. Each distinct role an
ingress route names gets exactly one upstream and therefore exactly one
candidate port: the roles are sorted alphabetically by code and assigned
`candidate_port_base + <1-based rank>`. Sorting by role code — not by primary
port number — keeps the mapping stable even if two roles happened to share a
primary port, and keeps it independent of the order routes appear in the
descriptor.

## The handoff mechanism, restated

The candidate upstream member (`backup`) is not activated by rewriting this
file and reloading nginx. It is present in every render, closed at the
kernel/process level outside a deployment window, and nginx itself promotes
it the moment the primary stops answering — see the comment emitted above
each `upstream {}` block. The deployment engine's only job is to VERIFY the
member is present (`handoff_contract_pattern`, ported from Sub's
`assert_proxy_handoff_contract`), never to add it.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

from ..errors import SpecError
from ..spec import IngressRoute, ProductDeploymentSpec

# ── documented, product-independent conventions ─────────────────────────────

# Every upstream member is loopback-only: nginx and the roles it proxies to
# always share a host/network namespace in this facility's deployment model.
# 0.0.0.0 or a container DNS name here would mean nginx can be reached over
# the network by something other than itself, which is the surface this
# constant exists to close off.
_LOOPBACK: Final[str] = "127.0.0.1"
_UPSTREAM_MAX_FAILS: Final[int] = 1
_UPSTREAM_FAIL_TIMEOUT_SECONDS: Final[int] = 1
_UPSTREAM_KEEPALIVE: Final[int] = 32
_UPSTREAM_MEMBER_SUFFIX: Final[str] = (
    f"max_fails={_UPSTREAM_MAX_FAILS} fail_timeout={_UPSTREAM_FAIL_TIMEOUT_SECONDS}s;"
)

_ACME_WEBROOT: Final[str] = "/var/www/certbot"
_CERT_ROOT: Final[str] = "/etc/letsencrypt/live"
_LOG_ROOT: Final[str] = "/var/log/nginx"
_VOLUME_MOUNT_ROOT: Final[str] = "/srv/volumes"
_SSL_SESSION_CACHE_SIZE: Final[str] = "10m"
_STATIC_CACHE_DAYS: Final[int] = 30
_UPLOADS_CACHE_DAYS: Final[int] = 7

# The `intermediate` TLS policy's explicit cipher list, ported verbatim from
# the canonical source file. `modern` needs no cipher list: TLS 1.3 has its
# own fixed, safe suite and does not honour `ssl_ciphers` at all.
_INTERMEDIATE_CIPHERS: Final[str] = (
    "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
    "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
    "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
    "DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384"
)

_HSTS_VALUE: Final[str] = "max-age=63072000; includeSubDomains; preload"
# A conservative default: same-origin everything, no plugin content. A
# product with a real need to relax this edits `deploy/product.toml`'s
# ingress declaration once that field exists — this default is deliberately
# not the place a per-product exception lives.
_CSP_VALUE: Final[str] = (
    "default-src 'self'; frame-ancestors 'self'; base-uri 'self'; object-src 'none'"
)


# ── shared naming, so the renderer and the verifier cannot diverge ──────────


def _upstream_name(product: str, role: str) -> str:
    return f"{product}_{role}"


def _ingress_roles(spec: ProductDeploymentSpec) -> tuple[tuple[str, int], ...]:
    """Every role an ingress route names, paired with its port, role-sorted.

    One role is one upstream and needs one port: a role declared at two
    different ports across its own routes cannot be rendered.
    """
    if spec.ingress is None:  # pragma: no cover - callers check first
        # Not an assert: `python -O` strips those, and this function would then
        # raise AttributeError deep inside a loop instead of saying what is
        # wrong.
        raise SpecError("no ingress is declared", where=spec.source)
    ports_by_role: dict[str, int] = {}
    for route in spec.ingress.routes:
        seen = ports_by_role.get(route.role)
        if seen is not None and seen != route.port:
            raise SpecError(
                f"ingress role {route.role!r} is declared at two different "
                f"ports ({seen} and {route.port}) across its routes; one "
                "role is one upstream and needs one port",
            )
        ports_by_role[route.role] = route.port
    return tuple(sorted(ports_by_role.items()))


def handoff_contract_pattern(spec: ProductDeploymentSpec, role: str) -> str:
    """The exact regex proving a candidate upstream member is present.

    Ported from Sub's `assert_proxy_handoff_contract`: the deployment engine
    runs `nginx -T` and searches its output for this pattern rather than
    trusting that a config file it did not just render still matches the
    live host. Built from the same `_upstream_name` this module's renderer
    uses, so the two agree BY CONSTRUCTION — a rename of one breaks the
    other at the point of use instead of silently diverging.
    """
    name = re.escape(_upstream_name(spec.product, role))
    loopback = re.escape(_LOOPBACK)
    suffix = re.escape(_UPSTREAM_MEMBER_SUFFIX)
    # `[^}]*?` and NOT `(?s).*?`. With DOTALL and an unrestricted lazy gap the
    # match could START at this role's `upstream {` and END at a LATER
    # upstream's backup member — so the contract check would pass for a role
    # whose candidate is missing entirely, as long as any other role still had
    # one. That is the precise failure this check exists to catch, arriving
    # through the check itself.
    #
    # Excluding `}` confines the match to one block, which is exact here because
    # an nginx `upstream` block contains no nested braces. Found by the
    # sensitivity test that deletes the backup member and requires the pattern
    # to stop matching — a test asserting only that it DOES match would never
    # have seen it.
    return (
        rf"upstream\s+{name}\s*\{{[^}}]*?"
        rf"server\s+{loopback}:\d+\s+backup\s+{suffix}[^}}]*?\}}"
    )


# ── header ────────────────────────────────────────────────────────────────


def _render_header(spec: ProductDeploymentSpec) -> list[str]:
    return [
        f"# {spec.product} — nginx site configuration",
        f"# image digest:    {spec.image_digest}",
        f"# manifest digest: {spec.manifest_digest}",
        "#",
        "# GENERATED by dotmac-deployment-foundation. Do not edit; edit",
        "# deploy/product.toml and re-run `dotmac-deploy render`.",
        "",
    ]


def _render_upgrade_map(spec: ProductDeploymentSpec) -> list[str]:
    if spec.ingress is None:  # pragma: no cover - callers check first
        # Not an assert: `python -O` strips those, and this function would then
        # raise AttributeError deep inside a loop instead of saying what is
        # wrong.
        raise SpecError("no ingress is declared", where=spec.source)
    if not any(route.websocket for route in spec.ingress.routes):
        return []
    return [
        "# This map MUST live directly inside the top-level `http {}` context.",
        "# nginx refuses a `map` directive nested in `server {}` or",
        "# `location {}` — if this file is ever `include`d somewhere other",
        "# than http scope, move this block up to where it is included.",
        "map $http_upgrade $connection_upgrade {",
        "    default upgrade;",
        "    '' close;",
        "}",
        "",
    ]


# ── upstreams ─────────────────────────────────────────────────────────────


def _render_upstreams(
    spec: ProductDeploymentSpec, candidate_port_base: int
) -> list[str]:
    lines: list[str] = []
    for ordinal, (role, port) in enumerate(_ingress_roles(spec), start=1):
        candidate_port = candidate_port_base + ordinal
        name = _upstream_name(spec.product, role)
        lines.append(f"upstream {name} {{")
        lines.append(f"    server {_LOOPBACK}:{port} {_UPSTREAM_MEMBER_SUFFIX}")
        lines.append(
            "    # Warm deployment candidate. Its PRESENCE is the handoff "
            "mechanism — nginx"
        )
        lines.append(
            "    # promotes it itself the moment the primary stops answering; "
            "nothing"
        )
        lines.append(
            "    # rewrites this file or reloads nginx to do that. The "
            "deployment engine"
        )
        lines.append(
            "    # only VERIFIES this line is present (see "
            "handoff_contract_pattern)."
        )
        lines.append(
            f"    server {_LOOPBACK}:{candidate_port} backup {_UPSTREAM_MEMBER_SUFFIX}"
        )
        lines.append(f"    keepalive {_UPSTREAM_KEEPALIVE};")
        lines.append("}")
        lines.append("")
    return lines


# ── HTTP → HTTPS redirect ────────────────────────────────────────────────


def _render_http_redirect_server(spec: ProductDeploymentSpec) -> list[str]:
    if spec.ingress is None:  # pragma: no cover - callers check first
        # Not an assert: `python -O` strips those, and this function would then
        # raise AttributeError deep inside a loop instead of saying what is
        # wrong.
        raise SpecError("no ingress is declared", where=spec.source)
    return [
        "server {",
        "    listen 80;",
        "    listen [::]:80;",
        f"    server_name {spec.ingress.host};",
        "",
        "    location /.well-known/acme-challenge/ {",
        f"        root {_ACME_WEBROOT};",
        "    }",
        "",
        "    location / {",
        "        return 301 https://$host$request_uri;",
        "    }",
        "}",
        "",
    ]


# ── TLS / security headers ───────────────────────────────────────────────


def _render_tls_policy(tls_policy: str) -> list[str]:
    lines: list[str] = []
    if tls_policy == "modern":
        lines.append("    ssl_protocols TLSv1.3;")
    else:
        lines.append("    ssl_protocols TLSv1.2 TLSv1.3;")
        lines.append(f"    ssl_ciphers {_INTERMEDIATE_CIPHERS};")
    lines.append("    ssl_prefer_server_ciphers off;")
    return lines


def _render_security_headers() -> list[str]:
    return [
        f'    add_header Strict-Transport-Security "{_HSTS_VALUE}" always;',
        '    add_header X-Frame-Options "SAMEORIGIN" always;',
        '    add_header X-Content-Type-Options "nosniff" always;',
        '    add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
        f'    add_header Content-Security-Policy "{_CSP_VALUE}" always;',
        "    # X-XSS-Protection is deliberately NOT emitted: the header is",
        "    # deprecated, current browsers ignore it, and older ones had",
        "    # exploitable bugs in the filter it turned on. CSP above is the",
        "    # replacement (inventory defect D12 — the canonical source ships",
        "    # every other security header and omits this one).",
    ]


# ── static / uploads ─────────────────────────────────────────────────────


def _static_role(spec: ProductDeploymentSpec) -> tuple[str, int]:
    """The single role that serves baked-in static assets and the favicon.

    Unambiguous when a route is declared at `/` (the conventional owner of
    everything a more specific route does not claim), or when ingress names
    exactly one role. Anything else cannot pick a role without guessing, and
    a renderer that guesses is the exact defect this facility exists to
    remove — so it is refused instead.
    """
    if spec.ingress is None:  # pragma: no cover - callers check first
        # Not an assert: `python -O` strips those, and this function would then
        # raise AttributeError deep inside a loop instead of saying what is
        # wrong.
        raise SpecError("no ingress is declared", where=spec.source)
    roles = _ingress_roles(spec)
    root_routes = [route for route in spec.ingress.routes if route.path == "/"]
    if root_routes:
        chosen = root_routes[0].role
    elif len(roles) == 1:
        chosen = roles[0][0]
    else:
        raise SpecError(
            "static assets need an unambiguous serving role: declare a "
            "route at path '/' or a single ingress role",
        )
    for role, port in roles:
        if role == chosen:
            return role, port
    raise SpecError(f"static role {chosen!r} is not among the ingress roles")


def _render_static_locations(spec: ProductDeploymentSpec) -> list[str]:
    if spec.ingress is None:  # pragma: no cover - callers check first
        # Not an assert: `python -O` strips those, and this function would then
        # raise AttributeError deep inside a loop instead of saying what is
        # wrong.
        raise SpecError("no ingress is declared", where=spec.source)
    static = spec.ingress.static
    lines: list[str] = []
    if static.static == "image":
        role, _port = _static_role(spec)
        upstream = _upstream_name(spec.product, role)
        lines += [
            "    # Static assets are baked into the image and promoted with",
            "    # its digest; nginx PROXIES rather than aliasing a host",
            "    # path, so a rollback restores them automatically",
            "    # (inventory defect D1 — a bind-mounted static/ directory",
            "    # left production serving a different tree than its image).",
            "    location /static/ {",
            f"        proxy_pass http://{upstream};",
            "        proxy_set_header Host $host;",
            f"        expires {_STATIC_CACHE_DAYS}d;",
            '        add_header Cache-Control "public, immutable";',
            "        access_log off;",
            "    }",
            "",
            "    location = /favicon.ico {",
            f"        proxy_pass http://{upstream};",
            "        proxy_set_header Host $host;",
            "        access_log off;",
            "        log_not_found off;",
            "    }",
            "",
        ]
    if static.uploads == "volume":
        alias = f"{_VOLUME_MOUNT_ROOT}/{static.uploads_volume}/"
        lines += [
            "    location /uploads/ {",
            f"        alias {alias};",
            f"        expires {_UPLOADS_CACHE_DAYS}d;",
            '        add_header Cache-Control "public";',
            "    }",
            "",
        ]
    return lines


# ── per-route locations ──────────────────────────────────────────────────


def _is_health_route(spec: ProductDeploymentSpec, route: IngressRoute) -> bool:
    role = spec.role(route.role)
    return role.ready is not None and role.ready.path == route.path


def _render_route(
    spec: ProductDeploymentSpec, route: IngressRoute, *, originate_forwarding: bool
) -> list[str]:
    upstream = _upstream_name(spec.product, route.role)
    lines = [f"    location {route.path} {{"]
    lines.append(f"        proxy_pass http://{upstream};")
    lines.append("        proxy_http_version 1.1;")
    lines.append("        proxy_set_header Host $host;")
    if originate_forwarding:
        lines.append("        proxy_set_header X-Real-IP $remote_addr;")
        lines.append(
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;"
        )
        lines.append("        proxy_set_header X-Forwarded-Proto $scheme;")
    if route.websocket:
        lines.append("        proxy_set_header Upgrade $http_upgrade;")
        lines.append("        proxy_set_header Connection $connection_upgrade;")
    else:
        lines.append('        proxy_set_header Connection "";')
    if route.sse:
        lines.append("        # Buffering would hold each event until the buffer fills")
        lines.append("        # or the response ends, turning a live stream into a")
        lines.append("        # batch delivery on arrival — disabled for this route")
        lines.append("        # (inventory defect D13).")
        lines.append("        proxy_buffering off;")
        lines.append("        proxy_cache off;")
        lines.append("        proxy_set_header X-Accel-Buffering no;")
    lines.append(f"        client_max_body_size {route.max_body_bytes};")
    lines.append(f"        proxy_read_timeout {route.read_timeout_seconds}s;")
    lines.append(f"        proxy_send_timeout {route.send_timeout_seconds}s;")
    if _is_health_route(spec, route):
        lines.append("        access_log off;")
    lines.append("    }")
    lines.append("")
    return lines


# ── https server ──────────────────────────────────────────────────────────


def _render_https_server(spec: ProductDeploymentSpec) -> list[str]:
    if spec.ingress is None:  # pragma: no cover - callers check first
        # Not an assert: `python -O` strips those, and this function would then
        # raise AttributeError deep inside a loop instead of saying what is
        # wrong.
        raise SpecError("no ingress is declared", where=spec.source)
    ingress = spec.ingress
    lines = [
        "server {",
        "    listen 443 ssl;",
        "    listen [::]:443 ssl;",
        "    http2 on;",
        f"    server_name {ingress.host};",
        "",
        f"    ssl_certificate {_CERT_ROOT}/{ingress.host}/fullchain.pem;",
        f"    ssl_certificate_key {_CERT_ROOT}/{ingress.host}/privkey.pem;",
        "",
        "    ssl_session_timeout 1d;",
        f"    ssl_session_cache shared:SSL:{_SSL_SESSION_CACHE_SIZE};",
        "    ssl_session_tickets off;",
        "",
    ]
    lines += _render_tls_policy(ingress.tls_policy)
    lines.append("")

    if ingress.trusted_proxies:
        lines.append("    # A real proxy sits in front of nginx: resolve the true")
        lines.append("    # client address from a TRUSTED CIDR's X-Forwarded-For")
        lines.append("    # instead of originating fresh headers below, which would")
        lines.append("    # just discard whatever the proxy already resolved.")
        for cidr in ingress.trusted_proxies:
            lines.append(f"    set_real_ip_from {cidr};")
        lines.append("    real_ip_header X-Forwarded-For;")
        lines.append("    real_ip_recursive on;")
        lines.append("")

    lines.append("    server_tokens off;")
    lines.append("")

    if ingress.security_headers:
        lines += _render_security_headers()
        lines.append("")

    lines.append(f"    access_log {_LOG_ROOT}/{spec.product}.access.log;")
    lines.append(f"    error_log {_LOG_ROOT}/{spec.product}.error.log;")
    lines.append("")
    lines.append("    # Retry the candidate only when the primary cannot accept the")
    lines.append("    # request. `non_idempotent` is deliberately absent: replaying a")
    lines.append("    # POST onto the candidate after the primary half-processed it")
    lines.append("    # would duplicate the write, not just the read.")
    lines.append("    proxy_next_upstream error timeout http_502 http_503 http_504;")
    lines.append("    proxy_next_upstream_tries 2;")
    lines.append("")

    lines += _render_static_locations(spec)

    originate_forwarding = not ingress.trusted_proxies
    for route in sorted(ingress.routes, key=lambda item: (-len(item.path), item.path)):
        lines += _render_route(spec, route, originate_forwarding=originate_forwarding)

    lines.append("    location ~ /\\. {")
    lines.append("        deny all;")
    lines.append("        access_log off;")
    lines.append("        log_not_found off;")
    lines.append("    }")
    lines.append("}")
    lines.append("")
    return lines


# ── entry points ──────────────────────────────────────────────────────────


def render_nginx(
    spec: ProductDeploymentSpec, *, candidate_port_base: int = 18000
) -> str:
    """Render `spec` into a complete Nginx site configuration.

    Returns the empty string when `spec.ingress is None` — a product with no
    ingress renders no site, and that is not an error. Raises `SpecError`
    when the descriptor cannot produce a valid configuration (an ingress
    role declared at two different ports, or static assets with no
    unambiguous serving role).
    """
    if spec.ingress is None:
        return ""
    _ingress_roles(spec)  # raises SpecError on a role/port conflict, up front

    lines: list[str] = []
    lines += _render_header(spec)
    lines += _render_upgrade_map(spec)
    lines += _render_upstreams(spec, candidate_port_base)
    if spec.ingress.redirect_http:
        lines += _render_http_redirect_server(spec)
    lines += _render_https_server(spec)
    return "\n".join(lines) + "\n"


def render_nginx_digest(
    spec: ProductDeploymentSpec, *, candidate_port_base: int = 18000
) -> str:
    """`sha256:<hex>` of the bytes `render_nginx` produces for `spec`."""
    rendered = render_nginx(spec, candidate_port_base=candidate_port_base)
    return f"sha256:{hashlib.sha256(rendered.encode('utf-8')).hexdigest()}"
