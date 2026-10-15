"""Package-owned assembly identity and census rendering (no script involved)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from dotmac_billing.opening_contracts import (
    OpeningEvidenceError,
    OpeningSourceIdentityV1,
)
from dotmac_billing.opening_rehearsal import (
    opening_rehearsal_assembly_digest,
    opening_rehearsal_assembly_wire,
    render_opening_rehearsal_census,
    render_opening_rehearsal_refusal,
)
from dotmac_billing.opening_rehearsal_contracts import (
    OpeningRehearsalAssemblyPartitionV1,
    OpeningRehearsalAssemblyV1,
    OpeningRehearsalBindingV1,
    OpeningRehearsalCensusBlockerV1,
    OpeningRehearsalCensusCountV1,
    OpeningRehearsalCensusDeltaV1,
    OpeningRehearsalCensusPartitionV1,
    OpeningRehearsalCensusProvenanceV1,
    OpeningRehearsalCensusRollupV1,
    OpeningRehearsalCensusSidecarV1,
    OpeningRehearsalCensusUnmappedV1,
    OpeningRehearsalCensusV1,
    OpeningRehearsalDischargeMappingV1,
    OpeningRehearsalInvoiceMappingV1,
)
from dotmac_kernel.cache import PlatformScope, TenantScope

_INSTANT = datetime(2026, 10, 13, tzinfo=UTC)

#: Pinned vector: this exact synthetic assembly must always digest to this.
#: A change here is a change of identity for every signed assembly; do not
#: update it to make a test pass.
PINNED_DIGEST = "0201014fd4355ea41f9a6310f77b1e516cbd883021c19bfa442fd5be47925e12"


def _uuid(n: int) -> UUID:
    return UUID(int=n)


def _assembly() -> OpeningRehearsalAssemblyV1:
    return OpeningRehearsalAssemblyV1(
        scope=TenantScope(_uuid(100)),
        source_system="sub",
        recorded_cutoff=_INSTANT,
        source_authority_ref="test:authority",
        account_mapping_version="test:account:1",
        cash_mapping_version="test:cash:1",
        receivable_mapping_version="test:receivable:1",
        tax_mapping_version="test:tax:1",
        source_resolver_version="test:resolver:1",
        collection_authority="test:collection",
        collection_mapping_version="test:collection-mapping:1",
        migration_authority_ref="test:migration",
        occurred_at=_INSTANT,
        starter_head_sha="0" * 40,
        sub_exit_code=1,
        binding=OpeningRehearsalBindingV1(_uuid(200), "test:artifact", "test:policy"),
        partitions=(
            OpeningRehearsalAssemblyPartitionV1(
                _uuid(10),
                "source-account:test",
                _uuid(1001),
                "NGN",
                2,
                "opening-rehearsal:test:ngn",
                "pristine-position",
                "posting:0",
            ),
        ),
        invoice_mappings=(
            OpeningRehearsalInvoiceMappingV1(
                _uuid(1), OpeningSourceIdentityV1("invoice", "invoice:1"), "invoice:1"
            ),
        ),
        discharge_mappings=(
            OpeningRehearsalDischargeMappingV1("payment", _uuid(5), "ref:5", _uuid(6)),
        ),
        accepted_manual_holds=("hold:a",),
    )


def test_assembly_digest_pinned_vector_and_determinism() -> None:
    first = opening_rehearsal_assembly_digest(_assembly())
    assert first == opening_rehearsal_assembly_digest(_assembly())
    assert len(first) == 64
    assert first == PINNED_DIGEST


def test_assembly_wire_is_stable_json_and_digest_is_sha256_of_it() -> None:
    import hashlib

    wire = opening_rehearsal_assembly_wire(_assembly())
    encoded = json.dumps(wire, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == opening_rehearsal_assembly_digest(
        _assembly()
    )
    assert wire["scope"] == {"kind": "tenant", "tenant_id": str(_uuid(100))}
    assert wire["recorded_cutoff"] == "2026-10-13T00:00:00+00:00"


def test_platform_scope_has_its_own_identity() -> None:
    platform = replace(_assembly(), scope=PlatformScope())
    assert opening_rehearsal_assembly_wire(platform)["scope"] == {"kind": "platform"}
    assert opening_rehearsal_assembly_digest(platform) != PINNED_DIGEST


@pytest.mark.parametrize(
    "change",
    [
        {"source_system": "other"},
        {"sub_exit_code": 2},
        {"starter_head_sha": "1" * 40},
        {"accepted_manual_holds": ()},
        {"occurred_at": datetime(2026, 10, 14, tzinfo=UTC)},
    ],
)
def test_every_declared_field_participates_in_identity(change: dict) -> None:
    assert opening_rehearsal_assembly_digest(replace(_assembly(), **change)) != (
        PINNED_DIGEST
    )


def test_partition_order_is_significant() -> None:
    base = _assembly()
    second = replace(base.partitions[0], source_account_key="source-account:b")
    one = replace(base, partitions=(base.partitions[0], second))
    two = replace(base, partitions=(second, base.partitions[0]))
    assert opening_rehearsal_assembly_digest(one) != opening_rehearsal_assembly_digest(
        two
    )


def test_closed_contract_rejects_malformed_values() -> None:
    base = _assembly()
    with pytest.raises(TypeError):
        replace(base, unexpected_field=1)  # type: ignore[call-arg]
    for change in (
        {"partitions": list(base.partitions)},  # not an immutable tuple
        {"recorded_cutoff": datetime(2026, 10, 13)},  # naive instant
        {"source_system": " "},  # empty field
        {"sub_exit_code": "1"},  # wrong type
        {"scope": object()},  # not a Scope
        {"accepted_manual_holds": ["hold"]},
    ):
        with pytest.raises(OpeningEvidenceError):
            replace(base, **change)
    with pytest.raises(OpeningEvidenceError):
        opening_rehearsal_assembly_digest(object())  # type: ignore[arg-type]


def _census(blocked: bool = True) -> OpeningRehearsalCensusV1:
    blockers = (
        (OpeningRehearsalCensusBlockerV1("verifier_unavailable", 2, ("a", "b")),)
        if blocked
        else ()
    )
    return OpeningRehearsalCensusV1(
        schema="sub-starter-opening-rehearsal-census/v1",
        provenance=OpeningRehearsalCensusProvenanceV1(
            "sub-sha",
            "head-sha",
            "pop-v2",
            "fp",
            "snap",
            ("h1",),
            "2026-10-13T00:00:00+00:00",
            1,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "d" * 64,
        ),
        partitions=(
            OpeningRehearsalCensusPartitionV1(
                str(_uuid(10)),
                "source-account:test",
                "NGN",
                2,
                False,
                0,
                5,
                blockers,
                (OpeningRehearsalCensusDeltaV1("cash", None, 0, None),),
            ),
        ),
        report_blockers=blockers,
        adapter_blockers=(),
        unmapped=OpeningRehearsalCensusUnmappedV1((), ("inv",), ()),
        v3_documentary_sidecar=OpeningRehearsalCensusSidecarV1(
            1,
            (OpeningRehearsalCensusCountV1("r1", 1),),
            (),
            (str(_uuid(1)),),
        ),
        dropped_source_fields=("lines",),
        rollup=OpeningRehearsalCensusRollupV1(
            1, 0, False, (OpeningRehearsalCensusCountV1("verifier_unavailable", 1),), ()
        ),
    )


def test_census_rendering_is_canonical_and_deterministic() -> None:
    text = render_opening_rehearsal_census(_census())
    assert text == render_opening_rehearsal_census(_census())
    assert text.endswith("}\n") and not text.endswith("\n\n")
    document = json.loads(text)
    assert text == json.dumps(document, sort_keys=True, indent=2) + "\n"
    assert document["rollup"]["unmapped_counts"] == {
        "accounts": 0,
        "invoices": 1,
        "settlements": 0,
    }
    assert document["v3_documentary_sidecar"]["by_requirement"] == {"r1": 1}
    assert document["partitions"][0]["deltas"][0]["source_minor"] is None


def test_census_blocked_property_and_closed_rejection() -> None:
    assert _census().blocked is True
    clean = replace(
        _census(False),
        partitions=(),
        unmapped=OpeningRehearsalCensusUnmappedV1((), (), ()),
    )
    assert clean.blocked is False
    with pytest.raises(OpeningEvidenceError):
        replace(_census(), schema="other")  # type: ignore[arg-type]
    with pytest.raises(OpeningEvidenceError):
        replace(_census(), partitions=[])  # type: ignore[arg-type]
    with pytest.raises(OpeningEvidenceError):
        render_opening_rehearsal_census(object())  # type: ignore[arg-type]


def test_refusal_line_is_fixed_and_value_free() -> None:
    assert render_opening_rehearsal_refusal("malformed_input") == (
        '{"complete": false, "error": "malformed_input"}'
    )
    for bad in ("", " ", None):
        with pytest.raises(OpeningEvidenceError):
            render_opening_rehearsal_refusal(bad)  # type: ignore[arg-type]
