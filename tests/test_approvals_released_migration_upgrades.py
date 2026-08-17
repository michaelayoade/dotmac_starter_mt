"""Every released ``ap_0001`` byte set upgrades to the canonical lineage.

Approvals shipped three different files under one revision id before the
released-migration guard enrolled it.  The architecture guard preserves the
exact tag/digest census; these PostgreSQL cases preserve the other half of the
obligation: a database produced by each historical meaning can move forward
without re-running ``ap_0001``, losing its rows, or inventing a different plane.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
CURRENT_APPROVALS = (
    REPO_ROOT / "packages/dotmac-approvals/src/dotmac_approvals/migrations/versions"
)
MIGRATION_PATH = (
    "packages/dotmac-approvals/src/dotmac_approvals/migrations/versions/"
    "ap_0001_approvals.py"
)
GIT = shutil.which("git")
assert GIT is not None, "git is required to reconstruct released migration bytes"

TENANT_TABLES = (
    "approval_policies",
    "approval_requests",
    "approval_decisions",
)
PLATFORM_TABLES = (
    "platform_approval_policies",
    "platform_approval_requests",
    "platform_approval_decisions",
)
DIGEST = "sha256:" + "a" * 64


@dataclass(frozen=True)
class HistoricalCase:
    tag: str
    digest: str
    planes: tuple[ModulePlane, ...]
    tenant_binding: bool


CASES = (
    # a1 always built both planes, so preserving both is the only truthful
    # selection when its database moves to the explicit-selection contract.
    HistoricalCase(
        "dotmac-approvals-v0.1.0a1",
        "ec5e1aa9e504de8143eebaafacb0615cf24b6ea930648f5b9cfd1a9afc2db70e",
        (ModulePlane.TENANT, ModulePlane.PLATFORM),
        True,
    ),
    # a2 always built platform and inferred tenant from provider availability.
    HistoricalCase(
        "dotmac-approvals-v0.1.0a2",
        "6c7b3263e05f860982dda125439171f62bba716d36d95b21e2c3a3224f19ad6a",
        (ModulePlane.PLATFORM,),
        False,
    ),
    HistoricalCase(
        "dotmac-approvals-v0.1.0a2",
        "6c7b3263e05f860982dda125439171f62bba716d36d95b21e2c3a3224f19ad6a",
        (ModulePlane.TENANT, ModulePlane.PLATFORM),
        True,
    ),
    # a3 and a4 have byte-identical explicit selection. One run per supported
    # selection exercises that byte set; the digest map proves both tags carry it.
    HistoricalCase(
        "dotmac-approvals-v0.1.0a3",
        "102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb",
        (ModulePlane.TENANT,),
        True,
    ),
    HistoricalCase(
        "dotmac-approvals-v0.1.0a3",
        "102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb",
        (ModulePlane.PLATFORM,),
        True,
    ),
    HistoricalCase(
        "dotmac-approvals-v0.1.0a3",
        "102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb",
        (ModulePlane.TENANT, ModulePlane.PLATFORM),
        True,
    ),
)


def _case_id(case: HistoricalCase) -> str:
    version = case.tag.rsplit("a", 1)[-1]
    planes = "+".join(plane.value for plane in case.planes)
    binding = "tenant-bound" if case.tenant_binding else "tenant-unbound"
    return f"a{version}-{planes}-{binding}"


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — upgrade proofs need PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


def _released_source(case: HistoricalCase) -> bytes:
    result = subprocess.run(  # noqa: S603 # nosec B603 B607
        [GIT, "show", f"{case.tag}:{MIGRATION_PATH}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert hashlib.sha256(result.stdout).hexdigest() == case.digest
    return result.stdout


def _config(versions: Path, admin_url: str, case: HistoricalCase) -> Any:
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option(
        "version_locations",
        f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {versions}",
    )
    cfg.attributes["module_plane_selections"] = (
        ModulePlaneSelection(module="approvals", planes=case.planes),
    )
    return cfg


def _seed_history(admin_url: str, planes: tuple[ModulePlane, ...]) -> None:
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        tenant_id = uuid.uuid4()
        if ModulePlane.TENANT in planes:
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, 'Approval upgrade proof')"
                ),
                {"id": tenant_id, "slug": f"approval-proof-{tenant_id.hex[:8]}"},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.approval_policies "
                    "(id, tenant_id, policy_code, version, levels, "
                    "allow_self_approval, document_digest) VALUES "
                    "(:id, :tenant, 'proof.tenant', 1, CAST('[]' AS json), "
                    "false, :digest)"
                ),
                {"id": uuid.uuid4(), "tenant": tenant_id, "digest": DIGEST},
            )
        if ModulePlane.PLATFORM in planes:
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_policies "
                    "(id, policy_code, version, levels, allow_self_approval, "
                    "document_digest) VALUES "
                    "(:id, 'proof.platform', 1, CAST('[]' AS json), false, :digest)"
                ),
                {"id": uuid.uuid4(), "digest": DIGEST},
            )
    engine.dispose()


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_each_released_approvals_shape_upgrades_without_rewriting_history(
    case: HistoricalCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dotmac_kernel.prerequisites as prerequisites

    import app.migration_bindings as assembly_bindings
    from alembic import command

    historical = tmp_path / "historical_approvals"
    historical.mkdir()
    (historical / "ap_0001_approvals.py").write_bytes(_released_source(case))

    full_bindings = tuple(assembly_bindings.ASSEMBLY_PREREQUISITE_BINDINGS)
    bindings = tuple(
        binding
        for binding in full_bindings
        if case.tenant_binding or binding.prerequisite != "tenant_scope_catalog.v1"
    )
    monkeypatch.setattr(assembly_bindings, "ASSEMBLY_PREREQUISITE_BINDINGS", bindings)

    # a2 used the removed `optional=` graph API. This compatibility adapter is
    # fixture machinery only: it reconstructs that release's exact semantics
    # (include the optional edge iff bound) without altering the tagged bytes.
    if case.tag.endswith("a2"):
        current_resolver = prerequisites.resolve_depends_on

        def historical_resolver(
            names: tuple[str, ...],
            *,
            optional: tuple[str, ...] = (),
            **kwargs: Any,
        ) -> tuple[str, ...]:
            selected = tuple(names) + tuple(
                name for name in optional if prerequisites.is_bound(name)
            )
            return current_resolver(selected, **kwargs)

        monkeypatch.setattr(prerequisites, "resolve_depends_on", historical_resolver)

    superuser = _superuser_url()
    name = f"approvals_upgrade_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    try:
        command.upgrade(_config(historical, admin_url, case), "heads")
        _seed_history(admin_url, case.planes)
        command.upgrade(_config(CURRENT_APPROVALS, admin_url, case), "heads")

        engine = create_engine(admin_url)
        with engine.connect() as conn:
            heads = set(conn.execute(text("SELECT version_num FROM alembic_version")))
            assert ("ap_0002_outbox_relay",) in heads
            for table in TENANT_TABLES:
                exists = conn.execute(
                    text("SELECT to_regclass(:table) IS NOT NULL"),
                    {"table": f"mod_approvals.{table}"},
                ).scalar_one()
                assert exists is (ModulePlane.TENANT in case.planes)
            for table in PLATFORM_TABLES:
                exists = conn.execute(
                    text("SELECT to_regclass(:table) IS NOT NULL"),
                    {"table": f"mod_approvals.{table}"},
                ).scalar_one()
                assert exists is (ModulePlane.PLATFORM in case.planes)
            if ModulePlane.TENANT in case.planes:
                assert (
                    conn.execute(
                        text(
                            "SELECT count(*) FROM mod_approvals.approval_policies "
                            "WHERE policy_code = 'proof.tenant'"
                        )
                    ).scalar_one()
                    == 1
                )
            if ModulePlane.PLATFORM in case.planes:
                assert (
                    conn.execute(
                        text(
                            "SELECT count(*) FROM "
                            "mod_approvals.platform_approval_policies "
                            "WHERE policy_code = 'proof.platform'"
                        )
                    ).scalar_one()
                    == 1
                )
        engine.dispose()
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()
