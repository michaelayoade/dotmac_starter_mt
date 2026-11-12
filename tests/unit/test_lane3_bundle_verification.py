"""Offline descriptor/rule consistency never grants publication or host standing."""

from __future__ import annotations

import dataclasses
import json
import pathlib
import sys

import pytest
from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.execution_plan import (
    HostPrestateV1,
    render_execution_plan,
)
from dotmac_deployment_foundation.execution_plan_v2 import render_execution_plan_v2
from dotmac_deployment_foundation.policy import build_firewall_plan
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.foundation_v3_support import v3_plan
from tests.unit.test_lane3_exposure_evidence import _observation

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_bundle_verification as verifier  # noqa: E402
from lane3_evidence_bundle import build_manifest  # noqa: E402
from lane3_exposure_evidence import serialize_exposure_calls  # noqa: E402
from lane3_exposure_recording import ExposureCallRecord  # noqa: E402
from lane3_plan_v3 import exposure_reconciliations_for_spec  # noqa: E402

PRIVATE = "PRIVATE-TOPOLOGY"


@pytest.fixture
def subject():
    spec = ProductDeploymentSpec.loads(
        (REPO / "scripts/exposure-rehearsal/product.toml").read_text("utf-8"),
        source="<bundle-verification-fixture>",
    )
    base = render_execution_plan(
        spec,
        build_plan(spec),
        target="rehearsal-target",
        operation="deploy",
        descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
        prestate=HostPrestateV1.first_deploy(),
        application_profile_digest="",
    )
    plan = v3_plan(
        render_execution_plan_v2(
            base, exposure_reconciliations=exposure_reconciliations_for_spec(spec)
        )
    )
    records = [ExposureCallRecord(0, "observe", observation=_observation())]
    for item in plan.exposure_reconciliations:
        records.append(
            ExposureCallRecord(
                len(records),
                "replace_rules",
                family=item.family,
                chain=item.chain,
                rules=tuple(
                    rule
                    for rule in build_firewall_plan(spec)
                    if rule.family == item.family
                ),
            )
        )
    return spec, plan, records


def bundle_input(records):
    members = {
        "probe-evidence.json": b"private probe",
        "exposure-calls.json": serialize_exposure_calls(records),
    }
    return build_manifest(members), members


def require_refused(spec, plan, records, *, members=None, manifest=None):
    if members is None:
        manifest, members = bundle_input(records)
    with pytest.raises(verifier.BundleVerificationRefused) as caught:
        verifier.require_plaintext_exposure_bundle(
            manifest, members, execution_plan=plan, descriptor=spec
        )
    assert str(caught.value) == "plaintext exposure consistency refused"
    assert caught.value.__suppress_context__


def test_valid_plaintext_joins_descriptor_and_exact_ordered_rules(subject) -> None:
    spec, plan, records = subject
    assert len(plan.exposure_reconciliations) == 2
    assert any(len(record.rules) > 1 for record in records)
    manifest, members = bundle_input(records)
    assert verifier.require_plaintext_exposure_bundle(
        manifest, members, execution_plan=plan, descriptor=spec
    ) == tuple(records)


def test_exact_failed_write_and_restore_do_not_claim_outcome(subject) -> None:
    spec, plan, records = subject
    records.extend(
        [
            dataclasses.replace(
                records[1],
                sequence=len(records),
                exception_type="builtins.RuntimeError",
            ),
            ExposureCallRecord(
                len(records) + 1, "restore_chains", chains=_observation().chains
            ),
        ]
    )
    manifest, members = bundle_input(records)
    assert verifier.require_plaintext_exposure_bundle(
        manifest, members, execution_plan=plan, descriptor=spec
    ) == tuple(records)


