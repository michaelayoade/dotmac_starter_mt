"""The retirement receipt: what it must name, and what it refuses.

A receipt commits at STEP 6 — the removal — and it is the only thing that moves
the programme scoreboard. Governance ADR 0018's authority-cutover receipt is
the model, and the relationship between the two is explicit rather than
implied: that receipt's field 7, `old_writer_retirement`, is where a displaced
executor's disposition goes, and THIS receipt is what a `retired` disposition
there points at. One is the cutover's evidence; this is the removal's.

Every refusal below exists because the thing it refuses is a shape that reads
as done. That is the whole design constraint: a receipt is only worth writing
if it can fail.

There is deliberately NO REGISTRY. Receipts are product-side artifacts, and
the cross-repository envelope is Governance's to own; the creation of that
store is a separate decision. These tests exercise the SCHEMA over fixtures,
which is the correct scope for a schema that this repository does not store
instances of.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "executor_retirement.py"
_MODULE_NAME = "executor_retirement"

COMMIT_A = "1" * 40
COMMIT_B = "2" * 40
COMMIT_C = "3" * 40


def _sweep():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[_MODULE_NAME]
        raise
    return module


# ── A minimal TOML writer, so a fixture is built rather than string-patched ──


def _scalar(value) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(item) for item in value) + "]"
    return str(value)


def _toml(document: dict) -> str:
    lines: list[str] = []
    for key, value in document.items():
        if isinstance(value, dict) or (
            isinstance(value, list) and value and isinstance(value[0], dict)
        ):
            continue
        lines.append(f"{key} = {_scalar(value)}")
    for key, value in document.items():
        if isinstance(value, dict):
            lines.append(f"\n[{key}]")
            for inner, item in value.items():
                lines.append(f"{inner} = {_scalar(item)}")
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for row in value:
                lines.append(f"\n[[{key}]]")
                for inner, item in row.items():
                    lines.append(f"{inner} = {_scalar(item)}")
    return "\n".join(lines) + "\n"


def _receipt(**overrides) -> dict:
    """A conforming receipt. Every refusal test below breaks exactly one thing."""
    document: dict = {
        "schema": "ExecutorRetirementReceipt.v1",
        "status": "committed",
        "receipt_id": "erp-deploy-sh-2026-09-12",
        "product": "dotmac_erp",
        "subject": {
            "entrypoint": "script:deploy.sh",
            "family": "script",
            "inventory_digest": "sha256:" + "a" * 64,
        },
        "controller_receipts": [
            {
                "cycle": "deploy",
                "run_id": "40000000001",
                "head_commit": COMMIT_A,
                "observed_at": "2026-09-10",
                "observed_by": "michaelayoade",
                "outcome": "success",
            },
            {
                "cycle": "redeploy",
                "run_id": "40000000002",
                "head_commit": COMMIT_B,
                "observed_at": "2026-09-11",
                "observed_by": "michaelayoade",
                "outcome": "success",
            },
        ],
        "removals": [
            {
                "class": "script",
                "identity": "scripts/deploy.sh",
                "removed_in": COMMIT_C,
            },
            {
                "class": "credential",
                "identity": "erp-deploy-host-ssh-key",
                "removed_in": COMMIT_C,
            },
        ],
        "zero_surface_guard": {
            "family": "script",
            "check": "scripts/executor_retirement.py --check",
            "sensitivity_proof": (
                "tests/architecture/test_executor_retirement_ratchet.py::"
                "test_an_undeclared_entrypoint_in_each_family_is_caught"
            ),
        },
        "recovery_verdict": {
            "verdict": "recovered",
            "restored_from": "sha256:" + "b" * 64,
            "exercise_run_id": "40000000003",
            "observed_at": "2026-09-12",
            "observed_by": "michaelayoade",
        },
        "retained_rollback": [
            {
                "identity": "docker-compose.yml",
                "why": "the prior release's topology, kept as the recovery path",
            }
        ],
    }
    document.update(overrides)
    for key in [k for k, v in document.items() if v is None]:
        del document[key]
    return document


def _text(document: dict, *, sign: bool = True) -> str:
    sweep = _sweep()
    if sign and document.get("status") == "committed":
        document = dict(document)
        document["digest"] = sweep.receipt_digest(document)
    return _toml(document)


def _validate(document: dict, *, sign: bool = True):
    return _sweep().validate_receipt(_text(document, sign=sign), source="probe.toml")


# ── The negative control ────────────────────────────────────────────────────


def test_a_conforming_receipt_is_admissible() -> None:
    """The discriminating control for every refusal below. Without it, a test
    suite of refusals proves only that the validator refuses things."""
    parsed = _validate(_receipt())
    assert parsed["status"] == "committed"
    assert parsed["subject"]["entrypoint"] == "script:deploy.sh"


def test_the_schema_carries_every_field_michael_enumerated() -> None:
    """Both successful controller receipts, everything removed, the zero-surface
    guard now covering that family, and the product's PROVED recovery verdict."""
    sweep = _sweep()
    assert {
        "subject",
        "controller_receipts",
        "removals",
        "zero_surface_guard",
        "recovery_verdict",
        "retained_rollback",
        "digest",
    } <= sweep.RECEIPT_KEYS
    assert set(sweep.REMOVAL_CLASSES) == {
        "script",
        "workflow",
        "cron_or_unit",
        "credential",
        "permission",
        "documentation",
        "configuration_flag",
    }


