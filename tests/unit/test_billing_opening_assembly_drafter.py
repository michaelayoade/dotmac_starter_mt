"""The assembly drafter maps identities only and never invents a value."""

from __future__ import annotations

import ast
import copy
import csv
import hashlib
import importlib.util
import io
import json
import sys
import tomllib
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from dotmac_billing.opening_rehearsal import (
    opening_rehearsal_assembly_digest,
    parse_opening_rehearsal_assembly,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "opening_rehearsal"
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


drafter = _load("billing_opening_assembly_drafter")
adapter = _load("billing_opening_rehearsal_adapter")

ACCOUNT = "00000000-0000-0000-0000-00000000000a"
TARGET_ACCOUNT = "00000000-0000-0000-0000-0000000003e9"
INVOICE_1 = "00000000-0000-0000-0000-000000000001"
SETTLEMENT_1 = "00000000-0000-0000-0000-000000001001"
PAYMENT_1 = "00000000-0000-0000-0000-000000002001"
NAMESPACE = "00000000-0000-0000-0000-0000000000f0"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _target(**overrides):
    base = _fixture("assembly.json")
    state = {key: base[key] for key in drafter._SCALAR_KEYS}
    state.update(
        schema=drafter.TARGET_STATE_SCHEMA,
        account_id_derivation=None,
        pristine_position=None,
        partition_targets=[
            {
                "source_account_id": ACCOUNT,
                "currency": "NGN",
                "account_id": TARGET_ACCOUNT,
                "expected_position_fingerprint": "fixture-pristine-position",
                "posting_watermark": "fixture-posting:0",
            }
        ],
    )
    state.update(overrides)
    return state


def _census_with_settlement(kind="payment", source_id=PAYMENT_1, active=True):
    payload = _fixture("census_one_eligible_v2.json")
    invoice = payload["invoices"][0]
    invoice["settlement_ids"] = [SETTLEMENT_1]
    payload["settlements"] = [
        {
            "kind": kind,
            "id": SETTLEMENT_1,
            "invoice_id": INVOICE_1,
            "source_id": source_id,
            "amount": "40.00",
            "ledger_entry_id": None,
            "consumption_ledger_entry_id": None,
            "active": active,
            "created_at": "2026-10-05T00:00:00+00:00",
        }
    ]
    return payload


def _draft(census=None, target=None):
    return drafter.draft_assembly(
        census if census is not None else _fixture("census_one_eligible_v2.json"),
        target if target is not None else _target(),
    )


def _codes(draft):
    return [b.code for b in draft.blockers]


def test_identity_mapping_is_complete_and_loads_in_the_adapter():
    draft = _draft(_census_with_settlement())
    assert draft.blockers == []
    assert draft.assembly["invoice_mappings"] == [
        {
            "source_invoice_id": INVOICE_1,
            "source_identity": {"kind": "invoice", "key": INVOICE_1},
            "source_invoice_key": INVOICE_1,
        }
    ]
    assert draft.assembly["discharge_mappings"] == [
        {
            "settlement_kind": "payment",
            "settlement_id": SETTLEMENT_1,
            "historical_reference": f"payment:{SETTLEMENT_1}",
            "source_id": PAYMENT_1,
        }
    ]
    (partition,) = draft.assembly["partitions"]
    assert partition["account_id"] == TARGET_ACCOUNT
    assert partition["precision"] == 2  # the census's declared precision
    assembly = adapter.load_assembly(draft.assembly)
    assert assembly.assembly_digest == draft.digest
    assert draft.digest == opening_rehearsal_assembly_digest(
        parse_opening_rehearsal_assembly(draft.assembly)
    )


def test_adapter_reports_no_unmapped_identity_for_the_draft():
    payload = _census_with_settlement()
    payload["invoices"][0]["canonical_discharge"] = "40.00"
    payload["invoices"][0]["residual"] = "60.00"
    artifact = adapter.rehearsal_census(
        payload, assembly=adapter.load_assembly(_draft(payload).assembly)
    )
    assert artifact.unmapped.accounts == ()
    assert artifact.unmapped.invoices == ()


def test_census_v3_documentary_fixture_also_drafts():
    draft = _draft(_fixture("census_v3_documentary.json"))
    assert draft.blockers == []
    # Only eligible invoices are mapped; the other census invoice is not.
    assert draft.counts["census_invoices"] == 2
    assert draft.counts["invoice_mappings"] == draft.counts["eligible_invoices"] == 1


def test_missing_target_account_is_a_blocker_never_a_default():
    draft = _draft(target=_target(partition_targets=[]))
    assert _codes(draft).count("target_account_unavailable") == 1
    assert draft.assembly["partitions"] == []
    assert "invoice_partition_unmapped" in _codes(draft)
    assert draft.assembly["invoice_mappings"] == []
    # The package still accepts the (empty) candidate; the adapter then
    # reports the identities as unmapped rather than the drafter inventing.
    artifact = adapter.rehearsal_census(
        _fixture("census_one_eligible_v2.json"),
        assembly=adapter.load_assembly(draft.assembly),
    )
    assert artifact.unmapped.accounts == (ACCOUNT,)


def test_derived_target_accounts_need_a_declared_namespace_and_pristine_position():
    only_namespace = _target(
        partition_targets=[],
        account_id_derivation={"method": "uuid5", "namespace": NAMESPACE},
    )
    draft = _draft(target=only_namespace)
    assert "target_position_unavailable" in _codes(draft)
    assert draft.assembly["partitions"] == []

    full = _target(
        partition_targets=[],
        account_id_derivation={"method": "uuid5", "namespace": NAMESPACE},
        pristine_position={
            "expected_position_fingerprint": "pristine-fp",
            "posting_watermark": "wm:0",
        },
    )
    draft = _draft(target=full)
    assert draft.blockers == []
    (partition,) = draft.assembly["partitions"]
    assert partition["account_id"] == str(uuid5(UUID(NAMESPACE), f"{ACCOUNT}:NGN"))
    assert partition["expected_position_fingerprint"] == "pristine-fp"


def test_target_account_collision_blocks_both_partitions():
    payload = _fixture("census_one_eligible_v2.json")
    other = "00000000-0000-0000-0000-00000000000b"
    for section in ("controls", "lanes"):
        row = copy.deepcopy(payload[section][0])
        row["account_id"] = other
        payload[section].append(row)
    targets = _target()["partition_targets"]
    second = dict(targets[0], source_account_id=other)
    draft = _draft(payload, _target(partition_targets=[*targets, second]))
    assert _codes(draft).count("target_account_collision") == 1
    assert draft.assembly["partitions"] == []


@pytest.mark.parametrize(
    "kind,source_id,expected",
    [
        ("opening_funding", PAYMENT_1, "unsupported_settlement_kind"),
        ("payment", None, "settlement_source_id_unavailable"),
    ],
)
def test_unmappable_settlements_are_blockers_not_guesses(kind, source_id, expected):
    draft = _draft(_census_with_settlement(kind=kind, source_id=source_id))
    assert expected in _codes(draft)
    assert draft.assembly["discharge_mappings"] == []


def test_inactive_settlements_are_not_mapped_or_blocked():
    draft = _draft(_census_with_settlement(active=False))
    assert draft.blockers == []
    assert draft.assembly["discharge_mappings"] == []


def test_incomplete_population_and_precision_mismatch_are_reported():
    payload = _fixture("census_one_eligible_v2.json")
    payload["capture"]["population_complete"] = False
    payload["invoices"][0]["precision"] = 3
    codes = _codes(_draft(payload))
    assert "census_population_incomplete" in codes
    assert "invoice_precision_mismatch" in codes


def test_eligible_invoice_missing_from_census_is_a_blocker():
    payload = _fixture("census_one_eligible_v2.json")
    payload["eligible_invoice_ids"].append("00000000-0000-0000-0000-0000000000ff")
    assert "eligible_invoice_not_in_census" in _codes(_draft(payload))


def test_undeclared_currency_precision_is_not_defaulted():
    payload = _fixture("census_one_eligible_v2.json")
    payload["capture"]["declared_source"]["currency_precisions"] = []
    draft = _draft(payload)
    assert "currency_precision_undeclared" in _codes(draft)
    assert draft.assembly["partitions"] == []


@pytest.mark.parametrize("missing", list(drafter._SCALAR_KEYS))
def test_every_declared_scalar_is_required(missing):
    target = _target()
    del target[missing]
    with pytest.raises(drafter.DraftError):
        _draft(target=target)


@pytest.mark.parametrize(
    "extra", ["amount_override", "approved_by", "residual", "cash_lots"]
)
def test_unknown_and_financial_keys_are_refused(extra):
    target = _target()
    target[extra] = "1.00"
    with pytest.raises(drafter.DraftError):
        _draft(target=target)
    target = _target()
    target["partition_targets"][0][extra] = "1.00"
    with pytest.raises(drafter.DraftError):
        _draft(target=target)


def test_output_carries_no_census_money_value():
    census = _census_with_settlement()
    census["invoices"][0]["total"] = "123456.78"
    census["invoices"][0]["residual"] = "98765.43"
    draft = _draft(census)
    assembly_text, summary_text = drafter.render_outputs(draft)
    for needle in ("123456.78", "98765.43", "40.00"):
        assert needle not in assembly_text
        assert needle not in summary_text


def _run_cli(tmp_path, census, target, name):
    census_path = tmp_path / "census.json"
    target_path = tmp_path / "target.json"
    census_path.write_text(json.dumps(census), encoding="utf-8")
    target_path.write_text(json.dumps(target), encoding="utf-8")
    out = tmp_path / name
    out.mkdir()
    code = drafter.main(
        [
            "--census",
            str(census_path),
            "--target-state",
            str(target_path),
            "--out-dir",
            str(out),
        ]
    )
    return code, out


def test_two_runs_are_byte_identical(tmp_path, capsys):
    census = _census_with_settlement()
    code1, out1 = _run_cli(tmp_path, census, _target(), "run1")
    first = capsys.readouterr().out
    code2, out2 = _run_cli(tmp_path, census, _target(), "run2")
    second = capsys.readouterr().out
    assert code1 == code2 == 0
    assert first == second
    for name in ("assembly.json", "summary.csv"):
        a, b = (out1 / name).read_bytes(), (out2 / name).read_bytes()
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()
    reported = json.loads(first)
    assert (
        reported["assembly_json_sha256"]
        == hashlib.sha256((out1 / "assembly.json").read_bytes()).hexdigest()
    )
    # file mode is private and the summary is well-formed CSV
    assert (out1 / "assembly.json").stat().st_mode & 0o077 == 0
    rows = list(csv.reader(io.StringIO((out1 / "summary.csv").read_text())))
    assert rows[0] == ["section", "key", "value"]


def test_input_ordering_does_not_change_the_digest(tmp_path):
    census = _census_with_settlement()
    shuffled = copy.deepcopy(census)
    for section in ("controls", "lanes", "invoices", "settlements"):
        shuffled[section].reverse()
    assert _draft(census).digest == _draft(shuffled).digest


def test_blockers_give_exit_one_and_still_write_a_candidate(tmp_path, capsys):
    code, out = _run_cli(
        tmp_path,
        _fixture("census_one_eligible_v2.json"),
        _target(partition_targets=[]),
        "blocked",
    )
    assert code == 1
    assert json.loads(capsys.readouterr().out)["blockers"] >= 1
    assert (out / "assembly.json").exists()
    assert "target_account_unavailable" in (out / "summary.csv").read_text()


def test_refuses_to_overwrite_and_writes_nothing_on_malformed_input(tmp_path, capsys):
    code, out = _run_cli(tmp_path, _census_with_settlement(), _target(), "once")
    assert code == 0
    before = (out / "assembly.json").read_bytes()
    capsys.readouterr()
    again = drafter.main(
        [
            "--census",
            str(tmp_path / "census.json"),
            "--target-state",
            str(tmp_path / "target.json"),
            "--out-dir",
            str(out),
        ]
    )
    assert again == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "output_exists"
    assert (out / "assembly.json").read_bytes() == before

    bad = _target()
    del bad["scope"]
    code, out = _run_cli(tmp_path, _census_with_settlement(), bad, "bad")
    assert code == 2
    assert list(out.iterdir()) == []


def test_duplicate_json_keys_and_wrong_population_are_refused(tmp_path):
    census_path = tmp_path / "c.json"
    census_path.write_text('{"capture": 1, "capture": 2}')
    target_path = tmp_path / "t.json"
    target_path.write_text(json.dumps(_target()))
    out = tmp_path / "o"
    out.mkdir()
    args = ["--census", str(census_path), "--target-state", str(target_path)]
    assert drafter.main([*args, "--out-dir", str(out)]) == 2
    payload = _fixture("census_one_eligible_v2.json")
    payload["capture"]["declared_source"]["population_version"] = "other"
    with pytest.raises(drafter.DraftError):
        _draft(payload)


def test_drafter_module_purity_ast():
    tree = ast.parse((SCRIPTS / "billing_opening_assembly_drafter.py").read_text())
    imported = set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert imported.isdisjoint(
        {"sqlalchemy", "requests", "httpx", "socket", "subprocess", "urllib", "app"}
    )
    # It must not use any arithmetic or monetary value type at all.
    assert names.isdisjoint({"Decimal", "Money", "float", "round", "quantize"})
    assert names.isdisjoint({"datetime", "now", "utcnow", "random", "uuid4"})


def test_drafter_is_registered_as_transitional_with_a_deletion_trigger():
    extraction = tomllib.loads(
        (ROOT / "packages/dotmac-billing/EXTRACTION.toml").read_text(encoding="utf-8")
    )
    note = extraction["source_mode_note"]
    assert "scripts/billing_opening_assembly_drafter.py" in note
    assert "deleted once" in note
    inventory = (
        ROOT / "docs/inventories/billing-opening-rehearsal-report-extraction.md"
    ).read_text(encoding="utf-8")
    section = inventory.split("## Opening-rehearsal assembly drafter", 1)[1]
    assert "Deletion trigger" in section
    assert "identity mapping only" in section


def test_sub_serializer_output_drafts_and_runs_through_the_adapter(tmp_path, capsys):
    """Real Sub wire output (synthetic data) -> drafter -> adapter, twice."""
    wire_text = (FIXTURES / "census_sub_wire_v3.json").read_text(encoding="utf-8")
    census = json.loads(wire_text)
    source_account = census["invoices"][0]["account_id"]
    target = _target(
        partition_targets=[
            dict(_target()["partition_targets"][0], source_account_id=source_account)
        ]
    )
    (tmp_path / "wire.json").write_text(wire_text, encoding="utf-8")
    shas = []
    for name in ("a", "b"):
        code, out = _run_cli(tmp_path, census, target, name)
        capsys.readouterr()
        assert code == 0
        shas.append((out / "assembly.json").read_bytes())
    assert shas[0] == shas[1]
    assembly = adapter.load_assembly(json.loads(shas[0]))
    rendered = []
    for _ in range(2):
        artifact = adapter.rehearsal_census(census, assembly=assembly)
        assert artifact.unmapped.accounts == ()
        assert artifact.unmapped.invoices == ()
        rendered.append(adapter.render_opening_rehearsal_census(artifact))
    assert rendered[0] == rendered[1]
