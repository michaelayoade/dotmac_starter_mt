"""The declared verification vocabulary and the checks, connected in both directions.

## What was actually wrong

Not "three of the seven declared verifications go unperformed". `verify_recovery`
never received the declared list AT ALL — it compared what it knew how to
compare, regardless of what the descriptor asked for. `BackupDataset.verify`
reached only descriptive surfaces and `accept_external_recovery_receipt`, which
judges an EXTERNAL party's claims. On the internally executed path the
declaration and the check were never connected, and three names happening to be
unperformed was a symptom rather than the defect.

A vocabulary nothing consumes cannot be wrong. That is why nobody noticed it was.

## Both directions, because the mirror is the invisible one

- **declared and not performed** — a descriptor requires something nothing does.
  `schema` is the measured case: `spec.py` makes it MANDATORY at parse for every
  postgres dataset, with the reason in its own refusal text, and nothing compared
  `CatalogEvidence.schemas`.
- **performed and not declarable** — the check WORKS, so nothing looks wrong. But
  no descriptor can require it and no external receipt can claim it, so an
  externally executed restore can claim every declarable name while never having
  looked at row security. Nobody reports a check that passes.

## The positive control is the load-bearing test in this file

`test_every_registered_checker_fires_on_a_planted_difference` plants a defect for
EVERY name in the registry and requires each to produce a finding. Without it,
the two set-equality tests above are satisfied by a registry of checkers that
return `[]` unconditionally — a vocabulary connected to twelve functions that do
nothing, which reads better than the defect it replaced and is worse.
"""

from __future__ import annotations

import pytest
from dotmac_deployment_foundation import recovery as R
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.spec import (
    UNPERFORMABLE_VERIFICATION,
    BackupDataset,
    ProductDeploymentSpec,
)

DECLARABLE = frozenset(BackupDataset.VERIFICATIONS)
PERFORMED = frozenset(R.VERIFICATION_CHECKS)


class _Manifest:
    """The only two manifest facts any checker reads."""

    role_closure: frozenset[str] = frozenset()
    migration_heads: tuple[str, ...] = ()


def _verify(source: R.CatalogEvidence, restored: R.CatalogEvidence, **kw: object):
    return R.verify_recovery(
        manifest=_Manifest(),  # type: ignore[arg-type]
        source=source,
        restored=restored,
        **kw,  # type: ignore[arg-type]
    )


# ── the two directions ──────────────────────────────────────────────────────


def test_every_declarable_verification_is_performed_or_declared_external_only() -> None:
    """Exact equality, so it fails in BOTH directions: a newly unperformed name
    fails, and a name that becomes performable fails until it is removed from
    the external-only set."""
    assert DECLARABLE - PERFORMED == set(R.EXTERNAL_ONLY_VERIFICATIONS), (
        "a descriptor can declare a verification nothing performs, or a name "
        "became performable and is still listed as external-only. Update "
        "VERIFICATION_CHECKS or EXTERNAL_ONLY_VERIFICATIONS"
    )


def test_every_performed_comparison_is_declarable_or_frozen_debt() -> None:
    """THE MIRROR. A comparison nothing can declare is a check no descriptor can
    require and no external receipt can claim — invisible precisely because it
    works. Exact equality again: adding an undeclarable comparison fails, and
    retiring one into the declarable vocabulary fails until this set shrinks."""
    assert PERFORMED - DECLARABLE == set(R.UNDECLARED_COMPARISONS), (
        "a comparison is performed that no descriptor can declare. Retiring one "
        "means adding it to BackupDataset.VERIFICATIONS *and* to "
        "external_recovery.VERIFICATION_EVIDENCE, which is read across a "
        "repository boundary — a contract change, not a line in this file"
    )


def test_the_two_frozen_sets_do_not_overlap() -> None:
    """A name cannot be both unperformable here and performed here."""
    assert not (set(R.EXTERNAL_ONLY_VERIFICATIONS) & set(R.UNDECLARED_COMPARISONS))


def test_the_order_and_the_registry_are_the_same_set() -> None:
    """A checker missing from the order never runs; a name in the order with no
    checker raises at the first call. Either is a registry that lies."""
    assert set(R.VERIFICATION_ORDER) == PERFORMED
    assert len(R.VERIFICATION_ORDER) == len(set(R.VERIFICATION_ORDER))


def test_the_sets_are_not_vacuously_satisfied() -> None:
    """Every equality above holds trivially over empty sets."""
    assert len(DECLARABLE) >= 6
    assert len(PERFORMED) >= 10
    assert R.EXTERNAL_ONLY_VERIFICATIONS
    assert R.UNDECLARED_COMPARISONS


