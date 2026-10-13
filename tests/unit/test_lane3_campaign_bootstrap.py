"""Bootstrap capture canaries; execution belongs exclusively to hosted CI."""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import lane3_campaign_bootstrap as cb  # noqa: E402
import lane3_handoff_protocol as hp  # noqa: E402
import lane3_handoff_resolver as hr  # noqa: E402

ORIGINS = ["https://a.example", "https://z.example"]
RESOLVERS = ["9.9.9.9", "149.112.112.112"]
NOW = 1_000_000_000


def document():
    return {
        "schema": cb.CONFIG_SCHEMA,
        "resolvers": RESOLVERS,
        "status": "PROPOSED_NOT_ADMITTED",
        "inventory_sha256": "a" * 64,
        "observation_sha256": "b" * 64,
        "max_union_addresses": 32,
        "minimum_ttl_seconds": 45,
        "snapshot_cap_seconds": 60,
        "transport": "TCP",
        "module_https": [],
        "new_origin_or_alias": "STOP",
        "shared_ip_representative": (
            "lexicographically-smallest-resolving-approved-origin"
        ),
        "resolver_comparison": "equal-normalized-chain-and-address-set",
        "shared_ip_limitation": (
            "Synthetic shared IP rules do not select an HTTP hostname."
        ),
        "origins": [
            {
                "origin": origin,
                "purpose": "runner-https",
                "aliases": {"exact": ["edge.example.net"], "namespaces": []},
            }
            for origin in ORIGINS
        ],
    }


def routable(address):
    return address in ipaddress.ip_network("203.0.113.0/24")


def resolver(origin, *, resolver, **kwargs):
    captured = NOW if origin == ORIGINS[0] else NOW + 2_000_000_000
    return hr.Snapshot(
        origin=origin,
        resolver=resolver,
        chain=(origin[8:], "edge.example.net"),
        addresses=((4, "203.0.113.10"),),
        ttl_s=45 if resolver == RESOLVERS[0] else 300,
        captured_at_monotonic_ns=captured,
        expires_at_monotonic_ns=captured + 45_000_000_000,
        digest=hp.digest({"origin": origin, "resolver": resolver}),
    )


def capture(resolve=resolver, **kwargs):
    return cb.capture(
        cb.parse_config(document()),
        deadline_ns=NOW + 60_000_000_000,
        clock_ns=lambda: NOW + 3_000_000_000,
        resolve=resolve,
        routable=routable,
        **kwargs,
    )


def test_shared_ip_uses_actual_lexical_representative_and_keeps_every_snapshot():
    frozen = capture()
    assert frozen.manifest == {
        "schema": "dotmac.lane3.handoff-bootstrap.v1",
        "destinations": [
            {
                "purpose": "runner-https",
                "origin": ORIGINS[0],
                "family": 4,
                "address": "203.0.113.10",
                "port": 443,
            }
        ],
    }
    assert len(frozen.snapshots) == 4
    assert {(s.origin, s.resolver) for s in frozen.snapshots} == {
        (origin, resolver) for origin in ORIGINS for resolver in RESOLVERS
    }
    assert frozen.digest == hp.digest(frozen.manifest)
    assert frozen.expires_at_monotonic_ns == NOW + 45_000_000_000
    assert frozen.remaining_ns(NOW + 44_000_000_000) == 1_000_000_000
    frozen.require_same(frozen.manifest)


@pytest.mark.parametrize("defect", ["chain", "addresses"])
def test_one_resolver_disagreement_refuses_the_entire_capture(defect):
    def disagree(origin, **kwargs):
        snapshot = resolver(origin, **kwargs)
        if kwargs["resolver"] == RESOLVERS[1]:
            import dataclasses

            snapshot = dataclasses.replace(
                snapshot,
                **{
                    defect: (origin[8:],)
                    if defect == "chain"
                    else ((4, "203.0.113.11"),)
                },
            )
        return snapshot

    with pytest.raises(cb.BootstrapRefused, match="bootstrap.dns_agreement"):
        capture(disagree)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(module_https=["https://module.example"]),
        lambda d: d.update(resolvers=[RESOLVERS[0], RESOLVERS[0]]),
        lambda d: d.update(resolvers=["dns.example", RESOLVERS[1]]),
        lambda d: d["origins"].append(copy.deepcopy(d["origins"][0])),
        lambda d: d["origins"][0].pop("aliases"),
        lambda d: d["origins"][0]["aliases"].update(namespaces=["example.net"]),
        lambda d: d["origins"][0]["aliases"].update(exact=["*.example.net"]),
        lambda d: d.update(approved=True),
    ],
)
def test_config_is_exact_finite_and_has_no_approval_or_namespace_escape(change):
    doc = copy.deepcopy(document())
    change(doc)
    with pytest.raises(cb.BootstrapRefused):
        cb.parse_config(doc)


