"""The licensing read surface: key IDs, never keys, and a boundary held open.

Wave 2 groundwork. A licence and issuance detail, acknowledgements, revocations
and revocation-list versions all have to be reachable without a consumer
querying `mod_licensing` — and two things must remain structurally impossible
while that happens:

* **No key material reaches a screen's contract.** The private half has no
  column in this distribution at all. The public half is real and distributed,
  but through `build_keyring`, which a deployment verifies with; a read contract
  names a `key_id` and stops.
* **The issuer does not learn to speak for the transport.** This module ends at
  a signed envelope and resumes at an acknowledgement. An attempt count, a retry
  outcome or a connection reference on a view here would make it a second
  delivery authority with no honest way to fill the field.

The shape established for `dotmac_deployment_control` is the reference this
follows.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest
from dotmac_licensing import facts, ports, service

MODULE_ROOT = Path(inspect.getfile(facts)).parent

#: Every public type a caller CONSTRUCTS and hands inward.
INPUT_TYPES = (
    service.IssueCommand,
    service.IssuanceTransitionCommand,
    service.RevokeCommand,
    service.AcknowledgeCommand,
    ports.LicensableGrant,
    ports.LicensedCapability,
    ports.InstallationReport,
    facts.LicenceFilter,
)

#: Every value type a read hands OUT.
VIEW_TYPES = (
    facts.IssuanceView,
    facts.LicenceView,
    facts.LicenceSummary,
    facts.LicencePage,
    facts.AcknowledgementView,
    facts.RevocationView,
    facts.RevocationListView,
    facts.SigningKeyView,
    facts.IssuanceHandoff,
    facts.InspectionResult,
)

#: Key material, in every spelling this module could plausibly acquire. A view
#: carrying one of these is a screen that can render a key.
KEY_MATERIAL = (
    "private_key",
    "secret_key",
    "signing_key",
    "public_key",
    "key_material",
    "seed",
    "passphrase",
)

#: The transport's, not the issuer's. A field for one of these here would be a
#: delivery claim this module has no way to make honestly.
TRANSPORT_STATE = (
    "attempt",
    "retry",
    "connection_ref",
    "next_attempt",
    "delivered_at",
    "delivery_status",
    "endpoint",
)

#: What the MODULE derives from rows it owns.
OWNER_DERIVED = (
    "acknowledgement_state",
    "record_version",
    "digest",
    "envelope",
    "replaced_by_version",
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


# ── Key IDs, never keys ─────────────────────────────────────────────────────


class TestNoReadContractCanCarryKeyMaterial:
    def test_no_view_carries_material_of_any_kind(self) -> None:
        """Public included. The public half is real and must be distributed —
        but through `build_keyring`, which is a protocol artefact a deployment
        consumes. Putting it on a screen's contract makes every future surface a
        place key material can appear by accident."""
        offenders = [
            name for cls in VIEW_TYPES for name in _offending_fields(cls, KEY_MATERIAL)
        ]
        assert not offenders, offenders

    def test_the_key_view_is_an_identifier_and_a_status(self) -> None:
        names = {field.name for field in dataclasses.fields(facts.SigningKeyView)}
        assert names == {"key_id", "status", "registered_at"}

    def test_the_keyring_stays_the_only_place_public_material_travels(self) -> None:
        """Two readers over one table, kept apart deliberately. `build_keyring`
        answers a verifier; `signing_keys` answers a screen."""
        assert "public_key_b64" in inspect.getsource(service.build_keyring)
        assert "public_key" not in inspect.getsource(service.signing_keys)

    def test_the_distribution_still_has_nowhere_to_put_a_private_key(self) -> None:
        """The rule the whole module is shaped by, re-asserted at the read
        boundary: a wheel, a dump, a replica and a stack trace are all
        structurally incapable of leaking one."""
        from dotmac_licensing.models import SigningKey

        columns = {column.name for column in SigningKey.__table__.columns}
        assert not {name for name in columns if "private" in name or "secret" in name}

    def test_the_guard_would_catch_material_that_appeared(self) -> None:
        """The sensitivity proof: a check over a clean surface passes for the
        wrong reason the day it stops being clean."""

        @dataclasses.dataclass(frozen=True)
        class Defective:
            private_key_b64: str = ""

        assert _offending_fields(Defective, KEY_MATERIAL) == [
            "Defective.private_key_b64"
        ]


# ── The issuer never speaks for the transport ───────────────────────────────


class TestTheIntegratorBoundaryHoldsOpen:
    def test_no_view_carries_transport_state(self) -> None:
        offenders = [
            name
            for cls in VIEW_TYPES
            for name in _offending_fields(cls, TRANSPORT_STATE)
        ]
        assert not offenders, offenders

    def test_the_handoff_names_what_goes_out_and_what_comes_back(self) -> None:
        names = {field.name for field in dataclasses.fields(facts.IssuanceHandoff)}
        assert {"envelope", "digest", "key_id", "deployment_ref"} <= names
        assert {"receipts", "receipt_references", "acknowledgement_state"} <= names

    def test_the_state_is_about_hearing_back_not_about_delivering(self) -> None:
        """`AWAITING` means nothing was reported, which is NOT "undelivered": a
        licence can be applied by a deployment that never reports. A surface
        rendering it as a delivery failure would be inventing a fact the issuer
        does not have."""
        assert {state.value for state in facts.AcknowledgementState} == {
            "awaiting",
            "acknowledged",
            "rejected",
        }
        assert "delivered" not in {state.value for state in facts.AcknowledgementState}

    def test_no_input_lets_a_caller_state_delivery(self) -> None:
        offenders = [
            name
            for cls in INPUT_TYPES
            for name in _offending_fields(cls, TRANSPORT_STATE)
        ]
        assert not offenders, offenders


# ── No client-supplied server-derived value ─────────────────────────────────


class TestNoInputCarriesAnOwnerDerivedValue:
    def test_no_public_input_type_carries_one(self) -> None:
        """`InstallationReport.digest` is NOT an exception, and is excluded by
        name here for the reason the reference states about an approver's
        content digest: it is the RECEIVER declaring which document it applied,
        and `acknowledge` refuses it unless it matches what was issued. Comparing
        against server-derived truth is the opposite of accepting one."""
        offenders = [
            name
            for cls in INPUT_TYPES
            if cls is not ports.InstallationReport
            for name in _offending_fields(cls, OWNER_DERIVED)
        ]
        assert not offenders, offenders

    def test_the_reported_digest_is_compared_and_never_adopted(self) -> None:
        assert "digest" in {
            field.name for field in dataclasses.fields(ports.InstallationReport)
        }
        source = inspect.getsource(service.acknowledge)
        assert "AcknowledgementRefusedError" in source
        assert "digest" in source

    def test_the_envelope_is_the_issuers_and_never_an_input(self) -> None:
        """A caller that could hand in an envelope could hand in a licence
        nobody signed."""
        for cls in (service.IssueCommand, service.IssuanceTransitionCommand):
            names = {field.name for field in dataclasses.fields(cls)}
            assert "envelope" not in names, cls


# ── The read contracts are typed and closed ─────────────────────────────────


class TestTheReadContractsAreTypedAndClosed:
    def test_the_filter_is_a_closed_set_of_fields(self) -> None:
        names = {field.name for field in dataclasses.fields(facts.LicenceFilter)}
        assert names == {
            "subject_ref",
            "product_code",
            "revoked",
            "issuance_status",
            "key_id",
            "page",
            "page_size",
        }

    @pytest.mark.parametrize(
        ("page", "size"), [(0, 50), (-1, 50), (1, 0), (1, 201), (1, -5)]
    )
    def test_the_filter_refuses_an_unbounded_or_nonsense_page(
        self, page: int, size: int
    ) -> None:
        with pytest.raises(ports.LicensingError):
            facts.LicenceFilter(page=page, page_size=size)

    def test_the_filter_admits_the_bounds_it_permits(self) -> None:
        """BOTH HALVES — a validator only ever seen refusing might refuse
        everything."""
        assert facts.LicenceFilter(page=1, page_size=1).page_size == 1
        limit = facts.LicenceFilter.MAX_PAGE_SIZE
        assert facts.LicenceFilter(page=9, page_size=limit).page_size == limit

    def test_the_refusal_stays_the_one_this_module_publishes(self) -> None:
        assert issubclass(ports.LicensingError, ValueError)

    def test_the_page_reports_enough_to_render_a_pager(self) -> None:
        page = facts.LicencePage(licences=(), total=412, page=1, page_size=50)
        assert page.has_more
        assert not facts.LicencePage(
            licences=(), total=50, page=1, page_size=50
        ).has_more

    def test_the_list_row_is_not_the_detail(self) -> None:
        """`LicenceView` carries every issuance in the lineage, which is right
        for a detail screen and is a nested page per row on a list."""
        summary = {field.name for field in dataclasses.fields(facts.LicenceSummary)}
        assert "issuances" not in summary
        assert {"current_version", "current_status", "issuance_count"} <= summary

    def test_revocation_lists_are_ordered_by_the_version_a_receiver_gates_on(
        self,
    ) -> None:
        """A receiver only ever accepts a list at or above the version it holds,
        so the version sequence IS the history."""
        source = inspect.getsource(service.revocation_lists)
        assert "order_by(RevocationList.list_version)" in source
        latest = inspect.getsource(service.latest_revocation_list)
        assert "RevocationList.list_version.desc()" in latest


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
            "list_licences": "LicencePage",
            "licence_view": "LicenceView",
            "get_issuance": "IssuanceView",
            "current_issuance": "IssuanceView",
            "acknowledgements": "AcknowledgementView",
            "issuance_handoff": "IssuanceHandoff",
            "signing_keys": "SigningKeyView",
            "revocations": "RevocationView",
            "revocation_lists": "RevocationListView",
            "latest_revocation_list": "RevocationListView",
            "inspect_issued_envelope": "InspectionResult",
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
        import dotmac_licensing as package

        for name in (
            "list_licences",
            "issuance_handoff",
            "signing_keys",
            "revocations",
            "revocation_lists",
            "latest_revocation_list",
            "LicenceFilter",
            "LicencePage",
            "SigningKeyView",
            "IssuanceHandoff",
        ):
            assert name in package.__all__
            assert getattr(package, name) is not None

    def test_the_module_imports_no_other_module_and_no_control_plane(self) -> None:
        allowed = {"dotmac_kernel", "dotmac_licensing"}
        offenders = [
            f"{path.name}: {name}"
            for path in MODULE_ROOT.rglob("*.py")
            for name in _imported_top_level(path)
            if name.startswith("dotmac_") and name not in allowed
        ]
        assert not offenders, offenders
