"""S8b-2: E9 two-target joins (N1-N7), the v2 bundle and the row 8 binding.

One negative target proves the compensation; its UNSIGNED proof sits inside the
positive ciphertext. Every join has a planted refusal. Synthetic keys only.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import timedelta
from typing import Any

import pytest
from dotmac_deployment_foundation.rehearsal import RequirementStatus

from tests.unit import lane3_negative_support as nsupport
from tests.unit.test_lane3_offline_orchestrator import (
    DISPATCH_ID,
    HOST_ID,
    NEG_DISPATCH_ID,
    TARGET_ID,
    T,
    World,
    cp6,
    digest,
    nproof,
    refused,
    ts,
    world,  # noqa: F401  (fixture)
)

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_offline_orchestrator as orchestrator  # noqa: E402
from lane3_evidence_finalization import _archive, _archive_v2  # noqa: E402
from lane3_exposure_evidence import serialize_exposure_calls  # noqa: E402

PROBE = b'{"probe":"synthetic"}'


def rebind(
    world: World,  # noqa: F811
    proof: bytes,
    *,
    receipt: bool = True,
) -> None:
    """Carry `proof` as the bundle's member and (optionally) in row 8."""
    world.proof_bytes = proof
    world.archive = _archive_v2(PROBE, serialize_exposure_calls(world.records), proof)
    world.decrypt = lambda _c: world.archive
    if receipt:
        world.build_receipt(world._grant, world._descriptor_digest)


def reproof(world: World, **changes: Any) -> bytes:  # noqa: F811
    """The world's proof with some claims changed (still closed and canonical)."""
    proof = nproof.parse_proof(world.proof_bytes)
    values: dict[str, Any] = {
        "positive_dispatch_id": proof.positive_dispatch_id,
        "dispatch_id": proof.dispatch_id,
        "target_id": proof.target_id,
        "host_id": proof.host_id,
        "execution_sequence": proof.execution_sequence,
        "attempt_no": proof.attempt_no,
        "lease_authorization_run": proof.lease_authorization_run,
        "terminal_refusal": proof.terminal.refusal,
        "host_mutated": proof.terminal.host_mutated,
        "exit_status": proof.terminal.exit_status,
        "window_done": proof.terminal.window_done,
        "exposure_calls": proof.exposure_calls,
        "probe_bytes": proof.probe_bytes,
    }
    values.update(changes)
    return nproof.build_proof(**values)


# --- the positive path ----


def test_the_pair_passes_and_both_targets_are_recorded(world: World) -> None:  # noqa: F811
    outcome = world.run()
    assert outcome.input_digests["negative_cp6_export"].startswith("sha256:")
    assert outcome.input_digests["negative_compensation"] == (
        "sha256:" + nproof.parse_proof(world.proof_bytes).member_digest
    )
    assert world.neg.dispatch_id == NEG_DISPATCH_ID != DISPATCH_ID


def test_the_negative_inputs_are_a_required_argument(world: World) -> None:  # noqa: F811
    values = world.verify_values()
    del values["negative"]
    with pytest.raises(TypeError):
        orchestrator.verify_offline(**values)
    refused(world, "negative-required", negative=None)


# --- the v2 bundle ----


def test_a_v1_bundle_offered_to_e9_is_refused_as_a_downgrade(world: World) -> None:  # noqa: F811
    v1 = _archive(PROBE, serialize_exposure_calls(world.records))
    refused(world, "bundle-version", decrypt=lambda _c: v1)


def test_a_missing_proof_member_is_refused(world: World) -> None:  # noqa: F811
    rebind(world, b"{}")
    refused(world, "proof-invalid")


def test_a_forged_proof_that_is_not_canonical_is_refused(world: World) -> None:  # noqa: F811
    refused(world, "bundle-inconsistent", decrypt=lambda _c: world.archive[:-512])


# --- row 8 ----


@pytest.mark.parametrize(
    "bound",
    [
        ("lane3-negative-compensation-proof:sha256:" + "0" * 64,),
        ("bundle:provoked_rollback",),
        (),
    ],
)
def test_row_8_must_carry_exactly_the_member_digest(world: World, bound) -> None:  # noqa: F811
    world.build_receipt(world._grant, world._descriptor_digest, row8_evidence=bound)
    refused(world, "compensation-row-mismatch")


def test_a_blocked_row_8_does_not_publish(world: World) -> None:  # noqa: F811
    world.build_receipt(
        world._grant, world._descriptor_digest, row8_status=RequirementStatus.BLOCKED
    )
    refused(world, "receipt-not-published")


