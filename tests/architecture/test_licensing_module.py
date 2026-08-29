"""Structural guards for `dotmac-licensing`, each with a sensitivity proof.

Two of the checks here have no equivalent in any other module's guard file, and
they are the reason this file exists rather than being a copy of the agreements
one:

- **No signing implementation ships.** A `LicenceSigner` in a shared library is a
  default that reaches every consumer. The source implementation's `ephemeral`
  mode was correct for a product and would be a hazard here.
- **No key-shaped column, name or dependency.** The absence has to be checked,
  because it is the kind of thing a helpful later change adds back.

Every detector is exercised twice — once against the real package and once
against a synthetic violation (ADR-0018). Several are regex or name sweeps, and a
sweep that matches nothing passes silently forever.

Static only; no database.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest
from dotmac_kernel.namespaces import (
    LICENSING_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    module_schema,
)
from dotmac_licensing import module

from tests.architecture import adoption_evidence as evidence_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-licensing"
SRC = PACKAGE_ROOT / "src/dotmac_licensing"
MIGRATION = SRC / "migrations/versions/li_0001_licensing.py"

SIBLING_ROOTS = frozenset(
    {
        "dotmac_template_studio",
        "dotmac_ticketing",
        "dotmac_release_catalog",
        "dotmac_entitlement_allocation",
        "dotmac_commercial_agreements",
        "dotmac_application_directory",
        "dotmac_files",
        "dotmac_imports",
        "dotmac_integration",
        "dotmac_approvals",
        "dotmac_numbering",
        "dotmac_people",
        "dotmac_durable_timers",
        "dotmac_auth_oidc",
        "dotmac_campaigns",
        "dotmac_ui",
        "app",
        "vendor_cp",
    }
)

#: Packages that would let this module hold or derive key material. `cryptography`
#: is the one that matters: importing it is the difference between a package that
#: NAMES signing material and one that could produce it.
CRYPTO_ROOTS = frozenset({"cryptography", "nacl", "ecdsa", "jwt", "jose"})


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imported_roots(source: str) -> set[str]:
    """Every top-level module name a source imports, from the AST.

    An AST walk rather than a regex: this package's docstrings name
    `cryptography`, `vendor_cp` and several siblings precisely to explain what it
    does NOT import, and a textual scan would flag every one of them.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


# ── Allocation parity ───────────────────────────────────────────────────────


class TestTheManifestMatchesTheLedger:
    def test_the_ledger_row_exists_and_is_registered(self) -> None:
        assert LICENSING_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER

    def test_every_manifest_field_matches_its_ledger_row(self) -> None:
        owner = LICENSING_MIGRATION_OWNER
        assert module.code == owner.owner == "licensing"
        assert module.migration_prefix == owner.prefix == "li"
        assert module.migration_branch == owner.branch_label == "licensing"
        assert module.db_schema == owner.db_schema == module_schema("licensing")

    def test_the_prefix_and_schema_are_unique_fleet_wide(self) -> None:
        prefixes = [owner.prefix for owner in MIGRATION_OWNER_LEDGER]
        schemas = [o.db_schema for o in MIGRATION_OWNER_LEDGER if o.db_schema]
        assert prefixes.count("li") == 1
        assert schemas.count(module_schema("licensing")) == 1


class TestThePlaneIsDeclaredNotDiscovered:
    def test_the_tenant_plane_is_empty(self) -> None:
        """Here the plane is a SECURITY boundary, not merely an absent consumer:
        issuance must not live inside the deployment it authorises."""
        assert module.tables == ()

    def test_the_declared_platform_tables_are_exactly_the_mapped_ones(self) -> None:
        from dotmac_licensing.models import (
            SCHEMA,
            Licence,
            LicenceAcknowledgement,
            LicenceIssuance,
            Revocation,
            RevocationList,
            SigningKey,
        )

        models = (
            SigningKey,
            Licence,
            LicenceIssuance,
            LicenceAcknowledgement,
            Revocation,
            RevocationList,
        )
        assert {m.__tablename__ for m in models} == set(module.platform_tables)
        assert all(m.__table__.schema == SCHEMA for m in models)

    def test_no_model_carries_a_tenant_column(self) -> None:
        from dotmac_licensing.models import (
            Licence,
            LicenceAcknowledgement,
            LicenceIssuance,
            Revocation,
            RevocationList,
            SigningKey,
        )

        for model in (
            SigningKey,
            Licence,
            LicenceIssuance,
            LicenceAcknowledgement,
            Revocation,
            RevocationList,
        ):
            assert "tenant_id" not in model.__table__.columns, model.__tablename__


