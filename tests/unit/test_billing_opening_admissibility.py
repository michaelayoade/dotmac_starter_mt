"""Opening admissibility is rules-as-data with exactly one reason per hold."""

from __future__ import annotations

import copy
from uuid import UUID, uuid5

import pytest
from dotmac_billing.opening_admissibility import (
    HOLD_REASONS,
    AdmissibilityError,
    parse_a10_classes,
    parse_admissibility_rules,
    parse_reseller_map,
    project_census,
    render_admissibility_decisions,
)

NS = UUID("00000000-0000-0000-0000-0000000000f1")


def uid(name: str) -> str:
    return str(uuid5(NS, name))


PRECEDENCE = [
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
]


def rules_doc(**over):
    doc = {
        "schema": "billing.opening.admissibility-rules.v1",
        "rules_version": "fixture-rules:1",
        "decision_refs": ["D-2", "M8"],
        "a10": {
            "exact_opening_class": "opening_exact",
            "matching_post_cause": "match",
            "draft_allocation_post_causes": ["known:stale_draft_allocation"],
        },
        "code_policies": [],
        "closed_invoice_statuses": ["paid", "written_off", "void"],
        "open_invoice_statuses": ["issued", "overdue", "partially_paid"],
        "drafts": {"draft_status": "draft"},
        "v1_gate": {
            "require_lines": True,
            "line_tax_snapshot_fields": [
                "tax_rate_code_snapshot",
                "tax_rate_percent_snapshot",
                "tax_rate_snapshot_version",
                "tax_application",
            ],
        },
        "originals": {"require_for_open_documents": True},
        "hold_precedence": PRECEDENCE,
    }
    doc.update(over)
    return doc


def rules(**over):
    return parse_admissibility_rules(rules_doc(**over))


def inv(name, account, status, total="100.00", residual="0.00"):
    return {
        "invoice_id": uid(name),
        "account_id": uid(account),
        "currency": "NGN",
        "status": status,
        "total": total,
        "residual": residual,
        "canonical_discharge": f"{float(total) - float(residual):.2f}",
        "settlement_ids": [],
    }


def lane(account, receivable="0.00", credit="0.00"):
    return {
        "account_id": uid(account),
        "currency": "NGN",
        "authority": "authoritative",
        "fingerprint": f"lane:{account}",
        "posting_group_count": 1,
        "collectible_receivable": receivable,
        "unapplied_customer_credit": credit,
        "prepaid_funding_reserved": "0.00",
        "prepaid_funding_consumed": "0.00",
        "written_off_total": "0.00",
        "refunded_total": "0.00",
        "adjustment_total": "0.00",
        "authority_cutover_id": uid("cutover"),
        "adjustment_total_ref": None,
    }


def control(account, invoices, residual="0.00"):
    return {
        "account_id": uid(account),
        "currency": "NGN",
        "invoice_count": invoices,
        "eligible_count": 0,
        "excluded_count": 0,
        "exception_count": 0,
        "original_total": "0.00",
        "canonical_discharge": "0.00",
        "residual": residual,
        "eligible_residual": "0.00",
        "source_count": 0,
    }


def exc(account, code, source):
    return {
        "account_id": uid(account),
        "currency": "NGN",
        "code": code,
        "source_id": uid(source),
        "detail": "x",
    }


def census(**over):
    base = {
        "invoices": [
            inv("i-open", "a", "issued", residual="60.00"),
            inv("i-closed", "a", "paid"),
        ],
        "controls": [control("a", 2, "60.00")],
        "lanes": [lane("a", "60.00")],
        "blocking_exceptions": [],
        "exclusions": [],
        "settlements": [],
        "eligible_invoice_ids": [],
        "documentary_observations": {
            "current_lines": [
                {
                    "invoice_id": uid("i-open"),
                    "tax_rate_snapshot_version": "v1",
                    "tax_rate_code_snapshot": "VAT",
                    "tax_rate_percent_snapshot": "7.50",
                    "tax_application": "exclusive",
                }
            ],
            "current_invoices": [],
            "unavailable_original_evidence": [],
        },
    }
    base.update(over)
    return base


def a10(*rows):
    return parse_a10_classes(
        {
            "schema": "billing.opening.a10-account-classes.v1",
            "accounts": [
                {"account_id": uid(a), "opening_class": o, "post_cause": p}
                for a, o, p in rows
            ],
        }
    )


EXACT = ("a", "opening_exact", "match")
ORIGINALS = frozenset({UUID(uid("i-open"))})


def run(c=None, r=None, classes=None, **kw):
    kw.setdefault("original_invoice_ids", ORIGINALS)
    return project_census(
        c if c is not None else census(),
        rules=r or rules(),
        a10=classes if classes is not None else a10(EXACT),
        **kw,
    )