def test_a_proof_not_the_one_row_8_binds_is_refused(world: World) -> None:  # noqa: F811
    other = reproof(world, attempt_no=7)
    rebind(world, other, receipt=False)
    refused(world, "compensation-row-mismatch")


# --- N5: the pair ----


def test_the_negative_presented_as_the_positive_is_refused(world: World) -> None:  # noqa: F811
    """The negative inputs ARE the positive's: the same dispatch, plan and host."""
    values = world.verify_values()
    own = orchestrator.NegativeTargetInputs(
        execution_plan=world.plan,
        export_bytes=values["export_bytes"],
        expected_e10_receipt_digest=values["expected_e10_receipt_digest"],
        candidate_envelope_bytes=values["candidate_envelope_bytes"],
        installed_envelope_bytes=values["installed_envelope_bytes"],
        host_admission_bytes=values["host_admission_bytes"],
        presentation_public_key_b64url=values["presentation_public_key_b64url"],
        anchor=values["anchor"],
        refresh_digest=values["refresh_digest"],
    )
    refused(world, "pair-dispatch-same", negative=own)


def test_a_proof_naming_the_positive_dispatch_as_its_own_is_refused(
    world: World,  # noqa: F811
) -> None:
    rebind(world, reproof(world, dispatch_id=DISPATCH_ID))
    refused(world, "pair-dispatch-same")


def test_the_same_target_for_both_is_refused(world: World) -> None:  # noqa: F811
    rebind(world, reproof(world, target_id=TARGET_ID))
    refused(world, "pair-target-same")


def test_the_same_host_for_both_targets_is_refused(world: World) -> None:  # noqa: F811
    rebind(world, reproof(world, host_id=HOST_ID))
    refused(world, "pair-host-same")


def test_a_proof_naming_another_positive_dispatch_is_refused(world: World) -> None:  # noqa: F811
    rebind(world, reproof(world, positive_dispatch_id=NEG_DISPATCH_ID))
    refused(world, "pair-positive-dispatch-mismatch")


def test_identity_claims_are_joined_to_the_negative_plan(world: World) -> None:  # noqa: F811
    rebind(world, reproof(world, target_id="some-other-target"))
    refused(world, "pair-claim-mismatch")


def test_both_plans_must_carry_the_checkouts_descriptor(world: World) -> None:  # noqa: F811
    from dotmac_deployment_foundation.engine.plan import build_plan
    from dotmac_deployment_foundation.execution_plan import (
        HostPrestateV1,
        render_execution_plan,
    )
    from dotmac_deployment_foundation.execution_plan_v2 import (
        render_execution_plan_v2,
    )
    from dotmac_deployment_foundation.execution_plan_v3 import (
        render_execution_plan_v3,
    )
    from lane3_plan_v3 import exposure_reconciliations_for_spec

    from tests.unit.foundation_v3_support import CONTROLLER, WHEEL

    spec = world.spec
    base = render_execution_plan(
        spec,
        build_plan(spec),
        target="rehearsal-target",
        operation="deploy",
        descriptor_digest="sha256:" + "9" * 64,
        prestate=HostPrestateV1.first_deploy(),
        application_profile_digest="",
    )
    world.neg.plan = render_execution_plan_v3(
        render_execution_plan_v2(
            base, exposure_reconciliations=exposure_reconciliations_for_spec(spec)
        ),
        candidate_wheel_digest=WHEEL,
        target_id=world.neg.plan.target_id,
        controller_ssh_fingerprint=CONTROLLER,
        host_id=world.neg.plan.host_id,
        host_incarnation=world.neg.plan.host_incarnation,
        host_enrolment_ref=world.neg.plan.host_enrolment_ref,
    )
    refused(world, "pair-descriptor-mismatch")


# --- N1: Control, from the proof's claims ----


def test_n1_a_forged_coordinate_claim_is_refused(world: World) -> None:  # noqa: F811
    rebind(world, reproof(world, attempt_no=9))
    refused(world, "dispatch-coordinate-mismatch")


def test_n1_a_dispatch_claim_that_is_not_the_export_is_refused(world: World) -> None:  # noqa: F811
    rebind(world, reproof(world, dispatch_id="11111111-2222-4333-8444-555555555555"))
    refused(world, "export-dispatch-mismatch")


