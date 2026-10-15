"""Actual coordinator freshness ordering; executable evidence is hosted-only."""

from __future__ import annotations

import hashlib
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import lane3_campaign_bootstrap as cb
import lane3_handoff_coordinator as co
import lane3_handoff_protocol as hp
import lane3_handoff_resolver as hr


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    clock = [1_000_000_000]
    events = []
    manifest = {
        "schema": "dotmac.lane3.handoff-bootstrap.v1",
        "destinations": [
            {
                "purpose": "runner-https",
                "origin": "https://runner.example",
                "family": 4,
                "address": "9.9.9.9",
                "port": 443,
            }
        ],
    }
    proposal = {
        "schema": cb.CONFIG_SCHEMA,
        "status": "PROPOSED_NOT_ADMITTED",
        "inventory_sha256": "a" * 64,
        "observation_sha256": "b" * 64,
        "origins": [
            {
                "origin": "https://runner.example",
                "purpose": "runner-https",
                "aliases": {"exact": [], "namespaces": []},
            }
        ],
        "resolvers": ["9.9.9.9", "149.112.112.112"],
        "max_union_addresses": 32,
        "minimum_ttl_seconds": 1,
        "maximum_observation_age_seconds": 60,
        "profile": hp.BOOTSTRAP_OBSERVATION_PROFILE,
        "transport": "TCP",
        "module_https": [],
        "new_origin_or_alias": "STOP",
        "shared_ip_representative": (
            "lexicographically-smallest-resolving-approved-origin"
        ),
        "resolver_comparison": "equal-normalized-chain-address-union",
        "shared_ip_limitation": "Synthetic shared address fixture.",
    }

    def held(name, value):
        raw = hp.canonical_json(value)
        path = tmp_path / name
        path.write_bytes(raw)
        path.chmod(0o600)
        return path, hashlib.sha256(raw).hexdigest()

    manifest_path, manifest_digest = held("manifest.json", manifest)
    proposal_path, proposal_digest = held("proposal.json", proposal)
    cfg = SimpleNamespace(
        purpose="qualification",
        state_path=tmp_path / "state.json",
        runner_group_id=4,
        runner_org="synthetic-org",
        runner_labels={"synthetic"},
        bootstrap_manifest_path=manifest_path,
        bootstrap_manifest_sha256=manifest_digest,
        bootstrap_proposal_path=proposal_path,
        bootstrap_proposal_sha256=proposal_digest,
    )
    qualification = {"synthetic": "qualified"}
    co.save_state(
        cfg.state_path,
        {
            "schema": co.STATE_SCHEMA,
            "lease_id": "a" * 32,
            "qualification": qualification,
            "bootstrap_manifest_digest": manifest_digest,
            "bootstrap_proposal_sha256": proposal_digest,
        },
    )
    monkeypatch.setattr(co, "prelaunch_binding", lambda *args: qualification)
    monkeypatch.setattr(co.time, "monotonic_ns", lambda: clock[0])
    snapshots = tuple(
        hr.BootstrapObservation(
            "https://runner.example",
            resolver,
            ("runner.example",),
            ((4, "9.9.9.9"),),
            60,
            clock[0],
            clock[0],
            hp.digest({"resolver": resolver}),
            (),
        )
        for resolver in proposal["resolvers"]
    )
    frozen = cb.FrozenBootstrap(
        snapshots, hp.canonical_json(manifest), manifest_digest, proposal_digest
    )

    def capture(config, *, deadline_ns):
        events.append(("capture", deadline_ns, config.proposal_sha256))
        return frozen

    monkeypatch.setattr(cb, "capture", capture)

    def channel(op, request):
        events.append((op, request))
        if op == "timing":
            return {"challenge": "c" * 64}
        return {"state": "BOOTSTRAP"}

    def api(path, method, payload):
        events.append(("jit", path, payload))
        return {"runner": {"id": 123}, "encoded_jit_config": "c3ludGhldGlj"}

    return SimpleNamespace(
        cfg=cfg,
        clock=clock,
        events=events,
        frozen=frozen,
        capture=capture,
        channel=channel,
        api=api,
    )


