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

import re
import secrets as _secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.execution import payload_digest
from dotmac_integration.models import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
)
from dotmac_integration.secret_refs import (
    CapabilityConfigurationError,
    validate_config_revision,
    verify_capability_configuration,
)
from dotmac_integration.spi import ConnectorMode, accepts_manifest_digest

__all__ = [
    "ENDPOINT_AUDIT_ACTIONS",
    "AdoptionPreview",
    "LifecycleError",
    "add_binding",
    "adopt_manifest",
    "create_draft",
    "disable",
    "enable",
    "mint_ingress_endpoint",
    "preview_adoption",
    "put_config_revision",
    "quarantine",
    "retire",
    "revoke_ingress_endpoint",
    "rotate_ingress_endpoint",
    "set_binding_enabled",
]


class LifecycleError(RuntimeError):
    """An installation cannot move the way the caller asked."""


_TERMINAL = frozenset({"retired"})
_ARTIFACT_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")


def _validated_connector_artifact_digest(digest: str | None) -> str | None:
    if digest is None:
        return None
    if _ARTIFACT_DIGEST_RE.fullmatch(digest) is None:
        raise LifecycleError(
            "connector_artifact_digest must be 'sha256:' plus 64 lowercase "
            "hex digits from the admitted Release Catalog artifact"
        )
    return digest


