"""The catalogue's public READ contract, and the values a client may not supply.

Wave 2 groundwork. A browser surface for this module has to be buildable
without a consumer reaching into `mod_rel`, without a client ever asserting how
well-vouched-for an artifact is, and without a template ever holding a live ORM
row. All three are structural properties, and this file is where they stop
being intentions.

The census found ZERO module web or API contribution across six manifests, so
there is no existing pattern to copy — correct or otherwise. The shape
established for `dotmac_deployment_control` is the reference this follows.

Behaviour of digest and reference parsing lives in
`tests/unit/test_release_catalog_identity.py`; this file is static structure, in
keeping with the repo's split.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import dotmac_release_catalog as api
import pytest
from dotmac_release_catalog import facts, service

MODULE_ROOT = Path(inspect.getfile(facts)).parent

#: Every public type a caller can CONSTRUCT and hand inward, and every public
#: callable it can pass arguments to. A derived value appearing on one of these
#: is a derived value a client can choose.
INPUT_TYPES = (facts.ArtifactFilter,)
PUBLIC_CALLABLES = (
    api.publish_artifact,
    api.attest_artifact,
    api.list_artifacts,
    api.get_artifact,
    api.artifact_attestations,
    api.preview_publication,
)

#: What THIS module decides from rows it owns. None of it may be nameable by a
#: caller: a shape where the client could supply one is a shape where someone
#: eventually will, and an artifact that says it is signed because the request
#: said so is worse than one that says nothing.
OWNER_DERIVED = (
    "evidence_state",
    "attested_kinds",
    "permitted_actions",
    "would_publish",
    "refusals",
    "recorded_at",
)

#: Rows owned by OTHER modules. The catalogue holds no foreign key to any of
#: them and its views must carry none either — see `facts` on linking out
#: through correlation keys rather than joining across owners.
FOREIGN_OWNER_KEYS = (
    "offer_id",
    "licence_id",
    "license_id",
    "plan_id",
    "deployment_id",
    "subscription_id",
    "tenant_id",
)

VIEW_TYPES = (
    facts.ArtifactView,
    facts.AttestationView,
    facts.ArtifactDetail,
    facts.ArtifactPage,
    facts.PublicationPreview,
)


def _offending_fields(cls: type, forbidden: tuple[str, ...]) -> list[str]:
    return [
        f"{cls.__name__}.{field.name}"
        for field in dataclasses.fields(cls)
        for name in forbidden
        if name in field.name
    ]


# ── The client may never supply what the catalogue derives ──────────────────


class TestNoInputCarriesAnOwnerDerivedValue:
    def test_no_public_input_type_carries_one(self) -> None:
        offenders = [
            name
            for cls in INPUT_TYPES
            for name in _offending_fields(cls, OWNER_DERIVED)
        ]
        assert not offenders, offenders

    def test_no_public_callable_takes_one_as_a_parameter(self) -> None:
        """Stated over the WHOLE public surface rather than one function.

        `publish_artifact` is the one that matters most: a publisher states the
        bytes' digest and the reference it produced, and the catalogue states
        everything else. A `verified=True` keyword would make "this artifact is
        signed" a claim the request got to make.
        """
        offenders = [
            f"{fn.__name__}({param})"
            for fn in PUBLIC_CALLABLES
            for param in inspect.signature(fn).parameters
            for name in OWNER_DERIVED
            if name in param
        ]
        assert not offenders, offenders

    def test_the_guard_would_catch_a_field_that_appeared(self) -> None:
        """The sensitivity proof. A check over a surface that happens to be
        clean passes for the wrong reason the day it stops being clean."""

        @dataclasses.dataclass(frozen=True)
        class Defective:
            evidence_state: str = "signature_recorded"

        assert _offending_fields(Defective, OWNER_DERIVED) == [
            "Defective.evidence_state"
        ]

    def test_the_preview_is_a_read_that_states_no_verdict_for_the_caller(
        self,
    ) -> None:
        """A preview that accepted its own verdict would be an assertion wearing
        a read's name."""
        params = set(inspect.signature(api.preview_publication).parameters)
        assert params == {
            "db",
            "product_code",
            "version",
            "artifact_kind",
            "digest",
            "artifact_ref",
        }


# ── The read contracts are typed and closed ─────────────────────────────────