def test_absence_is_not_a_status() -> None:
    """The status vocabulary, and the rule Governance states in the same words:
    a receipt that forgot to say whether the removal happened reads as if it
    did."""
    sweep = _sweep()
    assert set(sweep.RECEIPT_STATUSES) == {"proposed", "committed", "superseded"}
    with pytest.raises(sweep.ReceiptError) as caught:
        _validate(_receipt(status=None), sign=False)
    assert "Absence is not a status" in str(caught.value)


# ── Refusals ────────────────────────────────────────────────────────────────


def test_an_unknown_schema_is_refused() -> None:
    sweep = _sweep()
    with pytest.raises(sweep.ReceiptError, match="schema must be"):
        _validate(_receipt(schema="ExecutorRetirementReceipt.v2"), sign=False)


def test_an_unknown_key_is_refused() -> None:
    """A receipt whose reader and writer disagree about the vocabulary proves
    nothing, and the disagreement is silent unless it is refused."""
    sweep = _sweep()
    with pytest.raises(sweep.ReceiptError, match="unknown key"):
        _validate(_receipt(rollback_plan="see the runbook"), sign=False)


def test_one_controller_receipt_is_refused() -> None:
    """One successful deployment proves the replacement can deploy. Only the
    second proves it can deploy AGAIN, over its own previous state — which is
    the property the legacy executor had and the reason it is trusted."""
    sweep = _sweep()
    document = _receipt()
    document["controller_receipts"] = document["controller_receipts"][:1]
    with pytest.raises(sweep.ReceiptError, match="TWO successful"):
        _validate(document, sign=False)


def test_two_controller_receipts_naming_one_run_are_refused() -> None:
    """ "We deployed twice" is a sentence. One run cited twice is one
    deployment, however it is labelled."""
    sweep = _sweep()
    document = _receipt()
    document["controller_receipts"][1]["run_id"] = document["controller_receipts"][0][
        "run_id"
    ]
    with pytest.raises(sweep.ReceiptError, match="the same run"):
        _validate(document, sign=False)


def test_a_failed_controller_cycle_does_not_count() -> None:
    sweep = _sweep()
    document = _receipt()
    document["controller_receipts"][1]["outcome"] = "failure"
    with pytest.raises(sweep.ReceiptError, match="only a SUCCESSFUL"):
        _validate(document, sign=False)


def test_two_deploys_are_not_a_deploy_and_a_redeploy() -> None:
    sweep = _sweep()
    document = _receipt()
    document["controller_receipts"][1]["cycle"] = "deploy"
    with pytest.raises(sweep.ReceiptError, match="both cycles are required"):
        _validate(document, sign=False)


def test_a_branch_name_is_not_a_head_commit() -> None:
    """ADR 0013 coordinates. A branch tip is a timestamp presented as a fact."""
    sweep = _sweep()
    document = _receipt()
    document["controller_receipts"][0]["head_commit"] = "main"
    with pytest.raises(sweep.ReceiptError, match="40-character commit"):
        _validate(document, sign=False)


def test_a_retirement_with_no_removals_is_refused() -> None:
    """A status change is not a retirement. The scoreboard counts removals."""
    sweep = _sweep()
    with pytest.raises(sweep.ReceiptError, match="no removals is a status change"):
        _validate(_receipt(removals=[]), sign=False)


