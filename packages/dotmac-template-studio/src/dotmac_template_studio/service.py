"""Template Studio's business logic — the ONE owner of every decision here.

`router.py` and `web.py` are thin adapters: they validate, authorize, delegate,
and shape a response. Every query, every transition, every invariant is in this
module (hard rule 1, `tests/architecture/test_thin_wrappers.py`).

## Owned decisions

- **Version allocation.** A version number is `max(existing) + 1` within
  `(tenant, template)`, computed here and nowhere else. The unique constraint
  `uq_template_versions_number` is the backstop, not the allocator.
- **Publication.** Publishing sets `templates.published_version` and stamps the
  version's `published_at`. A template has at most one published version; the
  previous one is not deleted, which is the entire point of keeping revisions.
- **Immutability of a published revision.** Editing content creates a NEW
  version. `update_version` refuses to touch a revision that has been published
  — otherwise "what was sent" silently changes after the fact, which is the one
  guarantee an audit of customer-facing messages needs.
- **Variable extraction.** The declared variable list is derived from the body by
  `extract_variables`, so a caller cannot claim a variable the body never uses
  (or omit one it does).

## Transactions

Every mutator FLUSHES and leaves the commit to the caller's session scope —
`dotmac_kernel.db` is the one transaction authority (hard rule 8), and no
service here calls `db.rollback()` (hard rule 9).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.exceptions import BadRequestError, ConflictError, NotFoundError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_template_studio.models import (
    TEMPLATE_KINDS,
    Template,
    TemplateVersion,
)

# `{{ name }}` / `{{name}}` — the one placeholder syntax this module renders.
# Deliberately NOT a Jinja environment: a tenant-authored body is untrusted
# input, and handing it to a template engine would hand an operator arbitrary
# expression evaluation inside the server process. Substitution only.
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$")


def extract_variables(body: str, subject: str | None = None) -> list[str]:
    """The placeholder names a revision's content actually references, sorted.

    Derived rather than declared, so the stored list cannot drift from the body.
    """
    found = set(_PLACEHOLDER.findall(body))
    if subject:
        found |= set(_PLACEHOLDER.findall(subject))
    return sorted(found)


def render(body: str, values: dict[str, str], *, strict: bool = True) -> str:
    """Substitute `{{ name }}` placeholders with `values`.

    `strict` (the default) raises on a placeholder with no supplied value, so a
    half-substituted message is never produced. A caller that genuinely wants
    best-effort output — a preview screen — passes `strict=False` and gets the
    raw placeholder left in place.
    """
    missing: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in values:
            return values[name]
        missing.append(name)
        return match.group(0)

    rendered = _PLACEHOLDER.sub(_sub, body)
    if strict and missing:
        names = ", ".join(sorted(set(missing)))
        raise BadRequestError(f"missing value(s) for template variable(s): {names}")
    return rendered


# ── Templates ───────────────────────────────────────────────────────────────


def list_templates(
    db: Session, tenant_id: UUID, *, kind: str | None = None
) -> list[Template]:
    stmt = select(Template).where(Template.tenant_id == tenant_id)
    if kind is not None:
        stmt = stmt.where(Template.kind == kind)
    return list(db.execute(stmt.order_by(Template.kind, Template.slug)).scalars())


def get_template(db: Session, tenant_id: UUID, template_id: UUID) -> Template:
    stmt = select(Template).where(
        Template.tenant_id == tenant_id, Template.id == template_id
    )
    template = db.execute(stmt).scalar_one_or_none()
    if template is None:
        raise NotFoundError(f"template {template_id} not found")
    return template


def get_by_slug(db: Session, tenant_id: UUID, kind: str, slug: str) -> Template:
    """Look a template up the way a CALLER does — by its stable `(kind, slug)`."""
    stmt = select(Template).where(
        Template.tenant_id == tenant_id,
        Template.kind == kind,
        Template.slug == slug,
    )
    template = db.execute(stmt).scalar_one_or_none()
    if template is None:
        raise NotFoundError(f"template {kind}/{slug} not found")
    return template


def create_template(
    db: Session,
    tenant_id: UUID,
    *,
    kind: str,
    slug: str,
    name: str,
    description: str | None = None,
    channel: str | None = None,
) -> Template:
    if kind not in TEMPLATE_KINDS:
        raise BadRequestError(
            f"unknown template kind {kind!r} — expected one of "
            f"{', '.join(TEMPLATE_KINDS)}"
        )
    if not _SLUG.match(slug):
        raise BadRequestError(
            f"slug {slug!r} must be lowercase alphanumeric with hyphens — it is "
            "the stable identifier a caller looks the template up by"
        )
    existing = db.execute(
        select(Template.id).where(
            Template.tenant_id == tenant_id,
            Template.kind == kind,
            Template.slug == slug,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"a {kind} template with slug {slug!r} already exists")

    template = Template(
        tenant_id=tenant_id,
        kind=kind,
        slug=slug,
        name=name,
        description=description,
        channel=channel,
    )
    db.add(template)
    db.flush()
    return template


def update_template(
    db: Session,
    tenant_id: UUID,
    template_id: UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    channel: str | None = None,
    is_active: bool | None = None,
) -> Template:
    """Update a template's METADATA. Content changes create a version instead."""
    template = get_template(db, tenant_id, template_id)
    if name is not None:
        template.name = name
    if description is not None:
        template.description = description
    if channel is not None:
        template.channel = channel
    if is_active is not None:
        template.is_active = is_active
    db.flush()
    return template


