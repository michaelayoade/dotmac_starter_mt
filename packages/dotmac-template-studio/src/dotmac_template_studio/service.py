"""Template Studio's business logic — the ONE owner of every decision here.

`router.py` and `web.py` are thin adapters: they validate, authorize, delegate,
and shape a response. Every query, every transition, every invariant is in this
module (hard rule 1, `tests/architecture/test_thin_wrappers.py`).

## The placeholder contract (ADR-0006 § 5b)

**Single-brace `{variable}`, checked at save time against a registered
`RenderContext`.** This is Sub's contract, ported rather than reinvented: it is
the one in production, it is the one with behavioural proof, and its double-brace
predecessor is the syntax that leaked a literal `{{amount}}` to customers.

Three rules, and they work as a set:

1. **`{{double}}` braces are rejected at save time.** Not rendered-and-ignored —
   rejected. They are the failure mode this contract exists to prevent, so they
   fail loudly at authoring rather than quietly at send.
2. **A placeholder the template's context cannot supply is rejected at save
   time.** This is the load-bearing rule. A template that passes validation
   cannot produce a half-substituted message later, because every name it uses is
   known to exist before it can be published.
3. **Rendering is substitution, never evaluation.** Deliberately not a Jinja
   environment, sandboxed or otherwise: a tenant-authored body is untrusted
   input, and handing it to a template engine would give an operator expression
   evaluation inside the server process. This is why ERP's document templates are
   NOT served by this module — see the audit.

## Owned decisions

- **Version allocation.** `max(existing) + 1` within `(tenant, template)`,
  computed here and nowhere else. The unique constraint is the backstop, not the
  allocator.
- **Publication.** Publishing sets `templates.published_version` and stamps the
  version's `published_at`. The superseded revision is kept.
- **Immutability of a published revision.** Editing content creates a NEW
  version; `update_version` refuses a published one — otherwise "what was sent"
  silently changes after the fact.
- **Variable extraction and validation.** The declared list is derived from the
  body, so it cannot drift from the content, and validated against the template's
  context, so it cannot name something the send path lacks.

## Transactions

Every mutator FLUSHES and leaves the commit to the caller's session scope —
`dotmac_kernel.db` is the one transaction authority (hard rule 8), and no service
here calls `db.rollback()` (hard rule 9).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from dotmac_kernel.exceptions import BadRequestError, ConflictError, NotFoundError
from dotmac_kernel.flag_models import resolve_flag
from dotmac_kernel.settings_resolver import resolve as resolve_setting
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_template_studio.composition import (
    ComposedEmail as ComposedEmail,
)
from dotmac_template_studio.composition import (
    RenderedEmailPart as RenderedEmailPart,
)
from dotmac_template_studio.composition import (
    compose_email as compose_email,
)
from dotmac_template_studio.contexts import RenderContext, get_context
from dotmac_template_studio.htmlsanitize import sanitize_html
from dotmac_template_studio.layout import (
    LAYOUT_CHANNEL,
    LAYOUT_CONTEXT_NAME,
    LAYOUT_TARGET_CHANNEL,
    fragment_problem,
    layout_context,
    resolve_brand,
    wrap_part,
)
from dotmac_template_studio.models import Template, TemplateVersion
from dotmac_template_studio.rendering import (
    _PLACEHOLDER,
    MissingTemplateValueError,
    RenderRefusedError,
    render_part,
)
from dotmac_template_studio.rendering import (
    render as _render,
)
from dotmac_template_studio.sections import TemplateSyntaxError
from dotmac_template_studio.settings import REQUIRE_SEPARATE_PUBLISHER
from dotmac_template_studio.validation import check_revision, validate_revision

# Double-brace tokens, for save-time REJECTION only. Never rendered.
_DOUBLE_BRACE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$")
_CHANNEL = re.compile(r"^[a-z][a-z0-9_]{0,19}$")


def extract_variables(
    body: str, subject: str | None = None, html: str | None = None
) -> list[str]:
    """The placeholder names a revision's content actually references, sorted.

    Derived rather than declared, so the stored list cannot drift from the body.
    """
    found = set(_PLACEHOLDER.findall(body))
    if subject:
        found |= set(_PLACEHOLDER.findall(subject))
    if html:
        found |= set(_PLACEHOLDER.findall(html))
    return sorted(found)


def validate_template_text(
    *texts: str | None, context: RenderContext, kind_hint: str = "template"
) -> None:
    """Reject unsafe placeholder syntax before anything is stored.

    Raises `BadRequestError` if any text uses double braces, or names a variable
    the context cannot supply. Both problems are reported together — an author
    fixing one at a time is an avoidable round trip.
    """
    double: set[str] = set()
    unknown: set[str] = set()
    for text in texts:
        if not text:
            continue
        double.update(_DOUBLE_BRACE.findall(text))
        for name in _PLACEHOLDER.findall(text):
            if name not in context.variables:
                unknown.add(name)

    if not double and not unknown:
        return

    problems: list[str] = []
    if double:
        listed = ", ".join("{{" + name + "}}" for name in sorted(double))
        problems.append(
            "double braces are not a supported syntax — use single braces like "
            f"{{{sorted(context.variables)[0]}}}: {listed}"
        )
    if unknown:
        listed = ", ".join("{" + name + "}" for name in sorted(unknown))
        problems.append(
            f"variable(s) the {context.name!r} context cannot supply, which would "
            f"be sent literally: {listed}"
        )
    allowed = ", ".join("{" + name + "}" for name in context.sorted_variables())
    raise BadRequestError(
        f"{kind_hint} placeholder problem(s): "
        + "; ".join(problems)
        + f". Allowed for {context.name!r}: {allowed}."
    )


def render(body: str, values: dict[str, str], *, strict: bool = True) -> str:
    """Render through the pure seam, preserving the service's API error type."""
    try:
        return _render(body, values, strict=strict)
    except MissingTemplateValueError as exc:
        raise BadRequestError(str(exc)) from exc


