"""Behavior canaries written before the Digital Media implementation."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_digital_media import (
    AccessGrantCommand,
    AccessScope,
    AssetKind,
    AssetLifecycle,
    Conflict,
    CreateAsset,
    CreateLibrary,
    GrantEffect,
    InvalidEvidence,
    LifecycleError,
    MetadataObservationCommand,
    Permission,
    RecordDispositionObservation,
    RenditionCommand,
    RenditionKind,
    RenditionOutput,
    RevisionCommand,
    RightsCommand,
    RightsDeadlineObservation,
    RightsDeadlinePurpose,
    RightsUse,
    ScanObservation,
    SourceKind,
    UsageObservationCommand,
    add_revision,
    apply_rights_deadline,
    complete_rendition,
    create_asset,
    create_library,
    evaluate_disposition,
    evaluate_usage,
    find_duplicate_revisions,
    grant_access,
    may_access,
    observe_approval,
    observe_scan,
    observe_usage,
    record_metadata_observation,
    rendition_is_stale,
    request_rendition,
    request_rendition_regeneration,
    set_rights,
    transition_asset,
    usage_references,
)
from dotmac_digital_media.models import ALL_MODELS, MediaEvent, MediaRevision
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

TENANT_ID = uuid.uuid4()
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={
            "schema_translate_map": {"public": None, "mod_digitalmedia": None}
        },
    )
    tables = (Tenant.__table__, *(model.__table__ for model in ALL_MODELS))
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT_ID, slug="media", name="Media"))
        session.flush()
        yield session
    engine.dispose()


def _asset(db: Session):
    suffix = uuid.uuid4().hex[:8]
    library = create_library(
        db,
        tenant_id=TENANT_ID,
        command=CreateLibrary(code=f"brand-{suffix}", name="Brand library"),
        actor_ref="user:owner",
        recorded_at=NOW,
    )
    asset = create_asset(
        db,
        tenant_id=TENANT_ID,
        command=CreateAsset(
            library_id=library.id,
            kind=AssetKind.IMAGE,
            title="Abuja office",
            description="Exterior photograph",
            default_alt_text="Dotmac office exterior",
            creator_credit="Ada Example",
        ),
        actor_ref="user:owner",
        recorded_at=NOW,
    )
    return library, asset


def _revision(
    db: Session,
    asset_id: uuid.UUID,
    *,
    file_id: uuid.UUID | None = None,
    checksum: str = "a" * 64,
    perceptual_hash: str = "0f0f0f0f0f0f0f0f",
):
    return add_revision(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset_id,
        command=RevisionCommand(
            file_id=file_id or uuid.uuid4(),
            checksum=checksum,
            media_type="image/jpeg",
            byte_length=1_024,
            source_kind=SourceKind.UPLOAD,
            source_ref="browser-upload:42",
            author_ref="user:photographer",
            created_at=NOW,
            change_reason="initial ingest",
            perceptual_hash=perceptual_hash,
            perceptual_hash_algorithm="phash64-v1",
        ),
        actor_ref="user:owner",
        recorded_at=NOW,
    )


def test_replacement_allocates_an_immutable_revision_and_moves_only_the_pointer(
    db: Session,
) -> None:
    _, asset = _asset(db)
    first = _revision(db, asset.id)
    second = _revision(
        db,
        asset.id,
        checksum="b" * 64,
        perceptual_hash="0f0f0f0f0f0f0f0e",
    )

    assert (first.revision_number, second.revision_number) == (1, 2)
    assert asset.current_revision_id == second.id
    history = db.scalars(
        select(MediaRevision)
        .where(MediaRevision.asset_id == asset.id)
        .order_by(MediaRevision.revision_number)
    ).all()
    assert [row.checksum for row in history] == ["a" * 64, "b" * 64]
    assert history[0].file_id != history[1].file_id


def test_exact_and_perceptual_duplicates_are_evidence_not_uniqueness_errors(
    db: Session,
) -> None:
    _, first_asset = _asset(db)
    first = _revision(db, first_asset.id)
    _, second_asset = _asset(db)
    exact = _revision(
        db,
        second_asset.id,
        checksum=first.checksum,
        perceptual_hash="f0f0f0f0f0f0f0f0",
    )

    exact_matches = find_duplicate_revisions(
        db, tenant_id=TENANT_ID, checksum=first.checksum
    )
    assert {row.revision_id for row in exact_matches} == {first.id, exact.id}
    perceptual = find_duplicate_revisions(
        db,
        tenant_id=TENANT_ID,
        perceptual_hash="0f0f0f0f0f0f0f0e",
        perceptual_hash_algorithm="phash64-v1",
        max_hamming_distance=1,
    )
    assert [row.revision_id for row in perceptual] == [first.id]


def test_metadata_observes_the_exact_source_checksum(db: Session) -> None:
    _, asset = _asset(db)
    revision = _revision(db, asset.id)
    observation = record_metadata_observation(
        db,
        tenant_id=TENANT_ID,
        command=MetadataObservationCommand(
            revision_id=revision.id,
            source_checksum=revision.checksum,
            extractor_code="exiftool",
            extractor_version="12.99",
            observed_at=NOW,
            width=4096,
            height=2731,
            orientation="landscape",
            colour_profile="Display P3",
            accessibility={"flashing": False},
            exif={"Make": "Camera Co"},
            iptc={"City": "Abuja"},
            xmp={"Rating": 5},
        ),
        actor_ref="service:extractor",
        recorded_at=NOW,
    )
    assert observation.revision_id == revision.id
    assert observation.width == 4096

    with pytest.raises(InvalidEvidence, match="source checksum"):
        record_metadata_observation(
            db,
            tenant_id=TENANT_ID,
            command=MetadataObservationCommand(
                revision_id=revision.id,
                source_checksum="f" * 64,
                extractor_code="exiftool",
                extractor_version="12.99",
                observed_at=NOW,
            ),
            actor_ref="service:extractor",
            recorded_at=NOW,
        )


def test_rights_evaluation_is_explicit_and_fails_closed(db: Session) -> None:
    _, asset = _asset(db)
    revision = _revision(db, asset.id)
    rights = set_rights(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        command=RightsCommand(
            rights_holder="Dotmac Technologies",
            copyright_notice="Copyright 2026 Dotmac",
            licence_id="owned-media",
            licence_version="v1",
            territories=("NG",),
            channels=("web",),
            purposes=("corporate",),
            starts_at=NOW - timedelta(days=1),
            ends_at=NOW + timedelta(days=30),
            required_credit="Photo: Ada Example",
            commercial_use_allowed=False,
            modification_allowed=True,
            release_references=("privacy:model-release:17",),
            release_evidence_ref="privacy-decision:88",
            release_evidence_valid=True,
            sensitivity="internal",
            embargo_until=NOW - timedelta(hours=1),
            review_at=NOW + timedelta(days=20),
        ),
        actor_ref="user:rights-manager",
        recorded_at=NOW,
    )
    assert rights.version_number == 1
    observe_scan(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        observation=ScanObservation(
            revision_id=revision.id,
            file_id=revision.file_id,
            checksum=revision.checksum,
            decision="safe",
            scanner_ref="files-scan:rights",
            observed_at=NOW,
        ),
        actor_ref="service:files",
        recorded_at=NOW,
    )
    transition_asset(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        target=AssetLifecycle.AVAILABLE,
        reason="safe and rights-complete",
        actor_ref="user:rights-manager",
        effective_at=NOW,
    )

    allowed = evaluate_usage(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        use=RightsUse(
            at=NOW,
            territory="NG",
            channel="web",
            purpose="corporate",
            commercial=False,
            modifies=True,
        ),
    )
    assert allowed.allowed
    assert allowed.required_credit == "Photo: Ada Example"

    denied = evaluate_usage(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        use=RightsUse(
            at=NOW,
            territory="GB",
            channel="web",
            purpose="corporate",
            commercial=True,
            modifies=False,
        ),
    )
    assert not denied.allowed
    assert set(denied.reasons) == {"commercial_use_forbidden", "territory_forbidden"}

    apply_rights_deadline(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        observation=RightsDeadlineObservation(
            rights_version_id=rights.id,
            purpose=RightsDeadlinePurpose.REVIEW,
            due_at=NOW + timedelta(days=20),
            source_event_id="timer:rights-review:1",
        ),
        actor_ref="service:durable-timer-callback",
        observed_at=NOW + timedelta(days=20),
    )
    assert asset.lifecycle == AssetLifecycle.RESTRICTED.value
    restricted = evaluate_usage(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        use=RightsUse(
            at=NOW + timedelta(days=20),
            territory="NG",
            channel="web",
            purpose="corporate",
            commercial=False,
            modifies=False,
        ),
    )
    assert not restricted.allowed
    assert restricted.reasons == ("lifecycle_restricted",)


def test_renditions_are_idempotent_repairable_and_never_replace_source(
    db: Session,
) -> None:
    _, asset = _asset(db)
    revision = _revision(db, asset.id)
    command = RenditionCommand(
        source_revision_id=revision.id,
        source_checksum=revision.checksum,
        kind=RenditionKind.THUMBNAIL,
        recipe_code="web-thumb",
        recipe_version="v2",
        engine_code="sharp-adapter",
        engine_version="0.34.1",
        requested_width=640,
        requested_height=426,
        output_media_type="image/webp",
    )
    rendition = request_rendition(
        db,
        tenant_id=TENANT_ID,
        command=command,
        actor_ref="service:renderer",
        recorded_at=NOW,
    )
    replay = request_rendition(
        db,
        tenant_id=TENANT_ID,
        command=command,
        actor_ref="service:renderer",
        recorded_at=NOW,
    )
    assert replay.id == rendition.id

    complete_rendition(
        db,
        tenant_id=TENANT_ID,
        rendition_id=rendition.id,
        output=RenditionOutput(
            file_id=uuid.uuid4(),
            checksum="c" * 64,
            byte_length=12_000,
            width=640,
            height=426,
            codec="webp",
        ),
        actor_ref="service:renderer",
        completed_at=NOW + timedelta(seconds=2),
    )
    assert asset.current_revision_id == revision.id
    assert rendition.output_file_id is not None
    assert not rendition_is_stale(
        rendition,
        revision,
        recipe_version="v2",
        engine_version="0.34.1",
    )
    assert rendition_is_stale(
        rendition,
        revision,
        recipe_version="v3",
        engine_version="0.34.1",
    )
    regenerated = request_rendition_regeneration(
        db,
        tenant_id=TENANT_ID,
        rendition_id=rendition.id,
        reason="output_missing",
        actor_ref="service:reconciler",
        requested_at=NOW + timedelta(seconds=3),
    )
    assert regenerated.id == rendition.id
    assert regenerated.attempt_number == 2
    assert regenerated.state == "requested"
    assert regenerated.output_file_id is None
    assert asset.current_revision_id == revision.id


def test_scan_and_approval_are_observations_not_automatic_publication(
    db: Session,
) -> None:
    _, asset = _asset(db)
    revision = _revision(db, asset.id)
    observe_scan(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        observation=ScanObservation(
            revision_id=revision.id,
            file_id=revision.file_id,
            checksum=revision.checksum,
            decision="safe",
            scanner_ref="files-scan:991",
            observed_at=NOW,
        ),
        actor_ref="service:files",
        recorded_at=NOW,
    )
    observe_approval(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        revision_id=revision.id,
        checksum=revision.checksum,
        approval_ref="approvals:decision:55",
        decision="approved",
        actor_ref="service:approvals",
        observed_at=NOW,
    )
    assert asset.lifecycle == AssetLifecycle.INGESTING.value

    transition_asset(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        target=AssetLifecycle.AVAILABLE,
        reason="rights and review complete",
        actor_ref="user:media-admin",
        effective_at=NOW,
    )
    assert asset.lifecycle == AssetLifecycle.AVAILABLE.value

    with pytest.raises(LifecycleError):
        transition_asset(
            db,
            tenant_id=TENANT_ID,
            asset_id=asset.id,
            target=AssetLifecycle.INGESTING,
            reason="rewrite history",
            actor_ref="user:media-admin",
            effective_at=NOW,
        )


def test_access_uses_nearest_scope_and_expired_external_grants_fail_closed(
    db: Session,
) -> None:
    library, asset = _asset(db)
    _revision(db, asset.id)
    grant_access(
        db,
        tenant_id=TENANT_ID,
        command=AccessGrantCommand(
            scope=AccessScope.LIBRARY,
            scope_id=library.id,
            principal_type="group",
            principal_ref="group:designers",
            permission=Permission.VIEW,
            effect=GrantEffect.ALLOW,
        ),
        actor_ref="user:admin",
        recorded_at=NOW,
    )
    grant_access(
        db,
        tenant_id=TENANT_ID,
        command=AccessGrantCommand(
            scope=AccessScope.ASSET,
            scope_id=asset.id,
            principal_type="group",
            principal_ref="group:designers",
            permission=Permission.VIEW,
            effect=GrantEffect.DENY,
        ),
        actor_ref="user:admin",
        recorded_at=NOW,
    )
    decision = may_access(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        principal_refs=("group:designers",),
        permission=Permission.VIEW,
        at=NOW,
    )
    assert not decision.allowed
    assert decision.reason == "explicit_deny"

    share = grant_access(
        db,
        tenant_id=TENANT_ID,
        command=AccessGrantCommand(
            scope=AccessScope.ASSET,
            scope_id=asset.id,
            principal_type="external_share",
            principal_ref="share:digest:abc",
            permission=Permission.VIEW,
            effect=GrantEffect.ALLOW,
            expires_at=NOW + timedelta(minutes=5),
        ),
        actor_ref="user:admin",
        recorded_at=NOW,
    )
    assert share.expires_at is not None
    assert not may_access(
        db,
        tenant_id=TENANT_ID,
        asset_id=asset.id,
        principal_refs=("share:digest:abc",),
        permission=Permission.VIEW,
        at=NOW + timedelta(minutes=6),
    ).allowed


def test_usage_is_deduplicated_and_records_remains_disposition_authority(
    db: Session,
) -> None:
    _, asset = _asset(db)
    revision = _revision(db, asset.id)
    command = UsageObservationCommand(
        source_owner="dotmac-content",
        source_type="content_item_revision",
        source_id="content-17",
        source_version="v4",
        relation="hero_image",
        revision_id=revision.id,
        rendition_id=None,
        source_event_id="content-event-991",
        source_fingerprint="d" * 64,
        active=True,
        observed_at=NOW,
    )
    first = observe_usage(db, tenant_id=TENANT_ID, command=command)
    replay = observe_usage(db, tenant_id=TENANT_ID, command=command)
    assert replay.id == first.id
    assert [
        row.source_owner
        for row in usage_references(db, tenant_id=TENANT_ID, revision_id=revision.id)
    ] == ["dotmac-content"]

    with pytest.raises(Conflict):
        observe_usage(
            db,
            tenant_id=TENANT_ID,
            command=UsageObservationCommand(
                **{**command.as_dict(), "source_fingerprint": "e" * 64}
            ),
        )

    blocked = evaluate_disposition(
        db,
        tenant_id=TENANT_ID,
        revision_id=revision.id,
        record=RecordDispositionObservation(
            declared_as_record=True,
            records_allows_disposition=False,
            evidence_ref="records:record-91:hold-4",
        ),
    )
    assert not blocked.dispose_allowed
    assert set(blocked.reasons) == {
        "active_usage",
        "current_revision",
        "records_refused_disposition",
    }

    observe_usage(
        db,
        tenant_id=TENANT_ID,
        command=UsageObservationCommand(
            **{
                **command.as_dict(),
                "source_event_id": "content-event-992",
                "source_fingerprint": "f" * 64,
                "active": False,
                "observed_at": NOW + timedelta(seconds=1),
            }
        ),
    )
    assert usage_references(db, tenant_id=TENANT_ID, revision_id=revision.id) == ()

    events = db.scalars(select(MediaEvent)).all()
    assert {event.event_type for event in events} >= {
        "asset_created",
        "revision_created",
        "usage_observed",
    }
