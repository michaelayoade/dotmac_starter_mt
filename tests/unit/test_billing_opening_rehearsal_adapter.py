"""The rehearsal adapter is a blocker census: it synthesizes no evidence."""

from __future__ import annotations

import ast
import copy
import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from dotmac_billing.opening_rehearsal import (
    render_opening_rehearsal_census,
    report_opening_rehearsal,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "opening_rehearsal"
ADAPTER_PATH = ROOT / "scripts" / "billing_opening_rehearsal_adapter.py"
spec = importlib.util.spec_from_file_location(
    "billing_opening_rehearsal_adapter", ADAPTER_PATH
)
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
sys.modules["billing_opening_rehearsal_adapter"] = adapter
spec.loader.exec_module(adapter)

ACCOUNT = "00000000-0000-0000-0000-00000000000a"
INVOICE_1 = "00000000-0000-0000-0000-000000000001"
INVOICE_2 = "00000000-0000-0000-0000-000000000002"
SETTLEMENT_1 = "00000000-0000-0000-0000-000000001001"
PAYMENT_1 = "00000000-0000-0000-0000-000000002001"
PROOF_1 = "00000000-0000-0000-0000-000000003001"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _assembly(name="assembly.json"):
    return adapter.load_assembly(_fixture(name))


def _typed_census(payload, assembly=None):
    return adapter.rehearsal_census(payload, assembly=assembly or _assembly())


def _census(payload, assembly=None):
    # The package-owned canonical rendering, read back as a plain mapping.
    return json.loads(render_opening_rehearsal_census(_typed_census(payload, assembly)))


def _run(payload, assembly=None):
    obs, sidecar = adapter.load_sub_observation(payload)
    return adapter.assemble_rehearsal_run(
        obs, assembly=assembly or _assembly(), sidecar=sidecar
    )


def _codes(artifact, section):
    return {row["code"] for row in artifact[section]}


def _all_codes(artifact):
    codes = _codes(artifact, "report_blockers") | _codes(artifact, "adapter_blockers")
    for partition in artifact["partitions"]:
        codes.update(row["code"] for row in partition["blockers"])
    return codes


def _cli(
    tmp_path, census_payload, assembly_name="assembly.json", census_name="census.json"
):
    census_path = tmp_path / census_name
    census_path.write_text(json.dumps(census_payload), encoding="utf-8")
    out_path = tmp_path / "artifact.json"
    code = adapter.main(
        [
            "--census",
            str(census_path),
            "--assembly",
            str(FIXTURES / assembly_name),
            "--out",
            str(out_path),
        ]
    )
    artifact = (
        json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
    )
    return code, artifact


@pytest.mark.parametrize("fixture", ["census_empty_v2.json", "census_empty_v3.json"])
def test_empty_complete_population_command_free_clean(fixture, tmp_path):
    payload = _fixture(fixture)
    assembly = _assembly("assembly_empty.json")
    artifact = _census(payload, assembly)
    assert artifact["partitions"] == []
    assert artifact["report_blockers"] == []
    assert artifact["adapter_blockers"] == []
    assert artifact["rollup"]["complete_exact_reconciliation"] is True
    report = report_opening_rehearsal(_run(payload, assembly))
    assert report.activation_ready is False
    code, _ = _cli(tmp_path, payload, "assembly_empty.json")
    assert code == 0


def test_empty_population_with_partition_assembly_counts_missing_verifier(tmp_path):
    code, artifact = _cli(tmp_path, _fixture("census_empty_v2.json"))
    assert code == 1
    assert artifact["partitions"][0]["admissible"] is False
    assert "verifier_unavailable" in _all_codes(artifact)


def test_one_eligible_invoice_blocks_on_missing_original():
    payload = _fixture("census_one_eligible_v2.json")
    artifact = _census(payload)
    partition = artifact["partitions"][0]
    assert partition["admissible"] is False
    assert partition["cash_residual_minor"] == 0
    assert partition["receivable_residual_minor"] == 10000
    assert "original_documentary_evidence_unavailable" in _codes(
        artifact, "report_blockers"
    )
    assert "original_documentary_evidence_missing" in {
        row["code"] for row in partition["blockers"]
    }
    assert "verifier_unavailable" in _all_codes(artifact)
    run = _run(payload)
    assert run.manifest.cash_lots == ()
    assert all(item.original is None for item in run.manifest.receivables)
    assert "OpeningOriginalDocumentV1" not in repr(run.manifest)


def _type_names(value, seen=None):
    seen = seen if seen is not None else set()
    if id(value) in seen:
        return set()
    seen.add(id(value))
    names = {type(value).__name__}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            names.update(_type_names(getattr(value, field.name), seen))
    elif isinstance(value, tuple | list):
        for item in value:
            names.update(_type_names(item, seen))
    return names


@pytest.mark.parametrize(
    "fixture",
    [
        "census_empty_v2.json",
        "census_empty_v3.json",
        "census_one_eligible_v2.json",
        "census_v3_documentary.json",
    ],
)
def test_no_synthesis_invariant(fixture):
    assembly = (
        _assembly("assembly_empty.json") if fixture.startswith("census_empty") else None
    )
    run = _run(_fixture(fixture), assembly)
    names = _type_names(run)
    assert names.isdisjoint(
        {
            "OpeningOriginalDocumentV1",
            "OpeningCashConfirmationV1",
            "OpeningCashLotV1",
            "OpeningReplayBindingV1",
        }
    )
    assert run.manifest.cash_lots == ()
    for item in run.manifest.receivables:
        assert item.original is None
        assert item.historical_discharges == ()
        assert item.collection.status == "unknown_unverified"
        assert item.collection.automated_collection_allowed is False
    serialized = repr(run.manifest)
    # V3 current (not original) amounts must never enter the manifest.
    for current_value in ("77777.77", "8888.88", "66.66"):
        assert current_value not in serialized


def _cash_mutated(rows_by_table):
    payload = _fixture("census_one_eligible_v2.json")
    field_by_table = {
        "payments": "payments",
        "payment_settlements": "payment_settlements",
        "payment_proofs": "proofs",
    }
    for table, rows in rows_by_table.items():
        payload["cash_evidence"][field_by_table[table]] = rows
        for count_row in payload["cash_evidence"]["source_counts"]:
            if count_row["table"] == table:
                count_row["count"] = len(rows)
    return payload


def test_nonempty_cash_population_blocks_and_never_nets():
    payload = _cash_mutated(
        {
            "payments": [{"id": PAYMENT_1, "amount": "40.00"}],
            # Gross equal to its settlement row still cannot net to a residual.
            "payment_settlements": [
                {"id": SETTLEMENT_1, "payment_id": PAYMENT_1, "amount": "40.00"}
            ],
        }
    )
    artifact = _census(payload)
    partition = artifact["partitions"][0]
    assert partition["cash_residual_minor"] == 0
    cash_blocker = next(
        row
        for row in artifact["report_blockers"]
        if row["code"] == "cash_residual_evidence_unavailable"
    )
    assert set(cash_blocker["references"]) == {"payments", "payment_settlements"}
    cash_delta = next(row for row in partition["deltas"] if row["lane"] == "cash")
    assert cash_delta["source_minor"] is None
    assert cash_delta["unclassified_delta_minor"] is None
    run = _run(payload)
    assert run.manifest.cash_lots == ()


def test_qualified_payment_ids_are_never_cleared_by_the_adapter():
    payload = _fixture("census_one_eligible_v2.json")
    payload["cash_evidence"]["qualified_payment_ids"] = [PAYMENT_1]
    obs, _sidecar = adapter.load_sub_observation(payload)
    assert [str(item) for item in obs.qualified_payment_ids] == [PAYMENT_1]
    artifact = _census(payload)
    assert "unsupported_cash_qualification" in _codes(artifact, "report_blockers")


def test_money_needing_rounding_blocks_instead_of_rounding():
    payload = _fixture("census_one_eligible_v2.json")
    invoice = payload["invoices"][0]
    invoice["total"] = invoice["residual"] = invoice["stored_residual"] = "10.005"
    control = payload["controls"][0]
    control["original_total"] = control["residual"] = control["eligible_residual"] = (
        "10.005"
    )
    payload["lanes"][0]["collectible_receivable"] = "10.005"
    artifact = _census(payload)
    assert "currency_precision_mismatch" in _all_codes(artifact)
    run = _run(payload)
    assert run.manifest.receivables == ()
    assert all(item.residual_minor != 1001 for item in run.manifest.dispositions)


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", 10.5, 100])
def test_non_finite_and_json_number_money_is_malformed(bad_value, tmp_path):
    payload = _fixture("census_one_eligible_v2.json")
    payload["invoices"][0]["residual"] = bad_value
    with pytest.raises(adapter.AdapterInputError):
        adapter.load_sub_observation(payload)
    code, artifact = _cli(tmp_path, payload)
    assert code == 2 and artifact is None


def test_missing_currency_precision_blocks():
    payload = _fixture("census_one_eligible_v2.json")
    payload["capture"]["declared_source"]["currency_precisions"] = []
    artifact = _census(payload)
    assert "currency_precision_mismatch" in _all_codes(artifact)


def test_disposition_overlap_blocking_wins():
    payload = _fixture("census_one_eligible_v2.json")
    payload["exclusions"] = [{"source_id": INVOICE_1, "reason": "draft"}]
    payload["blocking_exceptions"] = [
        {
            "code": "residual_mismatch",
            "account_id": ACCOUNT,
            "currency": "NGN",
            "source_id": INVOICE_1,
            "detail": "fixture overlap",
        }
    ]
    payload["controls"][0]["exception_count"] = 1
    run = _run(payload)
    dispositions = run.manifest.dispositions
    assert len(dispositions) == 1
    assert dispositions[0].disposition == "blocking"
    assert dispositions[0].reason == "residual_mismatch"
    assert run.manifest.counts.blocking == 1
    assert run.manifest.counts.eligible == 0
    assert run.manifest.receivables == ()
    artifact = _census(payload)
    assert "invoice_mapping_coverage_mismatch" in _codes(artifact, "report_blockers")


def test_disposition_overlap_excluded_beats_eligible():
    payload = _fixture("census_one_eligible_v2.json")
    payload["exclusions"] = [{"source_id": INVOICE_1, "reason": "draft"}]
    run = _run(payload)
    assert run.manifest.dispositions[0].disposition == "excluded"
    artifact = _census(payload)
    assert "excluded_nonzero_residue" in _all_codes(artifact)


def test_unmapped_account_invents_no_partition(tmp_path):
    code, artifact = _cli(
        tmp_path, _fixture("census_one_eligible_v2.json"), "assembly_empty.json"
    )
    assert code == 1
    assert artifact["partitions"] == []
    assert artifact["unmapped"]["accounts"] == [ACCOUNT]
    assert artifact["unmapped"]["invoices"] == [INVOICE_1]
    assert artifact["rollup"]["unmapped_counts"] == {
        "accounts": 1,
        "invoices": 1,
        "settlements": 0,
    }
    run = _run(
        _fixture("census_one_eligible_v2.json"), _assembly("assembly_empty.json")
    )
    assert run.mappings == ()
    assert run.manifest.controls == ()


def test_unmapped_invoice_mapping_is_counted_not_invented():
    assembly_payload = _fixture("assembly.json")
    assembly_payload["invoice_mappings"] = []
    assembly = adapter.load_assembly(assembly_payload)
    artifact = _census(_fixture("census_one_eligible_v2.json"), assembly)
    assert artifact["unmapped"]["invoices"] == [INVOICE_1]
    assert "unmapped_source_invoice" in _all_codes(artifact)
    run = _run(_fixture("census_one_eligible_v2.json"), assembly)
    assert run.manifest.receivables == ()
    assert run.invoice_mappings == ()


def _discharged_fixture(kind, amount, discharge, residual):
    payload = _fixture("census_one_eligible_v2.json")
    invoice = payload["invoices"][0]
    invoice["canonical_discharge"] = discharge
    invoice["residual"] = invoice["stored_residual"] = residual
    invoice["settlement_ids"] = [SETTLEMENT_1]
    payload["settlements"] = [
        {
            "kind": kind,
            "id": SETTLEMENT_1,
            "invoice_id": INVOICE_1,
            "source_id": PAYMENT_1,
            "amount": amount,
            "ledger_entry_id": None,
            "consumption_ledger_entry_id": None,
            "active": True,
            "created_at": "2026-10-05T00:00:00+00:00",
        }
    ]
    control = payload["controls"][0]
    control["canonical_discharge"] = discharge
    control["residual"] = control["eligible_residual"] = residual
    payload["lanes"][0]["collectible_receivable"] = residual
    return payload


def test_opening_funding_and_wht_looking_proof_never_become_discharges():
    payload = _discharged_fixture("opening_funding", "40.00", "40.00", "60.00")
    payload["cash_evidence"]["proofs"] = [
        {"id": PROOF_1, "amount": "40.00", "wht_amount": "13.13", "currency": "NGN"}
    ]
    for count_row in payload["cash_evidence"]["source_counts"]:
        if count_row["table"] == "payment_proofs":
            count_row["count"] = 1
    artifact = _census(payload)
    codes = _all_codes(artifact)
    assert "historical_discharge_evidence_unavailable" in codes
    assert "cash_residual_evidence_unavailable" in codes
    assert artifact["unmapped"]["settlements"] == [SETTLEMENT_1]
    run = _run(payload)
    assert all(item.historical_discharges == () for item in run.manifest.receivables)
    assert "13.13" not in repr(run.manifest)


def test_fractional_settlement_attribution_stays_blocked_without_penny_allocation():
    payload = _discharged_fixture("payment", "10.005", "0.00", "100.00")
    artifact = _census(payload)
    assert "historical_discharge_evidence_unavailable" in _all_codes(artifact)
    run = _run(payload)
    assert all(item.historical_discharges == () for item in run.manifest.receivables)


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (
            lambda p: p["capture"].__setitem__("isolation", "read committed"),
            "source_snapshot_unproven",
        ),
        (
            lambda p: p["capture"]["declared_source"].__setitem__(
                "currency_precisions", [["NGN", 3]]
            ),
            "currency_precision_mismatch",
        ),
        (
            lambda p: p.__setitem__("invoices", p["invoices"] * 2),
            "duplicate_evidence",
        ),
        (
            lambda p: p["controls"][0].__setitem__("residual", "100.01"),
            "source_control_equation_mismatch",
        ),
        (
            lambda p: p["capture"].__setitem__(
                "schema_heads", ["fixture-schema:1", "fixture-schema:1"]
            ),
            "source_binding_mismatch",
        ),
    ],
)
def test_binding_tamper_surfaces_source_blockers(mutate, expected, tmp_path):
    payload = _fixture("census_one_eligible_v2.json")
    mutate(payload)
    artifact = _census(payload)
    assert expected in _codes(artifact, "report_blockers")
    code, _ = _cli(tmp_path, payload)
    assert code == 1