# ── Templates ───────────────────────────────────────────────────────────────


def list_templates(
    db: Session, tenant_id: UUID, *, channel: str | None = None
) -> list[Template]:
    stmt = select(Template).where(Template.tenant_id == tenant_id)
    if channel is not None:
        stmt = stmt.where(Template.channel == channel)
    return list(db.execute(stmt.order_by(Template.slug, Template.channel)).scalars())


def get_template(db: Session, tenant_id: UUID, template_id: UUID) -> Template:
    stmt = select(Template).where(
        Template.tenant_id == tenant_id, Template.id == template_id
    )
    template = db.execute(stmt).scalar_one_or_none()
    if template is None:
        raise NotFoundError(f"template {template_id} not found")
    return template


def get_by_slug(db: Session, tenant_id: UUID, slug: str, channel: str) -> Template:
    """Look a template up the way a CALLER does — by `(slug, channel)`.

    Channel is part of the identity, not an attribute of it: one message often
    exists as an email and an SMS with different wording, and both are the
    "same" template to the code that sends it.
    """
    stmt = select(Template).where(
        Template.tenant_id == tenant_id,
        Template.slug == slug,
        Template.channel == channel,
    )
    template = db.execute(stmt).scalar_one_or_none()
    if template is None:
        raise NotFoundError(f"template {slug}/{channel} not found")
    return template


