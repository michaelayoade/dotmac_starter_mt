#!/usr/bin/env python3
"""Read-only opening-rehearsal assembly drafter: identity mapping only.

Drafts a *candidate* ``billing.opening.rehearsal.assembly.v1`` document (plus a
summary CSV) from two inputs: Sub's serialized opening census
(``sub-billing-opening-census-v2``/``-v3`` JSON) and a target-state file that
declares the rehearsal copy's coordinates and pristine partition positions.

It maps identities only. Source account, invoice and settlement identifiers are
carried across verbatim (or, for target accounts, derived by an explicitly
declared UUIDv5 namespace). It never invents, defaults, rounds or computes a
financial value: no amount, residual, fingerprint, posting mark or precision is
ever produced here. Anything it cannot map identically is reported as a
blocker, and the unmappable partition/invoice/settlement is left out of the
candidate so the adapter reports it as unmapped. The draft is neither an
approval nor evidence of truth; Michael approves the exact digest separately.

Deleted once a verified OpeningWindowSealV1 is stored for the target scope and
three complete runs have been compared (same trigger as the rehearsal adapter).

Exit 0: no blockers. 1: candidate written with blockers. 2: refused (malformed
input, existing output); nothing is written.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import string
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from uuid import UUID, uuid5

from dotmac_billing.opening_admissibility import (
    AdmissibilityError,
    AdmissibilityProjection,
    parse_a10_input,
    parse_admissibility_rules,
    parse_reseller_map,
    project_census,
    render_admissibility_decisions,
)
from dotmac_billing.opening_contracts import (
    OpeningEvidenceError,
)
from dotmac_billing.opening_rehearsal import (
    opening_rehearsal_assembly_digest,
    opening_rehearsal_assembly_wire,
    parse_opening_rehearsal_assembly,
    parse_opening_rehearsal_json,
)

__all__ = [
    "Draft",
    "DraftError",
    "draft_assembly",
    "main",
    "project_admissibility",
    "render_admissibility",
    "render_outputs",
]

TARGET_STATE_SCHEMA: Final = "billing.opening.rehearsal.target-state.v1"
_POPULATION_VERSIONS = frozenset(
    {"sub-billing-opening-census-v2", "sub-billing-opening-census-v3"}
)
# Assembly scalars the target state must declare verbatim (never defaulted).
_SCALAR_KEYS = (
    "scope",
    "source_system",
    "recorded_cutoff",
    "source_authority_ref",
    "account_mapping_version",
    "cash_mapping_version",
    "receivable_mapping_version",
    "tax_mapping_version",
    "source_resolver_version",
    "collection_authority",
    "collection_mapping_version",
    "migration_authority_ref",
    "occurred_at",
    "starter_head_sha",
    "sub_exit_code",
    "binding",
    "accepted_manual_holds",
)
_TARGET_KEYS = frozenset(
    {
        "schema",
        "account_id_derivation",
        "idempotency_key_format",
        "pristine_positions",
        "partition_targets",
    }
    | set(_SCALAR_KEYS)
)
_PARTITION_TARGET_KEYS = frozenset(
    {
        "source_account_id",
        "currency",
        "account_id",
        "expected_position_fingerprint",
        "posting_watermark",
    }
)
_PRISTINE_KEYS = frozenset({"expected_position_fingerprint", "posting_watermark"})
_DERIVATION_KEYS = frozenset({"method", "namespace", "name_format"})
_FORMAT_FIELDS = frozenset({"source_account_id", "currency"})
_MAPPABLE_SETTLEMENT_KINDS = frozenset({"payment", "credit_note"})


class DraftError(ValueError):
    """Malformed or unsafe input; only the sanitised code is ever shown."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Blocker:
    code: str
    reference: str
    detail: str = ""


@dataclass(slots=True)
class Draft:
    assembly: dict[str, object]
    digest: str
    blockers: list[Blocker]
    counts: dict[str, int]
    partitions: list[tuple[str, str, str]]  # source account, currency, target
    census_sha256: str = ""
    target_state_sha256: str = ""
    census_facts: dict[str, str] = field(default_factory=dict)
    admissibility: AdmissibilityProjection | None = None


