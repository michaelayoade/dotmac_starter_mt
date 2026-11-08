"""Opening admissibility as data: a pure census projection with one reason per hold.

The Sub census applies an all-or-nothing rule per account and currency: one
blocking exception anywhere blocks every invoice in it. Starter's opening
instead evaluates a reviewed, digest-identified rules document (``rules-as-data``)
against the census plus the A10 comparison rebuild, then projects the census so
the rehearsal drafter and adapter see the same eligible population. Nothing here
opens a session, calls a writer, repairs a Sub row or invents a value.

Policy carried as data, never as code:

* D-2: a partition is admissible only if A10 reports ``opening_exact`` and a
  post-cutover ``match``. Every other census blocking exception code stays
  blocking. No rule can relax, drop or carry a census finding: an unlisted or
  unknown code is always ``blocking`` (variance handling is a separate change).
* X1: drafts are excluded from the population and listed in an archive
  record; accounts whose A10 post-cutover cause is a draft allocation are held.
* V1: only status-open documents are gated on tax-snapshot evidence.
* D-3: status-open documents need a supplied original (M8-minimal).
* Reseller option A: reseller-managed subscribers' invoices and wallet lanes are
  re-keyed to the reseller account, with the subscriber service as the single
  ``service_ref`` per document. Any gap holds the subscriber's own partition.

A partition that is not admitted carries exactly one hold reason, chosen by the
rules' closed precedence list. The output is a decision, never an approval.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from typing import Final
from uuid import UUID

from dotmac_kernel.fingerprints import fingerprint_of

__all__ = [
    "ADMISSIBILITY_RULES_SCHEMA",
    "A10_CLASSES_SCHEMA",
    "RESELLER_MAP_SCHEMA",
    "HOLD_REASONS",
    "A10AccountClassV1",
    "AdmissibilityError",
    "AdmissibilityProjection",
    "AdmissibilityRulesV1",
    "PartitionDecision",
    "ResellerMapV1",
    "a10_classes_digest",
    "census_measure_figures",
    "admissibility_rules_digest",
    "parse_a10_classes",
    "parse_admissibility_rules",
    "parse_reseller_map",
    "project_census",
    "render_admissibility_decisions",
    "reseller_map_digest",
]

ADMISSIBILITY_RULES_SCHEMA: Final = "billing.opening.admissibility-rules.v1"
A10_CLASSES_SCHEMA: Final = "billing.opening.a10-account-classes.v1"
RESELLER_MAP_SCHEMA: Final = "billing.opening.reseller-map.v1"
_DECISIONS_SCHEMA: Final = "billing.opening.admissibility-decisions.v1"

# The closed set of hold reasons. The rules must order ALL of them so no
# reason can be silently unranked and no partition can carry two.
HOLD_REASONS: Final = frozenset(
    {
        "draft_allocation",
        "a10_missing",
        "a10_opening_not_exact",
        "a10_post_cutover_drift",
        "census_blocking_exception",
        "status_residual_conflict",
        "lane_control_mismatch",
        "v1_vat_evidence_missing",
        "original_evidence_missing",
        "reseller_mapping_unavailable",
    }
)
# ``finding_only`` (design 2.2, M8): no amount difference and no variance; the
# census finding on CLOSED history stays readable in the decision record. On an
# open document it stays blocking. Every relaxing policy remains refused.
_POLICIES: Final = frozenset({"blocking", "finding_only"})
# Q1 (ruling 2026-11-08): a partition that is not admitted leaves the manifest
# and is routed to governed correction. There is no manifest-marked hold; the
# decision record lists every routed-out partition with its reason, row count
# and stated lane figures, so the window reconciliation (census = manifest +
# batch + routed-out) can be checked.
_LANE_MONEY: Final = (
    "collectible_receivable",
    "unapplied_customer_credit",
    "prepaid_funding_reserved",
    "prepaid_funding_consumed",
    "written_off_total",
    "refunded_total",
    "adjustment_total",
)
_CONTROL_SUMS: Final = (
    "invoice_count",
    "excluded_count",
    "exception_count",
    "source_count",
)
_CONTROL_MONEY: Final = ("original_total", "canonical_discharge", "residual")


class AdmissibilityError(ValueError):
    """Malformed rules or input; only a sanitised code is ever shown."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _bad(code: str = "malformed_admissibility_input") -> AdmissibilityError:
    return AdmissibilityError(code)


def _obj(
    value: object, keys: frozenset[str] | None = None, code: str | None = None
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(k, str) for k in value):
        raise _bad(code or "malformed_admissibility_input")
    if keys is not None and set(value) != keys:
        raise _bad(code or "malformed_admissibility_input")
    return value


def _list(value: object, code: str = "malformed_admissibility_input") -> list[object]:
    if type(value) is not list:
        raise _bad(code)
    return value


def _str(value: object, code: str = "malformed_admissibility_input") -> str:
    if type(value) is not str or not value:
        raise _bad(code)
    return value