def test_an_unknown_removal_class_is_refused() -> None:
    sweep = _sweep()
    document = _receipt()
    document["removals"].append(
        {"class": "other", "identity": "something", "removed_in": COMMIT_C}
    )
    with pytest.raises(sweep.ReceiptError, match="unknown removal class"):
        _validate(document, sign=False)


def test_a_pull_request_number_is_not_a_removal_coordinate() -> None:
    sweep = _sweep()
    document = _receipt()
    document["removals"][0]["removed_in"] = "#418"
    with pytest.raises(sweep.ReceiptError, match="40-character commit that removed it"):
        _validate(document, sign=False)


def test_a_secret_shaped_removal_identity_is_refused() -> None:
    """A credential is NAMED in a receipt, never held. A receipt is the most
    widely copied artifact in a retirement, which is the worst possible place
    for material."""
    sweep = _sweep()
    document = _receipt()
    document["removals"][1]["identity"] = "-----BEGIN OPENSSH PRIVATE KEY-----"
    with pytest.raises(sweep.ReceiptError, match="NAMED in a receipt"):
        _validate(document, sign=False)


def test_a_guard_over_the_wrong_family_is_not_coverage() -> None:
    sweep = _sweep()
    document = _receipt()
    document["zero_surface_guard"]["family"] = "workflow"
    with pytest.raises(sweep.ReceiptError, match="is not coverage"):
        _validate(document, sign=False)


def test_a_guard_with_no_sensitivity_proof_is_refused() -> None:
    """A guard nobody proved fires is a guard that passes over an empty set
    (ADR-0018 §5). A zero-surface guard is the shape most exposed to it: after
    a retirement there is, by construction, nothing left for it to find."""
    sweep = _sweep()
    document = _receipt()
    del document["zero_surface_guard"]["sensitivity_proof"]
    with pytest.raises(sweep.ReceiptError, match="sensitivity_proof"):
        _validate(document, sign=False)


def test_a_documented_rollback_is_not_a_proved_recovery() -> None:
    """The field that stands between a retirement and an outage. "Rollback is
    documented" is not "rollback was performed and observed"."""
    sweep = _sweep()
    document = _receipt()
    document["recovery_verdict"]["verdict"] = "documented"
    with pytest.raises(sweep.ReceiptError, match="not verdicts"):
        _validate(document, sign=False)


def test_a_recovery_verdict_without_an_exercise_coordinate_is_refused() -> None:
    sweep = _sweep()
    document = _receipt()
    del document["recovery_verdict"]["exercise_run_id"]
    with pytest.raises(sweep.ReceiptError, match="exercise_run_id"):
        _validate(document, sign=False)


def test_an_unrecovered_exercise_cannot_be_committed() -> None:
    """`not_recovered` is exactly the state in which deleting the legacy
    executor removes the rollback path. It is a permitted value — silence is
    not — but it cannot be committed."""
    sweep = _sweep()
    document = _receipt()
    document["recovery_verdict"]["verdict"] = "not_recovered"
    with pytest.raises(sweep.ReceiptError, match="requires a PROVED recovery"):
        _validate(document, sign=False)
    document["status"] = "proposed"
    assert _validate(document, sign=False)["status"] == "proposed"


def test_a_committed_receipt_whose_digest_does_not_match_is_refused() -> None:
    """An immutable record that can be edited is a mutable record with a stern
    comment."""
    sweep = _sweep()
    document = _receipt()
    document["digest"] = "sha256:" + "0" * 64
    with pytest.raises(sweep.ReceiptError, match="does not match"):
        _validate(document, sign=False)


def test_the_digest_is_a_function_of_content_not_formatting() -> None:
    sweep = _sweep()
    document = _receipt()
    reordered = {key: document[key] for key in reversed(list(document))}
    assert sweep.receipt_digest(document) == sweep.receipt_digest(reordered)
    assert sweep.receipt_digest(document).startswith("sha256:")


def test_a_correction_is_a_supersession_never_an_edit() -> None:
    sweep = _sweep()
    with pytest.raises(sweep.ReceiptError, match="names the receipt that replaced"):
        _validate(_receipt(status="superseded"), sign=False)
    assert _validate(
        _receipt(status="superseded", superseded_by="erp-deploy-sh-2026-09-20"),
        sign=False,
    )


# ── Refusals that need the inventory ────────────────────────────────────────


