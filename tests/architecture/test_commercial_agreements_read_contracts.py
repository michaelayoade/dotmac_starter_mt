"""The agreement read surface: one keyset reader, and a status nobody may state.

Wave 2 groundwork. A bounded list, an agreement detail with its lines, a
lifecycle timeline, owner-derived permitted actions and expected-version
conflict handling all have to be reachable without a consumer querying
`mod_agreements`, and without a screen deciding for itself which lifecycle
commands are legal.

This module already HAD the keyset reader — with no HTTP list route and no
closed input shape. What is added is the typed shape around that same reader,
not a second one: a parallel list implementation would be a second read
authority over these tables with its own drift.

The shape established for `dotmac_deployment_control` is the reference this
follows.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest
from dotmac_commercial_agreements import facts, ports, service
from dotmac_commercial_agreements.models import TERMINAL_STATUSES, AgreementStatus

MODULE_ROOT = Path(inspect.getfile(facts)).parent

#: Every public type a caller CONSTRUCTS and hands inward.
INPUT_TYPES = (
    service.DraftCommand,
    service.ProposeCommand,
    service.ApproveCommand,
    service.ActivateCommand,
    service.TransitionCommand,
    service.TerminateCommand,
    service.AmendCommand,
    ports.LineInput,
    ports.ApprovalEvidence,
    ports.ActivationEvidence,
    ports.AgreementPeriod,
    ports.CommercialTerms,
    facts.AgreementFilter,
)

#: What the MODULE decides from rows it owns. A caller that could state one
#: could state an agreement into a status nobody transitioned it to.
OWNER_DERIVED = (
    "permitted_actions",
    "record_version",
    "content_hash",
    "accepted_snapshot",
    "superseded_by_id",
    "supersedes_id",
    "approved_at",
    "activated_at",
    "agreement_version",
)

VIEW_TYPES = (
    facts.AgreementView,
    facts.AgreementPage,
    facts.AgreementDetail,
    facts.PromisedLine,
    facts.TransitionRecord,
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


# ── The client states evidence; the module states the agreement ─────────────


class TestNoInputCarriesAnOwnerDerivedValue:
    def test_no_public_input_type_carries_one(self) -> None:
        offenders = [
            name
            for cls in INPUT_TYPES
            for name in _offending_fields(cls, OWNER_DERIVED)
        ]
        assert not offenders, offenders

    def test_there_is_no_settable_status_anywhere_on_the_command_surface(self) -> None:
        """A command names a transition; it never names a destination.

        `expected_status` is the OPPOSITE of a settable status — it is the
        caller stating what it believes and `_require_expected` refusing when
        the row disagrees. The two are easy to confuse by name, which is exactly
        why this is asserted rather than assumed.
        """
        for cls in INPUT_TYPES:
            if cls is facts.AgreementFilter:
                continue  # a filter SELECTS on status — see the test below
            names = {field.name for field in dataclasses.fields(cls)}
            assert "status" not in names, cls
            assert "to_status" not in names, cls
        source = inspect.getsource(service._require_expected)
        assert "row.status == expected_status" in source
        assert "ExpectedStateError" in source

    def test_the_filter_selects_on_status_and_does_not_set_it(self) -> None:
        """`AgreementFilter.status` is the one place the word appears on an
        input, and it narrows a query over a column this module wrote.
        Selecting on a derived value is not asserting one — but the two look
        identical in a field list, which is why this is stated rather than
        assumed. It reaches a `where`, never an assignment.
        """
        assert "status" in {
            field.name for field in dataclasses.fields(facts.AgreementFilter)
        }
        source = inspect.getsource(service.list_agreements)
        assert "where(Agreement.status == criteria.status)" in source
        assert "row.status =" not in source
        assert "_advance(" not in source

    def test_the_guard_would_catch_a_field_that_appeared(self) -> None:
        """The sensitivity proof: a check over a clean surface passes for the
        wrong reason the day it stops being clean."""

        @dataclasses.dataclass(frozen=True)
        class Defective:
            record_version: int = 3

        assert _offending_fields(Defective, OWNER_DERIVED) == [
            "Defective.record_version"
        ]


# ── Expected-version conflict handling is carried by the read ───────────────


class TestExpectedVersionTravelsWithTheThingBeingLookedAt:
    def test_the_detail_carries_what_the_command_must_hand_back(self) -> None:
        names = {field.name for field in dataclasses.fields(facts.AgreementDetail)}
        assert {"expected_version", "expected_status"} <= names

    def test_every_lifecycle_command_can_carry_it(self) -> None:
        """A round trip is only reliable if every command has somewhere to put
        it. `DraftCommand` is exempt and says why: it creates the row, so there
        is no prior version to have observed."""
        for cls in INPUT_TYPES:
            if cls is service.DraftCommand or not cls.__name__.endswith("Command"):
                continue
            names = {field.name for field in dataclasses.fields(cls)}
            assert "expected_version" in names, cls


# ── Permitted actions come from the table the writes enforce ────────────────


class TestEligibilityIsOwnerDerivedFromOneTable:
    def test_the_table_covers_every_action(self) -> None:
        assert set(service._PERMITTED_FROM) == set(facts.AgreementAction)

    def test_amend_is_exactly_the_non_terminal_statuses(self) -> None:
        """Stated as a set rather than as `not in TERMINAL_STATUSES` scattered
        through the code, so the read and the guard cannot disagree."""
        assert service._PERMITTED_FROM[facts.AgreementAction.AMEND] == (
            frozenset(status.value for status in AgreementStatus) - TERMINAL_STATUSES
        )

    def test_no_transition_hard_codes_its_own_source_statuses(self) -> None:
        """The property that makes the read trustworthy.

        If a write guard kept its own inline `frozenset({...})` of statuses, the
        permitted-actions read would be a SECOND opinion about the lifecycle,
        and the two would disagree the first time one was edited.
        """
        transitions = (
            service.propose,
            service.approve,
            service.reject,
            service.activate,
            service.suspend,
            service.reinstate,
            service.cancel,
            service.terminate,
            service.expire,
            service.amend,
        )
        for fn in transitions:
            source = inspect.getsource(fn)
            assert "_from_states(" in source or "_sole_from(" in source, fn.__name__

    def test_a_sole_source_transition_refuses_to_guess(self) -> None:
        """`_sole_from` unpacks rather than picking a member: growing a second
        source status must fail loudly, not silently enforce whichever sorted
        first."""
        with pytest.raises(ValueError):
            service._sole_from(facts.AgreementAction.CANCEL)

    def test_the_derivation_reads_the_table_and_the_row(self) -> None:
        source = inspect.getsource(service._permitted_actions)
        assert "_PERMITTED_FROM" in source
        for extra in ("row.lines", "row.content_hash", "row.superseded_by_id"):
            assert extra in source


# ── One keyset reader, given a closed shape ─────────────────────────────────


def _paging_functions(source: str) -> list[str]:
    """Every top-level function that builds a bounded, eagerly-loaded page.

    A reader over these tables is a `selectinload` plus a `.limit(...)` in one
    function. Found over the syntax tree so a comment or docstring naming those
    calls is not counted as a second reader.
    """
    found: list[str] = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        calls = [call for call in ast.walk(node) if isinstance(call, ast.Call)]
        bounded = any(
            isinstance(call.func, ast.Attribute) and call.func.attr == "limit"
            for call in calls
        )
        eager = any(
            isinstance(call.func, ast.Name) and call.func.id == "selectinload"
            for call in calls
        )
        if bounded and eager:
            found.append(node.name)
    return found


class TestOneListImplementation:
    def test_the_filter_is_a_closed_set_of_fields(self) -> None:
        names = {field.name for field in dataclasses.fields(facts.AgreementFilter)}
        assert names == {
            "status",
            "agreement_type",
            "counterparty_ref",
            "agreement_family_id",
            "after",
            "limit",
        }

    @pytest.mark.parametrize("limit", [0, -1, 201, 1000])
    def test_the_filter_refuses_an_unbounded_or_nonsense_limit(
        self, limit: int
    ) -> None:
        with pytest.raises(ports.AgreementError):
            facts.AgreementFilter(limit=limit)

    def test_a_bad_limit_is_still_the_refusal_published_callers_catch(self) -> None:
        """`AgreementError` is a `ValueError`, and `list_agreements` has raised
        it for these inputs since a1. Moving the bound into the type must not
        move the exception a caller catches."""
        assert issubclass(ports.AgreementError, ValueError)
        with pytest.raises(ports.AgreementError):
            facts.AgreementFilter(limit=True)  # a bool is not a page size
        with pytest.raises(ports.AgreementError):
            facts.AgreementFilter(after="not-a-uuid")  # type: ignore[arg-type]

    def test_the_filter_admits_the_bounds_it_permits(self) -> None:
        """BOTH HALVES — a validator only ever seen refusing might refuse
        everything."""
        assert facts.AgreementFilter(limit=1).limit == 1
        limit = facts.AgreementFilter.MAX_LIMIT
        assert facts.AgreementFilter(limit=limit).limit == limit

    def test_the_cursor_is_a_keyset_not_an_offset(self) -> None:
        """An offset over a moving estate skips one row and repeats another,
        which reads as data loss rather than as a paging bug."""
        source = inspect.getsource(service.list_agreements)
        assert "Agreement.id > after" in source
        assert "order_by(Agreement.id)" in source
        assert ".offset(" not in source

    def test_there_is_exactly_one_reader_behind_both_spellings(self) -> None:
        """The filter and the a1 ``after=``/``limit=`` keywords name the same
        page. A second implementation would be a second read authority over
        these tables, free to drift.

        Counted over the syntax tree, not the text. The first version of this
        check counted the string `.limit(limit + 1)` and found two — the second
        being the COMMENT in `list_agreements` explaining why that exact literal
        has to survive. A detector that counts the sentence describing the code
        as a second copy of the code is one somebody deletes.
        """
        assert _paging_functions(inspect.getsource(service)) == ["list_agreements"]

    def test_the_paging_detector_counts_code_and_not_prose(self) -> None:
        """Sensitivity, in both directions (ADR-0018)."""
        two = (
            "def first(db):\n"
            "    return db.execute(select(A).options("
            "selectinload(A.lines)).limit(2)).all()\n"
            "def second(db):\n"
            "    return db.execute(select(A).options("
            "selectinload(A.lines)).limit(3)).all()\n"
        )
        assert _paging_functions(two) == ["first", "second"]
        prose = (
            "def only_talks_about_it(db):\n"
            "    'Never write .limit(limit + 1) with selectinload(A.lines).'\n"
            "    return None\n"
        )
        assert _paging_functions(prose) == []

    def test_naming_the_page_twice_is_refused(self) -> None:
        source = inspect.getsource(service.list_agreements)
        assert "pass a filter or after/limit, not both" in source


# ── A UI never receives a live ORM object ───────────────────────────────────


class TestTheSurfaceHandsOutValuesNotRows:
    def test_every_view_is_a_frozen_dataclass(self) -> None:
        for cls in VIEW_TYPES:
            assert dataclasses.is_dataclass(cls), cls
            assert cls.__dataclass_params__.frozen, cls  # type: ignore[attr-defined]

    def test_the_contract_module_knows_nothing_about_the_database(self) -> None:
        imported = _imported_top_level(Path(inspect.getfile(facts)))
        assert "sqlalchemy" not in imported
        assert not any("models" in name for name in imported)

    def test_every_read_returns_a_contract_value(self) -> None:
        expected = {
            "get": "AgreementView",
            "detail": "AgreementDetail",
            "history": "TransitionRecord",
            "family": "AgreementView",
            "list_agreements": "AgreementPage",
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
        import dotmac_commercial_agreements as api

        for name in ("get", "detail", "history", "family", "list_agreements"):
            assert name in api.__all__
            assert callable(getattr(api, name))
        for name in ("AgreementFilter", "AgreementDetail", "AgreementAction"):
            assert name in api.__all__

    def test_the_module_imports_no_other_module_and_no_control_plane(self) -> None:
        allowed = {"dotmac_kernel", "dotmac_commercial_agreements"}
        offenders = [
            f"{path.name}: {name}"
            for path in MODULE_ROOT.rglob("*.py")
            for name in _imported_top_level(path)
            if name.startswith("dotmac_") and name not in allowed
        ]
        assert not offenders, offenders
