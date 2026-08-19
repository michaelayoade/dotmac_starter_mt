"""Tenant-scoped website composition inside the caller's transaction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TypeVar, cast
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_sites.contracts import (
    Conflict,
    ContractError,
    CreatePage,
    CreatePageRevision,
    CreateSite,
    CreateSiteRevision,
    NavigationItemV1,
    NotFound,
    RedirectRuleV1,
    SeoMetadataV1,
    SitePageSnapshotV1,
    SiteReleaseV1,
)
from dotmac_sites.lifecycle import (
    SiteRevisionState,
    SiteState,
    check_revision_transition,
    check_site_transition,
)
from dotmac_sites.models import Page, PageRevision, Site, SiteRevision, SiteRevisionPage

_Model = TypeVar("_Model")


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-sites requires an explicit TenantScope")
    return scope.tenant_id


def _one(db: Session, statement: Select[tuple[_Model]], *, detail: str) -> _Model:
    result = db.scalar(statement)
    if result is None:
        raise NotFound(detail)
    return result


def _flush_new(db: Session, record: _Model, *, detail: str) -> _Model:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(record)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(detail) from exc
    return record


def _site(
    db: Session, tenant_id: UUID, site_id: UUID, *, lock: bool = False
) -> Site:
    statement = select(Site).where(Site.tenant_id == tenant_id, Site.id == site_id)
    if lock:
        statement = statement.with_for_update()
    return _one(db, statement, detail=f"site {site_id} was not found")


def _page(
    db: Session, tenant_id: UUID, page_id: UUID, *, lock: bool = False
) -> Page:
    statement = select(Page).where(Page.tenant_id == tenant_id, Page.id == page_id)
    if lock:
        statement = statement.with_for_update()
    return _one(db, statement, detail=f"page {page_id} was not found")


def _site_revision(
    db: Session, tenant_id: UUID, revision_id: UUID
) -> SiteRevision:
    return _one(
        db,
        select(SiteRevision).where(
            SiteRevision.tenant_id == tenant_id, SiteRevision.id == revision_id
        ),
        detail=f"site revision {revision_id} was not found",
    )


def _require_active(site: Site) -> None:
    if site.state == SiteState.ARCHIVED:
        raise Conflict(f"site {site.id} is archived")


def create_site(db: Session, *, scope: TenantScope, command: CreateSite) -> Site:
    tenant_id = _tenant(scope)
    if db.scalar(
        select(Site.id).where(Site.tenant_id == tenant_id, Site.slug == command.slug)
    ):
        raise Conflict(f"site slug {command.slug!r} already exists")
    return _flush_new(
        db,
        Site(
            tenant_id=tenant_id,
            slug=command.slug,
            name=command.name,
            created_by_ref=command.created_by_ref,
        ),
        detail=f"site slug {command.slug!r} conflicts",
    )


def create_page(db: Session, *, scope: TenantScope, command: CreatePage) -> Page:
    tenant_id = _tenant(scope)
    site = _site(db, tenant_id, command.site_id)
    _require_active(site)
    if db.scalar(
        select(Page.id).where(
            Page.tenant_id == tenant_id,
            Page.site_id == site.id,
            Page.page_key == command.page_key,
        )
    ):
        raise Conflict(f"page key {command.page_key!r} already exists for this site")
    return _flush_new(
        db,
        Page(
            tenant_id=tenant_id,
            site_id=site.id,
            page_key=command.page_key,
            created_by_ref=command.created_by_ref,
        ),
        detail=f"page key {command.page_key!r} conflicts",
    )


def create_page_revision(
    db: Session, *, scope: TenantScope, command: CreatePageRevision
) -> PageRevision:
    tenant_id = _tenant(scope)
    page = _page(db, tenant_id, command.page_id, lock=True)
    _require_active(_site(db, tenant_id, page.site_id))
    current = db.scalar(
        select(func.max(PageRevision.revision_number)).where(
            PageRevision.tenant_id == tenant_id,
            PageRevision.page_id == page.id,
        )
    )
    return _flush_new(
        db,
        PageRevision(
            tenant_id=tenant_id,
            site_id=page.site_id,
            page_id=page.id,
            revision_number=int(current or 0) + 1,
            title=command.title,
            body=command.body,
            seo_payload=command.seo.as_dict(),
            file_refs=[str(value) for value in command.file_refs],
            form_refs=[str(value) for value in command.form_refs],
            content_digest=command.content_digest,
            created_by_ref=command.created_by_ref,
        ),
        detail=f"page revision conflicts for page {page.id}",
    )


def _seo(value: object) -> SeoMetadataV1:
    if not isinstance(value, dict):
        raise ContractError("persisted SEO metadata must be an object")
    title = value.get("title")
    description = value.get("description")
    canonical_path = value.get("canonical_path")
    no_index = value.get("no_index", False)
    if (
        not isinstance(title, str)
        or (description is not None and not isinstance(description, str))
        or (canonical_path is not None and not isinstance(canonical_path, str))
        or not isinstance(no_index, bool)
    ):
        raise ContractError("persisted SEO metadata has invalid field types")
    return SeoMetadataV1(
        title=title,
        description=cast(str | None, description),
        canonical_path=cast(str | None, canonical_path),
        no_index=no_index,
    )


def _uuid_tuple(value: object, *, field: str) -> tuple[UUID, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContractError(f"persisted {field} must be a string list")
    try:
        return tuple(UUID(item) for item in cast(list[str], value))
    except ValueError as exc:
        raise ContractError(f"persisted {field} contains an invalid UUID") from exc


def _page_value(revision: PageRevision, *, path: str) -> SitePageSnapshotV1:
    return SitePageSnapshotV1(
        page_ref=revision.page_id,
        page_revision_ref=revision.id,
        path=path,
        title=revision.title,
        body=revision.body,
        seo=_seo(revision.seo_payload),
        file_refs=_uuid_tuple(revision.file_refs, field="file_refs"),
        form_refs=_uuid_tuple(revision.form_refs, field="form_refs"),
    )


def create_site_revision(
    db: Session, *, scope: TenantScope, command: CreateSiteRevision
) -> SiteRevision:
    tenant_id = _tenant(scope)
    site = _site(db, tenant_id, command.site_id, lock=True)
    _require_active(site)
    current = db.scalar(
        select(func.max(SiteRevision.revision_number)).where(
            SiteRevision.tenant_id == tenant_id,
            SiteRevision.site_id == site.id,
        )
    )
    revision_id = uuid.uuid4()
    ordered = tuple(sorted(command.pages, key=lambda value: value.sort_order))
    page_values: list[SitePageSnapshotV1] = []
    revisions: list[PageRevision] = []
    for selection in ordered:
        page_revision = db.scalar(
            select(PageRevision).where(
                PageRevision.tenant_id == tenant_id,
                PageRevision.site_id == site.id,
                PageRevision.id == selection.page_revision_id,
            )
        )
        if page_revision is None:
            raise NotFound(
                f"page revision {selection.page_revision_id} was not found in site "
                f"{site.id}"
            )
        revisions.append(page_revision)
        page_values.append(_page_value(page_revision, path=selection.path))

    release = SiteReleaseV1(
        site_ref=site.id,
        site_revision_ref=revision_id,
        pages=tuple(page_values),
        navigation=command.navigation,
        redirects=command.redirects,
        seo=command.seo,
    )
    revision = SiteRevision(
        id=revision_id,
        tenant_id=tenant_id,
        site_id=site.id,
        revision_number=int(current or 0) + 1,
        snapshot_payload=release.as_dict(),
        snapshot_digest=release.digest,
        created_by_ref=command.created_by_ref,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(revision)
            db.flush()
            for selection, page_revision in zip(ordered, revisions, strict=True):
                db.add(
                    SiteRevisionPage(
                        tenant_id=tenant_id,
                        site_id=site.id,
                        site_revision_id=revision.id,
                        page_id=page_revision.page_id,
                        page_revision_id=page_revision.id,
                        path=selection.path,
                        sort_order=selection.sort_order,
                    )
                )
            db.flush()
    except IntegrityError as exc:
        raise Conflict(
            f"site revision membership conflicts for site {site.id}"
        ) from exc
    return revision


def _navigation(value: object) -> tuple[NavigationItemV1, ...]:
    if not isinstance(value, list):
        raise ContractError("persisted navigation must be a list")
    result: list[NavigationItemV1] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ContractError("persisted navigation entries must be objects")
        label, path = raw.get("label"), raw.get("path")
        if not isinstance(label, str) or not isinstance(path, str):
            raise ContractError("persisted navigation entry has invalid fields")
        result.append(
            NavigationItemV1(
                label=label,
                path=path,
                children=_navigation(raw.get("children", [])),
            )
        )
    return tuple(result)


def _release(revision: SiteRevision) -> SiteReleaseV1:
    payload = revision.snapshot_payload
    try:
        schema_version = payload.get("schema_version")
        site_ref = payload.get("site_ref")
        site_revision_ref = payload.get("site_revision_ref")
        raw_pages = payload.get("pages")
        raw_redirects = payload.get("redirects")
        if (
            not isinstance(schema_version, int)
            or not isinstance(site_ref, str)
            or not isinstance(site_revision_ref, str)
            or not isinstance(raw_pages, list)
            or not isinstance(raw_redirects, list)
        ):
            raise ContractError("persisted site release has invalid root fields")
        pages: list[SitePageSnapshotV1] = []
        for raw in raw_pages:
            if not isinstance(raw, dict):
                raise ContractError("persisted site pages must be objects")
            page_ref = raw.get("page_ref")
            page_revision_ref = raw.get("page_revision_ref")
            path, title, body = raw.get("path"), raw.get("title"), raw.get("body")
            if not all(isinstance(value, str) for value in (path, title, body)):
                raise ContractError("persisted site page has invalid text fields")
            if not isinstance(page_ref, str) or not isinstance(page_revision_ref, str):
                raise ContractError("persisted site page has invalid references")
            pages.append(
                SitePageSnapshotV1(
                    page_ref=UUID(page_ref),
                    page_revision_ref=UUID(page_revision_ref),
                    path=cast(str, path),
                    title=cast(str, title),
                    body=cast(str, body),
                    seo=_seo(raw.get("seo")),
                    file_refs=_uuid_tuple(raw.get("file_refs"), field="file_refs"),
                    form_refs=_uuid_tuple(raw.get("form_refs"), field="form_refs"),
                )
            )
        redirects: list[RedirectRuleV1] = []
        for raw in raw_redirects:
            if not isinstance(raw, dict):
                raise ContractError("persisted redirects must be objects")
            source, target, status = (
                raw.get("source_path"),
                raw.get("target_path"),
                raw.get("status_code"),
            )
            if (
                not isinstance(source, str)
                or not isinstance(target, str)
                or not isinstance(status, int)
            ):
                raise ContractError("persisted redirect has invalid fields")
            redirects.append(
                RedirectRuleV1(
                    source_path=source, target_path=target, status_code=status
                )
            )
        release = SiteReleaseV1(
            site_ref=UUID(site_ref),
            site_revision_ref=UUID(site_revision_ref),
            pages=tuple(pages),
            navigation=_navigation(payload.get("navigation")),
            redirects=tuple(redirects),
            seo=_seo(payload.get("seo")),
            schema_version=schema_version,
        )
    except (ContractError, TypeError, ValueError) as exc:
        raise Conflict(f"site revision {revision.id} has an invalid snapshot") from exc
    if release.site_revision_ref != revision.id or release.site_ref != revision.site_id:
        raise Conflict(f"site revision {revision.id} snapshot identity drift")
    if release.digest != revision.snapshot_digest:
        raise Conflict(f"site revision {revision.id} snapshot digest drift")
    return release


def mark_site_revision_ready(
    db: Session,
    *,
    scope: TenantScope,
    site_revision_id: UUID,
    recorded_at: datetime | None = None,
) -> SiteRevision:
    tenant_id = _tenant(scope)
    revision = _site_revision(db, tenant_id, site_revision_id)
    site = _site(db, tenant_id, revision.site_id, lock=True)
    _require_active(site)
    if revision.state == SiteRevisionState.READY:
        return revision
    check_revision_transition(revision.state, SiteRevisionState.READY)
    now = recorded_at or datetime.now(UTC)
    current = db.scalar(
        select(SiteRevision).where(
            SiteRevision.tenant_id == tenant_id,
            SiteRevision.site_id == site.id,
            SiteRevision.state == SiteRevisionState.READY,
        )
    )
    if current is not None:
        check_revision_transition(current.state, SiteRevisionState.RETIRED)
        current.state = SiteRevisionState.RETIRED
        current.retired_at = now
        db.flush()
    revision.state = SiteRevisionState.READY
    revision.ready_at = now
    revision.retired_at = None
    db.flush()
    return revision


def get_ready_release(
    db: Session, *, scope: TenantScope, site_id: UUID
) -> SiteReleaseV1:
    tenant_id = _tenant(scope)
    site = _site(db, tenant_id, site_id)
    _require_active(site)
    revision = db.scalar(
        select(SiteRevision).where(
            SiteRevision.tenant_id == tenant_id,
            SiteRevision.site_id == site.id,
            SiteRevision.state == SiteRevisionState.READY,
        )
    )
    if revision is None:
        raise NotFound(f"site {site.id} has no ready revision")
    return _release(revision)


def archive_site(
    db: Session,
    *,
    scope: TenantScope,
    site_id: UUID,
    recorded_at: datetime | None = None,
) -> Site:
    tenant_id = _tenant(scope)
    site = _site(db, tenant_id, site_id, lock=True)
    if site.state == SiteState.ARCHIVED:
        return site
    check_site_transition(site.state, SiteState.ARCHIVED)
    ready = db.scalar(
        select(SiteRevision).where(
            SiteRevision.tenant_id == tenant_id,
            SiteRevision.site_id == site.id,
            SiteRevision.state == SiteRevisionState.READY,
        )
    )
    if ready is not None:
        check_revision_transition(ready.state, SiteRevisionState.RETIRED)
        ready.state = SiteRevisionState.RETIRED
        ready.retired_at = recorded_at or datetime.now(UTC)
        db.flush()
    site.state = SiteState.ARCHIVED
    db.flush()
    return site


__all__ = [
    "archive_site",
    "create_page",
    "create_page_revision",
    "create_site",
    "create_site_revision",
    "get_ready_release",
    "mark_site_revision_ready",
]