@pytest.mark.parametrize(
    "field,value", [("snapshot_id", "tampered"), ("source_repository_sha", "f" * 40)]
)
def test_capture_sha_and_snapshot_tamper_break_binding(field, value):
    run = _run(_fixture("census_one_eligible_v2.json"))
    tampered_manifest = dataclasses.replace(
        run.manifest,
        capture=dataclasses.replace(run.manifest.capture, **{field: value}),
    )
    report = report_opening_rehearsal(
        dataclasses.replace(run, manifest=tampered_manifest)
    )
    assert "source_binding_mismatch" in {row.code for row in report.blockers}


def test_same_input_bytes_produce_byte_identical_artifact(tmp_path):
    payload = _fixture("census_v3_documentary.json")
    census_path = tmp_path / "census.json"
    census_path.write_text(json.dumps(payload), encoding="utf-8")
    outputs = []
    for index in range(2):
        out_path = tmp_path / f"artifact-{index}.json"
        assert (
            adapter.main(
                [
                    "--census",
                    str(census_path),
                    "--assembly",
                    str(FIXTURES / "assembly.json"),
                    "--out",
                    str(out_path),
                ]
            )
            == 1
        )
        outputs.append(out_path.read_bytes())
    assert outputs[0] == outputs[1]
    first = _typed_census(copy.deepcopy(payload))
    second = _typed_census(copy.deepcopy(payload))
    assert first == second
    assert render_opening_rehearsal_census(first) == render_opening_rehearsal_census(
        second
    )
    assert render_opening_rehearsal_census(first).encode() == outputs[0]