# ── The two guards unique to this module ────────────────────────────────────


class TestNoSigningMaterialCanBeHeldOrDerived:
    def test_the_package_imports_no_crypto_library(self) -> None:
        """The difference between a package that NAMES signing material and one
        that could produce it. `cryptography` appears in this module's docstrings
        and in its tests; it must not appear in an import."""
        offenders: list[str] = []
        for path in _sources():
            for bad in sorted(_imported_roots(path.read_text()) & CRYPTO_ROOTS):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {bad}")
        assert not offenders, "\n".join(offenders)

    def test_the_distribution_declares_no_crypto_dependency(self) -> None:
        data = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
        declared = set(data["tool"]["poetry"]["dependencies"])
        assert not (declared & CRYPTO_ROOTS), declared & CRYPTO_ROOTS

    def test_no_column_anywhere_is_named_like_a_private_key(self) -> None:
        """Structural: a database dump cannot leak what the schema has no column
        for. Checked by NAME because that is how such a column would arrive — a
        later change adding `private_key_b64` beside `public_key_b64`."""
        from dotmac_licensing.models import (
            Licence,
            LicenceAcknowledgement,
            LicenceIssuance,
            Revocation,
            RevocationList,
            SigningKey,
        )

        banned = re.compile(
            r"private|secret|passphrase|seed|key_material|signing_key\b"
        )
        offenders: list[str] = []
        for model in (
            SigningKey,
            Licence,
            LicenceIssuance,
            LicenceAcknowledgement,
            Revocation,
            RevocationList,
        ):
            for column in model.__table__.columns:
                if banned.search(column.name):
                    offenders.append(f"{model.__tablename__}.{column.name}")
        assert not offenders, offenders

    def test_the_migration_creates_no_such_column_either(self) -> None:
        """The models and the migration are two artifacts. A column added to one
        and not the other is a different defect, and both would leak."""
        sql = MIGRATION.read_text()
        columns = re.findall(r'sa\.Column\(\s*"([a-z_0-9]+)"', sql)
        banned = re.compile(r"private|secret|passphrase|seed|key_material")
        assert not [name for name in columns if banned.search(name)]

    def test_no_source_file_reads_a_file_an_env_var_or_a_network(self) -> None:
        """Custody is the product's (ADR-0009). A module that opened a path or
        read an environment variable would be doing custody by another name —
        the exact shape the source implementation had, correctly, as a product."""
        forbidden = re.compile(
            r"\bopen\s*\(|\bPath\s*\([^)]*\)\s*\.\s*read|os\.environ|"
            r"getenv\s*\(|\brequests\b|\bhttpx\b|socket\."
        )
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in _sources()
            if forbidden.search(path.read_text())
        ]
        assert not offenders, offenders

    def test_the_detectors_fire_against_synthetic_violations(self) -> None:
        """Sensitivity proof (ADR-0018) for all four sweeps above."""
        assert (
            _imported_roots("from cryptography.hazmat.primitives import x")
            & CRYPTO_ROOTS
        )
        assert (
            not _imported_roots("from dotmac_kernel.licensing import x") & CRYPTO_ROOTS
        )
        banned = re.compile(r"private|secret|passphrase|seed|key_material")
        assert banned.search("private_key_b64")
        assert not banned.search("public_key_b64")
        forbidden = re.compile(
            r"\bopen\s*\(|\bPath\s*\([^)]*\)\s*\.\s*read|os\.environ|getenv\s*\("
        )
        assert forbidden.search('key = open("/run/secrets/k").read()')
        assert forbidden.search('mode = os.environ["SIGNING_MODE"]')
        assert not forbidden.search("row = session.get(Licence, licence_id)")