def test_actual_launch_challenges_before_all_dns_and_jit_and_binds_every_snapshot(
    campaign,
):
    c = campaign
    deadline = c.clock[0] + 60_000_000_000
    assert (
        co.op_launch(c.cfg, c.channel, c.api, 12, deadline_ns=deadline)["state"]
        == "BOOTSTRAP"
    )
    assert [event[0] for event in c.events] == ["timing", "capture", "jit", "bootstrap"]
    assert c.events[1][1] == deadline
    receipt = c.events[-1][1]["bootstrap_snapshot"]
    assert receipt == {
        "challenge": "c" * 64,
        "manifest_digest": c.frozen.digest,
        "provenance_digest": c.frozen.provenance_digest,
        "profile": hp.BOOTSTRAP_OBSERVATION_PROFILE,
        "observation_remaining_ms": 60000,
    }
    state = co.load_state(c.cfg.state_path)
    assert state["bootstrap_provenance_digest"] == c.frozen.provenance_digest
    assert state["runner_id"] == 123
    assert c.frozen.provenance["proposal_sha256"] == c.cfg.bootstrap_proposal_sha256
    assert len(c.frozen.provenance["observations"]) == 2


@pytest.mark.parametrize("phase", ["dns", "provenance", "jit"])
def test_original_deadline_expiry_prevents_the_next_effect(
    campaign, monkeypatch, phase
):
    c = campaign
    deadline = c.clock[0] + 60_000_000_000
    if phase == "dns":

        def capture(*args, **kwargs):
            value = c.capture(*args, **kwargs)
            c.clock[0] = deadline
            return value

        monkeypatch.setattr(cb, "capture", capture)
    elif phase == "provenance":
        retain = co.retain_bootstrap_provenance

        def late_retain(*args):
            retain(*args)
            c.clock[0] = deadline

        monkeypatch.setattr(co, "retain_bootstrap_provenance", late_retain)
    else:
        api = c.api

        def late_api(*args):
            value = api(*args)
            c.clock[0] = deadline
            return value

        c.api = late_api
    with pytest.raises(co.CoordinatorRefused, match="deadline.insufficient"):
        co.op_launch(c.cfg, c.channel, c.api, 12, deadline_ns=deadline)
    assert "bootstrap" not in [event[0] for event in c.events]
    assert ("jit" in [event[0] for event in c.events]) == (phase == "jit")


def test_rebound_manifest_cannot_create_jit(campaign, monkeypatch):
    c = campaign
    changed = c.frozen.manifest
    changed["destinations"][0]["address"] = "149.112.112.112"
    frozen = cb.FrozenBootstrap(
        c.frozen.observations,
        hp.canonical_json(changed),
        hp.digest(changed),
        c.cfg.bootstrap_proposal_sha256,
    )
    monkeypatch.setattr(cb, "capture", lambda *args, **kwargs: frozen)
    with pytest.raises(co.CoordinatorRefused, match="snapshot.refused"):
        co.op_launch(
            c.cfg, c.channel, c.api, 12, deadline_ns=c.clock[0] + 60_000_000_000
        )
    assert [event[0] for event in c.events] == ["timing"]


def test_admission_drift_during_dns_prevents_registration(campaign, monkeypatch):
    c = campaign

    def capture(*args, **kwargs):
        frozen = c.capture(*args, **kwargs)
        monkeypatch.setattr(
            co, "prelaunch_binding", lambda *args: {"synthetic": "drift"}
        )
        return frozen

    monkeypatch.setattr(cb, "capture", capture)
    with pytest.raises(co.CoordinatorRefused, match="binding.mismatch"):
        co.op_launch(
            c.cfg, c.channel, c.api, 12, deadline_ns=c.clock[0] + 60_000_000_000
        )
    assert [event[0] for event in c.events] == ["timing", "capture"]
    assert "launch_intent" not in co.load_state(c.cfg.state_path)


def test_cleanup_does_not_load_config_manifest_or_resolve_dns(campaign, monkeypatch):
    c = campaign
    for name in ("capture", "load_config", "load_manifest"):
        monkeypatch.setattr(
            cb, name, lambda *args, **kwargs: pytest.fail("DNS in cleanup")
        )
    lease = "a" * 32
    closed = {
        "state": "CLOSED",
        "lease_id": lease,
        "evidence": co.hc.evidence(
            {"lease_id": lease, "outcome": "REFUSED", "category": None}
        ),
    }
    assert (
        co.op_cleanup(c.cfg, lambda *args: closed, lambda *args: {})["state"]
        == "CLOSED"
    )
    assert not c.cfg.state_path.exists()


def test_complete_provenance_is_private_exclusive_retained_evidence(campaign):
    c = campaign
    co.op_launch(c.cfg, c.channel, c.api, 12, deadline_ns=c.clock[0] + 60_000_000_000)
    path = c.cfg.state_path.with_name(
        c.cfg.state_path.name + "." + "a" * 32 + ".bootstrap-provenance.json"
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_bytes() == hp.canonical_json(c.frozen.provenance)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == c.frozen.provenance_digest
    with pytest.raises(FileExistsError):
        co.retain_bootstrap_provenance(c.cfg.state_path, "a" * 32, c.frozen)
