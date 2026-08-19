"""Greenfield behavior canaries for the product-neutral sites owner."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_sites import (
    Conflict,
    CreatePage,
    CreatePageRevision,
    CreateSite,
    CreateSiteRevision,
    NavigationItemV1,
    NotFound,
    RedirectRuleV1,
    SeoMetadataV1,
    SitePageSelection,
    SiteRevisionState,
    SiteState,
    archive_site,
    create_page,
    create_page_revision,
    create_site,
    create_site_revision,
    get_ready_release,
    mark_site_revision_ready,
)
from dotmac_sites.models import (
    TENANT_TABLES,
    PageRevision,
    SiteRevisionPage,
    metadata_table,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
ACTOR = uuid.uuid4()


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_sites": None}},
    )
    Tenant.__table__.create(engine)
    for table_name in TENANT_TABLES:
        metadata_table(table_name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="alpha", name="Alpha"),
                Tenant(id=TENANT_B, slug="bravo", name="Bravo"),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def _site(db: Session, *, tenant: uuid.UUID = TENANT_A, slug: str = "main"):
    return create_site(
        db,
        scope=TenantScope(tenant),
        command=CreateSite(slug=slug, name="  Dotmac Main  ", created_by_ref=ACTOR),
    )


def _page(db: Session, site_id: uuid.UUID, *, key: str = "home"):
    return create_page(
        db,
        scope=TenantScope(TENANT_A),
        command=CreatePage(site_id=site_id, page_key=key, created_by_ref=ACTOR),
    )


def _page_revision(
    db: Session,
    page_id: uuid.UUID,
    *,
    title: str = "Home",
    body: str = "Welcome",
):
    return create_page_revision(
        db,
        scope=TenantScope(TENANT_A),
        command=CreatePageRevision(
            page_id=page_id,
            title=title,
            body=body,
            seo=SeoMetadataV1(title=title, canonical_path="/"),
            file_refs=(uuid.uuid4(),),
            form_refs=(uuid.uuid4(),),
            created_by_ref=ACTOR,
        ),
    )


def _site_revision(
    db: Session,
    site_id: uuid.UUID,
    page_revision_id: uuid.UUID,
):
    return create_site_revision(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateSiteRevision(
            site_id=site_id,
            pages=(
                SitePageSelection(
                    page_revision_id=page_revision_id,
                    path="/",
                    sort_order=0,
                ),
            ),
            navigation=(NavigationItemV1(label="Home", path="/"),),
            redirects=(
                RedirectRuleV1(
                    source_path="/welcome", target_path="/", status_code=301
                ),
            ),
            seo=SeoMetadataV1(
                title="Dotmac", description="Connectivity", canonical_path="/"
            ),
            created_by_ref=ACTOR,
        ),
    )


def test_site_identity_is_tenant_scoped_and_unique(db: Session) -> None:
    site = _site(db)
    assert site.name == "Dotmac Main"
    assert site.slug == "main"
    assert site.state == SiteState.ACTIVE

    with pytest.raises(Conflict, match="slug"):
        _site(db)
    assert _site(db, tenant=TENANT_B).slug == "main"


def test_page_and_revision_numbers_are_owned_by_the_site(db: Session) -> None:
    site = _site(db)
    page = _page(db, site.id)
    first = _page_revision(db, page.id)
    second = _page_revision(db, page.id, title="Home v2", body="Updated")

    assert page.site_id == site.id
    assert first.revision_number == 1
    assert second.revision_number == 2
    assert first.content_digest != second.content_digest
    assert first.file_refs and first.form_refs
    assert db.scalar(select(func.count(PageRevision.id))) == 2


def test_page_cannot_reference_another_tenants_site(db: Session) -> None:
    foreign = _site(db, tenant=TENANT_B)
    with pytest.raises(NotFound, match="site"):
        _page(db, foreign.id)


def test_composed_revision_freezes_exact_page_content_and_membership(
    db: Session,
) -> None:
    site = _site(db)
    page = _page(db, site.id)
    page_revision = _page_revision(db, page.id)
    revision = _site_revision(db, site.id, page_revision.id)

    assert revision.state == SiteRevisionState.DRAFT
    assert revision.revision_number == 1
    assert revision.snapshot_payload["pages"][0]["body"] == "Welcome"
    assert revision.snapshot_digest
    assert db.scalar(select(func.count(SiteRevisionPage.id))) == 1

    later = _page_revision(db, page.id, title="Changed", body="New draft")
    assert later.revision_number == 2
    assert revision.snapshot_payload["pages"][0]["body"] == "Welcome"


def test_composed_revision_refuses_a_page_from_another_site(db: Session) -> None:
    site = _site(db)
    other = _site(db, slug="other")
    foreign_page = _page(db, other.id, key="foreign")
    foreign_revision = _page_revision(db, foreign_page.id)
    with pytest.raises(NotFound, match="page revision"):
        _site_revision(db, site.id, foreign_revision.id)


def test_ready_selection_retires_the_previous_revision_and_is_idempotent(
    db: Session,
) -> None:
    site = _site(db)
    page = _page(db, site.id)
    first = _site_revision(db, site.id, _page_revision(db, page.id).id)
    second = _site_revision(
        db,
        site.id,
        _page_revision(db, page.id, title="Second", body="Second").id,
    )

    mark_site_revision_ready(
        db,
        scope=TenantScope(TENANT_A),
        site_revision_id=first.id,
    )
    assert first.state == SiteRevisionState.READY
    release = get_ready_release(
        db, scope=TenantScope(TENANT_A), site_id=site.id
    )
    assert release.site_revision_ref == first.id
    assert release.digest == first.snapshot_digest

    mark_site_revision_ready(
        db,
        scope=TenantScope(TENANT_A),
        site_revision_id=first.id,
    )
    mark_site_revision_ready(
        db,
        scope=TenantScope(TENANT_A),
        site_revision_id=second.id,
    )
    assert first.state == SiteRevisionState.RETIRED
    assert second.state == SiteRevisionState.READY
    assert get_ready_release(
        db, scope=TenantScope(TENANT_A), site_id=site.id
    ).site_revision_ref == second.id


def test_archived_site_refuses_new_pages_and_release_changes(db: Session) -> None:
    site = _site(db)
    page = _page(db, site.id)
    revision = _site_revision(db, site.id, _page_revision(db, page.id).id)
    archive_site(db, scope=TenantScope(TENANT_A), site_id=site.id)
    assert site.state == SiteState.ARCHIVED

    with pytest.raises(Conflict, match="archived"):
        _page(db, site.id, key="late")
    with pytest.raises(Conflict, match="archived"):
        mark_site_revision_ready(
            db,
            scope=TenantScope(TENANT_A),
            site_revision_id=revision.id,
        )


def test_ready_release_is_not_visible_across_tenants(db: Session) -> None:
    site = _site(db)
    page = _page(db, site.id)
    revision = _site_revision(db, site.id, _page_revision(db, page.id).id)
    mark_site_revision_ready(
        db,
        scope=TenantScope(TENANT_A),
        site_revision_id=revision.id,
    )
    with pytest.raises(NotFound):
        get_ready_release(db, scope=TenantScope(TENANT_B), site_id=site.id)


def test_explicit_tenant_scope_is_required(db: Session) -> None:
    with pytest.raises(TypeError, match="TenantScope"):
        create_site(  # type: ignore[arg-type]
            db,
            scope=TENANT_A,
            command=CreateSite(slug="bad", name="Bad", created_by_ref=ACTOR),
        )
