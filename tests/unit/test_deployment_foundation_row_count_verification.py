"""Pure arithmetic controls for row counts; no deployment execution is claimed."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.row_count_verification import (
    ROW_COUNT_EXPECTATION_INCOMPLETE,
    ROW_COUNT_OBSERVATION_INCOMPLETE,
    ROW_COUNT_TABLES_DUPLICATE,
    ROW_COUNT_TABLES_EMPTY,
    ROW_COUNT_TOLERANCE_INVALID,
    ROW_COUNT_VALUE_INVALID,
    RowCountToleranceV1,
    compare_row_counts,
)
from dotmac_deployment_foundation.spec import (
    UNPERFORMABLE_VERIFICATION,
    ProductDeploymentSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = REPO_ROOT / "deploy" / "product.toml"


def test_comparison_reports_named_counts_within_tolerance() -> None:
    result = compare_row_counts(
        tables=("public.party", "mod_agreements.contract"),
        expected={"public.party": 100, "mod_agreements.contract": 40},
        observed={"public.party": 102, "mod_agreements.contract": 41},
        tolerance=RowCountToleranceV1(absolute=2),
    )
    assert result.within_tolerance
    assert result.breaches == ()


def test_comparison_exposes_breach_without_claiming_a_deployment_verdict() -> None:
    result = compare_row_counts(
        tables=("public.party",),
        expected={"public.party": 100},
        observed={"public.party": 103},
        tolerance=RowCountToleranceV1(absolute=2),
    )
    assert not result.within_tolerance
    assert result.breaches[0].table == "public.party"


@pytest.mark.parametrize(
    ("absolute", "percent"),
    [
        ("x", None),
        (1.5, None),
        (True, None),
        (-1, None),
        (None, "x"),
        (None, 1.5),
        (None, True),
        (None, -1),
        (None, 101),
    ],
)
def test_invalid_tolerance_values_refuse_with_one_stable_code(
    absolute, percent
) -> None:
    with pytest.raises(SpecError) as caught:
        RowCountToleranceV1(absolute=absolute, percent=percent)
    assert caught.value.code == ROW_COUNT_TOLERANCE_INVALID


@pytest.mark.parametrize(
    ("tables", "expected", "observed", "code"),
    [
        ((), {}, {}, ROW_COUNT_TABLES_EMPTY),
        (None, {"public.party": 1}, {"public.party": 1}, ROW_COUNT_VALUE_INVALID),
        (
            "public.party",
            {"public.party": 1},
            {"public.party": 1},
            ROW_COUNT_VALUE_INVALID,
        ),
        (object(), {"public.party": 1}, {"public.party": 1}, ROW_COUNT_VALUE_INVALID),
        (
            ("public.party", "public.party"),
            {"public.party": 1},
            {"public.party": 1},
            ROW_COUNT_TABLES_DUPLICATE,
        ),
        (("public.party",), {}, {"public.party": 1}, ROW_COUNT_EXPECTATION_INCOMPLETE),
        (("public.party",), {"public.party": 1}, {}, ROW_COUNT_OBSERVATION_INCOMPLETE),
        (("public.party",), [], {"public.party": 1}, ROW_COUNT_VALUE_INVALID),
        (("public.party",), {"public.party": 1}, [], ROW_COUNT_VALUE_INVALID),
        ((" ",), {"public.party": 1}, {"public.party": 1}, ROW_COUNT_VALUE_INVALID),
        ((1,), {"public.party": 1}, {"public.party": 1}, ROW_COUNT_VALUE_INVALID),
        (
            ("public.party",),
            {"public.party": True},
            {"public.party": 1},
            ROW_COUNT_VALUE_INVALID,
        ),
        (
            ("public.party",),
            {"public.party": 1},
            {"public.party": True},
            ROW_COUNT_VALUE_INVALID,
        ),
        (
            ("public.party",),
            {"public.party": 1},
            {"public.party": 1.5},
            ROW_COUNT_VALUE_INVALID,
        ),
        (
            ("public.party",),
            {"public.party": 1},
            {"public.party": -1},
            ROW_COUNT_VALUE_INVALID,
        ),
    ],
)
def test_invalid_inputs_refuse_with_a_distinguishable_code(
    tables, expected, observed, code
) -> None:
    with pytest.raises(SpecError) as caught:
        compare_row_counts(
            tables=tables,
            expected=expected,
            observed=observed,
            tolerance=RowCountToleranceV1(absolute=0),
        )
    assert caught.value.code == code


def test_arbitrary_tolerance_object_is_refused_before_comparison() -> None:
    class _ToleranceImpostor:
        def allowed_delta(self, expected: int) -> int:
            return expected

    with pytest.raises(SpecError) as caught:
        compare_row_counts(
            tables=("public.party",),
            expected={"public.party": 1},
            observed={"public.party": 1},
            tolerance=_ToleranceImpostor(),
        )
    assert caught.value.code == ROW_COUNT_TOLERANCE_INVALID


def test_descriptor_still_refuses_an_internal_row_counts_declaration() -> None:
    descriptor = DESCRIPTOR.read_text(encoding="utf-8").replace(
        'verify = [\n  "schema",',
        'verify = [\n  "schema",\n  "row_counts",',
        1,
    )
    with pytest.raises(SpecError) as caught:
        ProductDeploymentSpec.loads(descriptor, source="row-count-pure-contract")
    assert caught.value.code == UNPERFORMABLE_VERIFICATION