def _bad(code: str = "malformed_input") -> DraftError:
    return DraftError(code)


def _obj(value: object, code: str = "malformed_input") -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(k, str) for k in value):
        raise _bad(code)
    return value


def _closed(value: object, keys: frozenset[str], code: str) -> Mapping[str, object]:
    row = _obj(value, code)
    if set(row) != keys:
        raise _bad(code)
    return row


def _list(value: object, code: str = "malformed_input") -> list[object]:
    if type(value) is not list:
        raise _bad(code)
    return value


def _text(value: object, code: str = "malformed_input") -> str:
    if type(value) is not str or not value:
        raise _bad(code)
    return value


def _uuid(value: object, code: str = "malformed_input") -> UUID:
    text = _text(value, code)
    try:
        parsed = UUID(text)
    except ValueError:
        raise _bad(code) from None
    if str(parsed) != text:
        raise _bad(code)
    return parsed


def _opt_uuid(value: object, code: str = "malformed_input") -> UUID | None:
    return None if value is None else _uuid(value, code)


def _precision(value: object, code: str = "malformed_input") -> int:
    if type(value) is not int:
        raise _bad(code)
    return value


def _read_json(path: Path, code: str) -> tuple[bytes, Mapping[str, object]]:
    try:
        raw = path.read_bytes()
        value = parse_opening_rehearsal_json(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, OpeningEvidenceError):
        raise _bad(code) from None
    return raw, _obj(value, code)


def _template(value: object) -> str:
    """A declared format using only the two identity fields; nothing defaults."""
    text = _text(value, "malformed_target_state")
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError:
        raise _bad("malformed_target_state") from None
    for _literal, name, spec, conversion in parsed:
        if name is None:
            continue
        if name not in _FORMAT_FIELDS or spec or conversion:
            raise _bad("malformed_target_state")
    return text


def _target_state(payload: Mapping[str, object]) -> Mapping[str, object]:
    root = _closed(payload, _TARGET_KEYS, "malformed_target_state")
    if root["schema"] != TARGET_STATE_SCHEMA:
        raise _bad("malformed_target_state")
    derivation = root["account_id_derivation"]
    if derivation is not None:
        row = _closed(derivation, _DERIVATION_KEYS, "malformed_target_state")
        if row["method"] != "uuid5":
            raise _bad("malformed_target_state")
        _uuid(row["namespace"], "malformed_target_state")
        _template(row["name_format"])
    _template(root["idempotency_key_format"])
    pristine = root["pristine_positions"]
    if pristine is not None:
        for currency, entry in _obj(pristine, "malformed_target_state").items():
            _text(currency, "malformed_target_state")
            row = _closed(entry, _PRISTINE_KEYS, "malformed_target_state")
            _text(row["expected_position_fingerprint"], "malformed_target_state")
            _text(row["posting_watermark"], "malformed_target_state")
    for item in _list(root["partition_targets"], "malformed_target_state"):
        row = _closed(item, _PARTITION_TARGET_KEYS, "malformed_target_state")
        _uuid(row["source_account_id"], "malformed_target_state")
        _text(row["currency"], "malformed_target_state")
        _uuid(row["account_id"], "malformed_target_state")
        _text(row["expected_position_fingerprint"], "malformed_target_state")
        _text(row["posting_watermark"], "malformed_target_state")
    return root


def _precisions(declared: Mapping[str, object]) -> dict[str, int]:
    result: dict[str, int] = {}
    for entry in _list(declared["currency_precisions"]):
        pair = _list(entry)
        if len(pair) != 2:
            raise _bad()
        currency = _text(pair[0])
        if currency in result:
            raise _bad()
        result[currency] = _precision(pair[1])
    return result


def _canonical_json(value: object, *, indent: int | None = None) -> str:
    """The drafter's one JSON canonicalizer: sorted keys, UTF-8 text."""
    return json.dumps(value, sort_keys=True, indent=indent, ensure_ascii=False)


