"""Seeding a tenant's default templates (ADR-0006 § 5b follow-up).

The 2026-08-10 source audit found this module had **no seeding mechanism at all**,
and named Sub's as the qualifying source: upsert by identity, never clobber an
operator's edit, and derive the content from the executable spec so the database
row cannot fork from the canonical one.

Without this, every product adopting Template Studio rewrites that logic, which
is the build-once violation the 2026-08-09 amendment forbids — and it is the
prerequisite for a Sub cutover, since Sub's templates exist because its seeder
creates them.

## The three rules, and why each one is load-bearing

1. **Upsert by identity, never by content.** A seed is matched on
   `(tenant, slug, channel)`. Matching on body would re-create a template every
   time an operator edited it.

2. **Never clobber an edit.** If the template exists, seeding leaves it ALONE —
   it does not add a version, does not republish, does not touch metadata. An
   operator's wording is the whole point of a tenant-authored template, and a
   deploy that silently reverted it would be indistinguishable from data loss.
   This is why `seed_templates` reports what it skipped rather than returning a
   bare count.

3. **Derive from the declaration.** A `TemplateSeed` is the canonical definition;
   the seeded row is a projection of it. Sub reaches into its executable
   `EVENT_NOTIFICATION_SPECS` for exactly this reason — so a template's default
   wording has one home rather than two that drift.

## Seeded templates are published

A seeded draft would be invisible to every caller (`render_published` refuses a
template with no published version), so seeding publishes version 1. That makes
the deployment work out of the box, which is the point of seeding at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from dotmac_kernel.exceptions import NotFoundError
from sqlalchemy.orm import Session

from dotmac_template_studio import service


@dataclass(frozen=True, slots=True)
class TemplateSeed:
    """One default template a product ships.

    `context` must name a registered `RenderContext`, and `body`/`subject` are
    validated against it at seed time — a seed whose placeholders the send path
    cannot supply fails the deploy rather than the send.
    """

    slug: str
    channel: str
    context: str
    name: str
    body: str
    subject: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class SeedOutcome:
    """What seeding did, per template. Reported rather than counted.

    An operator asking "why is my edited template back to the default?" deserves
    an answer, and the answer must be "it never was" — `skipped` is the evidence.
    """

    created: tuple[str, ...] = field(default_factory=tuple)
    skipped: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.created) + len(self.skipped)


def seed_templates(
    db: Session,
    tenant_id: UUID,
    seeds: tuple[TemplateSeed, ...] | list[TemplateSeed],
    *,
    author_party_id: UUID | None = None,
) -> SeedOutcome:
    """Create any of `seeds` this tenant does not have. Idempotent.

    Existing templates are left completely untouched — see rule 2 above. Safe to
    run on every deploy, which is how it is meant to be run.

    Flush-only; `dotmac_kernel.db` is the one transaction authority.
    """
    created: list[str] = []
    skipped: list[str] = []

    for seed in seeds:
        identity = f"{seed.slug}/{seed.channel}"
        try:
            service.get_by_slug(db, tenant_id, seed.slug, seed.channel)
        except NotFoundError:
            pass
        else:
            skipped.append(identity)
            continue

        template = service.create_template(
            db,
            tenant_id,
            slug=seed.slug,
            channel=seed.channel,
            context=seed.context,
            name=seed.name,
            description=seed.description,
        )
        version = service.create_version(
            db,
            tenant_id,
            template.id,
            body=seed.body,
            subject=seed.subject,
            author_party_id=author_party_id,
        )
        # Published, or no caller could render it — see the module docstring.
        service.publish_version(db, tenant_id, template.id, version.version)
        created.append(identity)

    return SeedOutcome(created=tuple(created), skipped=tuple(skipped))


__all__ = ["SeedOutcome", "TemplateSeed", "seed_templates"]
