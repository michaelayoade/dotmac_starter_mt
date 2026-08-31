"""The allocation read surface, and the seal nobody may claim.

Wave 2 groundwork. A contract-scoped allocation view — sealed state, capability
quantities, reconciliation and refusal detail — has to be reachable without a
consumer querying `mod_ealloc`, and without any caller ever asserting that an
allocation is complete.

That last one is this module's sharp instance. The seal is set once, by the
staging path, after every entry is written; a trigger then refuses a late entry
and refuses to lift it. An allocation that could be told it was sealed would be
an entitlement set nobody validated, wearing the word that means somebody did.

The shape established for `dotmac_deployment_control` is the reference this
follows.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import uuid
from pathlib import Path

import pytest
from dotmac_entitlement_allocation import facts, ports, service

MODULE_ROOT = Path(inspect.getfile(facts)).parent

#: Every public type a caller CONSTRUCTS and hands inward.
INPUT_TYPES = (
    ports.ContractSnapshot,
    ports.ContractEntitlement,
    facts.AllocationFilter,
)

#: What the MODULE decides from rows it owns.
OWNER_DERIVED = (
    "integrity",
    "permitted_actions",
    "snapshot_fingerprint",
    "staged_at",
    "refusal",
    "issuable",
)

VIEW_TYPES = (
    facts.AllocationRecord,
    facts.AllocationPage,
    facts.AllocationReconciliation,
    facts.AllocatedCapability,
)


def _offending_fields(cls: type, forbidden: tuple[str, ...]) -> list[str]:
    return [
        f"{cls.__name__}.{field.name}"
        for field in dataclasses.fields(cls)
        for name in forbidden
        if name in field.name
    ]


def _imported_top_level(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }


# ── Nobody may claim the seal ───────────────────────────────────────────────


class TestTheSealIsNeverAClientsToClaim:
    def test_no_input_can_state_that_an_allocation_is_complete(self) -> None:
        """The property, over the whole write surface rather than one type."""
        for cls in (ports.ContractSnapshot, ports.ContractEntitlement):
            names = {field.name for field in dataclasses.fields(cls)}
            assert "sealed" not in names, cls
            assert "status" not in names, cls
        assert "sealed" not in inspect.signature(service.stage_allocation).parameters

    def test_the_filter_selects_on_the_seal_and_does_not_assert_it(self) -> None:
        """`AllocationFilter.sealed` is the one place the word appears on an
        input, and it narrows a query over a column this module wrote. Selecting
        on a derived value is not asserting one — but the two look identical in
        a field list, which is why this is stated rather than assumed."""
        assert "sealed" in {
            field.name for field in dataclasses.fields(facts.AllocationFilter)
        }
        source = inspect.getsource(service.list_allocations)
        assert "Allocation.sealed.is_(criteria.sealed)" in source

    def test_no_public_input_type_carries_an_owner_derived_value(self) -> None:
        offenders = [
            name
            for cls in INPUT_TYPES
            for name in _offending_fields(cls, OWNER_DERIVED)
        ]
        assert not offenders, offenders

    def test_no_public_function_takes_one_as_a_parameter(self) -> None:
        import dotmac_entitlement_allocation as package

        offenders = [
            f"{name}({param})"
            for name in package.__all__
            if inspect.isfunction(getattr(package, name))
            for param in inspect.signature(getattr(package, name)).parameters
            for derived in OWNER_DERIVED
            if derived in param
        ]
        assert not offenders, offenders

    def test_the_guard_would_catch_a_field_that_appeared(self) -> None:
        """The sensitivity proof: a check over a clean surface passes for the
        wrong reason the day it stops being clean."""

        @dataclasses.dataclass(frozen=True)
        class Defective:
            integrity: str = "sealed"

        assert _offending_fields(Defective, OWNER_DERIVED) == ["Defective.integrity"]

    def test_the_seal_is_the_only_thing_the_verdict_is_derived_from(self) -> None:
        source = inspect.getsource(service._record)
        assert "allocation.sealed" in source
        assert "AllocationIntegrity.SEALED" in source
        assert "AllocationIntegrity.UNSEALED" in source


# ── Owner-derived actions, and an action the grants can serve ───────────────


class TestEligibilityIsOwnerDerived:
    def test_the_action_vocabulary_offers_nothing_the_seal_forbids(self) -> None:
        """The seal is one-way: `platform_api` may update no column but
        `sealed`, and a trigger refuses to lift it. An `UNSEAL`, `AMEND` or
        `RESTAGE` member would let a screen offer an action the database
        refuses, so there is no such member to derive."""
        assert {action.value for action in facts.AllocationAction} == {"issue"}

    def test_issuing_is_permitted_only_on_a_sealed_allocation(self) -> None:
        """The same fact `allocation_product` fails closed on. Deriving the
        action from it means a screen cannot offer what that call rejects."""
        source = inspect.getsource(service._record)
        assert "(AllocationAction.ISSUE,) if allocation.sealed else ()" in source
        assert "not sealed" in inspect.getsource(service.allocation_product)

    def test_a_read_shows_an_incomplete_write_while_issuance_refuses_it(
        self,
    ) -> None:
        """The deliberate asymmetry, stated so it is not "fixed" later.

        `allocation_product` raises on an unsealed row because issuing against
        an entitlement set nobody finished validating must fail closed.
        `get_allocation` returns it, because LOOKING at one is how an operator
        finds the row that needs repairing — and refusing the read would hide
        the very thing somebody has to act on.
        """
        assert "IncompleteAllocationError" in inspect.getsource(
            service.allocation_product
        )
        assert "IncompleteAllocationError" not in inspect.getsource(
            service.get_allocation
        )
        assert facts.AllocationRefusal.NOT_SEALED.value == "not_sealed"


# ── The contract-scoped answer stays inside this module's authority ─────────


class TestReconciliationAnswersOnlyWhatThisModuleOwns:
    def test_the_caller_supplies_the_activation_and_nothing_else(self) -> None:
        """This module never reads a contract, so contract invariants stay
        proven where they live. What it answers is its own half."""
        params = set(inspect.signature(service.reconciliation).parameters)
        assert params == {"db", "contract_ref", "content_hash"}

    def test_every_refusal_state_has_a_typed_code_and_a_detail(self) -> None:
        names = {
            field.name for field in dataclasses.fields(facts.AllocationReconciliation)
        }
        assert {"state", "refusal", "detail", "allocation"} <= names
        assert {state.value for state in facts.ReconciliationState} == {
            "allocated",
            "missing",
            "incomplete",
        }

    def test_issuable_means_allocated_and_nothing_looser(self) -> None:
        allocated = facts.AllocationReconciliation(
            contract_ref=uuid.uuid4(),
            content_hash="abc",
            state=facts.ReconciliationState.ALLOCATED,
        )
        assert allocated.issuable
        for state in (
            facts.ReconciliationState.MISSING,
            facts.ReconciliationState.INCOMPLETE,
        ):
            assert not dataclasses.replace(allocated, state=state).issuable


# ── The read contracts are typed and closed ─────────────────────────────────


class TestTheReadContractsAreTypedAndClosed:
    def test_the_filter_is_a_closed_set_of_fields(self) -> None:
        names = {field.name for field in dataclasses.fields(facts.AllocationFilter)}
        assert names == {
            "contract_ref",
            "product_code",
            "customer_ref",
            "sealed",
            "page",
            "page_size",
        }

    @pytest.mark.parametrize(
        ("page", "size"), [(0, 50), (-1, 50), (1, 0), (1, 201), (1, -5)]
    )
    def test_the_filter_refuses_an_unbounded_or_nonsense_page(
        self, page: int, size: int
    ) -> None:
        with pytest.raises(ports.AllocationError):
            facts.AllocationFilter(page=page, page_size=size)

    def test_the_filter_admits_the_bounds_it_permits(self) -> None:
        """BOTH HALVES — a validator only ever seen refusing might refuse
        everything."""
        assert facts.AllocationFilter(page=1, page_size=1).page_size == 1
        limit = facts.AllocationFilter.MAX_PAGE_SIZE
        assert facts.AllocationFilter(page=9, page_size=limit).page_size == limit

    def test_the_refusal_stays_the_one_this_module_publishes(self) -> None:
        assert issubclass(ports.AllocationError, ValueError)

    def test_the_page_reports_enough_to_render_a_pager(self) -> None:
        page = facts.AllocationPage(allocations=(), total=412, page=1, page_size=50)
        assert page.has_more
        assert not facts.AllocationPage(
            allocations=(), total=50, page=1, page_size=50
        ).has_more

    def test_the_record_and_the_staging_outcome_do_not_drift(self) -> None:
        """Two views of one row, answering different questions.

        `AllocationView` is the OUTCOME of a staging call and carries
        `replayed`, which is meaningless to a reader who never staged anything.
        `AllocationRecord` is what is on file. Everything else must appear on
        both, or the two have started to disagree about the same row.
        """
        outcome = {field.name for field in dataclasses.fields(service.AllocationView)}
        record = {field.name for field in dataclasses.fields(facts.AllocationRecord)}
        assert outcome - {"replayed"} <= record


# ── A UI never receives a live ORM object ───────────────────────────────────


class TestTheSurfaceHandsOutValuesNotRows:
    def test_the_contract_module_knows_nothing_about_the_database(self) -> None:
        imported = _imported_top_level(Path(inspect.getfile(facts)))
        assert "sqlalchemy" not in imported
        assert not any("models" in name for name in imported)

    def test_the_status_vocabulary_is_a_value_the_orm_imports(self) -> None:
        """One definition, and it lives on the VALUE side. A read contract that
        had to import the ORM to name a status would drag persistence into every
        consumer's type-checker."""
        from dotmac_entitlement_allocation import models

        assert models.AllocationStatus is facts.AllocationStatus
        assert (
            facts.AllocationStatus.__module__ == "dotmac_entitlement_allocation.facts"
        )

    def test_every_read_returns_a_contract_value(self) -> None:
        expected = {
            "get_allocation": "AllocationRecord",
            "allocations_for_contract": "AllocationRecord",
            "list_allocations": "AllocationPage",
            "reconciliation": "AllocationReconciliation",
        }
        for name, kind in expected.items():
            annotation = inspect.signature(getattr(service, name)).return_annotation
            assert kind in annotation, (name, annotation)


