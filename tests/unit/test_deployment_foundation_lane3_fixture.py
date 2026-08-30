"""The Lane 3 fixture must be able to FAIL the checks it feeds.

The 2026-08-29 fixture published only `loopback` and `none`. Neither derives a
firewall rule, so `build_firewall_plan` returned empty and gate item 6
("firewall re-observation") passed without observing anything. A check whose
fixture cannot exercise it is vacuous, and a vacuous check is worse than a
missing one because it reports coverage.

This file is the ratchet. Every assertion here is a property the fixture must
keep, so a future simplification cannot quietly return it to a state where the
gate passes on an empty plan.
"""

from __future__ import annotations

import pathlib

import pytest
from dotmac_deployment_foundation.policy import build_firewall_plan
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

FIXTURE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts"
    / "exposure-rehearsal"
    / "product.toml"
)


@pytest.fixture(scope="module")
def spec() -> ProductDeploymentSpec:
    return ProductDeploymentSpec.load(str(FIXTURE))


def test_the_fixture_is_a_real_descriptor_not_a_special_format(
    spec: ProductDeploymentSpec,
) -> None:
    assert spec.product == "lane3_exposure"
    assert spec.environment == "rehearsal"


def test_the_firewall_plan_is_NOT_empty(spec: ProductDeploymentSpec) -> None:
    """The single most important property here. An empty plan is what made
    item 6 vacuous, and an empty plan cannot fail."""
    assert build_firewall_plan(spec), (
        "the Lane 3 fixture derives no firewall rules, so gate item 6 would "
        "pass without observing anything — exactly the 2026-08-29 defect"
    )


def test_the_fixture_covers_every_exposure_shape_the_lane_asserts(
    spec: ProductDeploymentSpec,
) -> None:
    """Each shape proves something the others cannot, so all four must be
    present: without the ipv4-only control, a host with no IPv6 reads the same
    as a host where dual-stack loopback worked."""
    by_port = {pub.host: pub for _code, pub in spec.publications}
    assert by_port[18443].exposure == "loopback"
    assert by_port[18443].address_family == "dual_stack"
    assert by_port[18444].exposure == "none"
    assert by_port[18445].exposure == "loopback"
    assert by_port[18445].address_family == "ipv4"
    assert by_port[19001].exposure == "private"


def test_the_private_publication_names_a_source_set_and_holds_no_address(
    spec: ProductDeploymentSpec,
) -> None:
    """A NAME, resolved by deployment control at authorization. If the
    descriptor held addresses, the rendered file could not live in Git."""
    private = next(p for _c, p in spec.publications if p.exposure == "private")
    assert private.source_set == "lane3-authorized"
    for rule in build_firewall_plan(spec):
        if rule.source_set:
            assert "@SOURCE_SET:" in rule.render()


def test_the_private_publication_is_REMAPPED(spec: ProductDeploymentSpec) -> None:
    """The only shape that can prove original-destination matching. With
    host == container, a wrong `--dport` rule and a correct `--ctorigdstport`
    rule are indistinguishable, and the test passes for the wrong reason."""
    private = next(p for _c, p in spec.publications if p.exposure == "private")
    assert private.host != private.container, (
        "a non-remapped private port cannot distinguish --ctorigdstport from "
        "--dport, so it cannot prove the match is correct"
    )


def test_ipv4_matches_original_destination_and_ipv6_matches_dport(
    spec: ProductDeploymentSpec,
) -> None:
    """The measured asymmetry, asserted. Post-DNAT the v4 packet's destination
    is the CONTAINER port, so `--dport 19001` in DOCKER-USER matches nothing
    while reading in a diff exactly like a rule that works. There is no DNAT on
    the v6 path — docker-proxy accepts on the published port — so `--dport` is
    both correct and the only thing that matches there."""
    rules = build_firewall_plan(spec)
    v4 = [r for r in rules if r.family == "ipv4"]
    v6 = [r for r in rules if r.family == "ipv6"]
    assert v4 and v6
    for rule in v4:
        assert rule.chain == "DOCKER-USER"
        assert "--ctorigdstport 19001" in rule.render()
        assert "--dport" not in rule.render()
    for rule in v6:
        assert rule.chain == "INPUT", (
            "an ip6tables DOCKER-USER rule for a published port is INERT: the "
            "chain is jumped only from FORWARD while a v6 publish terminates "
            "on INPUT inside docker-proxy"
        )
        assert "--dport 19001" in rule.render()


def test_every_allowlist_ends_in_a_terminal_drop(spec: ProductDeploymentSpec) -> None:
    """An allowlist whose last rule is an ACCEPT enforces nothing: everything
    unmatched falls through to the chain policy, which on a Docker host is
    ACCEPT. Found in production more than once, read by two people as
    containment."""
    rules = build_firewall_plan(spec)
    for family in ("ipv4", "ipv6"):
        family_rules = [r for r in rules if r.family == family]
        assert family_rules[-1].action == "DROP"
        assert family_rules[-1].terminal


def test_the_ports_are_unique_and_high(spec: ProductDeploymentSpec) -> None:
    hosts = [pub.host for _code, pub in spec.publications]
    assert len(hosts) == len(set(hosts))
    assert all(port > 1024 for port in hosts)