def test_measured_external_config_rejects_wrong_bytes_and_symlink(tmp_path):
    path = tmp_path / "approved.json"
    raw = hp.canonical_json(document())
    path.write_bytes(raw)
    path.chmod(0o600)
    pin = hashlib.sha256(raw).hexdigest()
    assert cb.load_config(path, pin) == cb.parse_config(document())
    with pytest.raises(cb.BootstrapRefused):
        cb.load_config(path, "0" * 64)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(cb.BootstrapRefused):
        cb.load_config(link, pin)


def test_original_earliest_expiry_is_not_restarted_at_completion():
    def stale(origin, **kwargs):
        import dataclasses

        return dataclasses.replace(
            resolver(origin, **kwargs), expires_at_monotonic_ns=NOW
        )

    with pytest.raises(cb.BootstrapRefused, match="bootstrap.expired"):
        capture(stale)


def test_fresh_capture_must_match_frozen_manifest_exactly():
    frozen = capture()
    changed = frozen.manifest
    changed["destinations"][0]["origin"] = ORIGINS[1]
    with pytest.raises(cb.BootstrapRefused, match="bootstrap.manifest_changed"):
        frozen.require_same(changed)


def test_parent_deadline_is_never_reset():
    with pytest.raises(cb.BootstrapRefused, match="bootstrap.deadline"):
        cb.capture(cb.parse_config(document()), deadline_ns=NOW, clock_ns=lambda: NOW)


def test_one_failed_resolver_has_no_partial_manifest_or_value_bearing_refusal():
    def failed(origin, **kwargs):
        if kwargs["resolver"] == RESOLVERS[1]:
            raise RuntimeError("private-fixture.example must not escape")
        return resolver(origin, **kwargs)

    with pytest.raises(cb.BootstrapRefused) as caught:
        capture(failed)
    assert str(caught.value) == "bootstrap.dns"
    assert caught.value.__suppress_context__


def test_capacity_counts_the_unique_address_union_without_truncation():
    import dataclasses

    doc = document()
    doc["origins"] = [
        {
            "origin": f"https://origin{i:02}.example",
            "purpose": "runner-https",
            "aliases": {"exact": [], "namespaces": []},
        }
        for i in range(17)
    ]

    def many(origin, **kwargs):
        i = int(origin.split("origin")[1][:2])
        return dataclasses.replace(
            resolver(origin, **kwargs),
            chain=(origin[8:],),
            addresses=((4, f"203.0.113.{i * 2 + 1}"), (4, f"203.0.113.{i * 2 + 2}")),
        )

    with pytest.raises(cb.BootstrapRefused, match="bootstrap.dns"):
        cb.capture(
            cb.parse_config(doc),
            deadline_ns=NOW + 60_000_000_000,
            clock_ns=lambda: NOW + 3_000_000_000,
            resolve=many,
            routable=routable,
        )


def test_manifest_copy_cannot_rewrite_held_frozen_bytes():
    frozen = capture()
    before = frozen.manifest_bytes
    changed = frozen.manifest
    changed["destinations"].clear()
    assert frozen.manifest_bytes == before
    assert len(frozen.manifest["destinations"]) == 1


def test_resolver_calls_receive_per_original_alias_policy_and_original_clock():
    observed = []

    def checking(origin, **kwargs):
        observed.append((origin, kwargs["resolver"], kwargs["aliases"]))
        assert kwargs["clock_ns"]() == NOW + 3_000_000_000
        assert kwargs["max_addresses"] == hp.MAX_ADDRESSES
        assert callable(kwargs["exchange"])
        return resolver(origin, **kwargs)

    capture(checking)
    assert len(observed) == 4
    assert all(policy.exact == {"edge.example.net"} for _, _, policy in observed)
    assert all(not policy.namespaces for _, _, policy in observed)


def test_private_config_and_frozen_provenance_are_redacted_from_repr():
    frozen = capture()
    assert repr(cb.parse_config(document())) == "BootstrapConfig(<private>)"
    assert repr(frozen) == "FrozenBootstrap(<private>)"
    assert len(frozen.provenance["snapshots"]) == 4
    assert frozen.provenance_digest == hp.digest(frozen.provenance)


@pytest.mark.parametrize("defect", ["future_capture", "wrong_origin", "wrong_resolver"])
def test_resolver_output_cannot_change_identity_or_claim_a_future_capture(defect):
    import dataclasses

    def bad(origin, **kwargs):
        changes = {
            "future_capture": {"captured_at_monotonic_ns": NOW + 4_000_000_000},
            "wrong_origin": {"origin": "https://wrong.example"},
            "wrong_resolver": {"resolver": "203.0.113.53"},
        }
        return dataclasses.replace(resolver(origin, **kwargs), **changes[defect])

    with pytest.raises(cb.BootstrapRefused, match="bootstrap.snapshot"):
        capture(bad)