def draft_assembly(
    census: Mapping[str, object],
    target: Mapping[str, object],
    *,
    census_sha256: str = "",
    target_state_sha256: str = "",
    admissibility: AdmissibilityProjection | None = None,
) -> Draft:
    """Draft one candidate assembly. Pure: no I/O, no clock, no randomness.

    With ``admissibility`` the eligible population is the rules' projection of
    the census (D-2), not the census's own all-or-nothing list; the drafter
    still invents no value and only maps identities.
    """
    if admissibility is not None:
        census = admissibility.census
    target = _target_state(target)
    capture = _obj(census.get("capture"))
    declared = _obj(capture.get("declared_source"))
    version = _text(declared.get("population_version"))
    if version not in _POPULATION_VERSIONS:
        raise _bad("unsupported_population_version")
    precisions = _precisions(declared)
    blockers: list[Blocker] = []
    if capture.get("population_complete") is not True:
        blockers.append(Blocker("census_population_incomplete", "capture"))

    invoices: dict[UUID, Mapping[str, object]] = {}
    duplicate_invoices: set[UUID] = set()
    for item in _list(census.get("invoices")):
        row = _obj(item)
        invoice_id = _uuid(row.get("invoice_id"))
        if invoice_id in invoices:
            duplicate_invoices.add(invoice_id)
        invoices[invoice_id] = row
    for invoice_id in sorted(duplicate_invoices, key=str):
        blockers.append(Blocker("duplicate_census_invoice", str(invoice_id)))

    eligible = [_uuid(x) for x in _list(census.get("eligible_invoice_ids"))]
    if len(set(eligible)) != len(eligible):
        blockers.append(Blocker("duplicate_eligible_invoice", "eligible_invoice_ids"))
    eligible_set = set(eligible)

    # Partition universe: every (account, currency) the census mentions.
    universe: set[tuple[UUID, str]] = set()
    for section in ("controls", "lanes", "invoices"):
        for item in _list(census.get(section)):
            row = _obj(item)
            account = _opt_uuid(row.get("account_id"))
            currency = row.get("currency")
            if account is None or type(currency) is not str or not currency:
                if section != "invoices":
                    blockers.append(
                        Blocker("census_row_unattributed", section, "account/currency")
                    )
                continue
            universe.add((account, currency))

    # --- partitions -------------------------------------------------------
    explicit: dict[tuple[UUID, str], Mapping[str, object]] = {}
    for item in _list(target["partition_targets"]):
        row = _obj(item)
        key = (_uuid(row["source_account_id"]), _text(row["currency"]))
        if key in explicit:
            raise _bad("malformed_target_state")
        explicit[key] = row
    for account, currency in sorted(set(explicit) - universe, key=str):
        blockers.append(
            Blocker(
                "target_entry_without_census_account",
                f"{account}:{currency}",
            )
        )
    derivation = target["account_id_derivation"]
    namespace = _uuid(_obj(derivation)["namespace"]) if derivation is not None else None
    name_format = _template(_obj(derivation)["name_format"]) if derivation else ""
    key_format = _template(target["idempotency_key_format"])
    pristine = (
        _obj(target["pristine_positions"])
        if target["pristine_positions"] is not None
        else {}
    )
    resolved: dict[tuple[UUID, str], dict[str, object]] = {}
    for account, currency in sorted(universe, key=lambda k: (str(k[0]), k[1])):
        reference = f"{account}:{currency}"
        if currency not in precisions:
            blockers.append(Blocker("currency_precision_undeclared", reference))
            continue
        row = explicit.get((account, currency))
        if row is not None:
            target_account = _uuid(row["account_id"])
            fingerprint = _text(row["expected_position_fingerprint"])
            position_mark = _text(row["posting_watermark"])
        else:
            if namespace is None:
                blockers.append(Blocker("target_account_unavailable", reference))
                continue
            target_account = uuid5(
                namespace,
                name_format.format_map(
                    {"source_account_id": str(account), "currency": currency}
                ),
            )
            position = pristine.get(currency)
            if position is None:
                blockers.append(Blocker("target_position_unavailable", reference))
                continue
            fingerprint = _text(_obj(position)["expected_position_fingerprint"])
            position_mark = _text(_obj(position)["posting_watermark"])
        resolved[(account, currency)] = {
            "source_account_id": str(account),
            "source_account_key": f"source-account:{account}",
            "account_id": str(target_account),
            "currency": currency,
            "precision": precisions[currency],
            "idempotency_key": key_format.format_map(
                {"source_account_id": str(account), "currency": currency}
            ),
            "expected_position_fingerprint": fingerprint,
            "posting_watermark": position_mark,
        }
    # One target account may back only one source partition.
    by_target: dict[str, list[tuple[UUID, str]]] = {}
    for key, row in resolved.items():
        by_target.setdefault(str(row["account_id"]), []).append(key)
    for target_account, keys in sorted(by_target.items()):
        if len(keys) > 1:
            blockers.append(Blocker("target_account_collision", target_account))
            for key in keys:
                del resolved[key]

    # --- invoice mappings (eligible invoices only) -------------------------
    invoice_mappings: list[dict[str, object]] = []
    mapped_eligible: set[UUID] = set()
    for invoice_id in sorted(eligible_set, key=str):
        row = invoices.get(invoice_id)
        if row is None:
            blockers.append(Blocker("eligible_invoice_not_in_census", str(invoice_id)))
            continue
        if invoice_id in duplicate_invoices:
            continue
        account = _opt_uuid(row.get("account_id"))
        currency = row.get("currency")
        if (account, currency) not in resolved:
            blockers.append(Blocker("invoice_partition_unmapped", str(invoice_id)))
            continue
        declared_precision = precisions[str(currency)]
        if row.get("precision") != declared_precision:
            blockers.append(Blocker("invoice_precision_mismatch", str(invoice_id)))
            continue
        invoice_mappings.append(
            {
                "source_invoice_id": str(invoice_id),
                "source_identity": {"kind": "invoice", "key": str(invoice_id)},
                "source_invoice_key": str(invoice_id),
            }
        )
        mapped_eligible.add(invoice_id)

    # --- discharge mappings (active settlements on eligible invoices) -------
    discharge_mappings: list[dict[str, object]] = []
    in_scope: list[tuple[Mapping[str, object], UUID, str, UUID]] = []
    occurrences: dict[tuple[str, UUID], int] = {}
    for item in _list(census.get("settlements")):
        row = _obj(item)
        invoice_id = _uuid(row.get("invoice_id"))
        if row.get("active") is not True or invoice_id not in eligible_set:
            continue
        kind = _text(row.get("kind"))
        settlement_id = _uuid(row.get("id"))
        in_scope.append((row, invoice_id, kind, settlement_id))
        key = (kind, settlement_id)
        occurrences[key] = occurrences.get(key, 0) + 1
    for _kind, settlement_id in sorted(
        (k for k, n in occurrences.items() if n > 1), key=str
    ):
        blockers.append(Blocker("duplicate_census_settlement", str(settlement_id)))
    for row, invoice_id, kind, settlement_id in in_scope:
        reference = str(settlement_id)
        if occurrences[(kind, settlement_id)] > 1:
            continue
        if kind not in _MAPPABLE_SETTLEMENT_KINDS:
            blockers.append(Blocker("unsupported_settlement_kind", reference, kind))
            continue
        source_id = _opt_uuid(row.get("source_id"))
        if source_id is None:
            blockers.append(Blocker("settlement_source_id_unavailable", reference))
            continue
        if invoice_id not in mapped_eligible:
            blockers.append(Blocker("settlement_invoice_unmapped", reference))
            continue
        discharge_mappings.append(
            {
                "settlement_kind": kind,
                "settlement_id": str(settlement_id),
                "historical_reference": f"{kind}:{settlement_id}",
                "source_id": str(source_id),
            }
        )

    ordered = [
        resolved[key] for key in sorted(resolved, key=lambda k: (str(k[0]), k[1]))
    ]
    document: dict[str, object] = {
        "schema": "billing.opening.rehearsal.assembly.v1",
        **{key: target[key] for key in _SCALAR_KEYS},
        "partitions": ordered,
        "invoice_mappings": sorted(
            invoice_mappings, key=lambda r: str(r["source_invoice_id"])
        ),
        "discharge_mappings": sorted(
            discharge_mappings,
            key=lambda r: (str(r["settlement_kind"]), str(r["settlement_id"])),
        ),
    }
    # The package is the only judge of a valid declaration and its identity.
    try:
        normalized = json.loads(_canonical_json(document))
        parsed = parse_opening_rehearsal_assembly(normalized)
        if opening_rehearsal_assembly_wire(parsed) != normalized:
            raise _bad("target_state_not_canonical")
        digest = opening_rehearsal_assembly_digest(parsed)
    except OpeningEvidenceError:
        raise _bad("malformed_target_state") from None
    blockers.sort(key=lambda b: (b.code, b.reference, b.detail))
    return Draft(
        assembly=document,
        digest=digest,
        blockers=blockers,
        counts={
            "census_invoices": len(invoices),
            "eligible_invoices": len(eligible_set),
            "invoice_mappings": len(invoice_mappings),
            "discharge_mappings": len(discharge_mappings),
            "census_partitions": len(universe),
            "partitions": len(ordered),
            "blockers": len(blockers),
        },
        partitions=[
            (str(r["source_account_id"]), str(r["currency"]), str(r["account_id"]))
            for r in ordered
        ],
        census_sha256=census_sha256,
        target_state_sha256=target_state_sha256,
        admissibility=admissibility,
        census_facts={
            "population_version": version,
            "content_fingerprint": str(census.get("content_fingerprint")),
            "snapshot_id": str(capture.get("snapshot_id")),
        },
    )


