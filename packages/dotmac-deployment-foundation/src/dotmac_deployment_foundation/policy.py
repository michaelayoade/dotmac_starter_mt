"""The ingress projections: the `IngressPolicy.v1` section, and its two plans.

This module answers "what does this descriptor expose, and what must a provider
do about it". It does NOT own a digest — `document.py` owns the one canonical
document and the one digest taken over it, because two digests over overlapping
content is two answers to "what was signed".

`ingress_policy_document()` is the section `DeploymentDescriptorDocument.v1`
embeds. It carries the derived facts a reader cannot recompute from the raw
descriptor without this facility: which families each publication serves, the
endpoint token an approval covers, which providers can enforce each declared
control, the derived firewall rules, and the set of public endpoints deployment
control needs in order to derive sensitivity rather than trust a caller's flag.

It carries no resolved address. A bind MATERIAL name is in; a bind ADDRESS is
not — see `document.py` for why that boundary is the whole point of the split.
"""

from __future__ import annotations

from typing import Any

from . import ingress
from .spec import PortPublication, ProductDeploymentSpec

__all__ = [
    "build_edge_plan",
    "build_firewall_plan",
    "ingress_policy_document",
    "public_endpoint_tokens",
]


# ── the canonical document ──────────────────────────────────────────────────


def _publication_document(
    spec: ProductDeploymentSpec, role_code: str, publication: PortPublication
) -> dict[str, Any]:
    families = list(publication.families)
    return {
        "address_family": publication.address_family,
        "approval_ref": publication.approval_ref,
        "authentication": publication.authentication,
        # The bind MATERIAL name, never the bind ADDRESS. A loopback literal is
        # derivable from `exposure` plus `family` plus this facility version,
        # all three of which the canonical document carries; a routable address
        # is resolved by deployment control and must not be able to reach a
        # digest a product repository produces.
        "binds": [
            {
                "family": family,
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
    return document


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