def create_template(
    db: Session,
    tenant_id: UUID,
    *,
    slug: str,
    channel: str,
    context: str,
    name: str,
    description: str | None = None,
) -> Template:
    if not _SLUG.match(slug):
        raise BadRequestError(
            f"slug {slug!r} must be lowercase alphanumeric with hyphens — it is "
            "the stable identifier a caller looks the template up by"
        )
    if not _CHANNEL.match(channel):
        raise BadRequestError(
            f"channel {channel!r} must be a lowercase code such as `email` or "
            "`sms` — it is part of the template's identity"
        )
    # Raises `UnknownRenderContextError` (a BadRequestError) naming the registry
    # as the fix. Validated here so a template can never exist with a context
    # that no send path implements.
    get_context(context)
    if (context == LAYOUT_CONTEXT_NAME) != (channel == LAYOUT_CHANNEL):
        raise BadRequestError(
            f"the {LAYOUT_CONTEXT_NAME!r} context and the {LAYOUT_CHANNEL!r} "
            "channel go together: a layout uses both, nothing else uses either"
        )

    existing = db.execute(
        select(Template.id).where(
            Template.tenant_id == tenant_id,
            Template.slug == slug,
            Template.channel == channel,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"a template with slug {slug!r} already exists on channel {channel!r}"
        )

    template = Template(
        tenant_id=tenant_id,
        slug=slug,
        channel=channel,
        context=context,
        name=name,
        description=description,
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
    is_active: bool | None = None,
) -> Template:
    """Update a template's METADATA.

    Content changes create a version instead. `slug`, `channel` and `context` are
    deliberately absent: the first two are the identity callers address, and
    changing the third would silently re-validate every existing revision against
    a different vocabulary. Those are a delete-and-recreate, not an edit.
    """
    template = get_template(db, tenant_id, template_id)
    if is_active is False and template.is_active:
        _refuse_if_layout_in_use(db, tenant_id, template, "deactivated")
    if name is not None:
        template.name = name
    if description is not None:
        template.description = description
    if is_active is not None:
        template.is_active = is_active
    db.flush()
    return template


def delete_template(db: Session, tenant_id: UUID, template_id: UUID) -> None:
    template = get_template(db, tenant_id, template_id)
    _refuse_if_layout_in_use(db, tenant_id, template, "deleted")
    db.delete(template)
    db.flush()


# ── Versions ────────────────────────────────────────────────────────────────


class _Unset:
    """Sentinel: "the caller did not mention this field"."""


_UNSET = _Unset()


def _stored_html(
    html: str | None,
    context: RenderContext,
    *,
    subject: str | None,
    body: str,
) -> str | None:
    """The sanitized html to store. Call only after `check_revision` passed it.

    `check_revision` already refuses any html the sanitizer would change, so the
    output equals the source up to re-serialisation; storing the OUTPUT (never
    the source) is what keeps unsanitized markup out of the table. The stored
    text is then validated AGAIN against the full revision rules, because what is
    stored is what renders: any difference between source and output that the
    source-side rules could not see is caught here, before anything is saved.
    """
    if html is None:
        return None
    result = sanitize_html(html)
    if result.problems:  # unreachable after check_revision; fail closed anyway
        raise BadRequestError("html part refused: " + "; ".join(result.problems))
    check_revision(
        context, subject=subject, text=body, html=result.html, kind_hint="stored html"
    )
    return result.html


# ── Layouts (TS-5) ──────────────────────────────────────────────────────────

_SAMPLE_TEXT = "Sample message content."
_SAMPLE_HTML = "<p>Sample message content.</p>"


def list_layouts(db: Session, tenant_id: UUID) -> list[Template]:
    """This tenant's layouts (templates on the `email_layout` channel)."""
    return list_templates(db, tenant_id, channel=LAYOUT_CHANNEL)


def _check_layout_opt_in(
    db: Session, tenant_id: UUID, template: Template, layout_slug: str | None
) -> None:
    """Save-time rule for a revision's opt-in: only email, and the layout exists."""
    if not layout_slug:
        return
    if template.channel != LAYOUT_TARGET_CHANNEL:
        raise BadRequestError(
            f"only {LAYOUT_TARGET_CHANNEL!r} templates may use a layout, not "
            f"{template.channel!r}"
        )
    try:
        layout = get_by_slug(db, tenant_id, layout_slug, LAYOUT_CHANNEL)
    except NotFoundError as exc:
        raise BadRequestError(f"layout {layout_slug!r} does not exist") from exc
    if not layout.is_active:
        raise BadRequestError(f"layout {layout_slug!r} is not active")


def _apply_layout(
    db: Session,
    tenant_id: UUID,
    layout_slug: str,
    text: str,
    html: str | None,
    *,
    layout_revision: TemplateVersion | None = None,
) -> tuple[str, str | None, TemplateVersion]:
    """Wrap already-rendered parts in the layout. Fail closed: never unwrapped.

    Uses the layout's PUBLISHED revision unless `layout_revision` is given (the
    candidate being published). The layout is re-validated against the CURRENT
    layout context first, so a changed brand vocabulary cannot send half a layout.
    """
    try:
        layout = get_by_slug(db, tenant_id, layout_slug, LAYOUT_CHANNEL)
    except NotFoundError as exc:
        raise RenderRefusedError(f"layout {layout_slug!r} does not exist") from exc
    if not layout.is_active:
        raise RenderRefusedError(f"layout {layout_slug!r} is not active")
    revision = layout_revision or get_published(db, tenant_id, layout.id)
    if revision is None:
        raise RenderRefusedError(f"layout {layout_slug!r} has no published version")
    context = layout_context()
    problems = validate_revision(context, text=revision.body, html=revision.html_body)
    if problems:
        raise RenderRefusedError(
            f"layout {layout_slug!r} no longer validates: " + "; ".join(problems)
        )
    brand = resolve_brand(db, tenant_id)
    wrapped_text = wrap_part(revision.body, "text", text, brand, context)
    wrapped_html = None
    if html is not None:
        if revision.html_body is None:
            raise RenderRefusedError(
                f"layout {layout_slug!r} has no html part but the message does"
            )
        wrapped_html = wrap_part(revision.html_body, "html", html, brand, context)
    return wrapped_text, wrapped_html, revision


def layout_users(db: Session, tenant_id: UUID, layout_slug: str) -> list[str]:
    """`slug/channel` of every template that still depends on this layout: its
    CURRENT published revision or any unpublished draft opts in. Older published
    revisions are immutable history, not a dependency (restoring one is
    re-validated and refused while the layout is unusable)."""
    stmt = (
        select(Template.slug, Template.channel)
        .join(
            TemplateVersion,
            (TemplateVersion.tenant_id == Template.tenant_id)
            & (TemplateVersion.template_id == Template.id),
        )
        .where(
            Template.tenant_id == tenant_id,
            TemplateVersion.layout_slug == layout_slug,
            (TemplateVersion.published_at.is_(None))
            | (TemplateVersion.version == Template.published_version),
        )
        .distinct()
        .order_by(Template.slug, Template.channel)
    )
    return [f"{slug}/{channel}" for slug, channel in db.execute(stmt).all()]


def layout_audit_users(db: Session, tenant_id: UUID, template_id: UUID) -> list[str]:
    """For audit payloads: the templates using this layout; empty for a non-layout."""
    template = get_template(db, tenant_id, template_id)
    if template.channel != LAYOUT_CHANNEL:
        return []
    return layout_users(db, tenant_id, template.slug)


def _refuse_if_layout_in_use(
    db: Session, tenant_id: UUID, template: Template, verb: str
) -> None:
    if template.channel != LAYOUT_CHANNEL:
        return
    users = layout_users(db, tenant_id, template.slug)
    if users:
        raise ConflictError(
            f"layout {template.slug!r} cannot be {verb} while these templates "
            f"use it: {', '.join(users)}. Publish a revision without the layout "
            "(and remove it from drafts) first."
        )


def _check_fragment_for_layout(layout_slug: str | None, html: str | None) -> None:
    """Save-time fragment rule for html that will be wrapped in a layout."""
    if layout_slug and html:
        problem = fragment_problem(html)
        if problem:
            raise BadRequestError(f"html part cannot be used with a layout: {problem}")


def _layout_dependents(
    db: Session, tenant_id: UUID, layout_slug: str
) -> list[tuple[Template, TemplateVersion]]:
    stmt = (
        select(Template, TemplateVersion)
        .join(
            TemplateVersion,
            (TemplateVersion.tenant_id == Template.tenant_id)
            & (TemplateVersion.template_id == Template.id)
            & (TemplateVersion.version == Template.published_version),
        )
        .where(
            Template.tenant_id == tenant_id, TemplateVersion.layout_slug == layout_slug
        )
        .order_by(Template.slug)
    )
    return [tuple(row) for row in db.execute(stmt).all()]


def layout_problems(
    db: Session,
    tenant_id: UUID,
    template: Template,
    revision: TemplateVersion,
) -> list[str]:
    """Dry-run problems of a revision's layout assembly with the context sample.

    Empty when the revision has no layout. Used at publish and by the deploy check.
    """
    if not revision.layout_slug:
        return []
    try:
        _render_sample(db, tenant_id, template, revision)
    except BadRequestError as exc:
        return [str(exc)]
    return []


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


def list_published_revisions(
    db: Session, tenant_id: UUID
) -> list[tuple[Template, TemplateVersion]]:
    """Every template of this tenant that has a published revision, with it.

    For the deploy-time check (`deploycheck`). Inactive templates are included:
    reactivating one must not publish something that no longer validates.
    """
    stmt = (
        select(Template, TemplateVersion)
        .join(
            TemplateVersion,
            (TemplateVersion.tenant_id == Template.tenant_id)
            & (TemplateVersion.template_id == Template.id)
            & (TemplateVersion.version == Template.published_version),
        )
        .where(Template.tenant_id == tenant_id)
        .order_by(Template.slug, Template.channel)
    )
    return [tuple(row) for row in db.execute(stmt).all()]


def create_version(
    db: Session,
    tenant_id: UUID,
    template_id: UUID,
    *,
    body: str,
    subject: str | None = None,
    author_party_id: UUID | None = None,
    html_body: str | None = None,
    layout_slug: str | None = None,
) -> TemplateVersion:
    """Add a new revision. The version number is allocated here.

    `layout_slug` opts the revision in to an email layout (default none).

    `html_body` is the optional html part: validated, then sanitized, and the
    SANITIZED html is what is stored. Html the sanitizer would change is refused.

    Validated against the parent template's render context BEFORE anything is
    stored — an unsendable revision never reaches the database, so it can never
    be published by someone who did not author it.
    """
    template = get_template(db, tenant_id, template_id)
    layout_slug = layout_slug or None
    _check_layout_opt_in(db, tenant_id, template, layout_slug)
    check_revision(
        get_context(template.context),
        subject=subject,
        text=body,
        html=html_body,
        kind_hint="version",
    )
    stored_html = _stored_html(
        html_body, get_context(template.context), subject=subject, body=body
    )
    _check_fragment_for_layout(layout_slug, stored_html)
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
        html_body=stored_html,
        layout_slug=layout_slug,
        variables=extract_variables(body, subject, stored_html),
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
    html_body: str | None | _Unset = _UNSET,
    layout_slug: str | None | _Unset = _UNSET,
) -> TemplateVersion:
    """Edit a DRAFT revision in place.

    `html_body` left out keeps the html part; `None` removes it. `layout_slug`
    likewise keeps, or with None/empty removes, the layout opt-in.

    Refuses a published revision: "what was sent" must not change after the fact.
    Editing published content is a new version, which is what `create_version`
    is for.
    """
    template = get_template(db, tenant_id, template_id)
    revision = get_version(db, tenant_id, template_id, version)
    if revision.published_at is not None:
        raise ConflictError(
            f"version {version} is published and cannot be edited — create a new "
            "version instead"
        )
    new_body = revision.body if body is None else body
    # An explicit empty subject clears it (stored as NULL); omitted keeps it.
    new_subject = revision.subject if subject is None else (subject or None)
    new_html = revision.html_body if isinstance(html_body, _Unset) else html_body
    new_layout = (
        revision.layout_slug
        if isinstance(layout_slug, _Unset)
        else (layout_slug or None)
    )
    _check_layout_opt_in(db, tenant_id, template, new_layout)
    check_revision(
        get_context(template.context),
        subject=new_subject,
        text=new_body,
        html=new_html,
        kind_hint="version",
    )
    stored_html = _stored_html(
        new_html, get_context(template.context), subject=new_subject, body=new_body
    )
    _check_fragment_for_layout(new_layout, stored_html)
    revision.body = new_body
    revision.subject = new_subject
    revision.html_body = stored_html
    revision.layout_slug = new_layout
    revision.variables = extract_variables(new_body, new_subject, stored_html)
    db.flush()
    return revision