def test_n1_the_signed_descriptor_digest_is_joined(world: World) -> None:  # noqa: F811
    world.neg.resign(descriptor_digest="sha256:" + "8" * 64)
    world.neg.refresh_ledger()
    refused(world, "dispatch-descriptor-mismatch")


def test_n1_an_unpinned_signer_is_refused(world: World) -> None:  # noqa: F811
    stranger = orchestrator.PinnedIdentity("other-key", "ed25519", "A" * 43)
    refused(world, "dispatch-signer-unpinned", pinned_cp_identities=[stranger])


def test_n1_the_lease_run_claim_is_joined(world: World) -> None:  # noqa: F811
    world.neg.cp["lease"]["authorization_run"] = "lease-other"
    refused(world, "lease-mismatch")


def test_n1_a_missing_release_is_refused(world: World) -> None:  # noqa: F811
    world.neg.cp["release"] = None
    refused(world, "release-missing")


def test_n1_a_release_that_did_not_mutate_is_refused(world: World) -> None:  # noqa: F811
    world.neg.cp["release"]["host_mutated"] = False
    refused(world, "release-not-mutated")


def test_n1_a_stale_presentation_is_refused(world: World) -> None:  # noqa: F811
    world.neg.store_time = T + timedelta(minutes=30)
    world.neg.resign_admission()
    world.neg.refresh_ledger()
    refused(world, "host-admission-presentation-not-fresh")


# --- N2 / N3 / N4 ----


def test_n2_the_negative_e10_digest_is_its_own(world: World) -> None:  # noqa: F811
    world.neg.e10_override = digest(b"not the negative receipt")
    refused(world, "e10-receipt-digest-mismatch")


def test_n2_the_negative_installed_root_must_be_revoked(world: World) -> None:  # noqa: F811
    world.neg.cp["installed_root"]["revoked_at"] = None
    refused(world, "e10-root-not-revoked")


def test_n3_the_negative_anchor_is_its_own(world: World) -> None:  # noqa: F811
    world.neg.anchor_changes = {"key_fingerprint": digest(b"another key")}
    with pytest.raises(orchestrator.AnchorRefused) as caught:
        world.run()
    assert str(caught.value) == "offline-anchor-key-fingerprint-mismatch"


def test_n3_the_negative_refresh_digest_is_its_own(world: World) -> None:  # noqa: F811
    world.neg.refresh_override = "0" * 64
    with pytest.raises(orchestrator.AnchorRefused) as caught:
        world.run()
    assert str(caught.value) == "offline-anchor-refresh-digest-mismatch"


def test_n3_the_positive_anchor_does_not_stand_in_for_the_negative(
    world: World,  # noqa: F811
) -> None:
    world.neg.anchor_changes = {"host_id": HOST_ID}
    with pytest.raises(orchestrator.AnchorRefused) as caught:
        world.run()
    assert str(caught.value) == "offline-anchor-host-mismatch"


def test_n4_the_candidate_root_is_checked_for_the_negative_too(world: World) -> None:  # noqa: F811
    world.neg.cp["candidate_root"]["revoked_at"] = ts(T + timedelta(seconds=10))
    refused(world, "candidate-root-revoked")


# --- N6: compensation, re-derived offline ----


@pytest.mark.parametrize(
    ("options", "code"),
    [
        ({"inert": False}, "compensation-verification"),
        (
            {"first": nsupport.baseline_observation(foreign=False)},
            "compensation-baseline",
        ),
        (
            {"final": nsupport.baseline_observation(foreign=False)},
            "compensation-restore",
        ),
    ],
)
def test_n6_a_compensation_that_does_not_re_derive_is_refused(
    world: World,  # noqa: F811
    options,
    code,
) -> None:
    records = nsupport.negative_records(world.spec, world.neg.plan, **options)
    rebind(world, reproof(world, exposure_calls=serialize_exposure_calls(records)))
    refused(world, code)


def test_n6_a_terminal_that_is_not_the_refusal_is_refused(world: World) -> None:  # noqa: F811
    rebind(world, reproof(world, host_mutated=False))
    refused(world, "compensation-terminal")


# --- N7: the probe ----


def test_n7_a_probe_after_the_release_is_refused(world: World) -> None:  # noqa: F811
    late = nsupport.probe_bytes(
        world.neg.dispatch_id, world.neg.host_id, T + timedelta(minutes=40)
    )
    rebind(world, reproof(world, probe_bytes=late))
    refused(world, "probe-outside-window")