def only(projection):
    assert len(projection.decisions) == 1
    return projection.decisions[0]


# -- rules as data ------------------------------------------------------------


def test_rules_are_a_closed_document_with_a_stable_digest():
    from dotmac_billing.opening_admissibility import admissibility_rules_digest

    assert admissibility_rules_digest(rules()) == admissibility_rules_digest(rules())
    changed = rules(rules_version="fixture-rules:2")
    assert admissibility_rules_digest(changed) != admissibility_rules_digest(rules())
    with pytest.raises(AdmissibilityError):
        parse_admissibility_rules({**rules_doc(), "extra": 1})
    with pytest.raises(AdmissibilityError):
        parse_admissibility_rules(
            rules_doc(code_policies=[{"code": "x", "policy": "ignore"}])
        )


def test_the_precedence_list_must_rank_every_hold_reason():
    assert set(PRECEDENCE) == HOLD_REASONS
    with pytest.raises(AdmissibilityError) as info:
        parse_admissibility_rules(rules_doc(hold_precedence=PRECEDENCE[:-1]))
    assert info.value.code == "rules_precedence_incomplete"


# -- D-2 ---------------------------------------------------------------------


def test_a10_exact_and_post_match_admits_and_only_status_open_is_eligible():
    result = run()
    assert only(result).admitted and only(result).reason is None
    assert result.census["eligible_invoice_ids"] == [uid("i-open")]
    ctl = result.census["controls"][0]
    assert (ctl["eligible_count"], ctl["eligible_residual"]) == (1, "60.00")
    assert ctl["exception_count"] == 0


def test_every_hold_has_exactly_one_reason():
    cases = [
        (a10(("a", "opening_gap_other", "match")), "a10_opening_not_exact"),
        (a10(("a", "opening_exact", "other")), "a10_post_cutover_drift"),
        (a10(), "a10_missing"),
        (
            a10(("a", "opening_exact", "known:stale_draft_allocation")),
            "draft_allocation",
        ),
    ]
    for classes, reason in cases:
        decision = only(run(classes=classes))
        assert (decision.admitted, decision.reason) == (False, reason)


def test_precedence_picks_one_reason_when_several_apply():
    classes = a10(("a", "opening_gap_other", "known:stale_draft_allocation"))
    assert only(run(classes=classes)).reason == "draft_allocation"
    reordered = rules(
        hold_precedence=[
            "a10_opening_not_exact",
            *[p for p in PRECEDENCE if p != "a10_opening_not_exact"],
        ]
    )
    assert only(run(r=reordered, classes=classes)).reason == "a10_opening_not_exact"


def test_no_census_code_is_relaxed_on_any_invoice():
    for source in ("i-closed", "i-open"):
        for code in ("settlement_provenance", "excluded_nonzero_residue"):
            held = only(run(census(blocking_exceptions=[exc("a", code, source)])))
            assert (held.admitted, held.reason, held.detail) == (
                False,
                "census_blocking_exception",
                (code,),
            )


def test_a_relaxing_policy_is_refused_by_the_closed_rules_parse():
    for policy in ("informational_on_closed_invoice", "informational_when_a10_exact"):
        with pytest.raises(AdmissibilityError) as info:
            rules(code_policies=[{"code": "residual_mismatch", "policy": policy}])
        assert info.value.code == "malformed_rules"


def test_an_unlisted_code_and_an_unresolvable_source_stay_blocking():
    res = run(census(blocking_exceptions=[exc("a", "residual_mismatch", "i-closed")]))
    assert only(res).reason == "census_blocking_exception"
    ghost = census(blocking_exceptions=[exc("a", "settlement_provenance", "ghost")])
    assert only(run(ghost)).reason == "census_blocking_exception"


def test_a_non_admitted_partition_is_routed_out_and_listed_with_one_reason():
    """Q1: no manifest-marked hold exists; the partition leaves the manifest and
    the decision record lists its rows, stated lane figures and census codes."""
    routed = run(classes=a10(("a", "opening_gap_other", "match")))
    decision = only(routed)
    assert (decision.admitted, decision.reason) == (False, "a10_opening_not_exact")
    assert decision.route == "governed_correction"
    assert routed.census["eligible_invoice_ids"] == []
    assert routed.census["invoices"] == []
    assert routed.census["controls"] == []
    assert decision.rows == 2  # i-open and i-closed are listed, never dropped
    assert dict(decision.lane_figures)["collectible_receivable"]
    assert routed.counts()["route:governed_correction"] == 1
    assert not hasattr(routed, "held_invoice_reasons")