class PublishRefusedError(ConflictError):
    """Publishing was refused by the tenant's maker-checker setting."""


def maker_checker_enabled(db: Session, tenant_id: UUID) -> bool:
    """Whether this tenant requires a publisher other than the author."""
    return resolve_setting(db, REQUIRE_SEPARATE_PUBLISHER, tenant_id=tenant_id) is True


def publish_version(
    db: Session,
    tenant_id: UUID,
    template_id: UUID,
    version: int,
    *,
    actor_party_id: UUID | None = None,
) -> TemplateVersion:
    """Make one revision the published one. Idempotent.

    Maker-checker: when the tenant's `require_separate_publisher` setting is on,
    the party who authored a revision may not be the one who FIRST publishes it
    (`PublishRefusedError`). Re-publishing a revision that was published before
    is a rollback and is not blocked: it was already approved once, and an
    incident rollback must not wait for a second person. While the setting is on,
    a revision with no recorded author is refused too. Editing a draft does not
    record the editor, so the rule compares the original author only.

    A revision that fails `validate_revision` against the CURRENT context cannot
    be published (or restored). `actor_party_id=None`
    is an in-process system caller with no author to match (seeding).
    """
    template = get_template(db, tenant_id, template_id)
    revision = get_version(db, tenant_id, template_id, version)
    if (
        revision.published_at is None
        and actor_party_id is not None
        and maker_checker_enabled(db, tenant_id)
    ):
        if revision.author_party_id is None:
            raise PublishRefusedError(
                f"version {version} has no recorded author, so this workspace "
                "cannot confirm a different person is publishing it"
            )
        if revision.author_party_id == actor_party_id:
            raise PublishRefusedError(
                f"version {version} was authored by you and this workspace "
                "requires a different person to publish it"
            )
    _require_valid_for_live(db, template, revision)
    if revision.published_at is None:
        revision.published_at = datetime.now(UTC)
    template.published_version = version
    db.flush()
    return revision


