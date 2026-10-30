"""Versioned dependency evidence; calculation remains in the Tax service owner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from typing import cast
from uuid import UUID

from dotmac_kernel.money import Currency
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_tax.contracts import (
    TaxBaseDependencyPolicyV1,
    TaxDependencyComponentV1,
    TaxDeterminationSetV2,
    TaxFactSignatureV1,
    TaxFactV2,
    TaxPriorResultReferenceV1,
)
from dotmac_tax.models import (
    TaxDetermination,
    TaxDeterminationDependency,
    TaxDeterminationPriorSet,
    TaxDeterminationSet,
    TaxRule,
)

DEPENDENCY_BASE = "source_plus_dependency_tax"


def policy_payload(policy: TaxBaseDependencyPolicyV1) -> dict[str, object]:
    return {
        "prior_signature": {
            field.name: str(getattr(policy.prior_signature, field.name))
            for field in fields(policy.prior_signature)
        },
        "selection": policy.selection,
        "tax_code_ids": [str(code) for code in policy.tax_code_ids],
    }


def policy_from_payload(payload: dict[str, object]) -> TaxBaseDependencyPolicyV1:
    signature = cast(dict[str, str], payload["prior_signature"])
    return TaxBaseDependencyPolicyV1(
        TaxFactSignatureV1(
            UUID(signature["jurisdiction_id"]),
            signature["fact_kind"],
            signature["recognition_basis_code"],
            signature["transaction_side"],
        ),
        cast("str", payload["selection"]),  # type: ignore[arg-type]
        tuple(UUID(code) for code in cast(list[str], payload["tax_code_ids"])),
    )


def rule_policy(rule: TaxRule) -> TaxBaseDependencyPolicyV1 | None:
    if rule.dependency_selection is None:
        if (
            any(
                value is not None
                for value in (
                    rule.dependency_jurisdiction_id,
                    rule.dependency_fact_kind,
                    rule.dependency_recognition_basis_code,
                    rule.dependency_transaction_side,
                )
            )
            or rule.dependency_codes
        ):
            raise ValueError("persisted dependency policy is incomplete")
        return None
    return TaxBaseDependencyPolicyV1(
        TaxFactSignatureV1(
            cast(UUID, rule.dependency_jurisdiction_id),
            cast(str, rule.dependency_fact_kind),
            cast(str, rule.dependency_recognition_basis_code),
            cast(str, rule.dependency_transaction_side),
        ),
        cast("str", rule.dependency_selection),  # type: ignore[arg-type]
        tuple(code.tax_code_id for code in rule.dependency_codes),
    )


def prior_reference(row: TaxDeterminationPriorSet) -> TaxPriorResultReferenceV1:
    return TaxPriorResultReferenceV1(
        row.prior_set_id,
        row.source_ref,
        row.source_version,
        row.source_fingerprint,
        row.result_fingerprint,
    )


def dependency_component(row: TaxDeterminationDependency) -> TaxDependencyComponentV1:
    from dotmac_tax.service import _exact_money

    return TaxDependencyComponentV1(
        row.prior_set_id,
        row.prior_determination_id,
        row.tax_code_id,
        row.rule_id,
        row.rule_version,
        row.component_sequence,
        _exact_money(
            row.tax_amount,
            currency=Currency(row.currency_code, row.minor_units),
            label="dependency tax amount",
        ),
    )


def result_content_fingerprint_v2(row: TaxDeterminationSet) -> str:
    # rv1 is an unmodified byte-level encoding of the common persisted fields.
    # rv2 domain-separates that encoding and adds every new immutable field.
    from dotmac_tax.service import _result_content_fingerprint

    payload = {
        "contract": "TaxDeterminationSetV2",
        "common": _result_content_fingerprint(row),
        "commercial_source": [row.commercial_source_ref, row.commercial_source_version],
        "prior_results": [
            [
                str(ref.prior_set_id),
                ref.source_ref,
                ref.source_version,
                ref.source_fingerprint,
                ref.result_fingerprint,
            ]
            for ref in row.prior_sets
        ],
        "components": [
            {
                "id": str(component.id),
                "selected_dependency_policy": component.selected_dependency_policy,
                "dependency_components": [
                    [
                        str(item.tenant_id),
                        str(item.determination_id),
                        str(item.prior_set_id),
                        str(item.prior_determination_id),
                        str(item.tax_code_id),
                        str(item.rule_id),
                        item.rule_version,
                        item.component_sequence,
                        format(item.tax_amount, ".6f"),
                        item.currency_code,
                        item.minor_units,
                    ]
                    for item in component.dependency_components
                ],
            }
            for component in row.components
        ],
    }
    return (
        "rv2:"
        + hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest()
    )


def fact_fingerprint_v2(command: TaxFactV2, *, common_fingerprint: str) -> str:
    payload = {
        "contract": "TaxFactV2",
        "common": common_fingerprint,
        "commercial_source": [
            command.commercial_source.source_ref,
            command.commercial_source.source_version,
        ],
        "prior_results": [
            [
                str(ref.determination_set_id),
                ref.source_ref,
                ref.source_version,
                ref.source_fingerprint,
                ref.result_fingerprint,
            ]
            for ref in command.prior_results
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def resolve_prior_results(
    db: Session, *, tenant_id: UUID, command: TaxFactV2
) -> tuple[TaxDeterminationSetV2, ...]:
    from dotmac_tax.service import TaxConflict, TaxNotFound, _determination_set_contract

    results = []
    fact = command.fact
    for ref in command.prior_results:
        row = db.scalar(
            select(TaxDeterminationSet).where(
                TaxDeterminationSet.tenant_id == tenant_id,
                TaxDeterminationSet.id == ref.determination_set_id,
            )
        )
        if row is None:
            raise TaxNotFound("prior tax result was not found in this tenant")
        if row.contract_version != 2:
            raise TaxConflict("prior tax result has no V2 commercial binding")
        result = cast(TaxDeterminationSetV2, _determination_set_contract(row, _v2=True))
        if (
            result.source_ref,
            result.source_version,
            result.source_fingerprint,
            result.result_fingerprint,
        ) != (
            ref.source_ref,
            ref.source_version,
            ref.source_fingerprint,
            ref.result_fingerprint,
        ):
            raise TaxConflict("prior tax reference does not match sealed evidence")
        if (
            result.commercial_source != command.commercial_source
            or result.occurred_on != fact.occurred_on
            or result.source_amount != fact.base_amount
            or result.recognition_basis_code != fact.recognition_basis_code
            or any(
                getattr(result, key)
                != (getattr(fact, key).strip() if getattr(fact, key) else None)
                for key in ("counterparty_ref", "supply_ref", "place_ref")
            )
        ):
            raise TaxConflict("prior tax result describes a different commercial fact")
        # Jurisdictions may differ but the statutory authority must be identical.
        from dotmac_tax.service import _jurisdiction

        prior_jurisdiction = _jurisdiction(db, tenant_id, result.jurisdiction_id)
        current_jurisdiction = _jurisdiction(db, tenant_id, fact.jurisdiction_id)
        if prior_jurisdiction.authority_id != current_jurisdiction.authority_id:
            raise TaxConflict("prior tax result uses a different authority")
        results.append(result)
    return tuple(results)


def select_dependency_components(
    policy: TaxBaseDependencyPolicyV1, results: tuple[TaxDeterminationSetV2, ...]
) -> tuple[TaxDependencyComponentV1, ...]:
    from dotmac_tax.service import TaxRuleViolation

    matches = [
        result
        for result in results
        if TaxFactSignatureV1(
            result.jurisdiction_id,
            result.fact_kind,
            result.recognition_basis_code,
            result.transaction_side,
        )
        == policy.prior_signature
    ]
    if len(matches) != 1:
        raise TaxRuleViolation(
            "dependency policy requires exactly one matching complete prior result"
        )
    result = matches[0]
    by_code = {component.tax_code_id: component for component in result.components}
    if len(by_code) != len(result.components):
        raise TaxRuleViolation("prior tax code identity is ambiguous")
    selected = (
        result.components
        if policy.selection == "all_prior"
        else tuple(
            component
            for component in result.components
            if component.tax_code_id in policy.tax_code_ids
        )
    )
    if policy.selection == "tax_code_ids" and set(by_code).intersection(
        policy.tax_code_ids
    ) != set(policy.tax_code_ids):
        raise TaxRuleViolation("dependency policy selected a missing prior tax code")
    if not selected:
        raise TaxRuleViolation(
            "dependency policy requires a prior component including explicit zero"
        )
    return tuple(
        TaxDependencyComponentV1(
            result.determination_set_id,
            component.determination_id,
            component.tax_code_id,
            component.rule_id,
            component.rule_version,
            component.component_sequence,
            component.tax_amount,
        )
        for component in selected
    )


def attach_dependency_components(
    component: TaxDetermination,
    policy: TaxBaseDependencyPolicyV1,
    snapshots: tuple[TaxDependencyComponentV1, ...],
) -> None:
    component.selected_dependency_policy = policy_payload(policy)
    component.dependency_components.extend(
        TaxDeterminationDependency(
            tenant_id=component.tenant_id,
            determination_id=component.id,
            prior_determination_id=snapshot.determination_id,
            prior_set_id=snapshot.determination_set_id,
            tax_code_id=snapshot.tax_code_id,
            rule_id=snapshot.rule_id,
            rule_version=snapshot.rule_version,
            component_sequence=snapshot.component_sequence,
            tax_amount=snapshot.tax_amount.amount,
            currency_code=snapshot.tax_amount.currency.code,
            minor_units=snapshot.tax_amount.currency.minor_units,
        )
        for snapshot in snapshots
    )


def attach_prior_results(row: TaxDeterminationSet, command: TaxFactV2) -> None:
    row.contract_version = 2
    row.commercial_source_ref = command.commercial_source.source_ref
    row.commercial_source_version = command.commercial_source.source_version
    row.prior_sets.extend(
        TaxDeterminationPriorSet(
            tenant_id=row.tenant_id,
            determination_set_id=row.id,
            prior_set_id=ref.determination_set_id,
            source_ref=ref.source_ref,
            source_version=ref.source_version,
            source_fingerprint=ref.source_fingerprint,
            result_fingerprint=ref.result_fingerprint,
        )
        for ref in command.prior_results
    )
