"""S8b: the negative compensation proof, the probe document and N6/N7.

Synthetic observations only (documentation names, no estate address or key).
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import sys
from datetime import UTC, datetime, timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_lease_store  # noqa: E402
import lane3_negative_proof as nproof  # noqa: E402
from lane3_exposure_evidence import serialize_exposure_calls  # noqa: E402

from tests.unit import lane3_negative_support as support  # noqa: E402

T = datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC)
INCARNATION = "sha256:" + "a" * 64
ENROL = "6f1c2f4e-52a8-4d0e-9a1b-2f0c7e6d9b11"


@pytest.fixture(scope="module")
def spec():
    return support.load_spec()


@pytest.fixture(scope="module")
def plan(spec):
    return support.make_plan(
        spec,
        target_id="3f2b8c1e-7a44-4d0b-9c55-0e1d2a3b4c5d",
        host_id="host-n",
        incarnation=INCARNATION,
        enrolment=ENROL,
    )


def _proof(spec, plan, **record_options) -> nproof.NegativeProof:
    records = support.negative_records(spec, plan, **record_options)
    return nproof.parse_proof(
        support.proof_bytes(exposure_calls=serialize_exposure_calls(records))
    )


def refused(code: str, call, *args, **kwargs) -> None:
    with pytest.raises(nproof.ProofRefused) as caught:
        call(*args, **kwargs)
    assert caught.value.code == code


# --- the documents ----------------------------------------------------------


def test_proof_round_trips_and_binds_its_member_digest() -> None:
    raw = support.proof_bytes()
    proof = nproof.parse_proof(raw)
    assert proof.member_bytes == raw
    assert proof.member_digest == hashlib.sha256(raw).hexdigest()
    assert nproof.row_evidence(raw) == nproof.ROW_EVIDENCE_PREFIX + proof.member_digest
    assert nproof.ROW_EVIDENCE_PATTERN.fullmatch(nproof.row_evidence(raw))


def test_the_row_8_row_is_executed_passed_with_exactly_the_digest() -> None:
    raw = support.proof_bytes()
    row = nproof.compensation_row(raw)
    assert row["code"] == nproof.ROW_CODE == "provoked_rollback"
    assert row["status"] == "executed_passed"
    assert row["evidence"] == [nproof.row_evidence(raw)]
    nproof.require_row_shape(row["code"], row["status"], row["detail"], row["evidence"])


def test_the_launcher_will_not_assert_a_proof_it_cannot_parse() -> None:
    refused("proof-invalid", nproof.compensation_row, b"{}")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=1),
        lambda d: d.update(schema="lane3.negative-compensation-proof.v0"),
        lambda d: d["negative"].update(extra=1),
        lambda d: d["negative"].update(execution_sequence=True),
        lambda d: d["negative"].update(host_id=""),
        lambda d: d["negative"]["terminal"].update(host_mutated="yes"),
        lambda d: d["negative"].update(exposure_calls_b64="not base64!"),
        lambda d: d["negative"].update(post_compensation_probe_b64=""),
    ],
)
def test_a_forged_or_malformed_proof_is_refused(mutate) -> None:
    document = json.loads(support.proof_bytes())
    mutate(document)
    forged = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    with pytest.raises(nproof.ProofRefused):
        nproof.parse_proof(forged)


def test_a_reformatted_proof_is_not_canonical() -> None:
    document = json.loads(support.proof_bytes())
    reformatted = json.dumps(document, indent=2).encode()
    refused("proof-not-canonical", nproof.parse_proof, reformatted)


def test_a_missing_or_non_bytes_proof_is_refused() -> None:
    refused("proof-invalid", nproof.parse_proof, b"")
    refused("proof-invalid", nproof.parse_proof, None)  # type: ignore[arg-type]


def test_the_proof_calls_must_be_the_canonical_exposure_bytes() -> None:
    reformatted = json.dumps(json.loads(serialize_exposure_calls(())), indent=2)
    with pytest.raises(nproof.ProofRefused):
        support.proof_bytes(exposure_calls=reformatted.encode())


def test_row_shape_refuses_every_deviation() -> None:
    good = nproof.row_evidence(b"x")
    ok = ("provoked_rollback", "executed_passed", "detail", [good])
    nproof.require_row_shape(*ok)
    for changed in (
        ("apply_under_lock", "executed_passed", "detail", [good]),
        ("provoked_rollback", "blocked", "detail", [good]),
        ("provoked_rollback", "executed_passed", " ", [good]),
        ("provoked_rollback", "executed_passed", "detail", []),
        ("provoked_rollback", "executed_passed", "detail", [good, good]),
        ("provoked_rollback", "executed_passed", "detail", ["bundle:provoked"]),
        ("provoked_rollback", "executed_passed", "detail", [good + "0"]),
    ):
        refused("row-shape", nproof.require_row_shape, *changed)


# --- the probe document -----------------------------------------------------


def test_probe_is_closed_to_exactly_the_two_rows_in_order() -> None:
    raw = support.probe_bytes("d", "h", T)
    parsed = nproof.parse_probe(raw)
    assert tuple(row.code for row in parsed.rows) == nproof.PROBE_ROW_CODES
    assert parsed.measured_at == T
    document = json.loads(raw)
    for rows in (document["rows"][:1], document["rows"][::-1], document["rows"] * 2):
        broken = {**document, "rows": rows}
        data = (
            json.dumps(broken, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        refused("probe-rows", nproof.parse_probe, data)


def test_probe_with_the_positive_row_code_is_refused() -> None:
    with pytest.raises(nproof.ProofRefused):
        support.probe_bytes("d", "h", T, codes=("external_positive_v6", "external_v4"))


def test_probe_skew_bound_matches_the_launchers_existing_bound() -> None:
    assert nproof.PROBE_SKEW_SECONDS == lane3_lease_store.MAX_RELEASE_SKEW_SECONDS


# --- N6: compensation re-derived ---------------------------------------------


def test_a_genuine_compensated_failure_passes_n6(spec, plan) -> None:
    nproof.require_compensation(
        _proof(spec, plan), execution_plan=plan, descriptor=spec
    )


@pytest.mark.parametrize(
    ("options", "code"),
    [
        # the verification did not genuinely fail: no inert chain after the apply
        ({"inert": False}, "compensation-verification"),
        # the baseline never held the inert rule
        (
            {"first": support.baseline_observation(inert=False)},
            "compensation-baseline",
        ),
        # the baseline held no foreign rules, so the restore comparison is vacuous
        (
            {"first": support.baseline_observation(foreign=False)},
            "compensation-baseline",
        ),
        # restore_chains was given something other than the snapshot
        (
            {"restore_argument": support.baseline_observation(foreign=False).chains},
            "compensation-restore",
        ),
        # the compensation deleted a bystander's rule
        (
            {"final": support.baseline_observation(foreign=False)},
            "compensation-restore",
        ),
        # a record of the sequence failed
        ({"failed_index": 4}, "compensation-sequence"),
    ],
)
def test_n6_refuses_each_planted_break(spec, plan, options, code) -> None:
    proof = _proof(spec, plan, **options)
    refused(
        code, nproof.require_compensation, proof, execution_plan=plan, descriptor=spec
    )


def test_n6_refuses_a_sequence_without_the_compensation(spec, plan) -> None:
    records = support.negative_records(spec, plan)[:-2]
    proof = nproof.parse_proof(
        support.proof_bytes(exposure_calls=serialize_exposure_calls(records))
    )
    refused(
        "compensation-sequence",
        nproof.require_compensation,
        proof,
        execution_plan=plan,
        descriptor=spec,
    )


def test_n6_refuses_rules_that_are_not_the_descriptors(spec, plan) -> None:
    records = support.negative_records(spec, plan)
    replaced = records[1]
    records[1] = type(replaced)(
        replaced.sequence, "replace_rules", family=replaced.family, chain=replaced.chain
    )
    proof = nproof.parse_proof(
        support.proof_bytes(exposure_calls=serialize_exposure_calls(records))
    )
    refused(
        "compensation-records",
        nproof.require_compensation,
        proof,
        execution_plan=plan,
        descriptor=spec,
    )


@pytest.mark.parametrize(
    "terminal",
    [
        {"terminal_refusal": "evidence_unreadable"},
        {"terminal_refusal": ""},
        {"host_mutated": False},
        {"exit_status": 0},
    ],
)
def test_n6_refuses_a_terminal_that_is_not_the_refusal(spec, plan, terminal) -> None:
    records = support.negative_records(spec, plan)
    proof = nproof.parse_proof(
        support.proof_bytes(
            exposure_calls=serialize_exposure_calls(records), **terminal
        )
    )
    refused(
        "compensation-terminal",
        nproof.require_compensation,
        proof,
        execution_plan=plan,
        descriptor=spec,
    )


# --- N7: the probe ------------------------------------------------------------


def _probed(measured_at: datetime, **probe) -> nproof.NegativeProof:
    return nproof.parse_proof(
        support.proof_bytes(
            probe=support.probe_bytes(
                probe.pop("dispatch_id", "22222222-2222-4222-8222-222222222222"),
                probe.pop("host_id", "host-n"),
                measured_at,
                **probe,
            )
        )
    )


def test_a_probe_inside_the_lease_window_passes_n7() -> None:
    nproof.require_probe(
        _probed(T + timedelta(minutes=7)),
        ledger_created_at=T + timedelta(minutes=5),
        released_at=T + timedelta(minutes=10),
    )


@pytest.mark.parametrize(
    ("probe", "code"),
    [
        ({"dispatch_id": "other"}, "probe-identity"),
        ({"host_id": "other"}, "probe-identity"),
        ({"statuses": ("executed_passed", "executed_failed")}, "probe-not-passed"),
        ({"statuses": ("blocked", "executed_passed")}, "probe-not-passed"),
    ],
)
def test_n7_refuses_a_probe_that_is_not_this_targets_or_not_passed(probe, code) -> None:
    refused(
        code,
        nproof.require_probe,
        _probed(T + timedelta(minutes=7), **probe),
        ledger_created_at=T + timedelta(minutes=5),
        released_at=T + timedelta(minutes=10),
    )


@pytest.mark.parametrize(
    "measured",
    [
        T + timedelta(minutes=5) - timedelta(seconds=61),
        T + timedelta(minutes=10) + timedelta(seconds=61),
    ],
)
def test_n7_refuses_a_probe_outside_the_window(measured) -> None:
    refused(
        "probe-outside-window",
        nproof.require_probe,
        _probed(measured),
        ledger_created_at=T + timedelta(minutes=5),
        released_at=T + timedelta(minutes=10),
    )


def test_a_b64_field_must_be_canonical_base64() -> None:
    document = json.loads(support.proof_bytes())
    raw = base64.b64decode(document["negative"]["exposure_calls_b64"])
    document["negative"]["exposure_calls_b64"] = base64.urlsafe_b64encode(
        raw + b"\xfb\xff"
    ).decode()
    forged = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    with pytest.raises(nproof.ProofRefused):
        nproof.parse_proof(forged)


# --- M1: the window acknowledgement; L4: the size cap ------------------------------


def test_n7_requires_the_terminal_acknowledgement_that_done_closed_the_window() -> None:
    proof = nproof.parse_proof(support.proof_bytes(window_done=False))
    refused(
        "probe-window-unacknowledged",
        nproof.require_probe,
        proof,
        ledger_created_at=T + timedelta(minutes=5),
        released_at=T + timedelta(minutes=10),
    )


def test_the_acknowledgement_is_a_closed_boolean_of_the_terminal_block() -> None:
    document = json.loads(support.proof_bytes())
    for bad in ("yes", 1, None):
        document["negative"]["terminal"]["window_done"] = bad
        forged = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        refused("proof-invalid", nproof.parse_proof, forged)
    del document["negative"]["terminal"]["window_done"]
    forged = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    refused("proof-invalid", nproof.parse_proof, forged)


def test_the_proof_member_is_capped_as_a_whole() -> None:
    """The calls ride inside the proof as base64: the effective raw cap is lower
    than the 16 MiB member cap (about 12 MiB), and an oversize proof is refused."""
    assert nproof.MAX_CALLS_BYTES == 16 * 1024 * 1024
    refused("proof-invalid", nproof.parse_proof, b"x" * (nproof.MAX_CALLS_BYTES + 1))
    effective = nproof.MAX_CALLS_BYTES * 3 // 4
    assert effective < nproof.MAX_CALLS_BYTES
