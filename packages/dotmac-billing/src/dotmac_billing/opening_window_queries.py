"""Scope-explicit immutable opening-window reads; never provision or admit."""

from __future__ import annotations

from dotmac_kernel.cache import Scope
from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_billing.opening_contracts import OpeningManifestV1
from dotmac_billing.opening_queries import _decode_row, _require, _scoped, _table
from dotmac_billing.opening_receivable_contracts import OpeningManifestV2
from dotmac_billing.opening_receivable_validation import opening_manifest_fingerprint_v2
from dotmac_billing.opening_validation import opening_manifest_fingerprint
from dotmac_billing.opening_window_contracts import (
    OpeningWindowAdmissionV1,
    OpeningWindowSealV1,
    OpeningWindowV1,
    StoredOpeningWindowV1,
    opening_admission_population_digest,
)


def get_opening_window(db: Session, *, scope: Scope) -> OpeningWindowV1 | None:
    table = _table(scope, "opening_windows")
    with db.no_autoflush:
        row = db.execute(_scoped(select(table), scope, table)).mappings().one_or_none()
    if row is None:
        return None
    stored = _decode_row(row, StoredOpeningWindowV1)
    window, proof = stored.window, stored.verification
    digest = (
        opening_manifest_fingerprint_v2(window.manifest)
        if isinstance(window.manifest, OpeningManifestV2)
        else opening_manifest_fingerprint(window.manifest)
    )
    _require(
        window.scope == scope == window.manifest.scope == proof.scope
        and window.window_id == row["id"] == proof.window_id
        and window.epoch == row["epoch"] == proof.epoch
        and window.manifest_fingerprint
        == row["manifest_fingerprint"]
        == proof.manifest_fingerprint
        == digest
        and window.principal_name == row["principal_name"] == proof.principal_name
        and window.principal_oid == row["principal_oid"] == proof.principal_oid
        and window.provision_authority_ref
        == row["provision_authority_ref"]
        == proof.authority_ref
        and window.manifest_version
        == row["manifest_version"]
        == (1 if type(window.manifest) is OpeningManifestV1 else 2)
        and proof.operation == "billing.opening.window.provision.v1"
        and proof.command_digest
        == fingerprint_of(
            {
                "domain": "billing.opening.window.provision.v1",
                "command": {"window": row["evidence"]["window"]},
            }
        )
    )
    return window


def get_opening_window_admissions(
    db: Session, *, scope: Scope
) -> tuple[OpeningWindowAdmissionV1, ...]:
    table = _table(scope, "opening_window_admissions")
    with db.no_autoflush:
        rows = (
            db.execute(
                _scoped(select(table).order_by(table.c.receipt_id), scope, table)
            )
            .mappings()
            .all()
        )
    return tuple(
        OpeningWindowAdmissionV1(
            scope,
            row["window_id"],
            row["receipt_id"],
            row["billing_account_id"],
            row["request_identity"],
            row["request_digest"],
            row["manifest_fingerprint"],
        )
        for row in rows
    )


def get_opening_window_seal(db: Session, *, scope: Scope) -> OpeningWindowSealV1 | None:
    table = _table(scope, "opening_window_seals")
    with db.no_autoflush:
        row = db.execute(_scoped(select(table), scope, table)).mappings().one_or_none()
    if row is None:
        return None
    seal = _decode_row(row, OpeningWindowSealV1)
    _require(
        seal.command.scope == scope == seal.verification.scope
        and seal.command.epoch == seal.verification.epoch
        and seal.command.manifest_fingerprint == seal.verification.manifest_fingerprint
        and seal.command.authority_ref == seal.verification.authority_ref
        and seal.command.window_id == row["window_id"] == seal.verification.window_id
        and seal.population_digest
        == row["population_digest"]
        == seal.verification.population_digest
        and seal.population_count
        == row["population_count"]
        == seal.verification.population_count
        and seal.command.coupled_watermark
        == row["coupled_watermark"]
        == seal.verification.coupled_watermark
        and seal.verification.command_digest == row["command_digest"]
        and seal.verification.operation == "billing.opening.window.seal.v1"
        and seal.population_digest
        == opening_admission_population_digest(seal.command.admissions)
        and seal.population_count == len(seal.command.admissions)
        and seal.verification.command_digest
        == fingerprint_of(
            {
                "domain": "billing.opening.window.seal.v1",
                "command": row["evidence"]["command"],
            }
        )
    )
    return seal