def test_n7_a_failed_probe_row_is_refused(world: World) -> None:  # noqa: F811
    failed = nsupport.probe_bytes(
        world.neg.dispatch_id,
        world.neg.host_id,
        T + timedelta(seconds=40),
        statuses=("executed_passed", "executed_failed"),
    )
    rebind(world, reproof(world, probe_bytes=failed))
    refused(world, "probe-not-passed")


def test_n7_a_probe_for_another_host_is_refused(world: World) -> None:  # noqa: F811
    other = nsupport.probe_bytes(
        world.neg.dispatch_id, HOST_ID, T + timedelta(seconds=40)
    )
    rebind(world, reproof(world, probe_bytes=other))
    refused(world, "probe-identity")


def test_a_ledger_time_helper_is_consistent() -> None:
    assert cp6.CONSUME_STATUS == "executed"


# --- H1: one run, negative first ----


def test_h1_a_proof_from_another_run_is_refused(world: World) -> None:  # noqa: F811
    rebind(world, reproof(world, lease_authorization_run="lease-other"))
    refused(world, "pair-lease-run-mismatch")


def test_h1_a_pair_spliced_from_two_runs_is_refused(world: World) -> None:  # noqa: F811
    """The negative target's own lease row carries another run's authorization."""
    world.neg.cp["lease"]["authorization_run"] = "lease-other"
    rebind(world, reproof(world, lease_authorization_run="lease-other"))
    refused(world, "pair-lease-run-mismatch")


def test_h1_the_positive_must_not_be_consumed_before_the_negative_release(
    world: World,  # noqa: F811
) -> None:
    world.neg.cp["release"]["released_at"] = ts(T + timedelta(minutes=6))
    refused(world, "pair-order")


def test_h1_equal_instants_are_negative_first(world: World) -> None:  # noqa: F811
    positive_ledger = world.export["control"]["ledger"]["created_at"]
    world.neg.cp["release"]["released_at"] = positive_ledger
    world.neg.cp["lease"]["expires_at"] = ts(T + timedelta(minutes=61))
    world.run()


def test_h1_a_positive_driven_first_is_refused(world: World) -> None:  # noqa: F811
    """Positive ledger before the negative's whole window: the reverse order."""
    world.export["control"]["ledger"]["created_at"] = ts(T + timedelta(seconds=40))
    world.store_time = T + timedelta(seconds=40)
    world.resign_admission()
    world.refresh_ledger()
    world.cp["lease"]["acquired_at"] = ts(T)
    refused(world, "pair-order")


# --- L1: two hosts, not one twice ----


def _with_negative_plan(world: World, **subject: Any) -> None:  # noqa: F811
    plan = world.neg.plan
    values = {
        "candidate_wheel_digest": plan.candidate_wheel_digest,
        "target_id": plan.target_id,
        "controller_ssh_fingerprint": plan.controller_ssh_fingerprint,
        "host_id": plan.host_id,
        "host_incarnation": plan.host_incarnation,
        "host_enrolment_ref": plan.host_enrolment_ref,
    }
    values.update(subject)
    from dotmac_deployment_foundation.execution_plan_v3 import (
        render_execution_plan_v3,
    )

    world.neg.plan = render_execution_plan_v3(plan.base, **values)


def test_l1_the_same_incarnation_for_both_is_refused(world: World) -> None:  # noqa: F811
    _with_negative_plan(world, host_incarnation=world.plan.host_incarnation)
    refused(world, "pair-incarnation-same")


def test_l1_the_same_enrolment_for_both_is_refused(world: World) -> None:  # noqa: F811
    _with_negative_plan(world, host_enrolment_ref=world.plan.host_enrolment_ref)
    refused(world, "pair-enrolment-same")


def test_l1_the_same_presentation_key_for_both_is_refused(world: World) -> None:  # noqa: F811
    world.neg.pres_key = world.pres_key
    world.neg.pres_pub = world.pres_pub
    world.neg.resign_admission()
    world.neg.refresh_ledger()
    world.neg.anchor_changes = {
        "key_fingerprint": world.anchor().key_fingerprint,
    }
    refused(world, "pair-presentation-key-same")


# --- L3 / M1 ----


def test_l3_a_negative_export_without_a_release_is_an_explicit_refusal(
    world: World,  # noqa: F811
) -> None:
    world.neg.cp["release"] = None
    refused(world, "release-missing")


def test_m1_a_proof_without_the_window_acknowledgement_fails_n7(
    world: World,  # noqa: F811
) -> None:
    rebind(world, reproof(world, window_done=False))
    refused(world, "probe-window-unacknowledged")