class TestThePackageShipsNoSignerImplementation:
    def test_no_public_name_satisfies_the_signer_protocol(self) -> None:
        """A signer in a shared library is a default that ships to every
        consumer, and a default signer silently becomes a real issuer."""
        import dotmac_licensing
        from dotmac_licensing.ports import LicenceSigner

        offenders = []
        for name in dotmac_licensing.__all__:
            value = getattr(dotmac_licensing, name)
            if not isinstance(value, type) or value is LicenceSigner:
                continue
            if all(
                hasattr(value, member)
                for member in ("key_id", "public_key_b64", "sign")
            ):
                offenders.append(name)
        assert not offenders, offenders

    def test_no_source_file_defines_a_sign_method(self) -> None:
        offenders: list[str] = []
        for path in _sources():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for member in node.body:
                        if (
                            isinstance(member, ast.FunctionDef)
                            and member.name == "sign"
                            and not _is_protocol(node)
                        ):
                            offenders.append(
                                f"{path.relative_to(REPO_ROOT)}::{node.name}.sign"
                            )
        assert not offenders, offenders

    def test_the_detector_fires_against_a_synthetic_violation(self) -> None:
        """Sensitivity proof (ADR-0018)."""
        source = "class Real:\n    def sign(self, payload):\n        return b''\n"
        tree = ast.parse(source)
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert classes and not _is_protocol(classes[0])
        assert any(
            isinstance(m, ast.FunctionDef) and m.name == "sign" for m in classes[0].body
        )


def _is_protocol(node: ast.ClassDef) -> bool:
    """A `Protocol` declares a signature; it does not implement one."""
    return any(
        (isinstance(base, ast.Name) and base.id == "Protocol")
        or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
        for base in node.bases
    )


# ── Independence and transaction authority ──────────────────────────────────


class TestTheModuleImportsNoSibling:
    def test_no_source_file_imports_another_distribution(self) -> None:
        offenders: list[str] = []
        for path in _sources():
            for bad in sorted(_imported_roots(path.read_text()) & SIBLING_ROOTS):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {bad}")
        assert not offenders, "\n".join(offenders)

    def test_the_detector_fires_against_a_synthetic_violation(self) -> None:
        assert (
            _imported_roots("from dotmac_commercial_agreements import get")
            & SIBLING_ROOTS
        )
        assert not _imported_roots("from dotmac_kernel.audit import x") & SIBLING_ROOTS


class TestTheModuleOwnsNoTransaction:
    _FORBIDDEN = re.compile(
        r"\b(?:db|session|self\._session)\.(?:commit|rollback)\s*\(|"
        r"\bsessionmaker\s*\(|\bSessionLocal\s*\(|\bPlatformSessionLocal\s*\(|"
        r"\bSession\s*\([^)]"
    )

    def test_no_source_file_commits_rolls_back_or_builds_a_session(self) -> None:
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in _sources()
            if self._FORBIDDEN.search(path.read_text())
        ]
        assert not offenders, offenders

    def test_the_detector_fires_against_a_synthetic_violation(self) -> None:
        assert self._FORBIDDEN.search("    db.commit()")
        assert not self._FORBIDDEN.search("    session.flush()")


class TestNoDeliveryTransportCameAcross:
    """ADR-0024 / hard rule 28: transport is the Integrator's.

    Roughly 1,600 LOC of the source is delivery — `transport.py`,
    `delivery_models.py` and the delivery half of `projection.py`. It is easy to
    bring one helper across "just for convenience" and end up with a second
    delivery authority, which is the failure this guards.

    Scans IDENTIFIERS from the AST rather than sweeping text, and the difference
    matters here more than anywhere else in this file: this module's prose names
    every piece of transport state precisely to explain what it does NOT own, so
    a text sweep would flag the documentation that exists to prevent the defect.
    """

    _TRANSPORT_NAMES = frozenset(
        {
            "connection_ref",
            "attempt_no",
            "attempt_count",
            "retry_after",
            "retry_count",
            "transport",
            "webhook",
            "endpoint_url",
            "delivery_attempt",
            "delivery_id",
            "checkpoint",
        }
    )

    def _identifiers(self, source: str) -> set[str]:
        """Every name this source DEFINES or READS, from the AST.

        Attribute names, argument names, assignment targets, class and function
        names. Deliberately not string contents: a docstring saying
        "`transport.py` stayed with the Integrator" is the opposite of a
        violation.
        """
        names: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.ClassDef | ast.FunctionDef):
                names.add(node.name)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
        return names

    def test_no_source_file_names_transport_state(self) -> None:
        offenders: list[str] = []
        for path in _sources():
            for bad in sorted(
                self._identifiers(path.read_text()) & self._TRANSPORT_NAMES
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)} names {bad}")
        assert not offenders, "\n".join(offenders)

    def test_the_detector_fires_against_a_synthetic_violation(self) -> None:
        """Sensitivity proof (ADR-0018), including the false positive it must
        NOT produce."""
        assert self._identifiers("row.attempt_no = 3") & self._TRANSPORT_NAMES
        assert (
            self._identifiers("def send(connection_ref): pass") & self._TRANSPORT_NAMES
        )
        # Prose about what the module does not own is not a violation.
        assert not (
            self._identifiers('"""transport.py stayed with the Integrator."""')
            & self._TRANSPORT_NAMES
        )
        assert not (
            self._identifiers("row.digest = payload_digest(payload)")
            & self._TRANSPORT_NAMES
        )