# ── the positive control ────────────────────────────────────────────────────

#: One planted difference per registered checker: (source, restored, extra kwargs).
PLANTED: dict[str, tuple[R.CatalogEvidence, R.CatalogEvidence, dict]] = {
    "roles": (
        R.CatalogEvidence(
            roles=(R.RoleFact("app", True, True, False, False, False, False, False),)
        ),
        R.CatalogEvidence(
            roles=(R.RoleFact("app", True, False, False, False, False, False, False),)
        ),
        {},
    ),
    "memberships": (
        R.CatalogEvidence(memberships=(R.MembershipFact("app", "readers"),)),
        R.CatalogEvidence(),
        {},
    ),
    "ownership": (
        R.CatalogEvidence(ownership=(R.OwnershipFact("table", "t", "app"),)),
        R.CatalogEvidence(ownership=(R.OwnershipFact("table", "t", "postgres"),)),
        {},
    ),
    "direct_privileges": (
        R.CatalogEvidence(privileges=(R.PrivilegeFact("table", "t", "app", "SELECT"),)),
        R.CatalogEvidence(),
        {},
    ),
    "effective_privileges": (
        R.CatalogEvidence(
            effective_privileges=(
                R.EffectivePrivilegeFact("app", "t", "SELECT", False),
            )
        ),
        R.CatalogEvidence(
            effective_privileges=(R.EffectivePrivilegeFact("app", "t", "SELECT", True),)
        ),
        {},
    ),
    "default_privileges": (
        R.CatalogEvidence(
            default_privileges=(
                R.DefaultPrivilegeFact("app", "public", "TABLES", "readers", "SELECT"),
            )
        ),
        R.CatalogEvidence(),
        {},
    ),
    "row_security": (
        R.CatalogEvidence(row_security=(R.RlsFact("t", True, True),)),
        R.CatalogEvidence(row_security=(R.RlsFact("t", True, False),)),
        {},
    ),
    "security_definer_routines": (
        R.CatalogEvidence(
            functions=(R.FunctionSecurityFact("f()", "app", True, False),)
        ),
        R.CatalogEvidence(),
        {},
    ),
    "schema": (
        R.CatalogEvidence(schemas=("public", "mod_x")),
        R.CatalogEvidence(schemas=("public",)),
        {},
    ),
    "extensions": (
        R.CatalogEvidence(extensions=(R.ExtensionFact("pgcrypto", "1.3"),)),
        R.CatalogEvidence(),
        {},
    ),
    "migration_heads": (
        R.CatalogEvidence(migration_heads=("a003",)),
        R.CatalogEvidence(migration_heads=("a002",)),
        {},
    ),
    "isolation_invariants": (
        R.CatalogEvidence(
            effective_privileges=(
                R.EffectivePrivilegeFact("stranger", "t", "SELECT", False),
            )
        ),
        R.CatalogEvidence(
            effective_privileges=(
                R.EffectivePrivilegeFact("stranger", "t", "SELECT", True),
            )
        ),
        {
            "isolation": [
                type(
                    "Inv",
                    (),
                    {
                        "code": "plane",
                        "role": "stranger",
                        "scope": "table",
                        "objects": ("t",),
                        "privileges": ("SELECT",),
                        "denied": True,
                    },
                )()
            ]
        },
    ),
}


def test_the_planted_set_covers_every_registered_checker() -> None:
    """The control's own extent. A checker with no planted defect is a checker
    this file never proves can fire, and it would be added silently."""
    assert set(PLANTED) == PERFORMED


@pytest.mark.parametrize("name", sorted(PLANTED))
def test_every_registered_checker_fires_on_a_planted_difference(name: str) -> None:
    """THE POSITIVE CONTROL. Without this, both set-equality tests above are
    satisfied by twelve checkers that return [] unconditionally."""
    source, restored, extra = PLANTED[name]
    checker = R.VERIFICATION_CHECKS[name]
    comparison = R._Comparison(
        manifest=_Manifest(),  # type: ignore[arg-type]
        source=source,
        restored=restored,
        isolation=extra.get("isolation", ()),
    )
    findings = checker(comparison)
    assert findings, f"{name} produced no finding for a planted difference"


