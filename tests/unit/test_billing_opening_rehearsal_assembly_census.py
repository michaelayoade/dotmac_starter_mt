"""Package-owned assembly identity and census rendering (no script involved)."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from dotmac_billing.opening_contracts import (
    OpeningEvidenceError,
    OpeningSourceIdentityV1,
)
from dotmac_billing.opening_rehearsal import (
    opening_rehearsal_assembly_digest,
    opening_rehearsal_assembly_wire,
    parse_opening_rehearsal_assembly,
    parse_opening_rehearsal_json,
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

#: Pinned vector. The literal canonical bytes (domain-separated, sorted keys,
#: compact) sit next to the digest so a reviewer can see exactly what is hashed.
#: A change here is a change of identity for every signed assembly; do not
#: update it to make a test pass.
CANONICAL_BYTES = '{"domain":"billing.opening.rehearsal.assembly.v1","value":{"accepted_manual_holds":["hold:a"],"account_mapping_version":"test:account:1","binding":{"artifact_identity":"test:artifact","policy_identity":"test:policy","target_installation_id":"00000000-0000-0000-0000-0000000000c8"},"cash_mapping_version":"test:cash:1","collection_authority":"test:collection","collection_mapping_version":"test:collection-mapping:1","discharge_mappings":[{"historical_reference":"ref:5","settlement_id":"00000000-0000-0000-0000-000000000005","settlement_kind":"payment","source_id":"00000000-0000-0000-0000-000000000006"}],"invoice_mappings":[{"source_identity":{"key":"invoice:1","kind":"invoice"},"source_invoice_id":"00000000-0000-0000-0000-000000000001","source_invoice_key":"invoice:1"}],"migration_authority_ref":"test:migration","occurred_at":"2026-10-13T00:00:00+00:00","partitions":[{"account_id":"00000000-0000-0000-0000-0000000003e9","currency":"NGN","expected_position_fingerprint":"pristine-position","idempotency_key":"opening-rehearsal:test:ngn","posting_watermark":"posting:0","precision":2,"source_account_id":"00000000-0000-0000-0000-00000000000a","source_account_key":"source-account:test"}],"receivable_mapping_version":"test:receivable:1","recorded_cutoff":"2026-10-13T00:00:00+00:00","schema":"billing.opening.rehearsal.assembly.v1","scope":{"kind":"tenant","tenant_id":"00000000-0000-0000-0000-000000000064"},"source_authority_ref":"test:authority","source_resolver_version":"test:resolver:1","source_system":"sub","starter_head_sha":"0000000000000000000000000000000000000000","sub_exit_code":1,"tax_mapping_version":"test:tax:1"}}'  # noqa: E501
PINNED_DIGEST = "4e9d4b1cc907dd02ec6459c98f9b6b47e16d37d8d5bdf30737f86aadd961b802"


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


def _wire() -> dict:
    return json.loads(json.dumps(opening_rehearsal_assembly_wire(_assembly())))


def _digest_of(assembly: OpeningRehearsalAssemblyV1) -> str:
    return opening_rehearsal_assembly_digest(assembly)


# ── identity ────────────────────────────────────────────────────────────────


def test_pinned_vector_literal_bytes_and_determinism() -> None:
    assert _digest_of(_assembly()) == _digest_of(_assembly()) == PINNED_DIGEST
    assert hashlib.sha256(CANONICAL_BYTES.encode()).hexdigest() == PINNED_DIGEST
    wire = opening_rehearsal_assembly_wire(_assembly())
    rebuilt = json.dumps(
        {"domain": "billing.opening.rehearsal.assembly.v1", "value": wire},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert rebuilt == CANONICAL_BYTES
    assert wire["schema"] == "billing.opening.rehearsal.assembly.v1"
    assert wire["recorded_cutoff"] == "2026-10-13T00:00:00+00:00"


def test_digest_is_domain_separated() -> None:
    wire = opening_rehearsal_assembly_wire(_assembly())
    bare = hashlib.sha256(
        json.dumps(wire, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert bare != PINNED_DIGEST


def _each_field_changed() -> list[tuple[str, OpeningRehearsalAssemblyV1]]:
    base = _assembly()
    part = base.partitions[0]
    inv = base.invoice_mappings[0]
    dis = base.discharge_mappings[0]
    other = datetime(2026, 10, 14, tzinfo=UTC)
    top = {
        "scope": TenantScope(_uuid(101)),
        "source_system": "other",
        "recorded_cutoff": other,
        "source_authority_ref": "x",
        "account_mapping_version": "x",
        "cash_mapping_version": "x",
        "receivable_mapping_version": "x",
        "tax_mapping_version": "x",
        "source_resolver_version": "x",
        "collection_authority": "x",
        "collection_mapping_version": "x",
        "migration_authority_ref": "x",
        "occurred_at": other,
        "starter_head_sha": "1" * 40,
        "sub_exit_code": 2,
        "binding": replace(base.binding, artifact_identity="x"),
        "partitions": (),
        "invoice_mappings": (),
        "discharge_mappings": (),
        "accepted_manual_holds": (),
    }
    assert set(top) == {f.name for f in fields(base)} - {"schema"}
    # `binding` is covered field by field below.
    cases = [
        (f"top.{k}", replace(base, **{k: v})) for k, v in top.items() if k != "binding"
    ]
    cases.append(("scope.platform", replace(base, scope=PlatformScope())))
    for f in fields(base.binding):
        value = _uuid(201) if f.name.endswith("_id") else "x"
        cases.append(
            (
                f"binding.{f.name}",
                replace(base, binding=replace(base.binding, **{f.name: value})),
            )
        )

    def nested(group: str, row: object, attr: str, value: object):
        mapped = tuple(
            replace(row, **{attr: value})
            for _ in (0,)  # type: ignore[type-var]
        )
        return (f"{group}.{attr}", replace(base, **{group: mapped}))

    for f in fields(part):
        value = (
            _uuid(2000)
            if f.name.endswith("_id")
            else (3 if f.name == "precision" else "x")
        )
        cases.append(nested("partitions", part, f.name, value))
    cases.append(nested("invoice_mappings", inv, "source_invoice_id", _uuid(2)))
    cases.append(nested("invoice_mappings", inv, "source_invoice_key", "x"))
    cases.append(
        nested(
            "invoice_mappings",
            inv,
            "source_identity",
            OpeningSourceIdentityV1("other", "invoice:1"),
        )
    )
    cases.append(
        nested(
            "invoice_mappings",
            inv,
            "source_identity",
            OpeningSourceIdentityV1("invoice", "other"),
        )
    )
    for f in fields(dis):
        value = _uuid(3000) if f.name.endswith("_id") else "x"
        cases.append(nested("discharge_mappings", dis, f.name, value))
    return cases


def test_changing_every_field_including_nested_changes_the_digest() -> None:
    cases = _each_field_changed()
    digests = {name: _digest_of(a) for name, a in cases}
    assert all(d != PINNED_DIGEST for d in digests.values()), digests
    assert len(set(digests.values())) == len(digests)  # and no two collide
    assert len(cases) >= 35


def test_wire_keys_exactly_equal_the_dataclass_fields_at_every_level() -> None:
    base = _assembly()
    wire = opening_rehearsal_assembly_wire(base)

    def names(obj: object) -> set[str]:
        return {f.name for f in fields(obj)}  # type: ignore[arg-type]

    assert set(wire) == names(base)
    assert set(wire["binding"]) == names(base.binding)
    assert set(wire["partitions"][0]) == names(base.partitions[0])
    assert set(wire["invoice_mappings"][0]) == names(base.invoice_mappings[0])
    assert set(wire["invoice_mappings"][0]["source_identity"]) == names(
        base.invoice_mappings[0].source_identity
    )
    assert set(wire["discharge_mappings"][0]) == names(base.discharge_mappings[0])
    assert set(wire["scope"]) == {"kind", "tenant_id"}
    assert set(
        opening_rehearsal_assembly_wire(replace(base, scope=PlatformScope()))["scope"]
    ) == {"kind"}


def test_partition_order_is_significant() -> None:
    base = _assembly()
    second = replace(base.partitions[0], source_account_key="source-account:b")
    one = replace(base, partitions=(base.partitions[0], second))
    two = replace(base, partitions=(second, base.partitions[0]))
    assert _digest_of(one) != _digest_of(two)


# ── strict parsing ──────────────────────────────────────────────────────────


def test_parse_is_the_inverse_of_the_wire() -> None:
    for assembly in (_assembly(), replace(_assembly(), scope=PlatformScope())):
        wire = opening_rehearsal_assembly_wire(assembly)
        assert parse_opening_rehearsal_assembly(wire) == assembly
        assert _digest_of(parse_opening_rehearsal_assembly(wire)) == _digest_of(
            assembly
        )


def _inject(path: tuple, key: str) -> dict:
    wire = _wire()
    node = wire
    for step in path:
        node = node[step]
    node[key] = "x"
    return wire


@pytest.mark.parametrize(
    "path,key",
    [
        ((), "approved_by"),
        ((), "amount_override"),
        (("scope",), "approved_by"),
        (("binding",), "approved_by"),
        (("partitions", 0), "cash_lots"),
        (("invoice_mappings", 0), "amount_override"),
        (("invoice_mappings", 0, "source_identity"), "extra"),
        (("discharge_mappings", 0), "amount_override"),
    ],
)
def test_unknown_keys_refuse_at_every_level(path: tuple, key: str) -> None:
    with pytest.raises(OpeningEvidenceError):
        parse_opening_rehearsal_assembly(_inject(path, key))
    platform = opening_rehearsal_assembly_wire(
        replace(_assembly(), scope=PlatformScope())
    )
    platform["scope"]["tenant_id"] = str(_uuid(1))  # type: ignore[index]
    with pytest.raises(OpeningEvidenceError):
        parse_opening_rehearsal_assembly(platform)


def test_every_declared_key_is_required_including_manual_holds() -> None:
    wire = _wire()
    for key in list(wire):
        broken = {k: v for k, v in wire.items() if k != key}
        with pytest.raises(OpeningEvidenceError):
            parse_opening_rehearsal_assembly(broken)
    for key in list(wire["partitions"][0]):
        broken = _wire()
        del broken["partitions"][0][key]
        with pytest.raises(OpeningEvidenceError):
            parse_opening_rehearsal_assembly(broken)


def test_wrong_schema_and_loose_types_refuse() -> None:
    for mutate in (
        lambda w: w.update(schema="billing.opening.rehearsal.assembly.v2"),
        lambda w: w.update(sub_exit_code=True),
        lambda w: w.update(sub_exit_code="1"),
        lambda w: w.update(accepted_manual_holds="hold:a"),
        lambda w: w.update(accepted_manual_holds=[1]),
        lambda w: w["binding"].update(target_installation_id=str(_uuid(200)).upper()),
        lambda w: w["binding"].update(target_installation_id="not-a-uuid"),
        lambda w: w["scope"].update(kind="other"),
        lambda w: w.update(source_system=""),
    ):
        wire = _wire()
        mutate(wire)
        with pytest.raises(OpeningEvidenceError):
            parse_opening_rehearsal_assembly(wire)
    with pytest.raises(OpeningEvidenceError):
        parse_opening_rehearsal_assembly([])  # type: ignore[arg-type]


def test_duplicate_json_keys_and_non_finite_constants_refuse() -> None:
    with pytest.raises(OpeningEvidenceError):
        parse_opening_rehearsal_json('{"a": 1, "a": 2}')
    with pytest.raises(OpeningEvidenceError):
        parse_opening_rehearsal_json('{"a": {"b": 1, "b": 1}}')
    with pytest.raises(OpeningEvidenceError):
        parse_opening_rehearsal_json('{"a": NaN}')
    with pytest.raises(OpeningEvidenceError):
        parse_opening_rehearsal_json("{")
    assert parse_opening_rehearsal_json('{"a": [1, 2]}') == {"a": [1, 2]}


# ── instants ────────────────────────────────────────────────────────────────


def test_instants_must_be_utc_and_z_equals_plus_zero() -> None:
    plus = _wire()
    zulu = _wire()
    zulu["recorded_cutoff"] = "2026-10-13T00:00:00Z"
    zulu["occurred_at"] = "2026-10-13T00:00:00Z"
    assert _digest_of(parse_opening_rehearsal_assembly(zulu)) == _digest_of(
        parse_opening_rehearsal_assembly(plus)
    )
    assert _digest_of(parse_opening_rehearsal_assembly(zulu)) == PINNED_DIGEST
    for bad in ("2026-10-13T01:00:00+01:00", "2026-10-13T00:00:00", "garbage"):
        for key in ("recorded_cutoff", "occurred_at"):
            wire = _wire()
            wire[key] = bad
            with pytest.raises(OpeningEvidenceError):
                parse_opening_rehearsal_assembly(wire)
    plus_one = timezone(timedelta(hours=1))
    with pytest.raises(OpeningEvidenceError):
        replace(_assembly(), occurred_at=datetime(2026, 10, 13, 1, tzinfo=plus_one))
    with pytest.raises(OpeningEvidenceError):
        replace(_assembly(), recorded_cutoff=datetime(2026, 10, 13))


# ── closed contract ─────────────────────────────────────────────────────────


def test_closed_contract_rejects_malformed_values() -> None:
    base = _assembly()
    with pytest.raises(TypeError):
        replace(base, unexpected_field=1)  # type: ignore[call-arg]
    for change in (
        {"partitions": list(base.partitions)},  # not an immutable tuple
        {"source_system": " "},  # empty field
        {"sub_exit_code": "1"},  # wrong type
        {"scope": object()},  # not a Scope
        {"accepted_manual_holds": ["hold"]},
        {"schema": "other"},
    ):
        with pytest.raises(OpeningEvidenceError):
            replace(base, **change)
    with pytest.raises(OpeningEvidenceError):
        opening_rehearsal_assembly_digest(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):  # accepted_manual_holds has no default
        OpeningRehearsalAssemblyV1(
            **{
                f.name: getattr(base, f.name)
                for f in fields(base)
                if f.name not in ("accepted_manual_holds", "schema")
            }
        )  # type: ignore[call-arg]


# ── lookup-key uniqueness ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"account_id": _uuid(1002)},
        {"source_account_key": "source-account:other"},
        {"precision": 3},
        {"idempotency_key": "other-import-key"},
        {"expected_position_fingerprint": "other-target-position"},
        {"posting_watermark": "posting:other"},
    ],
    ids=[
        "identical",
        "account",
        "source-key",
        "precision",
        "retry-key",
        "position",
        "watermark",
    ],
)
def test_duplicate_partition_lookup_key_refuses_at_closed_construction(change) -> None:
    base = _assembly()
    first = base.partitions[0]
    duplicate = replace(first, **change)
    # A changed destination/target never disambiguates the source lookup key.
    assert (duplicate.source_account_id, duplicate.currency) == (
        first.source_account_id,
        first.currency,
    )
    with pytest.raises(OpeningEvidenceError, match="duplicate assembly partition"):
        replace(base, partitions=(first, duplicate))
    with pytest.raises(OpeningEvidenceError, match="duplicate assembly partition"):
        replace(base, partitions=(duplicate, first))


@pytest.mark.parametrize("changed_target", [False, True], ids=["identical", "target"])
def test_duplicate_partition_lookup_key_refuses_during_json_decode(
    changed_target,
) -> None:
    wire = _wire()
    duplicate = deepcopy(wire["partitions"][0])
    if changed_target:
        duplicate["account_id"] = str(_uuid(1002))
        duplicate["posting_watermark"] = "posting:other"
    wire["partitions"].append(duplicate)
    with pytest.raises(OpeningEvidenceError, match="duplicate assembly partition"):
        parse_opening_rehearsal_assembly(wire)
    decoded = parse_opening_rehearsal_json(json.dumps(wire))
    with pytest.raises(OpeningEvidenceError, match="duplicate assembly partition"):
        parse_opening_rehearsal_assembly(decoded)


@pytest.mark.parametrize("changed_target", ["identical", "identity", "invoice-key"])
def test_duplicate_invoice_lookup_key_refuses_at_closed_construction(
    changed_target,
) -> None:
    base = _assembly()
    first = base.invoice_mappings[0]
    duplicate = first
    if changed_target == "identity":
        duplicate = replace(
            first, source_identity=OpeningSourceIdentityV1("invoice", "invoice:other")
        )
    elif changed_target == "invoice-key":
        duplicate = replace(first, source_invoice_key="invoice:other")
    assert duplicate.source_invoice_id == first.source_invoice_id
    with pytest.raises(OpeningEvidenceError, match="duplicate assembly invoice"):
        replace(base, invoice_mappings=(first, duplicate))
    with pytest.raises(OpeningEvidenceError, match="duplicate assembly invoice"):
        replace(base, invoice_mappings=(duplicate, first))


@pytest.mark.parametrize("changed_target", [False, True], ids=["identical", "target"])
def test_duplicate_invoice_lookup_key_refuses_during_json_decode(
    changed_target,
) -> None:
    wire = _wire()
    duplicate = deepcopy(wire["invoice_mappings"][0])
    if changed_target:
        duplicate["source_identity"]["key"] = "invoice:other"
        duplicate["source_invoice_key"] = "invoice:other"
    wire["invoice_mappings"].append(duplicate)
    with pytest.raises(OpeningEvidenceError, match="duplicate assembly invoice"):
        parse_opening_rehearsal_assembly(wire)
    decoded = parse_opening_rehearsal_json(json.dumps(wire))
    with pytest.raises(OpeningEvidenceError, match="duplicate assembly invoice"):
        parse_opening_rehearsal_assembly(decoded)


@pytest.mark.parametrize("scope", [TenantScope(_uuid(100)), PlatformScope()])
def test_distinct_source_lookup_keys_preserve_multicurrency_and_account_mappings(
    scope,
) -> None:
    base = _assembly()
    first = base.partitions[0]
    other_currency = replace(first, currency="USD", idempotency_key="import:usd")
    other_account = replace(
        first,
        source_account_id=_uuid(11),
        source_account_key="source-account:11",
        account_id=_uuid(1002),
        idempotency_key="import:other-account",
    )
    other_invoice = OpeningRehearsalInvoiceMappingV1(
        _uuid(2), OpeningSourceIdentityV1("invoice", "invoice:2"), "invoice:2"
    )
    assembly = replace(
        base,
        scope=scope,
        partitions=(first, other_currency, other_account),
        invoice_mappings=(*base.invoice_mappings, other_invoice),
    )
    decoded = parse_opening_rehearsal_json(
        json.dumps(opening_rehearsal_assembly_wire(assembly))
    )
    assert parse_opening_rehearsal_assembly(decoded) == assembly
    assert len(assembly.partitions) == 3 and len(assembly.invoice_mappings) == 2


# ── census ──────────────────────────────────────────────────────────────────


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


def test_duplicate_census_count_keys_refuse_instead_of_merging() -> None:
    dup = (
        OpeningRehearsalCensusCountV1("r1", 1),
        OpeningRehearsalCensusCountV1("r1", 2),
    )
    sidecar = replace(_census().v3_documentary_sidecar, by_requirement=dup)
    with pytest.raises(OpeningEvidenceError):
        render_opening_rehearsal_census(
            replace(_census(), v3_documentary_sidecar=sidecar)
        )
    rollup = replace(_census().rollup, blocked_by_reason=dup)
    with pytest.raises(OpeningEvidenceError):
        render_opening_rehearsal_census(replace(_census(), rollup=rollup))