@pytest.mark.parametrize(
    "kind",
    [
        "descriptor",
        "product",
        "digest",
        "dict",
        "subclass",
        "plan_dict",
        "empty",
        "extra",
        "duplicate",
    ],
)
def test_trusted_inputs_must_bind_concrete_descriptor(subject, kind) -> None:
    spec, plan, records = subject
    if kind == "descriptor":
        spec = dataclasses.replace(spec, product="other_product")
    elif kind == "product":
        plan = dataclasses.replace(
            plan, base=dataclasses.replace(plan.base, product=PRIVATE)
        )
    elif kind == "digest":
        plan = dataclasses.replace(
            plan,
            base=dataclasses.replace(plan.base, descriptor_digest="sha256:" + "1" * 64),
        )
    elif kind == "dict":
        spec = {"product": PRIVATE}
    elif kind == "subclass":

        class Derived(ProductDeploymentSpec):
            pass

        spec = object.__new__(Derived)
    elif kind == "plan_dict":
        plan = plan.as_document()
    elif kind == "extra":
        body = (REPO / "scripts/exposure-rehearsal/product.toml").read_text("utf-8")
        spec = ProductDeploymentSpec.loads(
            body.replace('address_family = "dual_stack"', 'address_family = "ipv4"'),
            source="<single-family>",
        )
        assert len(exposure_reconciliations_for_spec(spec)) == 1
        plan = dataclasses.replace(
            plan,
            base=dataclasses.replace(
                plan.base,
                descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
            ),
        )
    else:
        acts = plan.exposure_reconciliations
        # Bypass constructor validation to prove the consumer refuses too.
        plan = dataclasses.replace(plan, base=dataclasses.replace(plan.base))
        object.__setattr__(
            plan.base,
            "exposure_reconciliations",
            () if kind == "empty" else acts + acts[:1],
        )
    require_refused(spec, plan, records)


@pytest.mark.parametrize(
    "kind",
    [
        "family",
        "chain",
        "missing",
        "extra",
        "order",
        "host_port",
        "source_set",
        "action",
        "terminal",
        "protocol",
        "rule_chain",
        "compose",
    ],
)
@pytest.mark.parametrize("failed", [False, True])
def test_every_replace_crossing_has_exact_descriptor_arguments(
    subject, kind, failed, capsys
) -> None:
    spec, plan, records = subject
    record = records[1]
    if kind == "compose":
        changed = ExposureCallRecord(
            0, "apply_compose", command=(PRIVATE,), timeout_seconds=30
        )
    elif kind in ("family", "chain"):
        changed = dataclasses.replace(record, **{kind: PRIVATE})
    elif kind in ("missing", "extra", "order"):
        rules = record.rules
        rules = (
            rules[:-1]
            if kind == "missing"
            else rules + rules[:1]
            if kind == "extra"
            else tuple(reversed(rules))
        )
        changed = dataclasses.replace(record, rules=rules)
    else:
        field = "chain" if kind == "rule_chain" else kind
        value = (
            65534
            if kind == "host_port"
            else not record.rules[0].terminal
            if kind == "terminal"
            else PRIVATE
        )
        changed = dataclasses.replace(
            record,
            rules=(dataclasses.replace(record.rules[0], **{field: value}),)
            + record.rules[1:],
        )
    # Keep legitimate successes: extra failed writes must also be checked.
    records.append(
        dataclasses.replace(
            changed,
            sequence=len(records),
            exception_type="builtins.RuntimeError" if failed else "",
        )
    )
    require_refused(spec, plan, records)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize(
    "kind",
    [
        "missing_join",
        "failed_only",
        "sequence",
        "invalid",
        "noncanonical",
        "member_tamper",
    ],
)
def test_incomplete_or_changed_members_refuse(subject, kind) -> None:
    spec, plan, records = subject
    if kind == "missing_join":
        records = records[:1]
    elif kind == "failed_only":
        records = [records[0]] + [
            dataclasses.replace(r, exception_type="builtins.RuntimeError")
            for r in records[1:]
        ]
    manifest, members = bundle_input(records)
    if kind == "member_tamper":
        members["probe-evidence.json"] += b"changed"
    elif kind in ("sequence", "invalid", "noncanonical"):
        document = json.loads(members["exposure-calls.json"])
        if kind == "sequence":
            document["calls"][1]["sequence"] = 7
        elif kind == "invalid":
            document["calls"][0]["method"] = PRIVATE
        raw = (
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        )
        members["exposure-calls.json"] = raw + (b" " if kind == "noncanonical" else b"")
        manifest = build_manifest(members)
    require_refused(spec, plan, records, manifest=manifest, members=members)


@pytest.mark.parametrize("owner", ["canonical", "reconciliations", "rules"])
def test_owner_exceptions_are_sanitized(subject, monkeypatch, owner) -> None:
    spec, plan, records = subject

    def refuse(*args, **kwargs):
        raise RuntimeError(PRIVATE)

    if owner == "canonical":
        monkeypatch.setattr(ProductDeploymentSpec, "to_canonical_document", refuse)
    else:
        monkeypatch.setattr(
            verifier,
            "exposure_reconciliations_for_spec"
            if owner == "reconciliations"
            else "build_firewall_plan",
            refuse,
        )
    require_refused(spec, plan, records)