def _require_valid_for_live(
    db: Session, template: Template, revision: TemplateVersion
) -> None:
    """Refuse to make a revision live if it no longer validates.

    A rule may have tightened, or the context changed, since it was saved or
    last published; going live is the moment that must not be skipped.
    """
    check_revision(
        get_context(template.context),
        subject=revision.subject,
        text=revision.body,
        html=revision.html_body,
        kind_hint="version cannot go live:",
    )
    # Layout re-validation (TS-5). A revision that opts in must assemble with its
    # layout; a layout going live must keep every message that uses it assembling.
    problems = layout_problems(db, template.tenant_id, template, revision)
    if template.channel == LAYOUT_CHANNEL:
        for other, other_rev in _layout_dependents(
            db, template.tenant_id, template.slug
        ):
            try:
                _render_sample(
                    db,
                    template.tenant_id,
                    other,
                    other_rev,
                    layout_revision=revision,
                )
            except BadRequestError as exc:
                problems.append(f"{other.slug}/{other.channel}: {exc}")
    if problems:
        raise BadRequestError("version cannot go live: " + "; ".join(problems))


def restore_version(
    db: Session, tenant_id: UUID, template_id: UUID, version: int
) -> tuple[TemplateVersion, int | None]:
    """Make an EARLIER published revision the published one again (rollback).

    Returns `(revision, previously_published_version)`. Refuses a revision that
    was never published (that is a first publish, with its own checks) and the
    one already live.
    """
    template = get_template(db, tenant_id, template_id)
    revision = get_version(db, tenant_id, template_id, version)
    if revision.published_at is None:
        raise ConflictError(
            f"version {version} was never published; publish it instead of "
            "restoring it"
        )
    previous = template.published_version
    if previous == version:
        raise ConflictError(f"version {version} is already the published version")
    _require_valid_for_live(db, template, revision)
    template.published_version = version
    db.flush()
    return revision, previous


