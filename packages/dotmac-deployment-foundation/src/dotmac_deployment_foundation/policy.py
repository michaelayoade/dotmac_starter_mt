"""The non-mutating projection: one canonical ingress document, and its digest.

## Why this module exists at all

`dotmac-deployment-control` owns authorization. Its frozen ``plan_snapshot``
embeds the desired specification verbatim and hashes it into ``plan_digest``,
and `approve_plan` requires the approver's evidence to carry that exact digest.
Everything placed inside the desired specification is therefore digest-covered
with no field allow-list to drift.

The gap that made ingress unauthorizable was earlier in the chain: this
facility could parse a descriptor and could render assets, but had no
**canonical document** in between. Its only digests were of rendered bytes.
There was nothing to put into ``desired_spec``, so no ingress fact was inside
any plan digest at all. :func:`ingress_policy_document` is that missing hop,
and every other function here is a projection of it.

## The five canonicalization rules, and what each one prevents

1. **String keys only, and values restricted to string, integer, boolean and
   lists of those.** A digest must be re-derivable from stored JSONB months
   later by a reader who has only the JSON.
2. **No floats.** ``0.1`` does not round-trip identically through every JSON
   implementation, and a digest that depends on a float is a digest that
   sometimes differs from itself.
3. **No nulls, ever.** An unset axis is MATERIALIZED to its default rather than
   omitted, because "absent" and "null" and "default" are three states in JSON
   and one state in the descriptor.
4. **Defaults are materialized at normalization, not at read.** Ten of this
   schema's fields are supplied by the parser rather than written by the
   product. Digesting the raw TOML would let a change to one of those defaults
   alter running behaviour under an unchanged digest.
5. **The schema string AND the exact facility version are inside the
   document.** ``exposure = "public"`` is a word; its MEANING is the socket
   this version's renderer emits. Without the version in the digest, upgrading
   the facility changes running exposure while the approved digest stays
   identical — the approval would then cover a decision nobody made.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import ingress
from .errors import SpecError
from .spec import SCHEMA, PortPublication, ProductDeploymentSpec
from .version import VERSION

__all__ = [
    "build_edge_plan",
    "build_firewall_plan",
    "ingress_policy_digest",
    "ingress_policy_document",
    "public_endpoint_tokens",
]


# ── canonical JSON ──────────────────────────────────────────────────────────


def _canonical(value: Any, *, where: str) -> Any:
    """Refuse anything the digest cannot re-derive from stored JSON."""
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_canonical(item, where=f"{where}[]") for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SpecError(
                    f"{where}: a non-string key ({key!r}) cannot round-trip "
                    "through JSON, so the digest could not be re-derived",
                )
            out[key] = _canonical(item, where=f"{where}.{key}")
        return out
    if value is None:
        raise SpecError(
            f"{where}: null is refused. An unset axis is materialized to its "
            "default; 'absent', 'null' and 'defaulted' are three states in JSON "
            "and must be one state here",
        )
    if isinstance(value, float):
        raise SpecError(
            f"{where}: a float is refused. It does not round-trip identically "
            "through every JSON implementation, and a digest that depends on "
            "one sometimes differs from itself",
        )
    raise SpecError(f"{where}: {type(value).__name__} is not a canonical value")


def _digest(document: dict[str, Any]) -> str:
    payload = json.dumps(
        _canonical(document, where="document"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── the canonical document ──────────────────────────────────────────────────


def _publication_document(
    spec: ProductDeploymentSpec, role_code: str, publication: PortPublication
) -> dict[str, Any]:
    families = list(publication.families)
    return {
        "address_family": publication.address_family,
        "approval_ref": publication.approval_ref,
        "authentication": publication.authentication,
        "binds": [
            {
                "family": family,
                "host_ip": publication.host_ip(role_code, family),
                "material": (
                    ""
                    if publication.exposure == "loopback"
                    else ingress.bind_material_name(role_code, publication.host, family)
                ),
            }
            for family in families
        ],
        "container_port": publication.container,
        "endpoint_tokens": [
            ingress.endpoint_token(
                product=spec.product,
                environment=spec.environment,
                role=role_code,
                protocol=publication.protocol,
                family=family,
                host_port=publication.host,
                exposure=publication.exposure,
            )
            for family in families
        ],
        "exposure": publication.exposure,
        "families": families,
        "host_port": publication.host,
        "protocol": publication.protocol,
        "providers": sorted(
            ingress.available_providers(
                families=publication.families,
                protocol=publication.protocol,
                tls=publication.tls,
            )
        ),
        "rationale_url": publication.rationale_url,
        "role": role_code,
        "source_set": publication.source_set,
        "telemetry": publication.telemetry,
        "tls": publication.tls,
    }


def _edge_document(spec: ProductDeploymentSpec) -> dict[str, Any]:
    edge = spec.ingress
    if edge is None:
        # Materialized rather than omitted: rule 3. A deployment with no edge
        # is a fact worth digesting, and an absent key is indistinguishable
        # from a key a future writer forgot.
        return {
            "declared": False,
            "address_family": "",
            "approval_ref": "",
            "authentication": "",
            "endpoint_tokens": [],
            "exposure": "",
            "families": [],
            "host": "",
            "rationale_url": "",
            "redirect_http": False,
            "routes": [],
            "security_headers": False,
            "source_set": "",
            "tls_policy": "",
            "trusted_proxies": [],
            "upstream_address_family": "",
        }
    families = list(edge.families)
    return {
        "declared": True,
        "address_family": edge.address_family,
        "approval_ref": edge.approval_ref,
        "authentication": edge.authentication,
        "endpoint_tokens": [
            ingress.endpoint_token(
                product=spec.product,
                environment=spec.environment,
                role="edge",
                protocol="tcp",
                family=family,
                host_port=port,
                exposure=edge.exposure,
            )
            for family in families
            # 443 always; 80 only when the edge actually answers there to
            # redirect. A token set that claimed a listener the renderer does
            # not emit would make approval coverage wrong in the safe-looking
            # direction, which is still wrong.
            for port in ((80, 443) if edge.redirect_http else (443,))
        ],
        "exposure": edge.exposure,
        "families": families,
        "host": edge.host,
        "rationale_url": edge.rationale_url,
        "redirect_http": edge.redirect_http,
        "routes": [
            {
                "max_body_bytes": route.max_body_bytes,
                "path": route.path,
                "read_timeout_seconds": route.read_timeout_seconds,
                "role": route.role,
                "send_timeout_seconds": route.send_timeout_seconds,
                "sse": route.sse,
                "upstream_port": route.port,
                "websocket": route.websocket,
            }
            for route in sorted(edge.routes, key=lambda item: item.path)
        ],
        "security_headers": edge.security_headers,
        "source_set": edge.source_set,
        "tls_policy": edge.tls_policy,
        "trusted_proxies": sorted(edge.trusted_proxies),
        "upstream_address_family": edge.upstream_address_family,
    }


def ingress_policy_document(spec: ProductDeploymentSpec) -> dict[str, Any]:
    """The normalized `IngressPolicy.v1` document for `spec`.

    Same descriptor in, same document out — nothing here reads a clock, an
    environment variable or a filesystem, so the digest of this document is a
    property of the descriptor and this facility version, and of nothing else.
    """
    publications = [
        _publication_document(spec, code, publication)
        for code, publication in spec.publications
    ]
    document: dict[str, Any] = {
        "schema": ingress.INGRESS_POLICY_SCHEMA,
        # Rule 5. The version is not decoration: it is what makes the word
        # "public" mean one specific rendered socket rather than whatever the
        # installed renderer currently thinks.
        "foundation_version": VERSION,
        "descriptor_schema": SCHEMA,
        "product": spec.product,
        "environment": spec.environment,
        "edge": _edge_document(spec),
        "publications": publications,
        "firewall": [
            {
                "action": rule.action,
                "chain": rule.chain,
                "family": rule.family,
                "host_port": rule.host_port,
                "protocol": rule.protocol,
                "rule": rule.render(),
                "source_set": rule.source_set,
                "terminal": rule.terminal,
            }
            for rule in build_firewall_plan(spec)
        ],
        "public_endpoints": list(public_endpoint_tokens(spec)),
    }
    return _canonical(document, where="document")


def ingress_policy_digest(spec: ProductDeploymentSpec) -> str:
    """``sha256:<hex>`` over the canonical document.

    This is the value that belongs inside `dotmac-deployment-control`'s
    ``desired_spec`` so that ingress becomes part of ``plan_digest``. It is
    NOT an authorization and this package cannot make it one.
    """
    return _digest(ingress_policy_document(spec))


def public_endpoint_tokens(spec: ProductDeploymentSpec) -> tuple[str, ...]:
    """Every derived endpoint token whose exposure is ``public``.

    Deployment control's sensitivity check is meant to read this rather than
    trust a caller-supplied ``requires_approval`` flag: a plan carrying a
    non-empty public-endpoint set and ``requires_approval = False`` is a plan
    that skipped its own gate by its own terms.
    """
    tokens: list[str] = []
    for code, publication in spec.publications:
        if publication.exposure != "public":
            continue
        tokens.extend(
            ingress.endpoint_token(
                product=spec.product,
                environment=spec.environment,
                role=code,
                protocol=publication.protocol,
                family=family,
                host_port=publication.host,
                exposure=publication.exposure,
            )
            for family in publication.families
        )
    edge = spec.ingress
    if edge is not None and edge.exposure == "public":
        tokens.extend(
            ingress.endpoint_token(
                product=spec.product,
                environment=spec.environment,
                role="edge",
                protocol="tcp",
                family=family,
                host_port=port,
                exposure=edge.exposure,
            )
            for family in edge.families
            for port in ((80, 443) if edge.redirect_http else (443,))
        )
    return tuple(sorted(set(tokens)))


# ── provider-neutral plans ──────────────────────────────────────────────────


def build_edge_plan(spec: ProductDeploymentSpec) -> tuple[ingress.EdgeEndpoint, ...]:
    """The edge's requirements, with no provider named anywhere in the result.

    An Nginx renderer and a Caddy renderer both consume this. Neither is named
    in a branch, because an edge is a replaceable transport and the contract
    owner is this facility.
    """
    edge = spec.ingress
    if edge is None:
        return ()
    return tuple(
        ingress.EdgeEndpoint(
            host=edge.host,
            path=route.path,
            role=route.role,
            upstream_port=route.port,
            tls_mode="terminate",
            tls_policy=edge.tls_policy,
            authentication=edge.authentication,
            source_set=edge.source_set,
            websocket=route.websocket,
            sse=route.sse,
            max_body_bytes=route.max_body_bytes,
            read_timeout_seconds=route.read_timeout_seconds,
            send_timeout_seconds=route.send_timeout_seconds,
            security_headers=edge.security_headers,
            redirect_http=edge.redirect_http,
        )
        for route in sorted(edge.routes, key=lambda item: item.path)
    )


def build_firewall_plan(
    spec: ProductDeploymentSpec,
) -> tuple[ingress.FirewallRule, ...]:
    """Derived defense-in-depth for every routable publication.

    Derived, and second. The socket binding is the control; these rules are the
    layer behind it, and the ordering is not rhetorical — the fleet has already
    had a port whose only containment was a live iptables rule that did not
    survive a reboot, and a v6 rule in a chain that could never fire.

    Nothing here is emitted for ``none`` or ``loopback``: there is no socket to
    filter in the first case, and in the second the kernel never consults a
    filter for a loopback-bound listener reached from off-host, because the
    packet has nowhere to arrive.
    """
    rules: list[ingress.FirewallRule] = []
    for _code, publication in spec.publications:
        if publication.exposure in ("none", "loopback"):
            continue
        for family in publication.families:
            rules.extend(
                ingress.firewall_rules_for(
                    family=family,
                    protocol=publication.protocol,
                    host_port=publication.host,
                    source_set=publication.source_set,
                )
            )
    return tuple(rules)
