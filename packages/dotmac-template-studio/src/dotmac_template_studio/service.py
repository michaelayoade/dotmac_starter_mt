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
from dotmac_kernel.settings_resolver import resolve_value
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
from dotmac_template_studio.settings import TEMPLATE_STUDIO_DOMAIN
from dotmac_template_studio.validation import check_revision

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
) -> TemplateVersion:
    """Add a new revision. The version number is allocated here.

    `html_body` is the optional html part: validated, then sanitized, and the
    SANITIZED html is what is stored. Html the sanitizer would change is refused.

    Validated against the parent template's render context BEFORE anything is
    stored — an unsendable revision never reaches the database, so it can never
    be published by someone who did not author it.
    """
    template = get_template(db, tenant_id, template_id)
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
) -> TemplateVersion:
    """Edit a DRAFT revision in place.

    `html_body` left out keeps the html part; `None` removes it.

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
    revision.body = new_body
    revision.subject = new_subject
    revision.html_body = stored_html
    revision.variables = extract_variables(new_body, new_subject, stored_html)
    db.flush()
    return revision


class PublishRefusedError(ConflictError):
    """Publishing was refused by the tenant's maker-checker setting."""


def maker_checker_enabled(db: Session, tenant_id: UUID) -> bool:
    """Whether this tenant requires a publisher other than the author."""
    # By key, the dynamic path every settings reader uses; coercion to the spec's
    # boolean type happens in the resolver and a bad stored value degrades to the
    # default (off).
    value = resolve_value(
        db,
        TEMPLATE_STUDIO_DOMAIN,
        "require_separate_publisher",
        tenant_id=tenant_id,
        default=False,
    )
    return value is True


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
    _require_valid_for_live(template, revision)
    if revision.published_at is None:
        revision.published_at = datetime.now(UTC)
    template.published_version = version
    db.flush()
    return revision


def _require_valid_for_live(template: Template, revision: TemplateVersion) -> None:
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
    _require_valid_for_live(template, revision)
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
    context = get_context(template.context)
    values = context.sample
    try:
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
    except (MissingTemplateValueError, RenderRefusedError, TemplateSyntaxError) as exc:
        raise BadRequestError(str(exc)) from exc
    return RenderedTemplate(subject=subject, text=text, html=html)


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
        return RenderedTemplate(subject=subject, text=text, html=html)
    except (MissingTemplateValueError, RenderRefusedError, TemplateSyntaxError) as exc:
        raise BadRequestError(str(exc)) from exc