# ── Query construction stays in this distribution's service layer ───────────


def _query_calls(tree: ast.AST) -> list[str]:
    """Every actual query construction in a parsed module.

    AST, not text. A text scan for `select(` reports this package's own module
    docstring — which EXPLAINS that a consumer must never write `select(...)` —
    as a query, and reports `document.get("key")` on a plain mapping as a
    session read. Both appear in these packages, so a text scan is a detector
    that fires on the sentence warning against the defect.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "select":
            found.append("select()")
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in {"db", "session"}
            and func.attr in {"execute", "get", "query", "scalars"}
        ):
            found.append(f"{func.value.id}.{func.attr}()")
    return found


def _builds_a_query(path: Path) -> bool:
    return bool(_query_calls(ast.parse(path.read_text(encoding="utf-8"))))


class TestQueryConstructionStaysInTheServiceLayer:
    def test_query_construction_lives_only_in_the_service_layer(self) -> None:
        offenders = [
            path.name
            for path in MODULE_ROOT.glob("*.py")
            if path.name != "service.py" and _builds_a_query(path)
        ]
        assert (
            not offenders
        ), f"query construction outside the service layer: {offenders}"

    def test_the_query_detector_fires_and_does_not_fire_on_prose(self) -> None:
        """Sensitivity, in both directions (ADR-0018).

        The first version of this guard scanned TEXT and reported the package's
        own docstring — the one explaining that a consumer must never write
        `select(...)` — as query construction. A detector that cannot tell code
        from the sentence warning against the defect is one somebody deletes.
        """
        assert _query_calls(ast.parse("db.execute(select(Thing))"))
        assert _query_calls(ast.parse("row = db.get(Thing, key)"))
        assert not _query_calls(ast.parse('"""Never write select(Thing) here."""'))
        assert not _query_calls(ast.parse('document.get("sod_rule")'))

    def test_the_reads_are_reachable_from_the_public_namespace(self) -> None:
        import dotmac_entitlement_allocation as package

        for name in (
            "get_allocation",
            "allocations_for_contract",
            "list_allocations",
            "reconciliation",
            "AllocationFilter",
            "AllocationRecord",
            "AllocationReconciliation",
        ):
            assert name in package.__all__
            assert getattr(package, name) is not None

    def test_the_module_imports_no_other_module_and_no_control_plane(self) -> None:
        allowed = {"dotmac_kernel", "dotmac_entitlement_allocation"}
        offenders = [
            f"{path.name}: {name}"
            for path in MODULE_ROOT.rglob("*.py")
            for name in _imported_top_level(path)
            if name.startswith("dotmac_") and name not in allowed
        ]
        assert not offenders, offenders