@pytest.mark.parametrize("name", sorted(PLANTED))
def test_every_checker_is_silent_on_the_conforming_form(name: str) -> None:
    """The other half: the same instrument, the same scope, no difference. A
    checker that fires on everything and a database that has drifted are the
    same colour."""
    source, _restored, extra = PLANTED[name]
    comparison = R._Comparison(
        manifest=_Manifest(),  # type: ignore[arg-type]
        source=source,
        restored=source,
        isolation=extra.get("isolation", ()),
    )
    assert R.VERIFICATION_CHECKS[name](comparison) == []


def test_identical_catalogues_prove_the_recovery() -> None:
    assert _verify(R.CatalogEvidence(), R.CatalogEvidence()) == ()


# ── schema and effective_privileges, the two repaired names ─────────────────


def test_a_missing_schema_is_reported() -> None:
    findings = _verify(
        R.CatalogEvidence(schemas=("public", "mod_billing")),
        R.CatalogEvidence(schemas=("public",)),
    )
    assert any("mod_billing" in item for item in findings)


def test_an_extra_schema_is_reported() -> None:
    """A restore that produced MORE than was captured is not a restore."""
    findings = _verify(
        R.CatalogEvidence(schemas=("public",)),
        R.CatalogEvidence(schemas=("public", "old_tenant")),
    )
    assert any("old_tenant" in item for item in findings)


def test_an_effective_escalation_is_reported_without_any_declared_invariant() -> None:
    """The repair. Before this, the effective surface was read ONLY through
    declared isolation invariants, and the direct-grant diff standing in for it
    is the check this module's own documentation says "would go green exactly
    when the boundary is broken"."""
    findings = _verify(
        R.CatalogEvidence(
            effective_privileges=(
                R.EffectivePrivilegeFact("app", "t", "SELECT", False),
            )
        ),
        R.CatalogEvidence(
            effective_privileges=(R.EffectivePrivilegeFact("app", "t", "SELECT", True),)
        ),
    )
    assert any("EFFECTIVELY holds" in item for item in findings)


def test_a_direct_grant_comparison_alone_would_have_missed_it() -> None:
    """Why the effective check is not redundant with the direct-grant diff: the
    privilege arrives through PUBLIC or a group, so no grant to this role
    exists in either catalogue and the direct sets are IDENTICAL."""
    source = R.CatalogEvidence(
        effective_privileges=(R.EffectivePrivilegeFact("app", "t", "SELECT", False),)
    )
    restored = R.CatalogEvidence(
        effective_privileges=(R.EffectivePrivilegeFact("app", "t", "SELECT", True),)
    )
    assert (
        R.VERIFICATION_CHECKS["direct_privileges"](
            R._Comparison(_Manifest(), source, restored, ())  # type: ignore[arg-type]
        )
        == []
    )
    assert R.VERIFICATION_CHECKS["effective_privileges"](
        R._Comparison(_Manifest(), source, restored, ())  # type: ignore[arg-type]
    )


def test_an_unobserved_effective_privilege_is_a_finding_not_a_pass() -> None:
    """Silence is UNKNOWN. A boundary nobody read is a boundary nobody proved."""
    findings = _verify(
        R.CatalogEvidence(
            effective_privileges=(R.EffectivePrivilegeFact("app", "t", "SELECT", True),)
        ),
        R.CatalogEvidence(),
    )
    assert any("NOT OBSERVED" in item for item in findings)


# ── row_counts: the parse-time refusal, asserted on its CODE ───────────────


def _descriptor(verify: str) -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "deploy" / "product.toml").read_text(encoding="utf-8")
    return text.replace(
        'verify = [\n  "schema",', f'verify = [\n  "schema",\n{verify}', 1
    )


def test_the_shipped_descriptor_parses() -> None:
    """POSITIVE CONTROL for the refusal below: a negative suite whose subject
    cannot parse at all proves nothing about the refusal."""
    assert ProductDeploymentSpec.loads(_descriptor(""), source="control")


def test_an_unperformable_verification_is_refused_by_CODE() -> None:
    """Asserted on the typed code, never on the prose. A module with more than
    one refusal has to be testable on WHICH one fired."""
    with pytest.raises(SpecError) as caught:
        ProductDeploymentSpec.loads(_descriptor('  "row_counts",'), source="planted")
    assert caught.value.code == UNPERFORMABLE_VERIFICATION


def test_row_counts_stays_declarable_for_an_EXTERNALLY_executed_dataset() -> None:
    """The refusal is about WHO verifies, not about the check being worthless.
    An executor that can count rows genuinely claims it in a signed receipt."""
    from dotmac_deployment_foundation.external_recovery import VERIFICATION_EVIDENCE

    assert "row_counts" in VERIFICATION_EVIDENCE
    assert "row_counts" in BackupDataset.VERIFICATIONS