def render_outputs(draft: Draft) -> tuple[str, str]:
    """Deterministic (assembly.json, summary.csv) text for one draft."""
    assembly_text = _canonical_json(draft.assembly, indent=2) + "\n"
    assembly_sha = hashlib.sha256(assembly_text.encode()).hexdigest()
    rows: list[tuple[str, str, str]] = [
        ("input", "census_sha256", draft.census_sha256),
        ("input", "target_state_sha256", draft.target_state_sha256),
        ("input", "population_version", draft.census_facts["population_version"]),
        (
            "input",
            "census_content_fingerprint",
            draft.census_facts["content_fingerprint"],
        ),
        ("input", "census_snapshot_id", draft.census_facts["snapshot_id"]),
        ("output", "assembly_json_sha256", assembly_sha),
        ("output", "assembly_digest", draft.digest),
    ]
    rows.extend(("count", key, str(value)) for key, value in draft.counts.items())
    if draft.admissibility is not None:
        proj = draft.admissibility
        rows.extend(
            ("admissibility_digest", name, digest)
            for name, digest in sorted(proj.digests.items())
        )
        rows.extend(
            ("admissibility_count", key, str(value))
            for key, value in proj.counts().items()
        )
        rows.extend(
            ("hold", f"{d.account_id}:{d.currency}", d.reason or "")
            for d in proj.decisions
            if not d.admitted
        )
    rows.extend(
        ("partition", f"{source}:{currency}", target)
        for source, currency, target in draft.partitions
    )
    by_code: dict[str, int] = {}
    for blocker in draft.blockers:
        by_code[blocker.code] = by_code.get(blocker.code, 0) + 1
    rows.extend(("blocker_count", code, str(n)) for code, n in sorted(by_code.items()))
    rows.extend(
        ("blocker", f"{b.code}:{b.reference}", b.detail) for b in draft.blockers
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("section", "key", "value"))
    writer.writerows(rows)
    return assembly_text, buffer.getvalue()