# ── The published vocabulary ────────────────────────────────────────────────


class TestThePublishedFactsMatchWhatIsEmitted:
    def test_every_emitted_event_type_is_published(self) -> None:
        from dotmac_licensing import PUBLISHED_EVENT_TYPES
        from dotmac_licensing import facts as facts_module

        emitted = {
            value
            for name, value in vars(facts_module).items()
            if (name.startswith("LICENCE_") or name.startswith("REVOCATION_LIST_"))
            and isinstance(value, str)
        }
        assert emitted == set(PUBLISHED_EVENT_TYPES)

    def test_every_published_type_is_referenced_by_the_service(self) -> None:
        from dotmac_licensing import PUBLISHED_EVENT_TYPES
        from dotmac_licensing import facts as facts_module

        service_source = (SRC / "service.py").read_text()
        by_value = {
            value: name
            for name, value in vars(facts_module).items()
            if isinstance(value, str) and value in PUBLISHED_EVENT_TYPES
        }
        unreferenced = [
            value
            for value in PUBLISHED_EVENT_TYPES
            if f"facts.{by_value[value]}" not in service_source
        ]
        assert not unreferenced, unreferenced

    def test_every_type_carries_an_explicit_version_suffix(self) -> None:
        from dotmac_licensing import PUBLISHED_EVENT_TYPES

        assert all(re.search(r"\.v\d+$", t) for t in PUBLISHED_EVENT_TYPES)

    def test_no_fact_payload_carries_a_signed_envelope(self) -> None:
        """A signed licence is exactly the artifact that grants authority
        wherever it lands; putting one in an outbox row copies it into every
        relay log, dead-letter dump and consumer's storage."""
        service_source = (SRC / "service.py").read_text()
        emit_blocks = re.findall(
            r"_audit_and_emit\((.*?)\n        \)", service_source, re.S
        )
        assert emit_blocks, "the emit-site scan found nothing to check"
        for block in emit_blocks:
            assert "envelope" not in block, block[:120]


class TestTheAuditActionsAreDeclaredAndConsumed:
    def test_the_manifest_declares_exactly_the_three_the_service_writes(
        self,
    ) -> None:
        from dotmac_licensing import (
            AUDIT_ACTION_ACKNOWLEDGED,
            AUDIT_ACTION_ISSUED,
            AUDIT_ACTION_TRANSITIONED,
        )

        assert set(module.audit_actions) == {
            AUDIT_ACTION_ISSUED,
            AUDIT_ACTION_TRANSITIONED,
            AUDIT_ACTION_ACKNOWLEDGED,
        }
        service_source = (SRC / "service.py").read_text()
        for name in (
            "AUDIT_ACTION_ISSUED",
            "AUDIT_ACTION_TRANSITIONED",
            "AUDIT_ACTION_ACKNOWLEDGED",
        ):
            assert f"action={name}" in service_source, name


# ── The migration ───────────────────────────────────────────────────────────