# ── Preview and diff (editor) ───────────────────────────────────────────────


def render_preview(
    db: Session, tenant_id: UUID, template_id: UUID, version: int
) -> RenderedTemplate:
    """Render ANY revision (draft or published) with its context's `sample`.

    The API render is published-only; this is the editor's draft path. Lenient
    (`strict=False`): a sample need not cover every variable, and an unfilled
    placeholder is visible in the preview rather than a refusal. Everything else
    — an unsafe value, a size bound, bad sections — still refuses.
    """
    template = get_template(db, tenant_id, template_id)
    revision = get_version(db, tenant_id, template_id, version)
    return _render_sample(db, tenant_id, template, revision)


def _render_sample(
    db: Session,
    tenant_id: UUID,
    template: Template,
    revision: TemplateVersion,
    *,
    layout_revision: TemplateVersion | None = None,
) -> RenderedTemplate:
    context = get_context(template.context)
    values = context.sample
    try:
        if template.channel == LAYOUT_CHANNEL:
            # A layout previews around a fixed sample message, with the tenant's
            # real brand values from the product resolver.
            brand = resolve_brand(db, tenant_id)
            lctx = layout_context()
            return RenderedTemplate(
                subject=None,
                text=wrap_part(revision.body, "text", _SAMPLE_TEXT, brand, lctx),
                html=(
                    wrap_part(revision.html_body, "html", _SAMPLE_HTML, brand, lctx)
                    if revision.html_body
                    else None
                ),
            )
        subject = (
            render_part(
                revision.subject, values, "subject", context=context, strict=False
            )
            if revision.subject
            else None
        )
        text = render_part(revision.body, values, "text", context=context, strict=False)
        html = (
            render_part(
                revision.html_body, values, "html", context=context, strict=False
            )
            if revision.html_body
            else None
        )
        used = None
        if revision.layout_slug:
            text, html, used = _apply_layout(
                db,
                tenant_id,
                revision.layout_slug,
                text,
                html,
                layout_revision=layout_revision,
            )
    except (MissingTemplateValueError, RenderRefusedError, TemplateSyntaxError) as exc:
        raise BadRequestError(str(exc)) from exc
    return RenderedTemplate(
        subject=subject,
        text=text,
        html=html,
        layout_slug=revision.layout_slug if used else None,
        layout_version=used.version if used else None,
        layout_revision_id=used.id if used else None,
    )