def test_a_structural_hold_stays_out_with_a_governed_correction_route():
    held = run(census(lanes=[lane("a", "59.00")]))
    decision = only(held)
    assert (decision.reason, decision.route) == (
        "lane_control_mismatch",
        "governed_correction",
    )
    assert held.census["invoices"] == []


def test_lane_receivable_must_equal_the_admitted_residual():
    c = census(lanes=[lane("a", "59.00")])
    assert only(run(c)).reason == "lane_control_mismatch"


def test_open_status_conflict_holds():
    c = census(invoices=[inv("i-odd", "a", "paid", residual="5.00")])
    c["lanes"] = [lane("a", "5.00")]
    assert only(run(c)).reason == "status_residual_conflict"


# -- X1 ----------------------------------------------------------------------


def test_drafts_are_excluded_and_archived_and_never_eligible():
    c = census()
    c["invoices"].append(inv("d-1", "a", "draft", residual="30.00"))
    c["exclusions"] = [{"source_id": uid("d-1"), "reason": "draft"}]
    c["controls"] = [control("a", 3, "90.00")]
    c["controls"][0].update(excluded_count=1, original_total="300.00")
    c["controls"][0]["canonical_discharge"] = "170.00"
    result = run(c)
    assert uid("d-1") not in result.census["eligible_invoice_ids"]
    assert result.archived_drafts == (uid("d-1"),)
    assert only(result).admitted
    # removed from the population; the source control is reduced by exactly it
    assert {x["invoice_id"] for x in result.census["invoices"]} == {
        uid("i-open"),
        uid("i-closed"),
    }
    assert result.census["exclusions"] == []
    ctl = result.census["controls"][0]
    assert (ctl["invoice_count"], ctl["excluded_count"]) == (2, 0)
    assert (ctl["original_total"], ctl["canonical_discharge"], ctl["residual"]) == (
        "200.00",
        "100.00",
        "60.00",
    )
    # even without a Sub exclusion row a draft is never a candidate
    c["exclusions"] = []
    assert uid("d-1") not in run(c).census["eligible_invoice_ids"]


# -- V1 and D-3 ----------------------------------------------------------------


def test_v1_gates_only_status_open_documents():
    no_tax = census()
    no_tax["documentary_observations"]["current_lines"][0][
        "tax_rate_snapshot_version"
    ] = None
    held = only(run(no_tax))
    assert (held.reason, held.detail) == ("v1_vat_evidence_missing", (uid("i-open"),))
    no_lines = census()
    no_lines["documentary_observations"]["current_lines"] = []
    assert only(run(no_lines)).reason == "v1_vat_evidence_missing"
    # a closed invoice without any lines is never gated
    only_closed = census(invoices=[inv("i-closed", "a", "paid")], lanes=[lane("a")])
    assert only(run(only_closed, original_invoice_ids=frozenset())).admitted
    disabled = rules(v1_gate=None)
    assert only(run(no_lines, r=disabled)).admitted


def test_d3_requires_a_supplied_original_for_each_open_document():
    assert only(run(original_invoice_ids=frozenset())).reason == (
        "original_evidence_missing"
    )
    # drafter path: originals are not known yet, and the output says so
    pending = run(original_invoice_ids=None)
    assert only(pending).admitted and pending.originals_evaluated is False


# -- reseller option A -----------------------------------------------------------


def reseller_map(default="svc-1", overrides=()):
    return parse_reseller_map(
        {
            "schema": "billing.opening.reseller-map.v1",
            "resellers": [
                {
                    "reseller_account_id": uid("r"),
                    "subscribers": [
                        {
                            "subscriber_account_id": uid("a"),
                            "default_service_ref": default,
                            "invoice_service_refs": [
                                {"invoice_id": uid(i), "service_ref": s}
                                for i, s in overrides
                            ],
                        }
                    ],
                }
            ],
        }
    )


def reseller_census():
    c = census()
    c["lanes"] = [lane("a", "60.00", credit="7.00"), lane("r", "0.00", credit="3.00")]
    c["controls"] = [control("a", 2, "60.00"), control("r", 0)]
    return c


RES_CLASSES = a10(EXACT, ("r", "opening_exact", "match"))


def test_reseller_subscriber_invoices_and_wallet_map_to_the_reseller_account():
    result = run(reseller_census(), classes=RES_CLASSES, reseller=reseller_map())
    decisions = {d.account_id: d for d in result.decisions}
    assert uid("a") not in decisions  # folded away
    reseller = decisions[uid("r")]
    assert reseller.admitted and reseller.member_accounts == (uid("a"),)
    assert result.service_refs == {UUID(uid("i-open")): "svc-1"}
    lanes = {x["account_id"]: x for x in result.census["lanes"]}
    assert list(lanes) == [uid("r")]
    assert lanes[uid("r")]["unapplied_customer_credit"] == "10.00"
    assert lanes[uid("r")]["collectible_receivable"] == "60.00"
    owners = {x["invoice_id"]: x["account_id"] for x in result.census["invoices"]}
    assert set(owners.values()) == {uid("r")}
    ctl = result.census["controls"][0]
    assert (ctl["invoice_count"], ctl["eligible_count"]) == (2, 1)