class TestTheMigrationStatesItsWholeAccessSurface:
    @pytest.fixture
    def sql(self) -> str:
        return MIGRATION.read_text()

    def test_every_table_is_revoked_from_the_tenant_app_role(self, sql: str) -> None:
        for table in module.platform_tables:
            assert f'_revoke("{table}")' in sql, table

    def test_the_online_platform_role_can_reach_every_table(self, sql: str) -> None:
        """Declared-and-unusable is a violation too (hard rule 27)."""
        assert "GRANT USAGE ON SCHEMA mod_licensing TO platform_api" in sql
        for table in module.platform_tables:
            assert re.search(
                rf'_grant\("[A-Z, ]*SELECT[A-Z, ]*", "{table}", "platform_api"\)',
                sql,
            ), table

    def test_the_three_evidence_tables_grant_no_update_or_delete_to_any_role(
        self, sql: str
    ) -> None:
        for table in (
            "licence_acknowledgements",
            "revocations",
            "revocation_lists",
        ):
            for privileges, role in re.findall(
                rf'_grant\("([A-Z, ]+)", "{table}", "(\w+)"\)', sql
            ):
                assert "UPDATE" not in privileges, (table, role)
                assert "DELETE" not in privileges, (table, role)

    def test_the_append_only_trigger_covers_all_three_evidence_tables(
        self, sql: str
    ) -> None:
        for table in (
            "licence_acknowledgements",
            "revocations",
            "revocation_lists",
        ):
            assert f"BEFORE UPDATE OR DELETE ON mod_licensing.{table}" in sql

    def test_the_uniqueness_rules_that_prevent_double_authority_are_present(
        self, sql: str
    ) -> None:
        """One issued version per allocation, and one row per digest. The service
        checks both; the constraints police the path that never calls it."""
        assert (
            'sa.UniqueConstraint("allocation_ref", name="uq_issuance_allocation")'
            in sql
        )
        assert 'sa.UniqueConstraint("digest", name="uq_issuance_digest")' in sql

    def test_the_schema_is_a_literal_and_fully_qualified(self, sql: str) -> None:
        assert 'schema="mod_licensing"' in sql
        assert "search_path" not in sql

    def test_the_lineage_is_a_root_naming_its_own_branch(self, sql: str) -> None:
        assert 'revision = "li_0001_licensing"' in sql
        assert "down_revision = None" in sql
        assert 'branch_labels = ("licensing",)' in sql
        assert len("li_0001_licensing") <= 32

    def test_it_names_no_foreign_revision(self, sql: str) -> None:
        assert "depends_on = resolve_depends_on(COMMON_REQUIRES)" in sql
        assert not re.search(r'depends_on\s*=\s*[\'"]', sql)

    def test_it_verifies_both_prerequisites_before_any_ddl(self, sql: str) -> None:
        verify_at = sql.index("require_prerequisites(op.get_bind(), REQUIRES)")
        first_ddl = sql.index("CREATE SCHEMA IF NOT EXISTS mod_licensing")
        assert verify_at < first_ddl
        assert set(module.requires) == {
            "idempotency_ledger.v1",
            "platform_audit_log.v1",
        }

    def test_the_grant_sweep_fires_against_a_synthetic_violation(self) -> None:
        """Sensitivity proof (ADR-0018)."""
        leaky = '_grant("SELECT, DELETE", "revocations", "app_admin")'
        found = re.findall(r'_grant\("([A-Z, ]+)", "revocations", "(\w+)"\)', leaky)
        assert found and "DELETE" in found[0][0]
        # And the revoke sweep, which reads the other helper.
        assert '_revoke("revocations")' not in leaky


# ── The dossier ─────────────────────────────────────────────────────────────


