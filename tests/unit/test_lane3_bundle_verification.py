"""Plaintext consistency uses existing owners and never grants publication."""

from __future__ import annotations

import dataclasses
import json
import pathlib
import sys

import pytest
from dotmac_deployment_foundation.execution_plan_v2 import ExposureReconciliationV1

from tests.unit.test_deployment_foundation_rehearsal_receipt_v2 import (
    Subject,
)
from tests.unit.test_deployment_foundation_rehearsal_receipt_v2 import (
    subject as receipt_subject,  # noqa: F401
)
from tests.unit.test_lane3_exposure_evidence import _records

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_bundle_verification as verifier  # noqa: E402
from lane3_evidence_bundle import build_manifest  # noqa: E402
from lane3_exposure_evidence import serialize_exposure_calls  # noqa: E402


@pytest.fixture
def subject(receipt_subject: Subject) -> Subject:  # noqa: F811
    return receipt_subject


def populated_plan(subject: Subject):
    return dataclasses.replace(
        subject.plan,
        base=dataclasses.replace(
            subject.plan.base,
            exposure_reconciliations=(
                ExposureReconciliationV1(family="ipv4", chain="DOCKER-USER"),
            ),
        ),
    )


def bundle_input():
    members = {
        "probe-evidence.json": b"private probe",
        "exposure-calls.json": serialize_exposure_calls(_records()),
    }
    return build_manifest(members), members


def test_valid_plaintext_joins_concrete_plan(subject: Subject) -> None:
    manifest, members = bundle_input()
    assert verifier.require_plaintext_exposure_bundle(
        manifest, members, execution_plan=populated_plan(subject)
    ) == tuple(_records())


@pytest.mark.parametrize(
    "kind", ["dict", "empty", "family", "sequence", "invalid", "noncanonical"]
)
def test_invalid_evidence_refuses_without_private_payload(
    subject: Subject, kind: str
) -> None:
    manifest, members = bundle_input()
    plan = populated_plan(subject)
    if kind == "dict":
        plan = plan.as_document()
    elif kind == "empty":
        plan = subject.plan
    elif kind == "family":
        plan = dataclasses.replace(
            plan,
            base=dataclasses.replace(
                plan.base,
                exposure_reconciliations=(
                    ExposureReconciliationV1(family="ipv6", chain="INPUT"),
                ),
            ),
        )
    else:
        document = json.loads(members["exposure-calls.json"])
        if kind == "sequence":
            document["calls"][1]["sequence"] = 7
        elif kind == "invalid":
            document["calls"][0]["method"] = "PRIVATE-TOPOLOGY"
        raw = (
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        )
        members["exposure-calls.json"] = raw + (b" " if kind == "noncanonical" else b"")
        manifest = build_manifest(members)
    with pytest.raises(verifier.BundleVerificationRefused) as caught:
        verifier.require_plaintext_exposure_bundle(
            manifest, members, execution_plan=plan
        )
    assert "PRIVATE-TOPOLOGY" not in str(caught.value)
    assert "private probe" not in str(caught.value)