def test_one_service_ref_per_document_with_per_invoice_override():
    mapping = reseller_map(overrides=[("i-open", "svc-9")])
    result = run(reseller_census(), classes=RES_CLASSES, reseller=mapping)
    assert result.service_refs == {UUID(uid("i-open")): "svc-9"}


def test_a_missing_service_ref_holds_the_reseller_partition():
    mapping = reseller_map(default=None)
    result = run(reseller_census(), classes=RES_CLASSES, reseller=mapping)
    decision = only(
        type(
            "P",
            (),
            {"decisions": [d for d in result.decisions if d.account_id == uid("r")]},
        )
    )
    assert decision.reason == "reseller_mapping_unavailable"


def test_a_subscriber_without_a_reseller_lane_is_held_not_folded():
    c = census()  # no reseller lane at all
    result = run(c, classes=RES_CLASSES, reseller=reseller_map())
    decision = only(result)
    assert (decision.account_id, decision.reason) == (
        uid("a"),
        "reseller_mapping_unavailable",
    )


def test_reseller_map_refuses_ambiguity():
    base = {
        "schema": "billing.opening.reseller-map.v1",
        "resellers": [
            {
                "reseller_account_id": uid("r"),
                "subscribers": [
                    {
                        "subscriber_account_id": uid("r"),
                        "default_service_ref": "s",
                        "invoice_service_refs": [],
                    }
                ],
            }
        ],
    }
    with pytest.raises(AdmissibilityError):
        parse_reseller_map(base)


# -- determinism ------------------------------------------------------------------


def test_projection_is_deterministic_and_does_not_mutate_its_input():
    c = reseller_census()
    snapshot = copy.deepcopy(c)
    first = run(c, classes=RES_CLASSES, reseller=reseller_map())
    second = run(c, classes=RES_CLASSES, reseller=reseller_map())
    assert c == snapshot
    assert first.census == second.census
    assert render_admissibility_decisions(first) == render_admissibility_decisions(
        second
    )
    assert "amount" not in render_admissibility_decisions(first)


def test_a10_input_carries_no_amounts_and_refuses_duplicates():
    with pytest.raises(AdmissibilityError):
        parse_a10_classes(
            {
                "schema": "billing.opening.a10-account-classes.v1",
                "accounts": [
                    {
                        "account_id": uid("a"),
                        "opening_class": "x",
                        "post_cause": "y",
                        "net": "1",
                    }
                ],
            }
        )
    with pytest.raises(AdmissibilityError) as info:
        a10(EXACT, EXACT)
    assert info.value.code == "duplicate_a10_account"


def test_an_excluded_residue_is_never_dropped_it_holds_the_partition():
    for residual in ("5.00", "-5.00"):
        c = census()
        c["invoices"].append(inv("v-1", "a", "void", residual=residual))
        c["exclusions"] = [{"source_id": uid("v-1"), "reason": "void"}]
        c["blocking_exceptions"] = [exc("a", "excluded_nonzero_residue", "v-1")]
        result = run(c)
        assert only(result).reason == "census_blocking_exception"
        assert "dropped_residue_total" not in render_admissibility_decisions(result)


def test_every_member_of_a_reseller_partition_must_pass_a10():
    mapping = reseller_map()
    ok = run(reseller_census(), classes=RES_CLASSES, reseller=mapping)
    assert [d.admitted for d in ok.decisions] == [True]
    bad = a10(("a", "opening_gap_other", "match"), ("r", "opening_exact", "match"))
    held = run(reseller_census(), classes=bad, reseller=mapping)
    decision = only(held)
    assert (decision.account_id, decision.reason) == (uid("r"), "a10_opening_not_exact")
    assert decision.detail == (f"{uid('a')}:opening_gap_other",)
    missing = a10(("r", "opening_exact", "match"))
    decision = only(run(reseller_census(), classes=missing, reseller=mapping))
    assert (decision.reason, decision.detail) == ("a10_missing", (uid("a"),))
    # a reseller's folded subscribers are not "unused" A10 accounts
    assert ok.a10_accounts_unused == 0


def test_residual_mismatch_blocks_even_when_a10_is_exact():
    mismatch = census(blocking_exceptions=[exc("a", "residual_mismatch", "i-open")])
    assert only(run(mismatch)).reason == "census_blocking_exception"