@dataclass(frozen=True, slots=True)
class PartDiff:
    """A plain-text diff of one part. `lines` are `(tag, text)`; tag is one of
    `"+"`, `"-"`, `" "` or `"@"` (a hunk header). Text, never markup."""

    part: str
    lines: tuple[tuple[str, str], ...]
    changed: bool


def _diff_part(part: str, old: str | None, new: str | None) -> PartDiff | None:
    if old is None and new is None:
        return None
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    out: list[tuple[str, str]] = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=2):
        if line.startswith(("---", "+++")):
            continue
        tag = line[:1]
        out.append((tag if tag in "+-@" else " ", line[1:] if tag in "+- " else line))
    return PartDiff(part=part, lines=tuple(out), changed=old != new)


def diff_against_published(
    db: Session, tenant_id: UUID, template_id: UUID, version: int
) -> tuple[int | None, list[PartDiff]]:
    """Per-part diff of `version` against the published revision.

    Returns `(published_version, diffs)`; with nothing published the baseline is
    empty, so every line reads as added.
    """
    revision = get_version(db, tenant_id, template_id, version)
    published = get_published(db, tenant_id, template_id)
    base_subject = published.subject if published else None
    base_body = published.body if published else None
    base_html = published.html_body if published else None
    diffs = [
        d
        for d in (
            _diff_part("subject", base_subject, revision.subject),
            _diff_part("text", base_body, revision.body),
            _diff_part("html", base_html, revision.html_body),
        )
        if d is not None
    ]
    return (published.version if published else None), diffs