def render_admissibility(draft: Draft) -> str | None:
    """The deterministic per-partition decision record, when rules were applied."""
    if draft.admissibility is None:
        return None
    return render_admissibility_decisions(draft.admissibility)


def project_admissibility(
    census: Mapping[str, object],
    *,
    rules_payload: object,
    a10_payload: object,
    reseller_payload: object | None = None,
) -> AdmissibilityProjection:
    """Evaluate the reviewed rules; D-3 is left to the adapter (no originals yet)."""
    try:
        classes, rebuild_rows = parse_a10_input(a10_payload)
        return project_census(
            census,
            rules=parse_admissibility_rules(rules_payload),
            a10=classes,
            reseller=(
                None
                if reseller_payload is None
                else parse_reseller_map(reseller_payload)
            ),
            original_invoice_ids=None,
            rebuild=rebuild_rows,
            require_rebuild=True,  # never a silent all-gap run
        )
    except AdmissibilityError as exc:
        raise _bad(exc.code) from None


def _write_pair(targets: Sequence[Path], texts: Sequence[str]) -> None:
    """Write all files to private temps, then rename all; else leave nothing."""
    temps: list[Path] = []
    renamed: list[Path] = []
    try:
        for target, text in zip(targets, texts, strict=True):
            temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            temps.append(temp)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
        for temp, target in zip(temps, targets, strict=True):
            if target.exists():
                raise _bad("output_exists")
            os.replace(temp, target)
            renamed.append(target)
    except BaseException:
        for path in (*temps, *renamed):
            path.unlink(missing_ok=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census", required=True, help="Sub v2/v3 census wire JSON")
    parser.add_argument("--target-state", required=True, help="Target-state JSON")
    parser.add_argument("--out-dir", required=True, help="Existing directory")
    parser.add_argument(
        "--rules", help="Reviewed admissibility rules JSON (D-2 digest); needs --a10"
    )
    parser.add_argument("--a10", help="A10 per-account classes JSON; needs --rules")
    parser.add_argument("--reseller-map", help="Reseller option A map JSON")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    try:
        census_raw, census = _read_json(Path(args.census), "malformed_census")
        target_raw, target = _read_json(
            Path(args.target_state), "malformed_target_state"
        )
        if bool(args.rules) != bool(args.a10) or (args.reseller_map and not args.rules):
            raise _bad("rules_and_a10_required_together")
        projection = None
        if args.rules:
            _raw, rules_payload = _read_json(Path(args.rules), "malformed_rules")
            _raw, a10_payload = _read_json(Path(args.a10), "malformed_a10_classes")
            reseller_payload = None
            if args.reseller_map:
                _raw, reseller_payload = _read_json(
                    Path(args.reseller_map), "malformed_reseller_map"
                )
            projection = project_admissibility(
                census,
                rules_payload=rules_payload,
                a10_payload=a10_payload,
                reseller_payload=reseller_payload,
            )
        draft = draft_assembly(
            census,
            target,
            census_sha256=hashlib.sha256(census_raw).hexdigest(),
            target_state_sha256=hashlib.sha256(target_raw).hexdigest(),
            admissibility=projection,
        )
        assembly_text, summary_text = render_outputs(draft)
        decisions_text = render_admissibility(draft)
        if not out_dir.is_dir():
            raise _bad("output_directory_missing")
        names = ["assembly.json", "summary.csv"]
        texts = [assembly_text, summary_text]
        if decisions_text is not None:
            names.append("admissibility.json")
            texts.append(decisions_text)
        targets = tuple(out_dir / name for name in names)
        if any(path.exists() for path in targets):
            raise _bad("output_exists")
        _write_pair(targets, texts)
    except (DraftError, OpeningEvidenceError, OSError) as exc:
        code = exc.code if isinstance(exc, DraftError) else "malformed_input"
        print(_canonical_json({"drafted": False, "refused": code}))
        return 2
    print(
        _canonical_json(
            {
                "drafted": True,
                "assembly_json_sha256": hashlib.sha256(
                    assembly_text.encode()
                ).hexdigest(),
                "assembly_digest": draft.digest,
                "summary_csv_sha256": hashlib.sha256(summary_text.encode()).hexdigest(),
                "blockers": len(draft.blockers),
                **(
                    {
                        "admissibility_json_sha256": hashlib.sha256(
                            decisions_text.encode()
                        ).hexdigest(),
                        "rules_digest": projection.digests["rules"],
                    }
                    if decisions_text is not None and projection is not None
                    else {}
                ),
            }
        )
    )
    return 1 if draft.blockers else 0


if __name__ == "__main__":
    sys.exit(main())