def delete_template(db: Session, tenant_id: UUID, template_id: UUID) -> None:
    template = get_template(db, tenant_id, template_id)
    db.delete(template)
    db.flush()


# ── Versions ────────────────────────────────────────────────────────────────


def list_versions(
    db: Session, tenant_id: UUID, template_id: UUID
) -> list[TemplateVersion]:
    get_template(db, tenant_id, template_id)  # 404 rather than an empty list
    stmt = (
        select(TemplateVersion)
        .where(
            TemplateVersion.tenant_id == tenant_id,
            TemplateVersion.template_id == template_id,
        )
        .order_by(TemplateVersion.version.desc())
    )
    return list(db.execute(stmt).scalars())


def get_version(
    db: Session, tenant_id: UUID, template_id: UUID, version: int
) -> TemplateVersion:
    stmt = select(TemplateVersion).where(
        TemplateVersion.tenant_id == tenant_id,
        TemplateVersion.template_id == template_id,
        TemplateVersion.version == version,
    )
    found = db.execute(stmt).scalar_one_or_none()
    if found is None:
        raise NotFoundError(f"version {version} of template {template_id} not found")
    return found


def get_published(
    db: Session, tenant_id: UUID, template_id: UUID
) -> TemplateVersion | None:
    """The published revision, or None while a template has only drafts."""
    template = get_template(db, tenant_id, template_id)
    if template.published_version is None:
        return None
    return get_version(db, tenant_id, template_id, template.published_version)


def create_version(
    db: Session,
    tenant_id: UUID,
    template_id: UUID,
    *,
    body: str,
    subject: str | None = None,
    author_party_id: UUID | None = None,
) -> TemplateVersion:
    """Add a new revision. The version number is allocated here."""
    get_template(db, tenant_id, template_id)
    highest = db.execute(
        select(func.max(TemplateVersion.version)).where(
            TemplateVersion.tenant_id == tenant_id,
            TemplateVersion.template_id == template_id,
        )
    ).scalar()
    version = TemplateVersion(
        tenant_id=tenant_id,
        template_id=template_id,
        version=(highest or 0) + 1,
        subject=subject,
        body=body,
        variables=extract_variables(body, subject),
        author_party_id=author_party_id,
    )
    db.add(version)
    db.flush()
    return version


def update_version(
    db: Session,
    tenant_id: UUID,
    template_id: UUID,
    version: int,
    *,
    body: str | None = None,
    subject: str | None = None,
) -> TemplateVersion:
    """Edit a DRAFT revision in place.

    Refuses a published revision: "what was sent" must not change after the fact.
    Editing published content is a new version, which is what `create_version`
    is for.
    """
    revision = get_version(db, tenant_id, template_id, version)
    if revision.published_at is not None:
        raise ConflictError(
            f"version {version} is published and cannot be edited — create a new "
            "version instead"
        )
    if body is not None:
        revision.body = body
    if subject is not None:
        revision.subject = subject
    revision.variables = extract_variables(revision.body, revision.subject)
    db.flush()
    return revision


def publish_version(
    db: Session, tenant_id: UUID, template_id: UUID, version: int
) -> TemplateVersion:
    """Make one revision the published one. Idempotent."""
    template = get_template(db, tenant_id, template_id)
    revision = get_version(db, tenant_id, template_id, version)
    if revision.published_at is None:
        revision.published_at = datetime.now(UTC)
    template.published_version = version
    db.flush()
    return revision


def render_published(
    db: Session,
    tenant_id: UUID,
    kind: str,
    slug: str,
    values: dict[str, str],
    *,
    strict: bool = True,
) -> tuple[str | None, str]:
    """Render a template's PUBLISHED revision — the caller-facing entry point.

    Returns `(subject, body)`. Callers address a template by its stable
    `(kind, slug)` and never by a version number: which revision is live is this
    module's decision, not theirs.
    """
    template = get_by_slug(db, tenant_id, kind, slug)
    if not template.is_active:
        raise ConflictError(f"template {kind}/{slug} is not active")
    revision = get_published(db, tenant_id, template.id)
    if revision is None:
        raise ConflictError(f"template {kind}/{slug} has no published version")
    subject = (
        render(revision.subject, values, strict=strict) if revision.subject else None
    )
    return subject, render(revision.body, values, strict=strict)
