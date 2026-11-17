"""Deploy-time check: every PUBLISHED revision still validates (ADR-0006 § 5h, TS-6).

A consumer runs this BEFORE bumping its template-studio pin (and after changing
a registered `RenderContext`): a release can tighten a rule, and a revision that
was valid when it was published may no longer be. The check runs
`validation.validate_revision` over every published revision of every tenant and
reports each failure instead of stopping at the first.

Tenant isolation: tenants are enumerated from the tenancy table through the
runtime's `resolver_session` (the sanctioned unscoped read of a table that is not
tenant-scoped), and each tenant's revisions are read inside that tenant's own
`tenant_session`, so row-level security applies exactly as it does at send time.
No BYPASSRLS role and no platform session is involved.

The report names the template slug, channel, revision number and the problems.
Never the template's content. A problem string that quotes a snippet of markup
is cut before it is reported.

    python -m dotmac_template_studio.deploycheck --import app.assembly

`--import` names the consumer module that registers its render contexts (a
context the process has not registered is itself reported as a failure, so an
omitted import cannot pass silently). Exit status is 0 when every revision
validates, 1 on any failure, 2 on a usage or setup error.
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from dotmac_template_studio import service
from dotmac_template_studio.contexts import UnknownRenderContextError, get_context
from dotmac_template_studio.validation import validate_revision

_SNIPPET = re.compile(r"\s*\(first difference:.*$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class RevisionFailure:
    tenant_id: UUID
    slug: str
    channel: str
    version: int
    problems: tuple[str, ...]


@dataclass(slots=True)
class DeployCheckReport:
    tenants_checked: int = 0
    revisions_checked: int = 0
    failures: list[RevisionFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def lines(self) -> list[str]:
        out = [
            f"template-studio deploy check: {self.tenants_checked} tenant(s), "
            f"{self.revisions_checked} published revision(s), "
            f"{len(self.failures)} failing"
        ]
        for f in self.failures:
            out.append(f"FAIL tenant={f.tenant_id} {f.slug}/{f.channel} v{f.version}")
            out.extend(f"  - {p}" for p in f.problems)
        return out


def _clean(problem: str) -> str:
    return _SNIPPET.sub(" (content omitted)", problem)


def check_tenant(db: Any, tenant_id: UUID) -> tuple[int, list[RevisionFailure]]:
    """Validate one tenant's published revisions. `db` MUST be tenant-scoped."""
    failures: list[RevisionFailure] = []
    pairs = service.list_published_revisions(db, tenant_id)
    for template, revision in pairs:
        try:
            context = get_context(template.context)
        except UnknownRenderContextError:
            problems = [
                f"render context {template.context!r} is not registered in this "
                "deployment"
            ]
        else:
            problems = [
                _clean(p)
                for p in validate_revision(
                    context,
                    subject=revision.subject,
                    text=revision.body,
                    html=revision.html_body,
                )
            ]
        if problems:
            failures.append(
                RevisionFailure(
                    tenant_id=tenant_id,
                    slug=template.slug,
                    channel=template.channel,
                    version=revision.version,
                    problems=tuple(problems),
                )
            )
    return len(pairs), failures


def check_published_revisions(
    *,
    list_tenant_ids: Callable[[], Iterable[UUID]],
    tenant_session: Callable[[UUID], AbstractContextManager[Any]],
) -> DeployCheckReport:
    """Run the check over every tenant.

    Both seams are injected: production passes the runtime's `resolver_session`
    based lister and `tenant_session`; tests pass SQLite equivalents. There is no
    default that would reach for an unscoped session here.
    """
    report = DeployCheckReport()
    for tenant_id in list(list_tenant_ids()):
        with tenant_session(tenant_id) as db:
            checked, failures = check_tenant(db, tenant_id)
        report.tenants_checked += 1
        report.revisions_checked += checked
        report.failures.extend(failures)
    return report


def _runtime_seams() -> (
    tuple[Callable[[], Iterable[UUID]], Callable[[UUID], AbstractContextManager[Any]]]
):
    from dotmac_kernel.models import Tenant
    from dotmac_kernel.session_runtime import resolve_database_runtime
    from sqlalchemy import select

    runtime = resolve_database_runtime()

    def list_tenant_ids() -> list[UUID]:
        # `tenants` is the tenancy table: deliberately not RLS-protected, read
        # through the resolver session precisely so a scope can be chosen.
        with runtime.resolver_session() as db:
            return list(db.execute(select(Tenant.id).order_by(Tenant.id)).scalars())

    return list_tenant_ids, runtime.tenant_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dotmac_template_studio.deploycheck",
        description="Validate every published template revision of every tenant.",
    )
    parser.add_argument(
        "--import",
        dest="imports",
        action="append",
        default=[],
        metavar="MODULE",
        help="consumer module to import first (registers its render contexts)",
    )
    args = parser.parse_args(argv)
    try:
        for name in args.imports:
            importlib.import_module(name)
        list_tenant_ids, tenant_session = _runtime_seams()
    except Exception as exc:  # setup failure: never a pass
        print(f"deploy check could not start: {type(exc).__name__}", file=sys.stderr)
        return 2
    report = check_published_revisions(
        list_tenant_ids=list_tenant_ids, tenant_session=tenant_session
    )
    print("\n".join(report.lines()))
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "DeployCheckReport",
    "RevisionFailure",
    "check_published_revisions",
    "check_tenant",
    "main",
]