class TestTheReadContractsAreTypedAndClosed:
    def test_the_artifact_filter_is_a_closed_set_of_fields(self) -> None:
        """No predicate, no sort column, no raw where. A consumer that could
        pass a predicate would own every future query."""
        names = {field.name for field in dataclasses.fields(facts.ArtifactFilter)}
        assert names == {
            "product_code",
            "version",
            "artifact_kind",
            "attested_with",
            "page",
            "page_size",
        }

    @pytest.mark.parametrize(
        ("page", "size"), [(0, 50), (-1, 50), (1, 0), (1, 201), (1, -5)]
    )
    def test_the_filter_refuses_an_unbounded_or_nonsense_page(
        self, page: int, size: int
    ) -> None:
        """An unbounded list is how a catalogue screen becomes a full-table
        scan the day the fleet publishes its ten-thousandth artifact."""
        with pytest.raises(ValueError):
            facts.ArtifactFilter(page=page, page_size=size)

    def test_the_filter_admits_the_bounds_it_permits(self) -> None:
        """BOTH HALVES — a validator only ever seen refusing might refuse
        everything."""
        assert facts.ArtifactFilter(page=1, page_size=1).page_size == 1
        limit = facts.ArtifactFilter.MAX_PAGE_SIZE
        assert facts.ArtifactFilter(page=9, page_size=limit).page_size == limit

    def test_the_page_reports_enough_to_render_a_pager(self) -> None:
        page = facts.ArtifactPage(artifacts=(), total=412, page=1, page_size=50)
        assert page.has_more
        assert not facts.ArtifactPage(
            artifacts=(), total=50, page=1, page_size=50
        ).has_more


# ── A UI never receives a live ORM object ───────────────────────────────────


class TestTheSurfaceHandsOutValuesNotRows:
    def test_every_view_is_a_frozen_dataclass(self) -> None:
        for cls in VIEW_TYPES:
            assert dataclasses.is_dataclass(cls), cls
            assert cls.__dataclass_params__.frozen, cls  # type: ignore[attr-defined]

    def test_the_contract_module_knows_nothing_about_the_database(self) -> None:
        """`facts` importing SQLAlchemy or the models would be the first step
        back towards handing a session-bound row to a template."""
        source = Path(inspect.getfile(facts)).read_text(encoding="utf-8")
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        }
        assert "sqlalchemy" not in imported
        assert not any("models" in name for name in imported)

    def test_no_view_carries_another_owners_row(self) -> None:
        """Correlation keys, not foreign keys. A surface composes across modules
        by asking each owner; it never joins tables the catalogue does not
        own."""
        offenders = [
            name
            for cls in VIEW_TYPES
            for name in _offending_fields(cls, FOREIGN_OWNER_KEYS)
        ]
        assert not offenders, offenders


# ── Owner-derived eligibility, and an honest verdict ────────────────────────


class TestEligibilityIsTheOwnersDecision:
    def test_the_only_permitted_action_is_one_the_grants_allow(self) -> None:
        """`platform_api` holds SELECT and INSERT and nothing else. An `EDIT` or
        `WITHDRAW` member would let a screen offer an action the database
        refuses, so there is no such member to derive."""
        assert {action.value for action in facts.ArtifactAction} == {"attest"}

    def test_the_verdict_never_claims_a_signature_was_verified(self) -> None:
        """This module never fetches an attestation URI (ADR-0009), so it cannot
        know that one validated — only that one was recorded."""
        values = {state.value for state in facts.EvidenceState}
        assert values == {"unattested", "unsigned", "signature_recorded"}
        assert not any("verified" in value for value in values)


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
    """A consumer must never build a query over `mod_rel`.

    The vendor control plane owns the operator workflow; this module owns its
    schema. The moment a consumer writes its own `select()` it has taken a
    second read authority over tables it does not own, and every column rename
    becomes a cross-repository break.
    """

    def test_the_public_surface_exposes_reads_not_tables(self) -> None:
        for name in (
            "list_artifacts",
            "get_artifact",
            "artifact_attestations",
            "preview_publication",
        ):
            assert callable(getattr(api, name))
            assert name in api.__all__

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

    def test_the_module_imports_no_other_module_and_no_control_plane(self) -> None:
        """A module never imports Platform CP, and never imports a sibling
        module: both would make one distribution's release the other's
        problem."""
        allowed = {"dotmac_kernel", "dotmac_release_catalog"}
        offenders: list[str] = []
        for path in MODULE_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                (node.module or "").split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
            offenders += [
                f"{path.name}: {name}"
                for name in names
                if name.startswith("dotmac_") and name not in allowed
            ]
        assert not offenders, offenders


def test_the_service_module_is_the_only_place_the_session_is_touched() -> None:
    """Belt and braces on the rule above: the read functions all take a
    `Session` and they all live in one file."""
    for name in ("list_artifacts", "get_artifact", "preview_publication"):
        fn = getattr(service, name)
        assert inspect.getmodule(fn) is service
