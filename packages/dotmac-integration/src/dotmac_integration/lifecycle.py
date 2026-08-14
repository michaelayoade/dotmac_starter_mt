"""The installation lifecycle owner — draft, revise, bind, enable, retire.

Ported from `dotmac_sub`'s `installations.py`. This is the service the models
were waiting for: without it nothing drafts an installation, mints a revision,
or adopts a manifest, and the tables are a schema nobody can reach.

## Transaction authority stays with the host

Every function here MUTATES and FLUSHES. None commits, none rolls back, and none
opens a session. The composing assembly owns the transaction — the same rule
`dotmac_kernel.db` enforces for the starter's own services — so a caller can put
several of these in one unit of work and have it mean something.

## Enablement is CONNECTION-gated, not just schema-gated

Static validation (does the config match the declared schema, are the secret
references well-formed) is necessary and insufficient. A connector whose
credentials are wrong passes every static check and fails on its first real
event, by which time the operator has been told the integration is live. So
`enable` asks the plugin to validate a live connection and refuses on a failing
diagnostic.

## Adoption is a preview, then an atomic idempotent apply

`preview_adoption` answers "what would change?" without changing anything —
because an operator adopting a new connector version needs to see whether their
pinned digest is still honoured BEFORE the window closes. `adopt` then applies
it idempotently: adopting twice is not an error and does not mint a second
revision, which matters because adoption is exactly the operation someone
retries after a timeout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.execution import payload_digest
from dotmac_integration.models import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
)
from dotmac_integration.secret_refs import validate_config_revision
from dotmac_integration.spi import accepts_manifest_digest

__all__ = [
    "AdoptionPreview",
    "LifecycleError",
    "add_binding",
    "adopt_manifest",
    "create_draft",
    "disable",
    "enable",
    "preview_adoption",
    "put_config_revision",
    "quarantine",
    "retire",
    "set_binding_enabled",
]


class LifecycleError(RuntimeError):
    """An installation cannot move the way the caller asked."""


_TERMINAL = frozenset({"retired"})


def create_draft(
    db: Any,
    *,
    registry: ConnectorRegistry,
    connector_key: str,
    name: str,
    environment: str = "production",
    actor: str | None = None,
) -> ConnectorInstallation:
    """Start an installation, pinned to the connector installed RIGHT NOW.

    The manifest digest and SPI range are captured at draft time rather than
    read live later, which is what makes a later upgrade a visible ADOPTION
    decision instead of a silent change under a running integration.
    """
    plugin = registry.plugin(connector_key)
    manifest = plugin.manifest

    installation = ConnectorInstallation(
        connector_key=manifest.connector_key,
        connector_version=manifest.version,
        spi_range=str(manifest.spi_range),
        manifest_digest=manifest.digest,
        name=name.strip(),
        environment=environment,
        state="draft",
        created_by=actor,
        updated_by=actor,
    )
    db.add(installation)
    db.flush()
    return installation


def put_config_revision(
    db: Any,
    installation: ConnectorInstallation,
    *,
    config: dict[str, object],
    secret_refs: dict[str, object] | None = None,
    schema_version: str = "1",
    actor: str | None = None,
) -> tuple[ConnectorConfigRevision, bool]:
    """Add an immutable revision. Returns `(revision, is_new)`.

    IDEMPOTENT by digest: re-submitting an identical configuration returns the
    existing revision rather than minting another. Without that, every
    reconcile inflates the history until "when did this last change?" stops
    being answerable — which is the question an incident asks.
    """
    from sqlalchemy import func, select

    if installation.state in _TERMINAL:
        raise LifecycleError(
            f"installation is {installation.state} and cannot take configuration"
        )
    refs = secret_refs or {}
    # Refuse secret MATERIAL before it is ever written: a revision is immutable
    # and ends up in every backup.
    validate_config_revision(config, refs)
    digest = payload_digest({"config": config, "secret_refs": refs})

    existing = db.execute(
        select(ConnectorConfigRevision).where(
            ConnectorConfigRevision.installation_id == installation.id,
            ConnectorConfigRevision.config_digest == digest,
        )
    ).scalar_one_or_none()
    if existing is not None:
        installation.current_config_revision_id = existing.id
        db.flush()
        return existing, False

    highest = db.execute(
        select(func.max(ConnectorConfigRevision.revision)).where(
            ConnectorConfigRevision.installation_id == installation.id
        )
    ).scalar_one()

    revision = ConnectorConfigRevision(
        installation_id=installation.id,
        revision=int(highest or 0) + 1,
        schema_version=schema_version,
        config_json=config,
        secret_refs=refs,
        config_digest=digest,
        validation_status="valid",
        created_by=actor,
    )
    db.add(revision)
    db.flush()
    installation.current_config_revision_id = revision.id
    installation.validated_at = datetime.now(UTC)
    installation.updated_by = actor
    db.flush()
    return revision, True


def add_binding(
    db: Any,
    installation: ConnectorInstallation,
    *,
    registry: ConnectorRegistry,
    capability_id: str,
    scope: dict[str, object] | None = None,
    policy: dict[str, object] | None = None,
    actor: str | None = None,
) -> CapabilityBinding:
    """Declare that this installation implements a capability.

    Created DISABLED. Binding and enabling are separate acts because the first
    is a statement of intent and the second is a live decision — collapsing them
    would enable a capability the moment someone wrote it down.
    """
    manifest = registry.get(installation.connector_key)
    # The undeclared-capability refusal, at the write.
    manifest.require_declares(capability_id)

    binding = CapabilityBinding(
        installation_id=installation.id,
        capability_id=capability_id,
        state="disabled",
        scope_json=scope,
        policy_json=policy,
        created_by=actor,
        updated_by=actor,
    )
    db.add(binding)
    db.flush()
    return binding


def set_binding_enabled(
    db: Any,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
    *,
    registry: ConnectorRegistry,
    enabled: bool,
    actor: str | None = None,
) -> CapabilityBinding:
    """Enable or disable one binding, re-running activation on the way in."""
    from dotmac_integration.activation import require_activatable

    if enabled:
        require_activatable(installation, binding, registry)
        binding.state = "enabled"
        binding.enabled_at = datetime.now(UTC)
    else:
        binding.state = "disabled"
    binding.updated_by = actor
    db.flush()
    return binding


def enable(
    db: Any,
    installation: ConnectorInstallation,
    *,
    registry: ConnectorRegistry,
    secrets: dict[str, object] | None = None,
    actor: str | None = None,
) -> ConnectorInstallation:
    """Enable an installation, gated on a LIVE connection check.

    Static validation cannot tell a wrong credential from a right one. Enabling
    without asking the connector to prove it can connect means the failure
    surfaces on the first real event, after the operator was told it worked.
    """
    if installation.current_config_revision_id is None:
        raise LifecycleError(
            "installation has no configuration revision; there is nothing to "
            "validate a connection against"
        )
    if installation.state in _TERMINAL:
        raise LifecycleError(f"installation is {installation.state}")

    plugin = registry.plugin(installation.connector_key)
    plugin.manifest.spi_range.require()

    revision = db.get(ConnectorConfigRevision, installation.current_config_revision_id)
    diagnostics = plugin.validate_connection(
        config=(revision.config_json if revision else {}) or {},
        secrets=secrets or {},
    )
    failures = [d for d in diagnostics if not d.ok]
    if failures:
        installation.state = "validating"
        installation.state_reason = "; ".join(f"{d.code}: {d.detail}" for d in failures)
        db.flush()
        raise LifecycleError(
            f"connection validation failed: {installation.state_reason}"
        )

    installation.state = "enabled"
    installation.state_reason = None
    installation.enabled_at = datetime.now(UTC)
    installation.updated_by = actor
    db.flush()
    return installation


def disable(
    db: Any,
    installation: ConnectorInstallation,
    *,
    reason: str,
    actor: str | None = None,
) -> ConnectorInstallation:
    """An OPERATOR turned it off. Distinct from quarantine."""
    installation.state = "disabled"
    installation.state_reason = reason
    installation.updated_by = actor
    db.flush()
    return installation


def quarantine(
    db: Any,
    installation: ConnectorInstallation,
    *,
    reason: str,
    actor: str | None = None,
) -> ConnectorInstallation:
    """The PLATFORM stopped trusting it. Distinct from disable.

    Kept as a separate state because the two need different responses: a
    disabled installation is waiting for a person, a quarantined one is waiting
    for an explanation.
    """
    installation.state = "quarantined"
    installation.state_reason = reason
    installation.updated_by = actor
    db.flush()
    return installation


def retire(
    db: Any,
    installation: ConnectorInstallation,
    *,
    reason: str,
    actor: str | None = None,
) -> ConnectorInstallation:
    """Terminal. Configuration history is KEPT — retiring an integration must
    not destroy the evidence of what it did."""
    installation.state = "retired"
    installation.state_reason = reason
    installation.retired_at = datetime.now(UTC)
    installation.updated_by = actor
    db.flush()
    return installation


# ── Manifest adoption ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AdoptionPreview:
    """What adopting the installed connector would change."""

    installation_id: UUID
    installed_digest: str
    target_digest: str
    adoption_required: bool
    #: Bound capabilities the NEW manifest no longer declares. Non-empty means
    #: adoption would strand them, so it is refused rather than applied.
    dropped_capabilities: tuple[str, ...]
    honours_current_pin: bool

    @property
    def blocked(self) -> bool:
        return bool(self.dropped_capabilities)


def preview_adoption(
    db: Any, installation: ConnectorInstallation, *, registry: ConnectorRegistry
) -> AdoptionPreview:
    """Answer "what would change?" WITHOUT changing anything.

    An operator needs to see whether their pinned digest is still honoured and
    whether any bound capability would be stranded — before the decision, not
    after it.
    """
    from sqlalchemy import select

    plugin = registry.plugin(installation.connector_key)
    target = plugin.manifest

    bound = set(
        db.execute(
            select(CapabilityBinding.capability_id).where(
                CapabilityBinding.installation_id == installation.id
            )
        )
        .scalars()
        .all()
    )
    return AdoptionPreview(
        installation_id=installation.id,
        installed_digest=installation.manifest_digest,
        target_digest=target.digest,
        adoption_required=installation.manifest_digest != target.digest,
        dropped_capabilities=tuple(sorted(bound - target.capability_ids)),
        honours_current_pin=accepts_manifest_digest(
            plugin, installation.manifest_digest
        ),
    )


def adopt_manifest(
    db: Any,
    installation: ConnectorInstallation,
    *,
    registry: ConnectorRegistry,
    actor: str | None = None,
) -> AdoptionPreview:
    """Move the installation onto the installed connector's current manifest.

    ATOMIC and IDEMPOTENT: adopting when already adopted is a no-op that
    succeeds, because adoption is exactly the operation someone retries after a
    timeout, and a second attempt must not be an error.

    Refused when adoption would strand a bound capability. Applying it anyway
    would leave a binding pointing at a contract the connector no longer
    implements — an integration that reports healthy and cannot run.
    """
    preview = preview_adoption(db, installation, registry=registry)
    if preview.blocked:
        raise LifecycleError(
            f"adopting {preview.target_digest[:12]} would strand bound "
            f"capabilities {list(preview.dropped_capabilities)}; disable or "
            "rebind them first"
        )
    if not preview.adoption_required:
        return preview

    target = registry.get(installation.connector_key)
    installation.manifest_digest = target.digest
    installation.connector_version = target.version
    installation.spi_range = str(target.spi_range)
    installation.updated_by = actor
    db.flush()
    return preview
