"""ST-4c projection: carried variances, rebuild input, batch scope, reconciliation.

The projection never adjusts T and never plugs: itemised variances cite census
rows, at most one remainder per partition, lane and basis carries the rest, and
the validator's closure equations (E1-E4 and the count control) must find nothing.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from dotmac_billing.opening_admissibility import (
    A10_REBUILD_SCHEMA,
    AdmissibilityError,
    parse_a10_input,
    parse_a10_rebuild,
    parse_admissibility_rules,
    project_census,
    rebuild_reason,
    render_admissibility_decisions,
)
from dotmac_billing.opening_receivable_validation import (
    opening_manifest_variance_blockers,
)

from tests.unit.test_billing_opening_admissibility import rules_doc

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "opening_rehearsal"
spec = importlib.util.spec_from_file_location(
    "billing_opening_rehearsal_adapter_pl",
    ROOT / "scripts" / "billing_opening_rehearsal_adapter.py",
)
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
sys.modules["billing_opening_rehearsal_adapter_pl"] = adapter
spec.loader.exec_module(adapter)

ACCOUNT = "00000000-0000-0000-0000-00000000000a"
INV1 = "00000000-0000-0000-0000-000000000001"
INV2 = "00000000-0000-0000-0000-000000000002"
INV3 = "00000000-0000-0000-0000-000000000003"


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _rebuild_doc(net: str = "-100.00", klass="opening_exact", cause="match"):
    return {
        "schema": A10_REBUILD_SCHEMA,
        "accounts": [
            {
                "account_id": ACCOUNT,
                "currency": "NGN",
                "rebuild_net": net,
                "opening_class": klass,
                "post_cause": cause,
            }
        ],
    }


def _rules(**policies):
    codes = [
        {"code": code, "policy": policy} for code, policy in sorted(policies.items())
    ]
    return parse_admissibility_rules(
        rules_doc(
            code_policies=codes,
            v1_gate=None,
            originals={"require_for_open_documents": False},
        )
    )


def _census():
    """One transferred invoice, one carried-out open invoice, one closed invoice
    with a stored-balance difference, and an unattributed control row."""
    census = _fixture("census_one_eligible_v2.json")
    base = census["invoices"][0]

    def invoice(ident, number, status, total, residual, stored):
        row = copy.deepcopy(base)
        row.update(
            invoice_id=ident,
            invoice_number=number,
            status=status,
            total=total,
            residual=residual,
            stored_residual=stored,
            canonical_discharge=f"{Decimal(total) - Decimal(residual):.2f}",
        )
        return row

    census["invoices"] = [
        base,
        invoice(INV2, "FIXTURE-INV-2", "issued", "25.00", "25.00", "25.00"),
        invoice(INV3, "FIXTURE-INV-3", "paid", "50.00", "0.00", "7.00"),
    ]
    census["lanes"][0]["collectible_receivable"] = "125.00"
    census["exclusions"] = [{"source_id": INV3, "reason": "fully_closed"}]
    census["blocking_exceptions"] = [
        {
            "account_id": ACCOUNT,
            "currency": "NGN",
            "code": "invoice_provenance",
            "source_id": INV2,
            "detail": "x",
        },
        {
            "account_id": ACCOUNT,
            "currency": "NGN",
            "code": "residual_mismatch",
            "source_id": INV3,
            "detail": "x",
        },
    ]
    control = census["controls"][0]
    control.update(invoice_count=3, original_total="175.00", residual="125.00")
    census["controls"] = [
        control,
        {
            "account_id": None,
            "currency": "NGN",
            "invoice_count": 1,
            "eligible_count": 0,
            "excluded_count": 0,
            "exception_count": 0,
            "original_total": "9.00",
            "canonical_discharge": "0.00",
            "residual": "9.00",
            "eligible_residual": "0.00",
            "source_count": 0,
        },
    ]
    return census


def _project(census=None, rebuild=None, **policies):
    classes, rows = parse_a10_input(rebuild or _rebuild_doc())
    return project_census(
        census or _census(),
        rules=_rules(**policies),
        a10=classes,
        rebuild=rows,
        require_rebuild=True,
    )


CARRY = {
    "invoice_provenance": "carry_as_variance",
    "residual_mismatch": "carry_as_variance",
}


def _run(projection):
    obs, sidecar = adapter.load_sub_observation(projection.census)
    assembly = adapter.load_assembly(_fixture("assembly.json"))
    precisions = {r.currency: r.precision for r in obs.currency_precisions}
    return adapter.assemble_rehearsal_run(
        obs,
        assembly=assembly,
        sidecar=sidecar,
        ledger_for=adapter.census_variance_ledger(projection, assembly, precisions),
    )


# -- the A10 rebuild input ----------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["accounts"][0].update(rebuild_net="1.005"),  # inexact
        lambda d: d["accounts"].append(dict(d["accounts"][0])),  # duplicate account
        lambda d: d["accounts"][0].update(opening_class="Opening Exact"),  # unknown
        lambda d: d["accounts"][0].update(post_cause="whatever"),  # unknown cause
        lambda d: d["accounts"][0].update(currency="USD"),  # non-NGN
        lambda d: d["accounts"][0].pop("rebuild_net"),  # closed keys
        lambda d: d.update(schema="billing.opening.a10-account-classes.v1"),
    ],
)
def test_the_rebuild_parser_refuses(mutate) -> None:
    doc = _rebuild_doc()
    mutate(doc)
    with pytest.raises(AdmissibilityError):
        parse_a10_rebuild(doc)


def test_a_missing_rebuild_input_refuses_the_projection() -> None:
    classes, _rows = parse_a10_input(_rebuild_doc())
    with pytest.raises(AdmissibilityError) as refused:
        project_census(
            _census(),
            rules=_rules(**CARRY),
            a10=classes,
            rebuild=None,
            require_rebuild=True,
        )
    assert refused.value.code == "a10_rebuild_required"
    projection = project_census(
        _census(), rules=_rules(**CARRY), a10=classes, rebuild=None
    )
    assembly = adapter.load_assembly(_fixture("assembly.json"))
    with pytest.raises(adapter.AdapterInputError, match="a10_rebuild_required"):
        adapter.census_variance_ledger(projection, assembly, {})  # the builder too


def test_the_rebuild_reason_is_the_first_that_applies() -> None:
    def rows(klass, cause):
        return parse_a10_rebuild(_rebuild_doc(klass=klass, cause=cause))

    kwargs = {"exact_class": "opening_exact", "matching_cause": "match"}
    assert (
        rebuild_reason(rows("opening_gap_other", "known:pre_s1_payment"), **kwargs)
        == "rebuild_opening_gap_other"
    )
    assert (
        rebuild_reason(rows("opening_exact", "known:pre_s1_payment"), **kwargs)
        == "rebuild_pre_s1_payment"
    )
    assert (
        rebuild_reason(rows("opening_exact", "match"), **kwargs)
        == "unexplained_difference"
    )


# -- treatments, remainders and closure ---------------------------------------------


def test_carried_findings_become_itemised_variances_and_every_equation_closes() -> None:
    projection = _project(**CARRY)
    (decision,) = projection.decisions
    assert decision.admitted and decision.route == "import"
    run = _run(projection)
    manifest = run.manifest
    assert opening_manifest_variance_blockers(manifest) == ()
    (measure,) = manifest.measures
    assert (measure.document_residual_minor, measure.stored_balance_minor) == (
        12500,
        13200,
    )
    assert measure.rebuild_minor == 10000 and measure.rebuild_gap_reason is None
    by = {(v.basis, v.reason_code): v for v in manifest.variances}
    # the open invoice that was not transferred is carried in the ledger lane
    item = by[("ledger_lane", "item_not_transferred:invoice_provenance")]
    assert (item.amount_minor, item.source.kind, item.lane) == (
        2500,
        "invoice",
        "receivable",
    )
    assert item.source.key == INV2
    # the closed invoice's stored-balance difference cites the invoice
    stored = by[("stored_balance", "stored_balance_difference")]
    assert (stored.amount_minor, stored.source.key) == (700, INV3)
    # at most one remainder per partition, lane and basis; T is never adjusted
    remainders = [
        v for v in manifest.variances if v.reason_code == "unexplained_difference"
    ]
    assert {(v.basis, v.amount_minor) for v in remainders} == {
        ("document_residual", 2500),
        ("stored_balance", 2500),
    }
    assert len({(v.basis, v.lane) for v in remainders}) == len(remainders)
    assert sum(r.residual_minor for r in manifest.receivables) == 10000
    # carried and excluded rows are counted in their own buckets
    assert (measure.transferred_rows, measure.carried_rows, measure.excluded_rows) == (
        1,
        2,
        0,
    )
    # batch scope: outside every account lane, one control row, no GL fact
    (batch,) = manifest.batch_controls
    assert (batch.currency, batch.source_minor) == ("NGN", 900)
    (unattributed,) = (v for v in manifest.variances if v.level == "batch")
    assert unattributed.reason_code == "unattributed_source"
    assert unattributed.source.key == "NGN:receivable:ledger_lane"


def test_an_uncarried_code_still_routes_the_partition_out() -> None:
    projection = _project(residual_mismatch="carry_as_variance")
    (decision,) = projection.decisions
    assert not decision.admitted and decision.reason == "census_blocking_exception"
    assert decision.route == "governed_correction"


def test_the_rebuild_difference_is_one_attributed_memo_variance() -> None:
    drift = _project(
        rebuild=_rebuild_doc("-90.00", "opening_gap_other", "match"), **CARRY
    )
    run = _run(drift)
    assert opening_manifest_variance_blockers(run.manifest) == ()
    (rebuild,) = (v for v in run.manifest.variances if v.basis == "rebuild")
    # rebuild_minor = -(-90.00) = 9000; T_net = 10000; the difference is -1000
    assert (rebuild.reason_code, rebuild.amount_minor) == (
        "rebuild_opening_gap_other",
        -1000,
    )
    assert rebuild.source.kind == "rebuild_account" and rebuild.lane == "receivable"
    # the exact-and-matching class attributes an unexplained remainder instead
    plain = _run(_project(rebuild=_rebuild_doc("-90.00"), **CARRY))
    (remainder,) = (v for v in plain.manifest.variances if v.basis == "rebuild")
    assert remainder.reason_code == "unexplained_difference"
    assert remainder.source.kind == "partition_remainder"


def test_an_account_without_a_rebuild_row_opens_a_typed_gap_not_a_block() -> None:
    other = _rebuild_doc()
    other["accounts"][0]["account_id"] = "00000000-0000-0000-0000-0000000000ff"
    run = _run(_project(rebuild=other, **CARRY))
    (measure,) = run.manifest.measures
    assert measure.rebuild_minor is None and measure.rebuild_gap_reason == "a10_missing"
    assert opening_manifest_variance_blockers(run.manifest) == ()
    assert not [v for v in run.manifest.variances if v.basis == "rebuild"]


def test_the_projection_is_deterministic_and_reconciles_the_census() -> None:
    first, second = _project(**CARRY), _project(**CARRY)
    assert render_admissibility_decisions(first) == render_admissibility_decisions(
        second
    )
    reconciliation = first.reconciliation
    assert reconciliation["invoices"]["closed"] is True  # type: ignore[index]
    assert reconciliation["batch"] == {"NGN": "9.00"}
    lanes = reconciliation["lanes"]["NGN"]  # type: ignore[index]
    assert all(row["closed"] for row in lanes.values())
    assert "reconciliation" in first.digests and "a10_rebuild" in first.digests
    document = json.loads(render_admissibility_decisions(first))
    assert document["reconciliation"] == reconciliation
    assert document["carried"]["items"] == 2


# -- the Phase B kit converter (read-only tooling) -------------------------------------


def _converter():
    path = ROOT / "scripts" / "billing_opening_a10_rebuild_converter.py"
    spec = importlib.util.spec_from_file_location("billing_a10_converter", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["billing_a10_converter"] = module
    spec.loader.exec_module(module)
    return module


CSV_HEADER = (
    "account_id,status,mode,opening_S1,rebuild,sub_current,diff,opening_gap,"
    "opening_class,post_drift,post_cause\n"
)


def test_the_converter_writes_the_closed_input_and_refuses_inexact_amounts(tmp_path):
    converter = _converter()
    source = tmp_path / "rebuild-classified.csv"
    source.write_text(
        CSV_HEADER
        + f"{ACCOUNT},a,b,0,-100.50,0,0,0,opening_exact,0,match\n"
        + "00000000-0000-0000-0000-0000000000b0,a,b,0,12.00,0,0,0,"
        "opening_gap_other,0,known:pre_s1_payment\n",
        encoding="utf-8",
    )
    out = tmp_path / "rebuild.json"
    assert converter.main(["--classified", str(source), "--out", str(out)]) == 0
    document = json.loads(out.read_text())
    rows = parse_a10_rebuild(document)
    assert [str(r.rebuild_net) for r in rows] == ["-100.50", "12.00"]
    # never overwritten, and byte-identical on a second conversion
    assert converter.main(["--classified", str(source), "--out", str(out)]) == 2
    second = tmp_path / "again.json"
    converter.main(["--classified", str(source), "--out", str(second)])
    assert second.read_bytes() == out.read_bytes()
    inexact = tmp_path / "inexact.csv"
    inexact.write_text(
        CSV_HEADER + f"{ACCOUNT},a,b,0,1.005,0,0,0,opening_exact,0,match\n",
        encoding="utf-8",
    )
    assert (
        converter.main(
            ["--classified", str(inexact), "--out", str(tmp_path / "x.json")]
        )
        == 2
    )
    assert not (tmp_path / "x.json").exists()