def _inventory(disposition: str, credential: str = "erp-deploy-host-ssh-key"):
    sweep = _sweep()
    return sweep.Inventory(
        product="dotmac_erp",
        revision="0" * 40,
        production_targets=("erp.dotmac.io",),
        families_present=("script",),
        families_absent=tuple(n for n in sweep.FAMILY_NAMES if n != "script"),
        absences=(),
        entrypoints=(
            sweep.Entrypoint(
                name="script:deploy.sh",
                family="script",
                trigger="manual",
                credential=credential,
                disposition=disposition,
                path="scripts/deploy.sh",
                targets=("erp.dotmac.io",),
                receipt="erp-deploy-sh-2026-09-12",
            ),
        ),
    )


def _with_digest(document: dict, inventory) -> dict:
    document = dict(document)
    document["subject"] = dict(document["subject"])
    document["subject"]["inventory_digest"] = _sweep().inventory_digest(inventory)
    return document


def test_an_active_executor_cannot_be_receipted_straight_to_retired() -> None:
    """THE decisive refusal. A replacement is not adopted while the displaced
    executor can still act normally, so `active_executor` has no path to a
    retirement receipt — the jump this blocks is the one that takes the
    rollback path away with the script."""
    sweep = _sweep()
    inventory = _inventory("active_executor")
    document = _with_digest(_receipt(), inventory)
    with pytest.raises(sweep.ReceiptError, match="admissible only after"):
        sweep.validate_receipt(
            _text(document, sign=False), source="probe.toml", inventory=inventory
        )


def test_a_displaced_executor_may_be_receipted() -> None:
    sweep = _sweep()
    inventory = _inventory("displaced")
    document = _with_digest(_receipt(), inventory)
    assert sweep.validate_receipt(
        _text(document, sign=False), source="probe.toml", inventory=inventory
    )


def test_a_frozen_executor_may_not_be_receipted() -> None:
    """`frozen` is the state where the rollback path is deliberately intact and
    the replacement has not yet proven itself twice."""
    sweep = _sweep()
    inventory = _inventory("frozen")
    document = _with_digest(_receipt(), inventory)
    with pytest.raises(sweep.ReceiptError, match="admissible only after"):
        sweep.validate_receipt(
            _text(document, sign=False), source="probe.toml", inventory=inventory
        )


def test_a_subject_nobody_declared_is_refused() -> None:
    sweep = _sweep()
    inventory = _inventory("displaced")
    document = _with_digest(_receipt(), inventory)
    document["subject"] = dict(document["subject"])
    document["subject"]["entrypoint"] = "script:something_else.sh"
    with pytest.raises(sweep.ReceiptError, match="is not in"):
        sweep.validate_receipt(
            _text(document, sign=False), source="probe.toml", inventory=inventory
        )


def test_a_stale_inventory_digest_is_refused() -> None:
    """The receipt describes a census that has since changed, which is how a
    receipt survives the retirement it describes being partly undone."""
    sweep = _sweep()
    inventory = _inventory("displaced")
    with pytest.raises(sweep.ReceiptError, match="inventory digest does not match"):
        sweep.validate_receipt(
            _text(_receipt(), sign=False), source="probe.toml", inventory=inventory
        )


def test_a_live_credential_blocks_the_retirement() -> None:
    """A credential left live is a second executor waiting for whoever holds
    it. This is the removal class most often forgotten, because deleting the
    script feels like the end of the job."""
    sweep = _sweep()
    inventory = _inventory("displaced")
    document = _with_digest(_receipt(), inventory)
    document["removals"] = [
        row for row in document["removals"] if row["class"] != "credential"
    ]
    with pytest.raises(sweep.ReceiptError, match="second executor waiting"):
        sweep.validate_receipt(
            _text(document, sign=False), source="probe.toml", inventory=inventory
        )


def test_an_entrypoint_holding_no_credential_needs_no_credential_removal() -> None:
    """SPECIFICITY. A rule that fired on every subject would be satisfied by
    adding a decorative row, which is how a required field becomes a habit."""
    sweep = _sweep()
    inventory = _inventory("displaced", credential="none")
    document = _with_digest(_receipt(), inventory)
    document["removals"] = [
        row for row in document["removals"] if row["class"] != "credential"
    ]
    assert sweep.validate_receipt(
        _text(document, sign=False), source="probe.toml", inventory=inventory
    )
