"""The approvals read surface, and the approver a client may never name.

Wave 2 groundwork. A pending-request queue and a request detail have to be
buildable without a consumer reaching into `mod_approvals`, without a template
deciding who may approve, and without a request ever stating who approved it or
when. All three are structural, and this file is where they stop being
intentions.

The shape established for `dotmac_deployment_control` is the reference this
follows. Behaviour of the rules themselves lives in the unit suite; this file is
static structure, in keeping with the repo's split.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest
from dotmac_approvals import contracts, policy, service

MODULE_ROOT = Path(inspect.getfile(contracts)).parent

#: Every public type a caller CONSTRUCTS and hands inward.
INPUT_TYPES = (
    contracts.Actor,
    contracts.ApprovalLevel,
    contracts.PolicyRevision,
    contracts.RequestFilter,
)

#: Every function that can reach persistence. Scoped to the service layer on
#: purpose: `policy` is a pure evaluator over facts the service has already read
#: from rows it owns, so its parameters are a computation's inputs rather than a
#: client's assertions. Nothing in `policy` writes.
SERVICE_CALLABLES = tuple(
    getattr(service, name)
    for name in service.__all__
    if inspect.isfunction(getattr(service, name))
)

#: What the MODULE decides. An approver, a decision time, an evaluation and a
#: permitted action are all products of the policy revision and the recorded
#: decisions; a caller that could name one could name an approval that never
#: happened.
OWNER_DERIVED = (
    "approved_by",
    "rejected_by",
    "decided_by",
    "approver_name",
    "decided_at",
    "evaluation",
    "permitted_actions",
    "satisfied_levels",
    "is_approved",
)

VIEW_TYPES = (
    contracts.RequestView,
    contracts.RequestDetail,
    contracts.RequestPage,
    contracts.PolicyView,
    contracts.DecisionView,
    contracts.ActionRefusal,
)


def _offending_fields(cls: type, forbidden: tuple[str, ...]) -> list[str]:
    return [
        f"{cls.__name__}.{field.name}"
        for field in dataclasses.fields(cls)
        for name in forbidden
        if name in field.name
    ]


# ── No client-supplied approver, and no client-supplied decision time ───────


class TestTheClientCanNeverNameAnApprover:
    """The property, stated over the whole input surface rather than one type.

    A decision's attribution comes from the actor this module AUTHORISED, and
    its timestamp from this module's own clock. An approval bound to a
    caller-supplied `approved_by` is an approval by whoever the request said —
    which is not an approval at all.
    """

    def test_no_public_input_type_carries_one(self) -> None:
        offenders = [
            name
            for cls in INPUT_TYPES
            for name in _offending_fields(cls, OWNER_DERIVED)
        ]
        assert not offenders, offenders

    def test_no_service_function_takes_one_as_a_parameter(self) -> None:
        offenders = [
            f"{fn.__name__}({param})"
            for fn in SERVICE_CALLABLES
            for param in inspect.signature(fn).parameters
            for name in OWNER_DERIVED
            if name in param
        ]
        assert not offenders, offenders

    def test_recording_a_decision_takes_an_actor_and_never_an_approver(self) -> None:
        """The exact write. `actor` is a subject to authorise, not an
        attribution to accept, and there is no timestamp to backdate with."""
        params = set(inspect.signature(service.record_tenant_decision).parameters)
        assert params == {
            "db",
            "tenant_id",
            "request_id",
            "actor",
            "action",
            "content_digest",
            "comment",
            "delegated_from",
        }
        assert params == (
            set(inspect.signature(service.record_platform_decision).parameters) - {"db"}
            | {"db"}
        ) - {"tenant_id"} | {"tenant_id"}

    def test_the_decision_row_is_attributed_from_the_authorised_actor(self) -> None:
        """Not merely absent from the signature — read from the actor the rules
        just approved, after `authorise_approval` ran."""
        source = inspect.getsource(service.record_tenant_decision)
        assert "actor_id=actor.actor_id" in source
        assert "decided_at=datetime.now(UTC)" in source
        assert "authorise_approval" in source

    def test_the_guard_would_catch_a_field_that_appeared(self) -> None:
        """The sensitivity proof. A check over a surface that happens to be
        clean passes for the wrong reason the day it stops being clean."""

        @dataclasses.dataclass(frozen=True)
        class Defective:
            approved_by: str = "someone"

        assert _offending_fields(Defective, OWNER_DERIVED) == ["Defective.approved_by"]

    def test_the_cancel_identity_is_compared_not_accepted(self) -> None:
        """`cancelled_by` is NOT an exception to the rule.

        It is the caller stating who is acting, and `cancel_tenant_request`
        refuses it unless it equals the `requested_by` this module stored.
        Comparing against server-derived truth is the opposite of accepting an
        attribution.
        """
        source = inspect.getsource(service.cancel_tenant_request)
        assert "request.requested_by != cancelled_by" in source
        assert "NotRequester" in source


# ── The read contracts are typed and closed ─────────────────────────────────


class TestTheReadContractsAreTypedAndClosed:
    def test_the_request_filter_is_a_closed_set_of_fields(self) -> None:
        """No predicate, no sort column, no raw where. A consumer that could
        pass a predicate would own every future query."""
        names = {field.name for field in dataclasses.fields(contracts.RequestFilter)}
        assert names == {
            "state",
            "policy_code",
            "subject_type",
            "subject_id",
            "requested_by",
            "page",
            "page_size",
        }

    @pytest.mark.parametrize(
        ("page", "size"), [(0, 50), (-1, 50), (1, 0), (1, 201), (1, -5)]
    )
    def test_the_filter_refuses_an_unbounded_or_nonsense_page(
        self, page: int, size: int
    ) -> None:
        """An unbounded queue is how an approvals screen becomes a full-table
        scan the day a bulk import opens four thousand requests."""
        with pytest.raises(ValueError):
            contracts.RequestFilter(page=page, page_size=size)

    def test_the_filter_admits_the_bounds_it_permits(self) -> None:
        """BOTH HALVES — a validator only ever seen refusing might refuse
        everything."""
        assert contracts.RequestFilter(page=1, page_size=1).page_size == 1
        limit = contracts.RequestFilter.MAX_PAGE_SIZE
        assert contracts.RequestFilter(page=9, page_size=limit).page_size == limit

    def test_a_bad_page_size_is_not_a_domain_refusal(self) -> None:
        """A caller catching `ApprovalError` to render "you may not do that"
        must not have a mistyped page size land in the same branch."""
        with pytest.raises(ValueError) as raised:
            contracts.RequestFilter(page_size=0)
        assert not isinstance(raised.value, contracts.ApprovalError)

    def test_the_page_reports_enough_to_render_a_pager(self) -> None:
        page = contracts.RequestPage(requests=(), total=412, page=1, page_size=50)
        assert page.has_more
        assert not contracts.RequestPage(
            requests=(), total=50, page=1, page_size=50
        ).has_more


# ── Eligibility is the owner's decision ─────────────────────────────────────


class TestEligibilityIsOwnerDerived:
    def test_the_detail_carries_the_actions_rather_than_the_ingredients(self) -> None:
        names = {field.name for field in dataclasses.fields(contracts.RequestDetail)}
        assert {"permitted_actions", "refusals", "evaluation"} <= names

    def test_permitted_actions_are_decided_by_the_same_rules_the_writes_use(
        self,
    ) -> None:
        """Not a parallel judgement. A second implementation in a template would
        disagree the first moment a quorum was reached between the render and
        the click."""
        source = inspect.getsource(service._permitted)
        for rule in (
            "authorise_approval",
            "check_not_duplicate",
            "check_eligibility",
            "check_mfa",
        ):
            assert rule in source
            assert hasattr(policy, rule)

    def test_an_anonymous_read_is_offered_no_actions(self) -> None:
        """A detail with no viewer gets facts and no invitation to act."""
        defaults = {
            field.name: field.default
            for field in dataclasses.fields(contracts.RequestDetail)
        }
        assert defaults["permitted_actions"] == ()
        assert defaults["refusals"] == ()
        for name in ("get_tenant_request", "get_platform_request"):
            signature = inspect.signature(getattr(service, name))
            assert signature.parameters["viewer"].default is None

    def test_a_refusal_carries_a_stable_code_not_a_sentence(self) -> None:
        names = {field.name for field in dataclasses.fields(contracts.ActionRefusal)}
        assert names == {"action", "code", "detail"}


# ── A UI never receives a live ORM object ───────────────────────────────────


class TestTheSurfaceHandsOutValuesNotRows:
    def test_every_view_is_a_frozen_dataclass(self) -> None:
        for cls in VIEW_TYPES:
            assert dataclasses.is_dataclass(cls), cls
            assert cls.__dataclass_params__.frozen, cls  # type: ignore[attr-defined]

    def test_the_contract_module_knows_nothing_about_the_database(self) -> None:
        """`contracts` importing SQLAlchemy or the models would be the first
        step back towards handing a session-bound row to a template."""
        imported = _imported_top_level(Path(inspect.getfile(contracts)))
        assert "sqlalchemy" not in imported
        assert not any("models" in name for name in imported)

    def test_no_read_returns_a_model(self) -> None:
        """Every read's declared return type is a contract value.

        The writes still return rows — `publish_*_policy_version` hands back the
        row it inserted, which is the caller's own transaction to compose with.
        A READ has no such excuse.
        """
        reads = (
            "list_tenant_requests",
            "list_platform_requests",
            "get_tenant_request",
            "get_platform_request",
            "tenant_decision_history",
            "platform_decision_history",
            "get_tenant_policy",
            "get_platform_policy",
            "list_tenant_policy_versions",
            "list_platform_policy_versions",
        )
        models = {
            "ApprovalRequest",
            "ApprovalPolicy",
            "ApprovalDecision",
            "PlatformApprovalRequest",
            "PlatformApprovalPolicy",
            "PlatformApprovalDecision",
        }
        contract_types = {"RequestPage", "RequestDetail", "DecisionView", "PolicyView"}
        for name in reads:
            annotation = inspect.signature(getattr(service, name)).return_annotation
            assert not any(model in annotation for model in models), (name, annotation)
            assert any(kind in annotation for kind in contract_types), (
                name,
                annotation,
            )


# ── Query construction stays in this distribution's service layer ───────────


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
    """A consumer must never build a query over `mod_approvals`.

    The consuming assembly owns the operator workflow; this module owns its
    schema. The moment a consumer writes its own `select()` it has taken a
    second read authority over tables it does not own, and every column rename
    becomes a cross-repository break.
    """

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

    def test_the_reads_are_reachable_as_the_public_surface(self) -> None:
        for name in ("list_tenant_requests", "get_tenant_request", "get_tenant_policy"):
            assert name in service.__all__
            assert inspect.getmodule(getattr(service, name)) is service

    def test_the_module_imports_no_other_module_and_no_control_plane(self) -> None:
        """A module never imports Platform CP, and never imports a sibling
        module: both would make one distribution's release the other's
        problem."""
        allowed = {"dotmac_kernel", "dotmac_approvals"}
        offenders = [
            f"{path.name}: {name}"
            for path in MODULE_ROOT.rglob("*.py")
            for name in _imported_top_level(path)
            if name.startswith("dotmac_") and name not in allowed
        ]
        assert not offenders, offenders

    def test_both_planes_are_named_rather_than_flagged(self) -> None:
        """No `platform=` boolean anywhere on the read surface either. A caller
        states which security context it is in by naming the function, so a
        mistake is a TypeError at the call site rather than a row read from the
        wrong plane."""
        for fn in SERVICE_CALLABLES:
            assert "platform" not in inspect.signature(fn).parameters, fn.__name__
