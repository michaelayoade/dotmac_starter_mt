"""Tests for `dotmac_deployment_foundation.render.nginx` — the pure Nginx
site-configuration renderer.

Every fixture spec is built from a TOML string through
`ProductDeploymentSpec.loads`, never by constructing dataclasses by hand —
see `test_deployment_foundation_compose.py`'s docstring for why that
matters; the same reasoning applies here unchanged.

The base fixture carries four ingress roles on purpose, because the
properties this renderer must prove are properties of RELATIONSHIPS between
routes, not properties any single route has on its own:

- ``web`` serves both ``/`` (ordinary) and ``/health`` (its own readiness
  path, declared as a SECOND route) — the pair that proves `access_log off`
  is derived from "this route's path equals this role's readiness path"
  rather than a hardcoded string.
- ``api`` serves only ``/api/`` — an ordinary proxied route with default
  timeouts, the control every other route's overrides are compared against.
- ``ws`` serves ``/ws`` with `websocket = true` — the shortest path in the
  fixture, so the longest-path-first ordering test is not vacuously true.
- ``stream`` serves ``/api/v1/stream/live`` with `sse = true` — the longest
  path in the fixture, and the direct reinstatement of inventory defect D13.

## On negative controls

A check of the shape `assert "0.0.0.0" not in rendered` passes on an EMPTY
string, and would keep passing if the renderer stopped emitting upstreams
altogether. Every ADR-0018 sensitivity proof below therefore runs its
predicate against the real rendered config (must pass) AND a deliberately
corrupted copy of that same text (must fail) — proving the predicate
actually inspects structure rather than merely being silent.
"""

from __future__ import annotations

import re