def test_adapter_module_purity_ast():
    tree = ast.parse(ADAPTER_PATH.read_text(encoding="utf-8"))
    imported_modules = set()
    used_names = set()
    called_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Name):
            used_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            used_names.add(node.attr)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)
    assert not any(
        module == "service" or module.endswith(".service")
        for module in imported_modules
    )
    assert used_names.isdisjoint(
        {
            "import_opening_account",
            "import_opening_account_v2",
            "Session",
            "SessionLocal",
        }
    )
    assert called_names.isdisjoint(
        {
            "OpeningOriginalDocumentV1",
            "OpeningCashConfirmationV1",
            "OpeningCashLotV1",
            "OpeningReplayBindingV1",
        }
    )
    assert not any("Verif" in name for name in called_names)
    assert "DATABASE_URL" not in ADAPTER_PATH.read_text(encoding="utf-8")


def test_cli_runs_without_database_url(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    code, artifact = _cli(tmp_path, _fixture("census_one_eligible_v2.json"))
    assert code == 1 and artifact is not None


def test_cli_refuses_to_run_without_an_assembly(tmp_path):
    census_path = tmp_path / "census.json"
    census_path.write_text(
        json.dumps(_fixture("census_one_eligible_v2.json")), encoding="utf-8"
    )
    with pytest.raises(SystemExit) as raised:
        adapter.main(["--census", str(census_path)])
    assert raised.value.code == 2


def test_malformed_and_unsupported_inputs_exit_two(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not json", encoding="utf-8")
    assert (
        adapter.main(
            ["--census", str(bad_path), "--assembly", str(FIXTURES / "assembly.json")]
        )
        == 2
    )
    payload = _fixture("census_one_eligible_v2.json")
    payload["capture"]["declared_source"]["population_version"] = (
        "sub-billing-opening-census-v1"
    )
    code, artifact = _cli(tmp_path, payload)
    assert code == 2 and artifact is None


def test_v3_sidecar_counts_excluded_without_double_counting_eligible():
    artifact = _census(_fixture("census_v3_documentary.json"))
    sidecar = artifact["v3_documentary_sidecar"]
    assert sidecar["unavailable_count"] == 2
    assert sidecar["invoice_ids"] == [INVOICE_1, INVOICE_2]
    assert sidecar["by_requirement"]["documentary_reference"] == 2
    assert sidecar["by_reason"] == {"no_authenticated_frozen_document": 2}
    unavailable = next(
        row
        for row in artifact["report_blockers"]
        if row["code"] == "original_documentary_evidence_unavailable"
    )
    # Only the eligible invoice is double-referenced by the rehearsal (by id
    # and by mapped key); the excluded invoice lives solely in the sidecar.
    assert unavailable["references"] == [INVOICE_1, "invoice:fixture-1"]
    assert artifact["adapter_blockers"] == []
    assert (
        "documentary_observations.current_invoices" in artifact["dropped_source_fields"]
    )


def test_adapter_has_no_canonicalizing_call_of_its_own():
    """The canonical encodings are package-owned (no tooling ratchet entry)."""
    tree = ast.parse(ADAPTER_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            assert not (
                name == "dumps" and any(kw.arg == "sort_keys" for kw in node.keywords)
            )
            assert name not in {"sha256", "fingerprint_of"}


def test_adapter_digest_is_the_package_digest_and_pinned():
    # Domain-separated, versioned identity (supersedes the undomained digest).
    from dotmac_billing.opening_rehearsal import opening_rehearsal_assembly_digest

    for name, pinned in (
        (
            "assembly.json",
            "f5ae59d4cf966dceabb783a1e0935cef6ca3f87a6316a4fb5fed5d29d5d0e295",
        ),
        (
            "assembly_empty.json",
            "540312cff7d08deddebd66e09b86b517278fba776fe43ab3587125ddeb9f2a08",
        ),
    ):
        assert _assembly(name).assembly_digest == pinned
        assert (
            _typed_census(
                _fixture("census_empty_v2.json"), _assembly(name)
            ).provenance.assembly_digest
            == pinned
        )
    assert opening_rehearsal_assembly_digest  # the single definition exists


def test_assembly_wire_round_trips_through_the_loader_to_the_same_digest():
    from dotmac_billing.opening_rehearsal import (
        opening_rehearsal_assembly_digest,
        opening_rehearsal_assembly_wire,
    )

    for name in ("assembly.json", "assembly_empty.json"):
        declared = _fixture(name)
        loaded = adapter.load_assembly(declared)
        # The typed declaration re-encodes to exactly the file that produced it.
        assert opening_rehearsal_assembly_wire(loaded.declaration) == declared
        assert opening_rehearsal_assembly_digest(loaded.declaration) == (
            loaded.assembly_digest
        )
        again = adapter.load_assembly(
            opening_rehearsal_assembly_wire(loaded.declaration)
        )
        assert again.assembly_digest == loaded.assembly_digest
        assert again.declaration == loaded.declaration


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _main(tmp_path, census_path, assembly_path):
    return adapter.main(
        [
            "--census",
            str(census_path),
            "--assembly",
            str(assembly_path),
            "--out",
            str(tmp_path / "out.json"),
        ]
    )


def _refusal(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(approved_by="someone"),
        lambda d: d.update(amount_override="1"),
        lambda d: d["partitions"][0].update(cash_lots=[]),
        lambda d: d["binding"].update(approved=True),
        lambda d: d["scope"].update(extra=1),
        lambda d: d["invoice_mappings"][0].update(extra=1),
        lambda d: d["invoice_mappings"][0]["source_identity"].update(extra=1),
        lambda d: d.pop("accepted_manual_holds"),
        lambda d: d.update(schema="billing.opening.rehearsal.assembly.v0"),
        lambda d: d.update(recorded_cutoff="2026-10-13T01:00:00+01:00"),
        lambda d: d.update(occurred_at="2026-10-13T00:00:00"),
    ],
)
def test_strict_assembly_refuses_through_the_cli(tmp_path, capsys, mutate):
    declared = _fixture("assembly.json")
    mutate(declared)
    with pytest.raises(adapter.AdapterInputError):
        adapter.load_assembly(declared)
    census = _write(tmp_path, "c.json", json.dumps(_fixture("census_empty_v2.json")))
    assembly = _write(tmp_path, "a.json", json.dumps(declared))
    assert _main(tmp_path, census, assembly) == 2
    assert _refusal(capsys) == {"complete": False, "error": "malformed_assembly"}
    assert not (tmp_path / "out.json").exists()


def test_utc_z_and_plus_zero_are_the_same_assembly(tmp_path):
    declared = _fixture("assembly.json")
    zulu = copy.deepcopy(declared)
    zulu["recorded_cutoff"] = "2026-10-13T00:00:00Z"
    zulu["occurred_at"] = "2026-10-13T00:00:00Z"
    assert (
        adapter.load_assembly(zulu).assembly_digest
        == adapter.load_assembly(declared).assembly_digest
    )


def test_duplicate_json_keys_refuse_for_every_json_read(tmp_path, capsys):
    good_census = json.dumps(_fixture("census_empty_v2.json"))
    good_assembly = (FIXTURES / "assembly.json").read_text(encoding="utf-8")
    duplicated_assembly = good_assembly.replace(
        '"source_system": "sub"', '"source_system": "sub", "source_system": "sub"'
    )
    assert duplicated_assembly != good_assembly
    assert (
        _main(
            tmp_path,
            _write(tmp_path, "c.json", good_census),
            _write(tmp_path, "a.json", duplicated_assembly),
        )
        == 2
    )
    assert _refusal(capsys)["error"] == "malformed_assembly"
    duplicated_census = good_census.replace("{", '{"x": 1, "x": 2, ', 1)
    assert (
        _main(
            tmp_path,
            _write(tmp_path, "c2.json", duplicated_census),
            _write(tmp_path, "a2.json", good_assembly),
        )
        == 2
    )
    assert _refusal(capsys)["error"] == "malformed_input"
    assert not (tmp_path / "out.json").exists()