class TestTheExtractionDossierIsHonest:
    @pytest.fixture
    def dossier(self) -> dict[str, object]:
        return tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())

    def test_it_names_the_qualifying_source(self, dossier: dict[str, object]) -> None:
        assert dossier["source_mode"] == "product-first"
        assert "dotmac_vendor_control_plane" in dossier["source_repositories"]  # type: ignore[operator]
        assert any(
            "vendor_cp/licensing/service.py" in path
            for path in dossier["source_paths"]  # type: ignore[union-attr]
        )

    def test_its_status_matches_the_evidence_it_holds(
        self, dossier: dict[str, object]
    ) -> None:
        """The defect ADR-0031 seals against, checked in BOTH directions.

        This used to assert `status == "audit-complete"` with empty evidence.
        That was true when written and became false the moment the vendor
        control plane ran the module in production — so the guard failed on the
        change that made the dossier HONEST rather than on one that made it
        dishonest.

        A literal pinned the state of the world at authoring time. What the test
        NAME claims is a consistency property between two fields, and that
        survives the transition whichever way they move. `adopted` without
        evidence is the overclaim ADR-0031 seals against; evidence without
        `adopted` is the same defect pointing the other way, and it is the one
        that had set in here.

        Scoped to THIS dossier deliberately. It is not asserted repo-wide,
        because it does not hold repo-wide today — see the PR description.
        """
        status = dossier["status"]
        evidence = dossier["adoption_evidence"]
        consumers = dossier["contract_consumers"]
        assert isinstance(evidence, list)
        assert isinstance(consumers, list)

        if status == "audit-complete":
            assert evidence == [], "audit-complete cannot carry adoption evidence"
            assert consumers == [], "audit-complete cannot name a proven consumer"
        else:
            assert status in {"adopted", "reuse-proven"}, status
            assert evidence, "an adopted dossier must cite what it ran"
            assert consumers, "an adopted dossier must name who ran it"

    def test_adoption_evidence_is_re_checkable_not_merely_addressable(
        self, dossier: dict[str, object]
    ) -> None:
        """AdoptionEvidenceV1: every row is frozen to an immutable commit.

        This test used to check that a string split on a colon into two
        non-empty halves. That is ADDRESSABILITY — it says a reader could find
        something — and it passed a stale pin, a seven-hex abbreviation of a
        branch name and a run id with no commit, all on one field on one day.
        Immutability is the different property, and it is the one
        `dotmac_governance` ADR 0013 actually requires.
        """
        rows = dossier["adoption_evidence"]
        assert rows, "an adopted dossier must cite what it ran"
        for row in rows:  # type: ignore[union-attr]
            assert isinstance(row, dict), row
            assert row["kind"] in (
                evidence_schema.ASSERTION_KINDS | evidence_schema.ATTESTATION_KINDS
            ), row
            assert evidence_schema.IMMUTABLE_COMMIT.fullmatch(row["commit"]), row
            assert row["repository"] == "dotmac_vendor_control_plane"
        pin = next(r for r in rows if r["kind"] == "pinned_at")  # type: ignore[union-attr]
        assert pin["commit"] == "af9fcf6d3fbd259fbef6b589d37b39d548f7ba8e"
        assert pin["field"] == "tool.poetry.dependencies.dotmac-licensing.version"
        assert pin["expected"] == "0.1.0a1"

    def test_the_dossier_holds_no_copy_of_the_live_pin(
        self, dossier: dict[str, object]
    ) -> None:
        """The present tense is POINTED AT, never copied.

        A value copied out of the consumer's bill of materials into this file
        has no build that fails when it drifts, which is how a pin went stale in
        twenty minutes. A pointer's failure mode is loud instead.
        """
        pointer = dossier["adoption_evidence_pointer"]
        assert [p["subject"] for p in pointer] == ["current_pin"]  # type: ignore[union-attr]
        assert set(pointer[0]) <= evidence_schema.POINTER_FIELDS  # type: ignore[index]
        assert not (
            set(pointer[0]) & evidence_schema.POINTER_VALUE_FIELDS  # type: ignore[index]
        )

    def test_it_records_the_byte_for_byte_migration_constraint(
        self, dossier: dict[str, object]
    ) -> None:
        """The constraint with no equivalent in the other modules: re-serialising
        an envelope changes its digest, which invalidates the signature and turns
        every deployed licence into one the receiver rejects."""
        shadow = str(dossier["shadow_and_drift"])
        assert "BYTE-FOR-BYTE" in shadow
        assert "cumulative rule spans the cutover" in shadow

    def test_it_records_that_delivery_is_not_retired_by_this_extraction(
        self, dossier: dict[str, object]
    ) -> None:
        """Conflating the two retirements would strand delivery with no owner."""
        retirement = str(dossier["local_copy_retirement"])
        assert "transport.py" in retirement
        assert "NOT retired by this extraction" in retirement