# The flag this module declares and reads — see `manifest.py` for why it exists.
STRICT_RENDER_FLAG = "template_studio.strict_render"


def render_published(
    db: Session,
    tenant_id: UUID,
    slug: str,
    channel: str,
    values: Mapping[str, Any],
    *,
    strict: bool | None = None,
) -> tuple[str | None, str]:
    """Render a template's PUBLISHED revision — the caller-facing entry point.

    Returns `(subject, body)`. Callers address a template by its stable
    `(slug, channel)` and never by a version number: which revision is live is
    this module's decision, not theirs.
    """
    rendered = render_published_parts(
        db, tenant_id, slug, channel, values, strict=strict, with_html=False
    )
    return rendered.subject, rendered.text


@dataclass(frozen=True, slots=True)
class RenderedTemplate:
    """A published revision rendered into its parts. `html` is None without one."""

    subject: str | None
    text: str
    html: str | None = None
    # Which layout revision wrapped this render, for provenance (None: no layout).
    layout_slug: str | None = None
    layout_version: int | None = None
    layout_revision_id: UUID | None = None


def render_published_parts(
    db: Session,
    tenant_id: UUID,
    slug: str,
    channel: str,
    values: Mapping[str, Any],
    *,
    strict: bool | None = None,
    with_html: bool = True,
) -> RenderedTemplate:
    """Render every part of a template's PUBLISHED revision.

    `render_published` is this minus the html part, kept for its callers.
    """
    if strict is None:
        # The FLAG decides, unless a caller was explicit. A preview screen
        # passes `strict=False` because it knows it has no values yet; everyone
        # else gets the operator's current answer.
        strict = bool(resolve_flag(db, STRICT_RENDER_FLAG, tenant_id=tenant_id).value)
    template = get_by_slug(db, tenant_id, slug, channel)
    if template.channel == LAYOUT_CHANNEL:
        raise ConflictError("a layout is not a message and cannot be rendered alone")
    if not template.is_active:
        raise ConflictError(f"template {slug}/{channel} is not active")
    revision = get_published(db, tenant_id, template.id)
    if revision is None:
        raise ConflictError(f"template {slug}/{channel} has no published version")
    # Part-aware render so a saved section is never sent literally. For a
    # flat-placeholder body the text part is byte-identical to `render()`. A
    # subject now also refuses CR/LF in its result (header injection).
    context = get_context(template.context)
    try:
        subject = (
            render_part(
                revision.subject, values, "subject", context=context, strict=strict
            )
            if revision.subject
            else None
        )
        text = render_part(
            revision.body, values, "text", context=context, strict=strict
        )
        html = (
            render_part(
                revision.html_body, values, "html", context=context, strict=strict
            )
            if with_html and revision.html_body
            else None
        )
        used = None
        if revision.layout_slug:
            text, html, used = _apply_layout(
                db, tenant_id, revision.layout_slug, text, html
            )
        return RenderedTemplate(
            subject=subject,
            text=text,
            html=html,
            layout_slug=revision.layout_slug if used else None,
            layout_version=used.version if used else None,
            layout_revision_id=used.id if used else None,
        )
    except (MissingTemplateValueError, RenderRefusedError, TemplateSyntaxError) as exc:
        raise BadRequestError(str(exc)) from exc