import pytest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.render.nginx import (
    handoff_contract_pattern,
    render_nginx,
    render_nginx_digest,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

_MANIFEST_DIGEST = "sha256:" + "a" * 64
_IMAGE = f"registry.example.com/acme/app@sha256:{'b' * 64}"
_SOURCE_REVISION = "c" * 40
_OWNER_MATERIAL = "DATABASE_OWNER_URL"

_ROLES_AND_MIGRATION = f"""
[[roles]]
code = "web"
command = ["python", "-m", "app"]

[roles.resources]
cpus = "1.0"
memory = "512m"

[roles.health.ready]
path = "/health"
port = 8000

[[roles]]
code = "api"
command = ["python", "-m", "app.api"]

[roles.resources]
cpus = "1.0"
memory = "512m"

[roles.health.ready]
path = "/readyz"
port = 8001

[[roles]]
code = "ws"
command = ["python", "-m", "app.ws"]

[roles.resources]
cpus = "0.5"
memory = "256m"

[roles.health.ready]
path = "/readyz"
port = 8002

[[roles]]
code = "stream"
command = ["python", "-m", "app.stream"]

[roles.resources]
cpus = "0.5"
memory = "256m"

[roles.health.ready]
path = "/readyz"
port = 8003

[migration]
command = ["alembic", "upgrade", "heads"]
heads_command = ["alembic", "current"]
owner_material = "{_OWNER_MATERIAL}"
expected_heads = ["abc123"]
compatibility = "online"
"""


def _spec(ingress_toml: str) -> ProductDeploymentSpec:
    document = f"""
schema = "ProductDeploymentSpec.v1"
product = "acme"

[assembly]
manifest_path = "deploy/product.toml"
manifest_digest = "{_MANIFEST_DIGEST}"

[image]
reference = "{_IMAGE}"
source_revision = "{_SOURCE_REVISION}"
{_ROLES_AND_MIGRATION}
{ingress_toml}
"""
    return ProductDeploymentSpec.loads(document, source="<test-fixture>")


def _ingress_toml(
    *,
    trusted_proxies: tuple[str, ...] = (),
    tls_policy: str = "modern",
    redirect_http: bool = True,
    security_headers: bool = True,
    static: str = "image",
    uploads: str = "volume",
) -> str:
    trusted_line = ""
    if trusted_proxies:
        quoted = ", ".join(f'"{name}"' for name in trusted_proxies)
        trusted_line = f"trusted_proxies = [{quoted}]\n"
    return f"""
[ingress]
host = "acme.example.com"
exposure = "public"
address_family = "dual_stack"
approval_ref = "deployment.public-exposure"
rationale_url = "https://docs.example/why"
redirect_http = {str(redirect_http).lower()}
tls_policy = "{tls_policy}"
security_headers = {str(security_headers).lower()}
{trusted_line}
[[ingress.routes]]
path = "/"
role = "web"
port = 8000

[[ingress.routes]]
path = "/health"
role = "web"
port = 8000

[[ingress.routes]]
path = "/api/"
role = "api"
port = 8001

[[ingress.routes]]
path = "/ws"
role = "ws"
port = 8002
websocket = true
read_timeout_seconds = 300

[[ingress.routes]]
path = "/api/v1/stream/live"
role = "stream"
port = 8003
sse = true
read_timeout_seconds = 300

[ingress.static]
static = "{static}"
uploads = "{uploads}"
uploads_volume = "acme_uploads"
"""


_NO_WEBSOCKET_INGRESS = """
[ingress]
host = "acme.example.com"
exposure = "public"
address_family = "dual_stack"
approval_ref = "deployment.public-exposure"
rationale_url = "https://docs.example/why"

[[ingress.routes]]
path = "/"
role = "web"
port = 8000

[ingress.static]
static = "none"
uploads = "none"
"""

_AMBIGUOUS_STATIC_INGRESS = """
[ingress]
host = "acme.example.com"
exposure = "public"
address_family = "dual_stack"
approval_ref = "deployment.public-exposure"
rationale_url = "https://docs.example/why"

[[ingress.routes]]
path = "/api/"
role = "api"
port = 8001

[[ingress.routes]]
path = "/other"
role = "ws"
port = 8002
"""

_PORT_CONFLICT_INGRESS = """
[ingress]
host = "acme.example.com"
exposure = "public"
address_family = "dual_stack"
approval_ref = "deployment.public-exposure"
rationale_url = "https://docs.example/why"

[[ingress.routes]]
path = "/"
role = "web"
port = 8000

[[ingress.routes]]
path = "/other"
role = "web"
port = 9999

[ingress.static]
static = "none"
uploads = "none"
"""


@pytest.fixture(scope="module")
def spec() -> ProductDeploymentSpec:
    return _spec(_ingress_toml())


@pytest.fixture(scope="module")
def rendered(spec: ProductDeploymentSpec) -> str:
    return render_nginx(spec)


@pytest.fixture(scope="module")
def spec_intermediate() -> ProductDeploymentSpec:
    return _spec(_ingress_toml(tls_policy="intermediate"))


@pytest.fixture(scope="module")
def rendered_intermediate(spec_intermediate: ProductDeploymentSpec) -> str:
    return render_nginx(spec_intermediate)


@pytest.fixture(scope="module")
def spec_trusted_proxies() -> ProductDeploymentSpec:
    return _spec(_ingress_toml(trusted_proxies=("edge-fleet", "corporate-egress")))


@pytest.fixture(scope="module")
def rendered_trusted_proxies(spec_trusted_proxies: ProductDeploymentSpec) -> str:
    return render_nginx(spec_trusted_proxies)


@pytest.fixture(scope="module")
def rendered_no_redirect() -> str:
    return render_nginx(_spec(_ingress_toml(redirect_http=False)))


@pytest.fixture(scope="module")
def rendered_no_security_headers() -> str:
    return render_nginx(_spec(_ingress_toml(security_headers=False)))


@pytest.fixture(scope="module")
def rendered_no_static() -> str:
    return render_nginx(_spec(_ingress_toml(static="none", uploads="none")))


@pytest.fixture(scope="module")
def rendered_no_websocket() -> str:
    return render_nginx(_spec(_NO_WEBSOCKET_INGRESS))


@pytest.fixture(scope="module")
def spec_no_ingress() -> ProductDeploymentSpec:
    return _spec("")


# ── shared helper ────────────────────────────────────────────────────────


def _location_block(rendered_text: str, path: str) -> str:
    """The body of `location <path> { ... }`, exclusive of the braces.

    Assumes the simple, non-nested location bodies this renderer emits —
    the marker is the exact 4-space-indented opening line this module
    always writes, and the block ends at the matching 4-space-indented
    closing brace.
    """
    marker = f"    location {path} {{\n"
    start = rendered_text.index(marker) + len(marker)
    end = rendered_text.index("\n    }", start)
    return rendered_text[start:end]


# ── module-level predicates + ADR-0018 sensitivity proofs ──────────────────

_UPSTREAM_BLOCK_RE = re.compile(r"upstream\s+(\S+)\s*\{(.*?)\}", re.DOTALL)
_SERVER_ADDRESS_RE = re.compile(r"server\s+([0-9A-Za-z.:_-]+):(\d+)\b")


def _every_upstream_member_is_loopback(rendered_text: str) -> bool:
    """True only if EVERY `server host:port` in EVERY upstream block is
    loopback, AND at least one such member was actually found (an upstream
    section with nothing in it is not a pass)."""
    found_any = False
    for _name, body in _UPSTREAM_BLOCK_RE.findall(rendered_text):
        for host, _port in _SERVER_ADDRESS_RE.findall(body):
            found_any = True
            if host != "127.0.0.1":
                return False
    return found_any


def _candidate_backup_present(
    rendered_text: str, product: str, role: str, candidate_port: int
) -> bool:
    pattern = (
        rf"upstream\s+{re.escape(product)}_{re.escape(role)}\s*\{{[\s\S]*?"
        rf"server\s+127\.0\.0\.1:{candidate_port}\s+backup\s+max_fails=1\s+"
        r"fail_timeout=1s;[\s\S]*?\}"
    )
    return re.search(pattern, rendered_text) is not None


def _tls_server(rendered: str) -> str:
    """Only the TLS server block.

    The port-80 block contains its own `location /` (the HTTPS redirect) and an
    ACME carve-out, and both appear BEFORE the real routes. A test indexing the
    whole file for `location / {` finds the redirect and then reports that the
    routes are out of order — which is a fact about the test, not the renderer.
    """
    marker = "listen 443"
    index = rendered.index(marker)
    return rendered[rendered.rindex("server {", 0, index) :]


def _code_only(rendered: str) -> str:
    """The configuration with its comments stripped.

    Every "this string is absent" assertion below reads THIS, not the raw
    output. The renderer deliberately EXPLAINS its omissions in comments — why
    `X-XSS-Protection` is not emitted, why `non_idempotent` is deliberately
    absent — and an assertion over the raw text fails on the sentence that
    documents the very rule it is checking.

    That is the same failure the Workspace `.dmui-*` guard hit when it flagged a
    class name appearing only in a comment explaining why inventing it was
    wrong, and it is worth naming here because the fix is not "delete the
    comment".
    """
    return "\n".join(line.split("#", 1)[0] for line in rendered.splitlines())


def test_the_loopback_predicate_is_true_on_the_real_rendered_config(
    rendered: str,
) -> None:
    assert _every_upstream_member_is_loopback(rendered)


def test_the_loopback_predicate_catches_a_non_loopback_upstream_member(
    rendered: str,
) -> None:
    """Negative control: `"0.0.0.0" not in rendered` would pass vacuously on
    an unrelated or empty string. Corrupting one real upstream member and
    watching the predicate flip proves it actually inspects them."""
    assert "server 127.0.0.1:8000" in rendered
    broken = rendered.replace("server 127.0.0.1:8000", "server 0.0.0.0:8000", 1)
    assert not _every_upstream_member_is_loopback(broken)


def test_the_candidate_backup_predicate_is_true_on_the_real_rendered_config(
    rendered: str, spec: ProductDeploymentSpec
) -> None:
    assert _candidate_backup_present(rendered, spec.product, "web", 18003)


def test_the_candidate_backup_predicate_catches_a_dropped_backup_keyword(
    rendered: str, spec: ProductDeploymentSpec
) -> None:
    marker = "server 127.0.0.1:18003 backup max_fails=1 fail_timeout=1s;"
    assert marker in rendered
    broken = rendered.replace(
        marker, "server 127.0.0.1:18003 max_fails=1 fail_timeout=1s;", 1
    )
    assert not _candidate_backup_present(broken, spec.product, "web", 18003)


# ── 1 & 2. upstream pair per ingress role, and the candidate-port rank ─────


def test_each_ingress_role_gets_an_upstream_with_a_primary_and_backup_server(
    rendered: str, spec: ProductDeploymentSpec
) -> None:
    for role, port in (("web", 8000), ("api", 8001), ("ws", 8002), ("stream", 8003)):
        name = f"{spec.product}_{role}"
        assert f"upstream {name} {{" in rendered
        assert f"server 127.0.0.1:{port} max_fails=1 fail_timeout=1s;" in rendered
    assert "keepalive 32;" in rendered


def test_the_candidate_port_is_the_base_plus_the_alphabetical_role_rank(
    rendered: str,
) -> None:
    # api < stream < web < ws, alphabetically — ranks 1..4 off base 18000.
    expected = {"api": 18001, "stream": 18002, "web": 18003, "ws": 18004}
    for candidate_port in expected.values():
        assert f"server 127.0.0.1:{candidate_port} backup" in rendered


def test_the_candidate_port_base_parameter_actually_shifts_the_candidate_ports(
    spec: ProductDeploymentSpec,
) -> None:
    shifted = render_nginx(spec, candidate_port_base=20000)
    assert "server 127.0.0.1:20001 backup max_fails=1 fail_timeout=1s;" in shifted
    assert "server 127.0.0.1:18001 backup" not in shifted


def test_the_upstream_comment_explains_the_candidate_is_the_handoff_mechanism(
    rendered: str,
) -> None:
    assert "handoff" in rendered.lower()
    assert "handoff_contract_pattern" in rendered


# ── 3. loopback only ─────────────────────────────────────────────────────


def test_every_upstream_member_address_is_loopback_only(rendered: str) -> None:
    assert _every_upstream_member_is_loopback(rendered)

    # test for naming the very string it forbids is the guard-reads-its-own-
    # documentation failure, one level up.
    assert "0.0.0.0" not in rendered  # noqa: S104


# ── 4. http -> https redirect ───────────────────────────────────────────


def test_http_traffic_is_redirected_to_https_with_an_acme_challenge_carveout(
    rendered: str,
) -> None:
    assert "listen 80;" in rendered
    assert "location /.well-known/acme-challenge/ {" in rendered
    assert "return 301 https://$host$request_uri;" in rendered


def test_http_redirect_server_is_omitted_when_redirect_http_is_false(
    rendered_no_redirect: str,
) -> None:
    assert "listen 80;" not in rendered_no_redirect
    assert "return 301" not in rendered_no_redirect


# ── 4 (bis: TLS policy) ─────────────────────────────────────────────────


def test_modern_tls_policy_uses_tls13_only_with_no_cipher_list(rendered: str) -> None:
    assert "ssl_protocols TLSv1.3;" in rendered
    assert "ssl_ciphers" not in rendered
    assert "ssl_prefer_server_ciphers off;" in rendered


def test_intermediate_tls_policy_uses_tls12_and_tls13_with_the_cipher_list(
    rendered_intermediate: str,
) -> None:
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in rendered_intermediate
    assert "ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:" in rendered_intermediate
    assert "ssl_prefer_server_ciphers off;" in rendered_intermediate


# ── 5. trusted forwarding headers, both branches ────────────────────────


def test_with_no_trusted_proxies_nginx_originates_the_forwarding_headers(
    rendered: str,
) -> None:
    assert "proxy_set_header X-Real-IP $remote_addr;" in rendered
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in rendered
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in rendered
    assert "set_real_ip_from" not in rendered


def test_with_trusted_proxies_nginx_trusts_them_via_real_ip_directives(
    rendered_trusted_proxies: str,
) -> None:
    # NAMES, not CIDRs. The substitution token survives into the rendered
    # bytes so a product repository never commits environment topology;
    # deployment control resolves it at authorization.
    assert "set_real_ip_from @SOURCE_SET:edge-fleet@;" in rendered_trusted_proxies
    assert "set_real_ip_from @SOURCE_SET:corporate-egress@;" in rendered_trusted_proxies
    assert "real_ip_header X-Forwarded-For;" in rendered_trusted_proxies
    assert "real_ip_recursive on;" in rendered_trusted_proxies
    assert "proxy_set_header X-Real-IP $remote_addr;" not in rendered_trusted_proxies


# ── 6. security headers, including the reinstated CSP ───────────────────


def test_security_headers_are_emitted_including_a_content_security_policy(
    rendered: str,
) -> None:
    assert "max-age=63072000; includeSubDomains; preload" in rendered
    assert 'add_header X-Frame-Options "SAMEORIGIN" always;' in rendered
    assert 'add_header X-Content-Type-Options "nosniff" always;' in rendered
    assert (
        'add_header Referrer-Policy "strict-origin-when-cross-origin" always;'
        in rendered
    )
    assert "Content-Security-Policy" in rendered
    assert "default-src 'self'" in rendered


def test_the_deprecated_x_xss_protection_header_is_never_emitted(rendered: str) -> None:
    assert "X-XSS-Protection" not in _code_only(rendered)


def test_security_headers_are_omitted_when_security_headers_is_false(
    rendered_no_security_headers: str,
) -> None:
    assert "Strict-Transport-Security" not in rendered_no_security_headers
    assert "Content-Security-Policy" not in rendered_no_security_headers


# ── 7. one location per route, own timeouts and body size ──────────────


def test_each_ingress_route_gets_its_own_location_with_its_own_settings(
    rendered: str,
) -> None:
    root = _location_block(_tls_server(rendered), "/")
    assert "proxy_read_timeout 60s;" in root
    assert "proxy_send_timeout 60s;" in root
    assert "client_max_body_size 10485760;" in root

    ws_block = _location_block(rendered, "/ws")
    assert "proxy_read_timeout 300s;" in ws_block

    stream_block = _location_block(rendered, "/api/v1/stream/live")
    assert "proxy_read_timeout 300s;" in stream_block


def test_locations_are_ordered_longest_path_first(rendered: str) -> None:
    order = ["/api/v1/stream/live", "/health", "/api/", "/ws", "/"]
    code = _code_only(_tls_server(rendered))
    positions = [code.index(f"location {path} {{") for path in order]
    assert positions == sorted(positions)


# ── 8. websocket routes ─────────────────────────────────────────────────


def test_a_websocket_route_gets_upgrade_connection_and_the_http_scope_map(
    rendered: str,
) -> None:
    block = _location_block(rendered, "/ws")
    assert "proxy_http_version 1.1;" in block
    assert "proxy_set_header Upgrade $http_upgrade;" in block
    assert "proxy_set_header Connection $connection_upgrade;" in block

    assert "map $http_upgrade $connection_upgrade {" in rendered
    map_index = rendered.index("map $http_upgrade $connection_upgrade {")
    upstream_index = rendered.index("upstream ")
    assert map_index < upstream_index
    assert "http {}" in rendered or "http scope" in rendered.lower()


def test_the_upgrade_map_is_omitted_when_no_route_is_a_websocket(
    rendered_no_websocket: str,
) -> None:
    assert "map $http_upgrade $connection_upgrade" not in rendered_no_websocket


# ── 9. sse routes (inventory defect D13) ────────────────────────────────


def test_an_sse_route_disables_buffering_and_sets_the_no_buffering_header(
    rendered: str,
) -> None:
    block = _location_block(rendered, "/api/v1/stream/live")
    assert "proxy_buffering off;" in block
    assert "proxy_cache off;" in block
    assert "proxy_set_header X-Accel-Buffering no;" in block
    assert "proxy_read_timeout 300s;" in block


# ── 10. proxy_next_upstream, deliberately without non_idempotent ───────


def test_proxy_next_upstream_retries_the_candidate_but_never_replays_writes(
    rendered: str,
) -> None:
    assert "proxy_next_upstream error timeout http_502 http_503 http_504;" in rendered
    assert "proxy_next_upstream_tries 2;" in rendered
    assert "non_idempotent" not in _code_only(rendered)


# ── 11. static / uploads ─────────────────────────────────────────────────


def test_static_image_assets_are_proxied_never_aliased_to_a_host_path(
    rendered: str,
) -> None:
    block = _location_block(rendered, "/static/")
    assert "proxy_pass http://acme_web;" in block
    assert "alias" not in block


def test_uploads_volume_is_aliased_to_a_host_path_with_a_short_cache(
    rendered: str,
) -> None:
    block = _location_block(rendered, "/uploads/")
    assert "alias /srv/volumes/acme_uploads/;" in block
    assert "expires 7d;" in block


def test_no_static_or_uploads_strategy_emits_no_location_for_that_class(
    rendered_no_static: str,
) -> None:
    assert "location /static/ {" not in rendered_no_static
    assert "location = /favicon.ico {" not in rendered_no_static
    assert "location /uploads/ {" not in rendered_no_static


def test_static_image_with_no_root_route_and_multiple_roles_raises_specerror() -> None:
    with pytest.raises(SpecError):
        render_nginx(_spec(_AMBIGUOUS_STATIC_INGRESS))


def test_a_role_at_two_different_ports_across_its_routes_raises_specerror() -> None:
    with pytest.raises(SpecError):
        render_nginx(_spec(_PORT_CONFLICT_INGRESS))


# ── 12. logging ───────────────────────────────────────────────────────────


def test_access_log_is_off_for_the_health_path_and_for_favicon(rendered: str) -> None:
    health_block = _location_block(rendered, "/health")
    assert "access_log off;" in health_block

    root_block = _location_block(rendered, "/")
    assert "access_log off;" not in root_block

    favicon_start = rendered.index("location = /favicon.ico {")
    favicon_end = rendered.index("\n    }", favicon_start)
    favicon_block = rendered[favicon_start:favicon_end]
    assert "access_log off;" in favicon_block


def test_access_and_error_log_paths_are_derived_from_the_product(
    rendered: str, spec: ProductDeploymentSpec
) -> None:
    assert f"access_log /var/log/nginx/{spec.product}.access.log;" in rendered
    assert f"error_log /var/log/nginx/{spec.product}.error.log;" in rendered


# ── 13. generated header ───────────────────────────────────────────────


def test_the_generated_header_names_the_product_and_both_digests(
    rendered: str, spec: ProductDeploymentSpec
) -> None:
    header = "\n".join(rendered.splitlines()[:6])
    assert spec.product in header
    assert spec.image_digest in header
    assert spec.manifest_digest in header
    assert "GENERATED by dotmac-deployment-foundation" in header
    assert "dotmac-deploy render" in header


# ── 14. server_tokens ───────────────────────────────────────────────────


def test_server_tokens_off_is_emitted_and_server_tokens_on_never_is(
    rendered: str,
) -> None:
    assert "server_tokens off;" in rendered
    assert "server_tokens on" not in rendered


# ── no ingress ────────────────────────────────────────────────────────


def test_a_spec_with_no_ingress_renders_the_empty_string(
    spec_no_ingress: ProductDeploymentSpec,
) -> None:
    assert render_nginx(spec_no_ingress) == ""


def test_the_digest_of_an_ingressless_spec_is_the_digest_of_the_empty_string(
    spec_no_ingress: ProductDeploymentSpec,
) -> None:
    import hashlib

    expected = f"sha256:{hashlib.sha256(b'').hexdigest()}"
    assert render_nginx_digest(spec_no_ingress) == expected


# ── determinism ──────────────────────────────────────────────────────────


def test_rendering_the_same_spec_twice_produces_identical_bytes(
    spec: ProductDeploymentSpec,
) -> None:
    assert render_nginx(spec) == render_nginx(spec)


def test_rendered_nginx_has_exactly_one_trailing_newline(rendered: str) -> None:
    """Generated files must survive end-of-file-fixer byte-for-byte."""

    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")


def test_the_digest_is_the_sha256_of_the_rendered_bytes(
    spec: ProductDeploymentSpec, rendered: str
) -> None:
    import hashlib

    expected = f"sha256:{hashlib.sha256(rendered.encode('utf-8')).hexdigest()}"
    assert render_nginx_digest(spec) == expected


def test_the_digest_is_stable_across_calls(spec: ProductDeploymentSpec) -> None:
    assert render_nginx_digest(spec) == render_nginx_digest(spec)


# ── handoff_contract_pattern: the engine/renderer coupling proof ───────


def test_handoff_contract_pattern_matches_the_rendered_upstream_block(
    spec: ProductDeploymentSpec, rendered: str
) -> None:
    for role in ("web", "api", "ws", "stream"):
        pattern = handoff_contract_pattern(spec, role)
        assert re.search(pattern, rendered) is not None, role


def test_handoff_contract_pattern_stops_matching_after_an_upstream_rename(
    spec: ProductDeploymentSpec, rendered: str
) -> None:
    """The coupling proof: if the renderer's upstream naming ever drifts
    from what `handoff_contract_pattern` expects, the pattern must fail —
    otherwise the deployment engine could pass its handoff check against a
    site it never actually verified."""
    pattern = handoff_contract_pattern(spec, "web")
    assert re.search(pattern, rendered) is not None

    assert "upstream acme_web {" in rendered
    broken = rendered.replace("upstream acme_web {", "upstream acme_web_v2 {", 1)
    assert re.search(pattern, broken) is None


def test_handoff_contract_pattern_stops_matching_when_the_backup_keyword_is_dropped(
    spec: ProductDeploymentSpec, rendered: str
) -> None:
    pattern = handoff_contract_pattern(spec, "web")
    marker = "server 127.0.0.1:18003 backup max_fails=1 fail_timeout=1s;"
    assert marker in rendered
    broken = rendered.replace(
        marker, "server 127.0.0.1:18003 max_fails=1 fail_timeout=1s;", 1
    )
    assert re.search(pattern, broken) is None