def create_draft(
    db: Any,
    *,
    registry: ConnectorRegistry,
    connector_key: str,
    name: str,
    environment: str = "production",
    connector_artifact_digest: str | None = None,
    actor: str | None = None,
) -> ConnectorInstallation:
    """Start an installation, pinned to the connector installed RIGHT NOW.

    The manifest digest and SPI range are captured at draft time rather than
    read live later, which is what makes a later upgrade a visible ADOPTION
    decision instead of a silent change under a running integration.

    ``connector_artifact_digest`` is supplied by the assembly after resolving
    the exact artifact in the Release Catalog. This module validates its
    canonical content-address shape and stores it; it does not read another
    module's database or pretend a syntactically valid digest proves release.
    """
    plugin = registry.plugin(connector_key)
    manifest = plugin.manifest
    artifact_digest = _validated_connector_artifact_digest(connector_artifact_digest)

    installation = ConnectorInstallation(
        connector_key=manifest.connector_key,
        connector_version=manifest.version,
        spi_range=str(manifest.spi_range),
        manifest_digest=manifest.digest,
        connector_artifact_digest=artifact_digest,
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
        plugin = registry.plugin(installation.connector_key)
        if ConnectorMode.PROVISION in plugin.modes:
            _validated_connector_artifact_digest(installation.connector_artifact_digest)
            if installation.connector_artifact_digest is None:
                raise LifecycleError(
                    f"capability {binding.capability_id!r} cannot activate without "
                    "the exact Release Catalog connector artifact digest"
                )
            if installation.current_config_revision_id is None:
                raise CapabilityConfigurationError(
                    f"capability {binding.capability_id!r} has no current "
                    "configuration revision"
                )
            revision = db.get(
                ConnectorConfigRevision, installation.current_config_revision_id
            )
            if revision is None:
                raise CapabilityConfigurationError(
                    f"capability {binding.capability_id!r} current configuration "
                    "revision is unavailable"
                )
            declaration = plugin.manifest.require_declares(binding.capability_id)
            verify_capability_configuration(
                declaration,
                config=(revision.config_json or {}),
                secret_refs=(revision.secret_refs or {}),
            )
        binding.state = "enabled"
        binding.enabled_at = datetime.now(UTC)
    else:
        binding.state = "disabled"
    binding.updated_by = actor
    db.flush()
    return binding


# ── The ingress endpoint ────────────────────────────────────────────────────
#
# An ingress URL addresses ONE minted key on ONE binding. Minting is a
# deliberate act rather than a property every binding has: with the primary key
# as the address, every binding in the fleet — delivery-only ones included —
# would carry a live URL, and the PK is already disclosed in operator-facing
# error text. An unminted binding is a 404 that no plugin ever sees.
#
# THE KEY IS NEVER AUDITED. Every one of the three operations writes an audit
# event, and not one of them records the key, a prefix of it or a digest of it.
# The audit ledger is read by more people, kept for longer and exported more
# often than any other table in the fleet — a bearer credential in it is a
# credential in every one of those places. The event records WHICH endpoint
# changed (binding, installation, capability, connector); the key itself is
# returned to the caller once, at the moment it is minted, and never again.

#: 24 bytes of `secrets.token_hex` — 192 bits, 48 lowercase hex characters, and
#: exactly the shape `ingress._ENDPOINT_KEY_RE` admits before it will query.
_ENDPOINT_KEY_BYTES: Final[int] = 24

#: The three audit actions these operations write, unprefixed. Declared here,
#: beside their only writers, and pinned to `manifest.module.audit_actions` by
#: `test_integration_ingress.py` — so a code that stops being written, or one
#: written without being declared, fails the build rather than leaving the trail
#: quietly incomplete.
ENDPOINT_AUDIT_ACTIONS: Final[tuple[str, str, str]] = (
    "ingress_endpoint.minted",
    "ingress_endpoint.rotated",
    "ingress_endpoint.revoked",
)


def _endpoint_audit(
    db: Any,
    binding: CapabilityBinding,
    installation: ConnectorInstallation,
    *,
    action: str,
    actor: str | None,
) -> None:
    """Record WHICH endpoint changed. Never the key.

    An adapter over `operations.record_operation`, which is itself an adapter
    over the kernel's one platform audit ledger — this module keeps no second
    trail (ADR-0014's rule applied to audit, hard rule 21's sibling).

    The import is deferred for the same reason `record_operation`'s own is: the
    kernel's audit module reaches persistence, and a top-level import would make
    this package unimportable without a configured database.
    """
    from dotmac_integration.operations import record_operation

    record_operation(
        db,
        action=action,
        entity_type="capability_binding",
        entity_id=str(binding.id),
        details={
            "installation_id": str(installation.id),
            "connector_key": installation.connector_key,
            "capability_id": binding.capability_id,
            "actor": actor,
        },
    )


def _fresh_endpoint_key(db: Any, binding: CapabilityBinding) -> str:
    """Assign a new key, retrying ONCE against the unique index.

    192 bits makes a collision fictional, but a bare insert that CAN raise is
    worse than a loop that cannot. The attempt runs inside a SAVEPOINT rather
    than a transaction: this module never rolls back a caller's unit of work,
    and a failed flush without a savepoint would leave the session unusable.
    """
    from sqlalchemy.exc import IntegrityError

    for remaining in (1, 0):
        key = _secrets.token_hex(_ENDPOINT_KEY_BYTES)
        try:
            with db.begin_nested():
                binding.ingress_endpoint_key = key
                db.flush()
        except IntegrityError:
            if not remaining:
                raise
            continue
        return key
    raise LifecycleError("could not mint a distinct ingress endpoint key")


def _receiving_installation(
    db: Any, binding: CapabilityBinding, registry: ConnectorRegistry
) -> ConnectorInstallation:
    """The three checks that must happen BEFORE a key exists.

    Every one of them is a failure that would otherwise be DEFERRED to the
    provider's first request — the worst moment to discover it, because by then
    the address is in a third party's console, the operator has been told the
    integration is live, and the only symptom is a 503 nobody is watching.

    ==================== ==================================================
    compatibility        the installed distribution's declared SPI range
                         must admit the running module
    manifest pin         the installed distribution must still honour the
                         digest this installation was pinned to
    ingress mode         the connector must DECLARE `INGRESS` and actually
                         implement the receiving protocol
    ==================== ==================================================

    The pin check is the one most easily forgotten and the one that bites
    hardest: an installation whose connector was upgraded past its adoption
    window can still be `enabled`, still pass every state check, and still fail
    every single delivery — so minting an address for it publishes a URL whose
    only possible answer is 503.

    The mode check covers BOTH halves of the claim. A connector that declares
    `INGRESS` in its manifest but ships no `ingress_handler_for` would pass a
    declaration-only check and fail with an `AttributeError` at request time.
    """
    from dotmac_integration.ingress import IngressPlugin

    installation: ConnectorInstallation | None = db.get(
        ConnectorInstallation, binding.installation_id
    )
    if installation is None:
        raise LifecycleError(f"binding {binding.id} has no installation")

    try:
        registry.require_compatible(installation.connector_key)
    except Exception as exc:
        raise LifecycleError(
            f"connector {installation.connector_key!r} is not usable in this "
            f"runtime, so an ingress endpoint for binding {binding.id} would "
            f"answer nothing but 503: {exc}"
        ) from exc

    plugin = registry.plugin(installation.connector_key)
    if not accepts_manifest_digest(plugin, installation.manifest_digest):
        raise LifecycleError(
            f"the installed {installation.connector_key!r} no longer honours "
            f"installation {installation.id}'s manifest pin; adopt the current "
            "manifest before publishing an address for it"
        )

    if ConnectorMode.INGRESS not in plugin.modes:
        raise LifecycleError(
            f"connector {installation.connector_key!r} does not declare "
            f"{ConnectorMode.INGRESS.value}, so an ingress endpoint for binding "
            f"{binding.id} would answer nothing but 503"
        )
    if not isinstance(plugin, IngressPlugin):
        raise LifecycleError(
            f"connector {installation.connector_key!r} declares "
            f"{ConnectorMode.INGRESS.value} but serves no ingress handler, so a "
            "minted endpoint would fail at the provider's first request"
        )
    return installation


def mint_ingress_endpoint(
    db: Any,
    binding: CapabilityBinding,
    *,
    registry: ConnectorRegistry,
    actor: str | None = None,
) -> str:
    """Give this binding an ingress address. Refuses if it already has one.

    Gated on compatibility, the manifest pin and the INGRESS mode — see
    `_receiving_installation` for why all three belong HERE rather than at the
    provider's first request.

    Deliberately NOT gated on the binding being enabled. A binding is minted
    BEFORE it is enabled in every provider flow that requires a completed GET
    handshake first; requiring `enabled` here would rebuild, one layer up, the
    exact circularity `ingress.answer_challenge` exists to break.

    Re-minting is refused rather than treated as rotation. The two are different
    intentions — one is "this binding should be reachable", the other is "the
    address it already publishes has been compromised" — and only the second
    should silently retire a URL that lives in a third party's console.

    Returns the key ONCE. It is never returned again and never audited: show it
    to the operator now, or mint a new one later.
    """
    if binding.ingress_endpoint_key is not None:
        raise LifecycleError(
            f"binding {binding.id} already publishes an ingress endpoint; "
            "rotate it deliberately rather than minting a second address"
        )
    installation = _receiving_installation(db, binding, registry)

    key = _fresh_endpoint_key(db, binding)
    binding.updated_by = actor
    db.flush()
    _endpoint_audit(
        db, binding, installation, action=ENDPOINT_AUDIT_ACTIONS[0], actor=actor
    )
    return key


def rotate_ingress_endpoint(
    db: Any,
    binding: CapabilityBinding,
    *,
    registry: ConnectorRegistry,
    actor: str | None = None,
) -> str:
    """Replace the published address, keeping the entire inbox history.

    Receipts are keyed on the BINDING, so rotation retires a URL without
    touching a single row of evidence — the property the primary key cannot
    offer, since it is FK-referenced from three tables.

    The same three gates run again. Rotation cannot make an endpoint exist that
    did not, so it cannot bypass minting's gate — but it CAN be the moment an
    operator discovers the connector was upgraded past its pin, and publishing a
    fresh address for an installation that can no longer serve it would replace
    a working URL with a permanently broken one. An unminted binding is refused
    outright: there is nothing to rotate.
    """
    if binding.ingress_endpoint_key is None:
        raise LifecycleError(
            f"binding {binding.id} publishes no ingress endpoint; there is "
            "nothing to rotate. Mint one, which is where the gates live"
        )
    installation = _receiving_installation(db, binding, registry)

    key = _fresh_endpoint_key(db, binding)
    binding.updated_by = actor
    db.flush()
    _endpoint_audit(
        db, binding, installation, action=ENDPOINT_AUDIT_ACTIONS[1], actor=actor
    )
    return key


def revoke_ingress_endpoint(
    db: Any, binding: CapabilityBinding, *, actor: str | None = None
) -> None:
    """Withdraw the address. The endpoint 404s; the binding is untouched.

    Deliberately not the same act as disabling the binding: a revoked endpoint
    stops being reachable while the binding keeps delivering and keeps every
    receipt it ever recorded.

    Ungated on purpose, and the only one of the three that is. Every gate above
    exists to stop an address being published that cannot work; none of them has
    anything to say about WITHDRAWING one. Revocation is the operator's response
    to a leak, and a leak does not wait for a compatible connector — refusing to
    revoke because the distribution is missing would leave a live bearer
    credential in a third party's console with no way to kill it.

    Idempotent: revoking an unminted binding is a no-op that still records the
    intent, because "I revoked it" and "it was already gone" are the same
    outcome and an operator must not have to tell them apart under pressure.
    """
    binding.ingress_endpoint_key = None
    binding.updated_by = actor
    db.flush()
    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is not None:
        _endpoint_audit(
            db, binding, installation, action=ENDPOINT_AUDIT_ACTIONS[2], actor=actor
        )


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
    connector_artifact_digest: str | None = None,
    actor: str | None = None,
) -> AdoptionPreview:
    """Move the installation onto the installed connector's current manifest.

    ATOMIC and IDEMPOTENT: adopting when already adopted is a no-op that
    succeeds, because adoption is exactly the operation someone retries after a
    timeout, and a second attempt must not be an error.

    Refused when adoption would strand a bound capability. Applying it anyway
    would leave a binding pointing at a contract the connector no longer
    implements — an integration that reports healthy and cannot run.

    A PROVISION adoption also requires the new distribution's Release
    Catalog-backed ``connector_artifact_digest``. Reusing the old pin would
    falsely claim the new manifest runs from the old bytes.
    """
    artifact_digest = _validated_connector_artifact_digest(connector_artifact_digest)
    plugin = registry.plugin(installation.connector_key)
    preview = preview_adoption(db, installation, registry=registry)
    if preview.blocked:
        raise LifecycleError(
            f"adopting {preview.target_digest[:12]} would strand bound "
            f"capabilities {list(preview.dropped_capabilities)}; disable or "
            "rebind them first"
        )
    if not preview.adoption_required:
        if artifact_digest is not None:
            if (
                installation.connector_artifact_digest is not None
                and installation.connector_artifact_digest != artifact_digest
            ):
                raise LifecycleError(
                    "connector artifact digest conflicts with the digest already "
                    "pinned for this manifest"
                )
            if installation.connector_artifact_digest is None:
                installation.connector_artifact_digest = artifact_digest
                installation.updated_by = actor
                db.flush()
        return preview

    if ConnectorMode.PROVISION in plugin.modes and artifact_digest is None:
        raise LifecycleError(
            "adopting a PROVISION connector requires the exact Release Catalog "
            "connector artifact digest"
        )
    target = plugin.manifest
    installation.manifest_digest = target.digest
    installation.connector_version = target.version
    installation.spi_range = str(target.spi_range)
    # An artifact pin belongs to one exact released distribution. Preserving a
    # prior version's digest across adoption would be stronger-looking than
    # null and false. Legacy non-PROVISION adoption may remain unpinned; a
    # PROVISION target was required above to supply its replacement.
    installation.connector_artifact_digest = artifact_digest
    installation.updated_by = actor
    db.flush()
    return preview