def _flag(value: object, code: str = "malformed_admissibility_input") -> bool:
    if type(value) is not bool:
        raise _bad(code)
    return value


def _strs(value: object, code: str) -> tuple[str, ...]:
    items = tuple(_str(x, code) for x in _list(value, code))
    if len(set(items)) != len(items):
        raise _bad(code)
    return items


def _uuid(value: object, code: str = "malformed_admissibility_input") -> UUID:
    text = _str(value, code)
    try:
        parsed = UUID(text)
    except ValueError:
        raise _bad(code) from None
    if str(parsed) != text:
        raise _bad(code)
    return parsed


def _int(value: object, code: str = "malformed_admissibility_input") -> int:
    if type(value) is not int:
        raise _bad(code)
    return value


def _dec(value: object, code: str = "malformed_admissibility_input") -> Decimal:
    if type(value) is not str:
        raise _bad(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise _bad(code) from None
    if not parsed.is_finite():
        raise _bad(code)
    return parsed


# --------------------------------------------------------------------------
# Rules (D-2 digest)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdmissibilityRulesV1:
    rules_version: str
    decision_refs: tuple[str, ...]
    exact_opening_class: str
    matching_post_cause: str
    draft_allocation_post_causes: tuple[str, ...]
    code_policies: tuple[tuple[str, str], ...]
    closed_invoice_statuses: tuple[str, ...]
    open_invoice_statuses: tuple[str, ...]
    draft_status: str
    v1_require_lines: bool | None
    v1_line_tax_snapshot_fields: tuple[str, ...]
    require_originals_for_open_documents: bool
    hold_precedence: tuple[str, ...]

    def policy_of(self, code: str) -> str:
        for name, policy in self.code_policies:
            if name == code:
                return policy
        return "blocking"  # fail closed: an unlisted code is always blocking


_RULES_KEYS: Final = frozenset(
    {
        "schema",
        "rules_version",
        "decision_refs",
        "a10",
        "code_policies",
        "closed_invoice_statuses",
        "open_invoice_statuses",
        "drafts",
        "v1_gate",
        "originals",
        "hold_precedence",
    }
)


def parse_admissibility_rules(payload: object) -> AdmissibilityRulesV1:
    """Closed parse; unknown keys, unlisted precedence or bad policies refuse."""
    code = "malformed_rules"
    root = _obj(payload, _RULES_KEYS, code)
    if root["schema"] != ADMISSIBILITY_RULES_SCHEMA:
        raise _bad(code)
    a10 = _obj(
        root["a10"],
        frozenset(
            {
                "exact_opening_class",
                "matching_post_cause",
                "draft_allocation_post_causes",
            }
        ),
        code,
    )
    policies: list[tuple[str, str]] = []
    for entry in _list(root["code_policies"], code):
        row = _obj(entry, frozenset({"code", "policy"}), code)
        name, policy = _str(row["code"], code), _str(row["policy"], code)
        if policy not in _POLICIES or any(name == seen for seen, _p in policies):
            raise _bad(code)
        policies.append((name, policy))
    drafts = _obj(root["drafts"], frozenset({"draft_status"}), code)
    gate = root["v1_gate"]
    v1_lines: bool | None = None
    v1_fields: tuple[str, ...] = ()
    if gate is not None:
        row = _obj(gate, frozenset({"require_lines", "line_tax_snapshot_fields"}), code)
        v1_lines = _flag(row["require_lines"], code)
        v1_fields = _strs(row["line_tax_snapshot_fields"], code)
        if not v1_fields:
            raise _bad(code)
    originals = _obj(root["originals"], frozenset({"require_for_open_documents"}), code)
    precedence = _strs(root["hold_precedence"], code)
    if set(precedence) != HOLD_REASONS:
        raise _bad("rules_precedence_incomplete")
    closed = _strs(root["closed_invoice_statuses"], code)
    open_ = _strs(root["open_invoice_statuses"], code)
    if set(closed) & set(open_):
        raise _bad(code)
    return AdmissibilityRulesV1(
        _str(root["rules_version"], code),
        _strs(root["decision_refs"], code),
        _str(a10["exact_opening_class"], code),
        _str(a10["matching_post_cause"], code),
        _strs(a10["draft_allocation_post_causes"], code),
        tuple(policies),
        closed,
        open_,
        _str(drafts["draft_status"], code),
        v1_lines,
        v1_fields,
        _flag(originals["require_for_open_documents"], code),
        precedence,
    )


def _rules_wire(rules: AdmissibilityRulesV1) -> dict[str, object]:
    return {
        "rules_version": rules.rules_version,
        "decision_refs": list(rules.decision_refs),
        "a10": [
            rules.exact_opening_class,
            rules.matching_post_cause,
            list(rules.draft_allocation_post_causes),
        ],
        "code_policies": [list(p) for p in rules.code_policies],
        "closed": list(rules.closed_invoice_statuses),
        "open": list(rules.open_invoice_statuses),
        "draft_status": rules.draft_status,
        "v1": [rules.v1_require_lines, list(rules.v1_line_tax_snapshot_fields)],
        "originals": rules.require_originals_for_open_documents,
        "precedence": list(rules.hold_precedence),
    }


def admissibility_rules_digest(rules: AdmissibilityRulesV1) -> str:
    """Identity of the reviewed rules; it is neither approval nor authority."""
    return fingerprint_of(
        {
            "domain": "billing.opening.admissibility-rules.v1",
            "value": _rules_wire(rules),
        }
    )


# --------------------------------------------------------------------------
# A10 classes and reseller map
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class A10AccountClassV1:
    account_id: UUID
    opening_class: str
    post_cause: str


def parse_a10_classes(payload: object) -> tuple[A10AccountClassV1, ...]:
    """Per-account A10 classes only: no amounts are accepted or retained."""
    code = "malformed_a10_classes"
    root = _obj(payload, frozenset({"schema", "accounts"}), code)
    if root["schema"] != A10_CLASSES_SCHEMA:
        raise _bad(code)
    rows: list[A10AccountClassV1] = []
    seen: set[UUID] = set()
    for entry in _list(root["accounts"], code):
        row = _obj(
            entry, frozenset({"account_id", "opening_class", "post_cause"}), code
        )
        account = _uuid(row["account_id"], code)
        if account in seen:
            raise _bad("duplicate_a10_account")
        seen.add(account)
        rows.append(
            A10AccountClassV1(
                account, _str(row["opening_class"], code), _str(row["post_cause"], code)
            )
        )
    return tuple(sorted(rows, key=lambda r: str(r.account_id)))


def a10_classes_digest(rows: Sequence[A10AccountClassV1]) -> str:
    return fingerprint_of(
        {
            "domain": "billing.opening.a10-account-classes.v1",
            "value": [[str(r.account_id), r.opening_class, r.post_cause] for r in rows],
        }
    )


@dataclass(frozen=True, slots=True)
class ResellerSubscriberV1:
    subscriber_account_id: UUID
    default_service_ref: str | None
    invoice_service_refs: tuple[tuple[UUID, str], ...]


@dataclass(frozen=True, slots=True)
class ResellerMapV1:
    """Reseller option A (B3): reseller account -> its managed subscribers."""

    resellers: tuple[tuple[UUID, tuple[ResellerSubscriberV1, ...]], ...]

    def subscriber_index(self) -> dict[UUID, tuple[UUID, ResellerSubscriberV1]]:
        index: dict[UUID, tuple[UUID, ResellerSubscriberV1]] = {}
        for reseller, subscribers in self.resellers:
            for sub in subscribers:
                index[sub.subscriber_account_id] = (reseller, sub)
        return index


def parse_reseller_map(payload: object) -> ResellerMapV1:
    code = "malformed_reseller_map"
    root = _obj(payload, frozenset({"schema", "resellers"}), code)
    if root["schema"] != RESELLER_MAP_SCHEMA:
        raise _bad(code)
    resellers: list[tuple[UUID, tuple[ResellerSubscriberV1, ...]]] = []
    seen: set[UUID] = set()
    for entry in _list(root["resellers"], code):
        row = _obj(entry, frozenset({"reseller_account_id", "subscribers"}), code)
        reseller = _uuid(row["reseller_account_id"], code)
        subs: list[ResellerSubscriberV1] = []
        for item in _list(row["subscribers"], code):
            sub = _obj(
                item,
                frozenset(
                    {
                        "subscriber_account_id",
                        "default_service_ref",
                        "invoice_service_refs",
                    }
                ),
                code,
            )
            account = _uuid(sub["subscriber_account_id"], code)
            # A subscriber has one reseller, and a reseller is never its own
            # subscriber or another reseller's subscriber (no chains).
            if account in seen or account == reseller:
                raise _bad("reseller_map_ambiguous")
            seen.add(account)
            default = sub["default_service_ref"]
            refs: list[tuple[UUID, str]] = []
            for pair in _list(sub["invoice_service_refs"], code):
                ref = _obj(pair, frozenset({"invoice_id", "service_ref"}), code)
                refs.append(
                    (_uuid(ref["invoice_id"], code), _str(ref["service_ref"], code))
                )
            if len({i for i, _r in refs}) != len(refs):
                raise _bad("reseller_map_ambiguous")
            subs.append(
                ResellerSubscriberV1(
                    account,
                    None if default is None else _str(default, code),
                    tuple(sorted(refs, key=lambda r: str(r[0]))),
                )
            )
        resellers.append(
            (reseller, tuple(sorted(subs, key=lambda s: str(s.subscriber_account_id))))
        )
    reseller_ids = {r for r, _s in resellers}
    if len(reseller_ids) != len(resellers) or reseller_ids & seen:
        raise _bad("reseller_map_ambiguous")
    return ResellerMapV1(tuple(sorted(resellers, key=lambda r: str(r[0]))))


def reseller_map_digest(value: ResellerMapV1) -> str:
    return fingerprint_of(
        {
            "domain": "billing.opening.reseller-map.v1",
            "value": [
                [
                    str(reseller),
                    [
                        [
                            str(s.subscriber_account_id),
                            s.default_service_ref,
                            [[str(i), r] for i, r in s.invoice_service_refs],
                        ]
                        for s in subs
                    ],
                ]
                for reseller, subs in value.resellers
            ],
        }
    )


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartitionDecision:
    account_id: str
    currency: str
    admitted: bool
    reason: str | None  # exactly one hold reason, or None when admitted
    detail: tuple[str, ...]
    eligible_invoices: int
    member_accounts: tuple[str, ...]  # reseller-managed subscribers folded in
    findings: tuple[tuple[str, int], ...] = ()  # finding_only census codes (M8)
    route: str = "import"  # import | governed_correction
    rows: int = 0  # census invoice rows of the partition (routed-out only)
    lane_figures: tuple[tuple[str, str], ...] = ()  # stated lane money, routed-out
    census_codes: tuple[tuple[str, int], ...] = ()  # census exception codes


@dataclass(slots=True)
class AdmissibilityProjection:
    census: dict[str, object]
    decisions: tuple[PartitionDecision, ...]
    service_refs: dict[UUID, str]
    figures: dict[tuple[str, str], tuple[str, str | None]]
    archived_drafts: tuple[str, ...]
    archived_draft_total: str
    unattributed_controls: int
    a10_accounts_unused: int
    originals_evaluated: bool
    digests: dict[str, str] = field(default_factory=dict)

    @property
    def eligible_invoice_ids(self) -> tuple[str, ...]:
        return tuple(str(x) for x in _list(self.census["eligible_invoice_ids"]))

    def counts(self) -> dict[str, int]:
        by_reason = Counter(d.reason for d in self.decisions if not d.admitted)
        result = {
            "partitions": len(self.decisions),
            "admitted": sum(1 for d in self.decisions if d.admitted),
            "held": sum(1 for d in self.decisions if not d.admitted),
            "eligible_invoices": len(self.eligible_invoice_ids),
            "archived_drafts": len(self.archived_drafts),
            "unattributed_controls": self.unattributed_controls,
            "route:governed_correction": sum(
                1 for d in self.decisions if d.route == "governed_correction"
            ),
            "a10_accounts_unused": self.a10_accounts_unused,
        }
        for reason in sorted(HOLD_REASONS):
            result[f"hold:{reason}"] = by_reason.get(reason, 0)
        return result


def _fmt(value: Decimal, like: object = None) -> str:
    """Plain decimal text, keeping the scale of a sibling figure when given."""
    if isinstance(like, str):
        exponent = Decimal(like).as_tuple().exponent
        if isinstance(exponent, int) and exponent < 0:
            value = value.quantize(Decimal(1).scaleb(exponent))
    return format(value, "f")


def _zero_like(rows: Iterable[Decimal]) -> Decimal:
    return sum(rows, Decimal(0))


def census_measure_figures(
    census: Mapping[str, object], *, draft_status: str = "draft"
) -> dict[tuple[str, str], tuple[str, str | None]]:
    """Per (account, currency) document residual and stored balance, as exact text.

    Document residual sums the canonical residual over all non-draft documents
    (closed ones included); the stored balance sums the stored column, and is
    ``None`` when any row carries none (fail closed: no measures row follows).
    """
    docs: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for item in _list(census.get("invoices")):
        row = _obj(item)
        if row.get("status") == draft_status:
            continue
        key = (str(_uuid(row.get("account_id"))), _str(row.get("currency")))
        docs.setdefault(key, []).append(row)
    figures: dict[tuple[str, str], tuple[str, str | None]] = {}
    for key, rows in docs.items():
        doc_total = sum((_dec(r.get("residual")) for r in rows), Decimal(0))
        stored: Decimal | None = Decimal(0)
        for r in rows:
            if r.get("stored_residual") is None:
                stored = None
                break
            stored = (stored or Decimal(0)) + _dec(r["stored_residual"])
        figures[key] = (_fmt(doc_total), None if stored is None else _fmt(stored))
    return figures


def project_census(
    census: Mapping[str, object],
    *,
    rules: AdmissibilityRulesV1,
    a10: Sequence[A10AccountClassV1],
    reseller: ResellerMapV1 | None = None,
    original_invoice_ids: frozenset[UUID] | None = None,
) -> AdmissibilityProjection:
    """Evaluate the rules over a census and return the projected census.

    ``original_invoice_ids`` is the set of invoices with a supplied original;
    ``None`` means D-3 is not evaluated here (the drafter runs before originals
    exist) and the decision output says so. Deterministic: no clock, no I/O.
    """
    invoices_in = [_obj(x) for x in _list(census.get("invoices"))]
    controls_in = [_obj(x) for x in _list(census.get("controls"))]
    lanes_in = [_obj(x) for x in _list(census.get("lanes"))]
    exceptions_in = [_obj(x) for x in _list(census.get("blocking_exceptions"))]
    settlements_in = [_obj(x) for x in _list(census.get("settlements"))]
    exclusions = {
        _uuid(_obj(x).get("source_id")) for x in _list(census.get("exclusions"))
    }
    a10_by_account = {row.account_id: row for row in a10}

    # --- reseller option A re-keying (before evaluation) -------------------
    sub_index = reseller.subscriber_index() if reseller is not None else {}
    lane_rows: dict[tuple[UUID, str], Mapping[str, object]] = {}
    for lane in lanes_in:
        lane_rows[(_uuid(lane.get("account_id")), _str(lane.get("currency")))] = lane
    folded: dict[UUID, UUID] = {}  # subscriber -> reseller, only where mappable
    unmappable: dict[UUID, str] = {}
    for subscriber, (reseller_id, _sub) in sorted(sub_index.items(), key=str):
        currencies = {ccy for (acct, ccy) in lane_rows if acct == subscriber}
        ok = bool(currencies)
        for ccy in currencies:
            sub_lane = lane_rows[(subscriber, ccy)]
            res_lane = lane_rows.get((reseller_id, ccy))
            if (
                res_lane is None
                or sub_lane.get("authority") != "authoritative"
                or res_lane.get("authority") != "authoritative"
            ):
                ok = False
        if ok:
            folded[subscriber] = reseller_id
        else:
            unmappable[subscriber] = "reseller_lane_unavailable"

    def key_of(account: UUID) -> UUID:
        return folded.get(account, account)

    # --- index invoices (O(N)) --------------------------------------------
    by_id: dict[UUID, Mapping[str, object]] = {}
    for row in invoices_in:
        by_id[_uuid(row.get("invoice_id"))] = row
    open_statuses = frozenset(rules.open_invoice_statuses)

    # --- partition universe -------------------------------------------------
    PKey = tuple[UUID, str]
    members: dict[PKey, set[UUID]] = {}
    candidates: dict[PKey, list[UUID]] = {}
    drafts: list[str] = []
    invoice_partition: dict[UUID, PKey] = {}
    status_conflicts: dict[PKey, list[str]] = {}
    for invoice_id, row in by_id.items():
        account = _uuid(row.get("account_id"))
        currency = _str(row.get("currency"))
        pkey = (key_of(account), currency)
        invoice_partition[invoice_id] = pkey
        members.setdefault(pkey, set()).add(account)
        status = row.get("status")
        if status == rules.draft_status:
            drafts.append(str(invoice_id))
            continue
        residual = _dec(row.get("residual"))
        if residual > 0 and invoice_id not in exclusions:
            candidates.setdefault(pkey, []).append(invoice_id)
            if status not in open_statuses:
                status_conflicts.setdefault(pkey, []).append(str(invoice_id))
    for control in controls_in:
        if control.get("account_id") is None or control.get("currency") is None:
            continue
        account, currency = _uuid(control["account_id"]), _str(control["currency"])
        members.setdefault((key_of(account), currency), set()).add(account)
    for lane in lanes_in:
        account, currency = _uuid(lane["account_id"]), _str(lane["currency"])
        members.setdefault((key_of(account), currency), set()).add(account)
    unattributed = sum(
        1
        for c in controls_in
        if c.get("account_id") is None or c.get("currency") is None
    )

    # --- exceptions by partition -------------------------------------------
    census_codes: dict[PKey, Counter[str]] = {}
    blocking_codes: dict[PKey, set[str]] = {}
    finding_codes: dict[PKey, Counter[str]] = {}
    kept_exceptions: list[tuple[PKey | None, Mapping[str, object]]] = []
    finding_only = {c for c, policy in rules.code_policies if policy == "finding_only"}
    open_candidates = {i for ids in candidates.values() for i in ids}
    invoice_of_source: dict[UUID, UUID] = {}
    for settlement in settlements_in:
        invoice = _uuid(settlement.get("invoice_id"))
        for name in ("id", "source_id"):
            if settlement.get(name) is not None:
                invoice_of_source[_uuid(settlement[name])] = invoice
    for exc in exceptions_in:
        code = _str(exc.get("code"))
        if exc.get("account_id") is None or exc.get("currency") is None:
            kept_exceptions.append((None, exc))
            continue
        pkey = (key_of(_uuid(exc["account_id"])), _str(exc["currency"]))
        census_codes.setdefault(pkey, Counter())[code] += 1
        source = _uuid(exc["source_id"]) if exc.get("source_id") is not None else None
        owner = None if source is None else invoice_of_source.get(source, source)
        if (
            code in finding_only
            and owner is not None
            and owner in by_id
            and owner not in open_candidates
        ):
            finding_codes.setdefault(pkey, Counter())[code] += 1  # closed history
        else:
            blocking_codes.setdefault(pkey, set()).add(code)
        kept_exceptions.append((pkey, exc))

    # --- V1 gate lines -----------------------------------------------------
    lines_by_invoice: dict[UUID, list[Mapping[str, object]]] = {}
    documentary = census.get("documentary_observations")
    line_rows: Sequence[object] = []
    if isinstance(documentary, Mapping):
        line_rows = _list(documentary.get("current_lines", []))
    if not line_rows:
        line_rows = _list(census.get("lines", []))
    if rules.v1_require_lines is not None:
        for item in line_rows:
            lrow = _obj(item)
            if lrow.get("invoice_id") is not None:
                lines_by_invoice.setdefault(_uuid(lrow["invoice_id"]), []).append(lrow)

    def v1_missing(invoice_id: UUID) -> bool:
        lines = lines_by_invoice.get(invoice_id, [])
        if not lines:
            return bool(rules.v1_require_lines)
        return any(
            any(line.get(name) is None for name in rules.v1_line_tax_snapshot_fields)
            for line in lines
        )

    # --- reseller service refs ---------------------------------------------
    service_refs: dict[UUID, str] = {}
    reseller_gap: dict[PKey, list[str]] = {}
    candidate_ids = {i for ids in candidates.values() for i in ids}
    for invoice_id, pkey in invoice_partition.items():
        if invoice_id not in candidate_ids:
            continue  # only documents that may be imported need a service_ref
        account = _uuid(by_id[invoice_id].get("account_id"))
        entry = sub_index.get(account)
        if entry is None:
            continue
        if account in unmappable:
            reseller_gap.setdefault((account, pkey[1]), []).append(unmappable[account])
            continue
        sub = entry[1]
        per_invoice = dict(sub.invoice_service_refs)
        ref = per_invoice.get(invoice_id, sub.default_service_ref)
        if ref is None:
            reseller_gap.setdefault(pkey, []).append(f"service_ref:{invoice_id}")
        else:
            service_refs[invoice_id] = ref

    # --- lane totals per partition (folded) --------------------------------
    lane_total: dict[PKey, Decimal] = {}
    lane_present: set[PKey] = set()
    for (account, currency), lane in lane_rows.items():
        pkey = (key_of(account), currency)
        lane_present.add(pkey)
        lane_total[pkey] = lane_total.get(pkey, Decimal(0)) + _dec(
            lane.get("collectible_receivable")
        )

    # --- decide ------------------------------------------------------------
    decisions: list[PartitionDecision] = []
    admitted_candidates: set[UUID] = set()
    admitted: set[PKey] = set()
    for pkey in sorted(members, key=lambda k: (str(k[0]), k[1])):
        account, currency = pkey
        triggered: dict[str, tuple[str, ...]] = {}
        member_accounts = members[pkey]
        # Every member account (a reseller's folded subscribers included) must
        # pass the A10 rule; the failing member is named in the detail.
        a10_detail: dict[str, list[str]] = {}
        for member in sorted(member_accounts, key=str):
            a10_row = a10_by_account.get(member)
            if a10_row is None:
                a10_detail.setdefault("a10_missing", []).append(str(member))
                continue
            if a10_row.post_cause in rules.draft_allocation_post_causes:
                a10_detail.setdefault("draft_allocation", []).append(
                    f"{member}:{a10_row.post_cause}"
                )
            if a10_row.opening_class != rules.exact_opening_class:
                a10_detail.setdefault("a10_opening_not_exact", []).append(
                    f"{member}:{a10_row.opening_class}"
                )
            if (
                a10_row.post_cause != rules.matching_post_cause
                and a10_row.post_cause not in rules.draft_allocation_post_causes
            ):
                a10_detail.setdefault("a10_post_cutover_drift", []).append(
                    f"{member}:{a10_row.post_cause}"
                )
        for reason_code, detail in a10_detail.items():
            triggered[reason_code] = tuple(detail)
        cands = candidates.get(pkey, [])
        if status_conflicts.get(pkey):
            triggered["status_residual_conflict"] = tuple(
                sorted(status_conflicts[pkey])
            )
        lane_ok = pkey in lane_present and lane_total.get(
            pkey, Decimal(0)
        ) == _zero_like(_dec(by_id[i].get("residual")) for i in cands)
        if not lane_ok:
            triggered["lane_control_mismatch"] = ()
        blocking = set(blocking_codes.get(pkey, ()))
        if blocking:
            triggered["census_blocking_exception"] = tuple(sorted(blocking))
        if rules.v1_require_lines is not None:
            missing = sorted(str(i) for i in cands if v1_missing(i))
            if missing:
                triggered["v1_vat_evidence_missing"] = tuple(missing)
        if (
            rules.require_originals_for_open_documents
            and original_invoice_ids is not None
        ):
            absent = sorted(str(i) for i in cands if i not in original_invoice_ids)
            if absent:
                triggered["original_evidence_missing"] = tuple(absent)
        if reseller_gap.get(pkey) or any(m in unmappable for m in member_accounts):
            triggered["reseller_mapping_unavailable"] = tuple(
                sorted(set(reseller_gap.get(pkey, [])))
            )
        reason = next((r for r in rules.hold_precedence if r in triggered), None)
        if reason is None:
            admitted.add(pkey)
            admitted_candidates.update(cands)
            route = "import"
        else:
            route = "governed_correction"  # outside the manifest (Q1)
        routed_rows = 0
        figures_text: tuple[tuple[str, str], ...] = ()
        if reason is not None:
            routed_rows = sum(1 for p in invoice_partition.values() if p == pkey)
            figures_text = tuple(
                (
                    name,
                    _fmt(
                        sum(
                            (
                                _dec(lane_rows[(m, pkey[1])].get(name))
                                for m in sorted(member_accounts, key=str)
                                if (m, pkey[1]) in lane_rows
                            ),
                            Decimal(0),
                        )
                    ),
                )
                for name in _LANE_MONEY
            )
        decisions.append(
            PartitionDecision(
                str(account),
                currency,
                reason is None,
                reason,
                () if reason is None else triggered[reason][:20],
                len(cands) if reason is None else 0,
                tuple(sorted(str(m) for m in member_accounts if m != account)),
                tuple(sorted(finding_codes.get(pkey, Counter()).items())),
                route,
                routed_rows,
                figures_text,
                tuple(sorted(census_codes.get(pkey, Counter()).items()))
                if reason is not None
                else (),
            )
        )
    # --- projected census --------------------------------------------------
    # Only admitted partitions stay in the projected census; every other one is
    # routed out (listed in the decision record, never dropped). Drafts are
    # removed and archived (X1); controls are reduced by exactly what was
    # removed, so the source's own control totals stay an independent check.
    kept = admitted
    out: dict[str, object] = dict(census)
    kept_ids: set[UUID] = set()
    new_invoices: list[dict[str, object]] = []
    draft_by_partition: dict[PKey, list[UUID]] = {}
    for row in invoices_in:
        invoice_id = _uuid(row.get("invoice_id"))
        pkey = invoice_partition[invoice_id]
        if pkey not in kept:
            continue
        if row.get("status") == rules.draft_status:
            draft_by_partition.setdefault(pkey, []).append(invoice_id)
            continue
        copy = dict(row)
        copy["account_id"] = str(pkey[0])
        new_invoices.append(copy)
        kept_ids.add(invoice_id)
    out["invoices"] = new_invoices
    out["eligible_invoice_ids"] = sorted(str(i) for i in admitted_candidates)
    out["exclusions"] = [
        x
        for x in map(_obj, _list(census.get("exclusions")))
        if _uuid(x.get("source_id")) in kept_ids
    ]
    out["settlements"] = [
        x for x in settlements_in if _uuid(x["invoice_id"]) in kept_ids
    ]
    if isinstance(documentary, Mapping):
        out["documentary_observations"] = {
            **documentary,
            **{
                name: [
                    x
                    for x in map(_obj, _list(documentary.get(name, [])))
                    if _uuid(x.get("invoice_id")) in kept_ids
                ]
                for name in (
                    "current_invoices",
                    "current_lines",
                    "unavailable_original_evidence",
                )
                if name in documentary
            },
        }
    if census.get("lines"):
        out["lines"] = [
            x
            for x in map(_obj, _list(census.get("lines")))
            if x.get("invoice_id") is None or _uuid(x["invoice_id"]) in kept_ids
        ]

    elig_count: Counter[PKey] = Counter()
    elig_residual: dict[PKey, Decimal] = {}
    for invoice_id in admitted_candidates:
        pkey = invoice_partition[invoice_id]
        elig_count[pkey] += 1
        elig_residual[pkey] = elig_residual.get(pkey, Decimal(0)) + _dec(
            by_id[invoice_id].get("residual")
        )
    removed = {
        pkey: [by_id[i] for i in ids] for pkey, ids in draft_by_partition.items()
    }
    # Measure figures (census-derived, exact): document residual over all
    # non-draft documents (closed ones included) and the stored balance column.
    # ``None`` stored balance: the census row carries no stored figure (fail
    # closed, no measures row is produced for it).
    figures: dict[tuple[str, str], tuple[str, str | None]] = {}
    for pkey in admitted:
        docs = [
            by_id[i]
            for i, p in invoice_partition.items()
            if p == pkey and by_id[i].get("status") != rules.draft_status
        ]
        doc_total = sum((_dec(d.get("residual")) for d in docs), Decimal(0))
        stored_total: Decimal | None = Decimal(0)
        for d in docs:
            if d.get("stored_residual") is None:
                stored_total = None
                break
            stored_total = (stored_total or Decimal(0)) + _dec(d["stored_residual"])
        figures[(str(pkey[0]), pkey[1])] = (
            _fmt(doc_total),
            None if stored_total is None else _fmt(stored_total),
        )

    folded_controls: dict[PKey, dict[str, object]] = {}
    other_controls: list[dict[str, object]] = []
    with localcontext() as ctx:
        ctx.prec = 60
        for control in controls_in:
            if control.get("account_id") is None or control.get("currency") is None:
                other_controls.append(dict(control))
                continue
            account, currency = _uuid(control["account_id"]), _str(control["currency"])
            pkey = (key_of(account), currency)
            if pkey not in kept:
                continue
            if pkey not in folded_controls:
                base = dict(control)
                base["account_id"] = str(pkey[0])
                folded_controls[pkey] = base
                continue
            target = folded_controls[pkey]
            for name in _CONTROL_SUMS:
                target[name] = _int(target[name]) + _int(control[name])
            for name in _CONTROL_MONEY:
                target[name] = _fmt(_dec(target[name]) + _dec(control[name]))
        for pkey, row in folded_controls.items():
            for draft in removed.get(pkey, ()):
                row["invoice_count"] = _int(row["invoice_count"]) - 1
                if _uuid(draft.get("invoice_id")) in exclusions:
                    row["excluded_count"] = _int(row["excluded_count"]) - 1
                row["original_total"] = _fmt(
                    _dec(row["original_total"]) - _dec(draft.get("total"))
                )
                row["canonical_discharge"] = _fmt(
                    _dec(row["canonical_discharge"])
                    - _dec(draft.get("canonical_discharge"))
                )
                row["residual"] = _fmt(
                    _dec(row["residual"]) - _dec(draft.get("residual"))
                )
            row["eligible_count"] = elig_count[pkey]
            row["eligible_residual"] = _fmt(
                elig_residual.get(pkey, Decimal(0)), row["residual"]
            )
            row["exception_count"] = 0
        out["controls"] = [
            folded_controls[k]
            for k in sorted(folded_controls, key=lambda k: (str(k[0]), k[1]))
        ] + other_controls

        new_lanes: dict[PKey, dict[str, object]] = {}
        ordered_lanes = sorted(
            lane_rows.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])
        )
        for (account, currency), lane in ordered_lanes:
            if key_of(account) == account and (account, currency) in kept:
                new_lanes[(account, currency)] = dict(lane)
        for (account, currency), lane in ordered_lanes:
            pkey = (key_of(account), currency)
            if pkey[0] == account or pkey not in kept:
                continue
            target = new_lanes[pkey]  # folded only when the reseller lane exists
            for name in _LANE_MONEY:
                target[name] = _fmt(_dec(target[name]) + _dec(lane[name]))
            target["posting_group_count"] = _int(target["posting_group_count"]) + _int(
                lane["posting_group_count"]
            )
        out["lanes"] = [
            new_lanes[k] for k in sorted(new_lanes, key=lambda k: (str(k[0]), k[1]))
        ]

    # A finding about nobody in particular stays visible for the manual review.
    out["blocking_exceptions"] = [
        dict(exc) for pkey, exc in kept_exceptions if pkey is None
    ]
    all_members = {m for ms in members.values() for m in ms}
    unused = len(set(a10_by_account) - all_members)
    digests = {
        "rules": admissibility_rules_digest(rules),
        "a10_classes": a10_classes_digest(sorted(a10, key=lambda r: str(r.account_id))),
    }
    if reseller is not None:
        digests["reseller_map"] = reseller_map_digest(reseller)
    return AdmissibilityProjection(
        census=out,
        decisions=tuple(decisions),
        service_refs={
            i: r for i, r in service_refs.items() if i in admitted_candidates
        },
        figures=figures,
        archived_draft_total=_fmt(
            sum((_dec(by_id[UUID(d)].get("total")) for d in drafts), Decimal(0))
        ),
        archived_drafts=tuple(sorted(drafts)),
        unattributed_controls=unattributed,
        a10_accounts_unused=unused,
        originals_evaluated=original_invoice_ids is not None,
        digests=digests,
    )


def render_admissibility_decisions(projection: AdmissibilityProjection) -> str:
    """Deterministic decision record: ids, reasons, routes and the
    exact archived total Finance attests."""
    document = {
        "schema": _DECISIONS_SCHEMA,
        "digests": projection.digests,
        "originals_evaluated": projection.originals_evaluated,
        "counts": projection.counts(),
        "archived_drafts": list(projection.archived_drafts),
        # Finance attestation figure (X1): exact decimal.
        "archived_draft_total": projection.archived_draft_total,
        "decisions": [
            {
                "account_id": d.account_id,
                "currency": d.currency,
                "admitted": d.admitted,
                "reason": d.reason,
                "detail": list(d.detail),
                "eligible_invoices": d.eligible_invoices,
                "member_accounts": list(d.member_accounts),
                "findings": dict(d.findings),
                "route": d.route,
                "rows": d.rows,
                "lane_figures": dict(d.lane_figures),
                "census_codes": dict(d.census_codes),
            }
            for d in projection.decisions
        ],
    }
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
