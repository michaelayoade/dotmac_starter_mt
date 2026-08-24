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

## One owner per column, so a re-declaration cannot erase a decision

Four decisions about a binding are four operations, each the only writer of
what it decides:

======================================= ====================================
decision                                owner
======================================= ====================================
does this installation implement it     `add_binding`
is it enabled                           `set_binding_enabled`
is it the selection default             `set_binding_selection_policy`
what does the operator call it          `set_binding_scope`
======================================= ====================================

Where its traffic LANDS is a fifth, and it is not here at all — it is
`destination_binding.establish_destination`, writing its own append-only table.

`add_binding` is idempotent by contract, so every reconcile and activation
sequence calls it again on a binding that already exists. While it wrote all
four, a re-declaration that named only the capability reset the other three to
their defaults — and `policy_json` is what `selection` reads to pick between
several enabled bindings, so losing it stopped outbound dispatch with an
ambiguity refusal while every state column still read `enabled`.

`enable` is a LIFECYCLE TRANSITION and writes no binding column at all. That is
asserted statically, not merely intended, by
`tests/architecture/test_integration_lifecycle_writers.py`.

## Adoption is a preview, then an atomic idempotent apply

`preview_adoption` answers "what would change?" without changing anything —
because an operator adopting a new connector version needs to see whether their
pinned digest is still honoured BEFORE the window closes. `adopt` then applies
it idempotently: adopting twice is not an error and does not mint a second
revision, which matters because adoption is exactly the operation someone
retries after a timeout.
"""

from __future__ import annotations

import secrets as _secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from jsonschema import Draft202012Validator

from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.execution import payload_digest
from dotmac_integration.models import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
)
from dotmac_integration.secret_refs import validate_config_revision
from dotmac_integration.spi import (
    ConnectorManifest,
    ConnectorMode,
    ConnectorPlugin,
    accepts_manifest_digest,
)

__all__ = [
    "ENDPOINT_AUDIT_ACTIONS",
    "KEEP",
    "QUARANTINE_AUDIT_ACTIONS",
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
    "release_quarantine",
    "retire",
    "revoke_ingress_endpoint",
    "rotate_ingress_endpoint",
    "set_binding_enabled",
    "set_binding_scope",
    "set_binding_selection_policy",
]


class LifecycleError(RuntimeError):
    """An installation cannot move the way the caller asked."""


_TERMINAL = frozenset({"retired"})


class _Keep:
    """Leave this column exactly as it is — an omission marker distinct from
    `None`.

    `None` is a REAL value for both `scope_json` and `policy_json`: it means
    "this binding declares no scope / is not the selection default". A default
    of `None` therefore cannot mean "unspecified", because the write is then
    indistinguishable from an operator deliberately clearing the column — which
    is exactly how a re-declaration came to erase an established selection
    policy. A separate sentinel makes omission and clearing two different
    instructions, and keeps `policy=None` a usable one.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "KEEP"


#: The omission marker for :func:`add_binding`'s operator-set columns. Exported
#: so a composing assembly can pass it explicitly when it is forwarding an
#: optional field it may not have.
KEEP: Final[_Keep] = _Keep()


def _pinned_manifest(
    registry: ConnectorRegistry, installation: ConnectorInstallation
) -> ConnectorManifest:
    plugin: ConnectorPlugin = registry.plugin(installation.connector_key)
    for manifest in (plugin.manifest, *plugin.historical_manifests):
        if manifest.digest == installation.manifest_digest:
            return manifest
    raise LifecycleError(
        f"the installed {installation.connector_key!r} no longer honours "
        f"installation {installation.id}'s manifest pin; adopt the current "
        "manifest before changing its configuration or bindings"
    )


def _schema_error_codes(
    manifest: ConnectorManifest,
    capability_ids: set[str],
    config: dict[str, object],
) -> tuple[str, ...]:
    """Validate bound contracts without returning or persisting input values."""
    errors: set[str] = set()
    for capability_id in sorted(capability_ids):
        declaration = manifest.require_declares(capability_id)
        validator = Draft202012Validator(declaration.config_schema)
        for error in validator.iter_errors(config):
            # The instance path comes from operator input. A field name can be a
            # credential just as readily as a value, so persist only the closed
            # JSON-Schema validator code and the declared capability identity.
            errors.add(f"config_{error.validator}:{capability_id}")
    return tuple(sorted(errors))


def _bound_capability_ids(db: Any, installation: ConnectorInstallation) -> set[str]:
    from sqlalchemy import select

    return set(
        db.execute(
            select(CapabilityBinding.capability_id).where(
                CapabilityBinding.installation_id == installation.id
            )
        )
        .scalars()
        .all()
    )


def _invalidate_activation(
    db: Any, installation: ConnectorInstallation, *, reason: str, actor: str | None
) -> None:
    from sqlalchemy import select

    installation.state = "draft"
    installation.state_reason = reason
    installation.validated_at = None
    installation.updated_by = actor
    bindings = db.execute(
        select(CapabilityBinding).where(
            CapabilityBinding.installation_id == installation.id
        )
    ).scalars()
    for binding in bindings:
        binding.state = "disabled"
        binding.enabled_at = None
        binding.updated_by = actor


def _record_validation_failure(
    db: Any,
    installation: ConnectorInstallation,
    revision: ConnectorConfigRevision,
    *,
    codes: tuple[str, ...],
) -> None:
    revision.validation_status = "invalid"
    revision.validation_errors = list(codes)
    installation.state = "validating"
    installation.state_reason = ",".join(codes)
    installation.validated_at = None
    db.flush()


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
    registry: ConnectorRegistry,
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
    normalized_schema_version = schema_version.strip()
    if not normalized_schema_version:
        raise LifecycleError("configuration schema version is required")
    if len(normalized_schema_version) > 32:
        raise LifecycleError("configuration schema version exceeds 32 characters")
    refs = secret_refs or {}
    # Refuse secret MATERIAL before it is ever written: a revision is immutable
    # and ends up in every backup.
    validate_config_revision(config, refs)
    manifest = _pinned_manifest(registry, installation)
    schema_errors = _schema_error_codes(
        manifest, _bound_capability_ids(db, installation), config
    )
    if schema_errors:
        raise LifecycleError(
            "configuration does not match the bound capability schema(s): "
            + ",".join(schema_errors)
        )
    digest = payload_digest(
        {
            "config": config,
            "secret_refs": refs,
            "schema_version": normalized_schema_version,
        }
    )

    existing = db.execute(
        select(ConnectorConfigRevision).where(
            ConnectorConfigRevision.installation_id == installation.id,
            ConnectorConfigRevision.config_digest == digest,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if installation.current_config_revision_id != existing.id:
            _invalidate_activation(
                db, installation, reason="configuration_changed", actor=actor
            )
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
        schema_version=normalized_schema_version,
        config_json=config,
        secret_refs=refs,
        config_digest=digest,
        validation_status="pending",
        created_by=actor,
    )
    db.add(revision)
    db.flush()
    installation.current_config_revision_id = revision.id
    _invalidate_activation(
        db, installation, reason="configuration_changed", actor=actor
    )
    db.flush()
    return revision, True


def add_binding(
    db: Any,
    installation: ConnectorInstallation,
    *,
    registry: ConnectorRegistry,
    capability_id: str,
    scope: dict[str, object] | None | _Keep = KEEP,
    policy: dict[str, object] | None | _Keep = KEEP,
    actor: str | None = None,
) -> CapabilityBinding:
    """Declare that this installation implements a capability.

    Created DISABLED. Binding and enabling are separate acts because the first
    is a statement of intent and the second is a live decision — collapsing them
    would enable a capability the moment someone wrote it down.

    DECLARING a binding is not the same act as CONFIGURING one, and this
    function owns only the first. It is documented and tested as idempotent —
    "rebinding the same installation/capability updates the one existing
    binding" (0.1.0a6) — so every activation and reconcile sequence re-asserts
    the desired binding set through it. While omitted `scope`/`policy`
    arguments were written through as `None`, that idempotent re-declaration
    silently ERASED two operator-established columns:

    * `policy_json` decides which binding serves a capability when several are
      enabled (`dotmac_integration.selection`). Losing `{"default": true}`
      turns a working outbound configuration into a fail-closed
      `AmbiguousBindingError` at the next dispatch — activation appears to
      succeed and outbound traffic stops.
    * `scope_json` is the operator's displayed description of the binding.

    Omission now PRESERVES both; an explicit value — `None` included — is an
    explicit write. Changing either column on an existing binding without
    re-declaring it belongs to :func:`set_binding_selection_policy` and
    :func:`set_binding_scope`, which are their named owners.
    """
    from sqlalchemy import select

    if installation.state in _TERMINAL:
        raise LifecycleError(
            f"installation is {installation.state} and cannot receive capabilities"
        )
    manifest = _pinned_manifest(registry, installation)
    # The undeclared-capability refusal, at the write.
    manifest.require_declares(capability_id)

    if installation.current_config_revision_id is not None:
        revision = db.get(
            ConnectorConfigRevision, installation.current_config_revision_id
        )
        if revision is not None:
            schema_errors = _schema_error_codes(
                manifest, {capability_id}, revision.config_json or {}
            )
            if schema_errors:
                raise LifecycleError(
                    "configuration does not match the capability schema: "
                    + ",".join(schema_errors)
                )

    binding: CapabilityBinding | None = db.execute(
        select(CapabilityBinding).where(
            CapabilityBinding.installation_id == installation.id,
            CapabilityBinding.capability_id == capability_id,
        )
    ).scalar_one_or_none()
    if binding is None:
        binding = CapabilityBinding(
            installation_id=installation.id,
            capability_id=capability_id,
            created_by=actor,
        )
        db.add(binding)
        # A NEW binding starts with both columns unset, exactly as before: an
        # omitted argument has nothing to preserve here.
        binding.scope_json = None
        binding.policy_json = None
    binding.state = "disabled"
    binding.enabled_at = None
    if not isinstance(scope, _Keep):
        binding.scope_json = scope
    if not isinstance(policy, _Keep):
        binding.policy_json = policy
    binding.updated_by = actor
    _invalidate_activation(
        db, installation, reason="capability_binding_changed", actor=actor
    )
    db.flush()
    return binding


def set_binding_scope(
    db: Any,
    binding: CapabilityBinding,
    *,
    scope: dict[str, object] | None,
    actor: str | None = None,
) -> CapabilityBinding:
    """The named owner of `CapabilityBinding.scope_json`.

    Display only — `scope_json` is never read by routing, and
    `dotmac_integration.destination_binding` proves it does not read it. Where
    traffic LANDS is `capability_destination_revisions`, written by
    `establish_destination`; this column is the operator's own label.

    Deliberately does NOT invalidate activation. Activation is a statement
    about the CONFIGURATION and the live connection, and neither was validated
    against this column — returning the installation to `draft` here would make
    editing a label an outage.
    """
    binding.scope_json = scope
    binding.updated_by = actor
    db.flush()
    return binding


def set_binding_selection_policy(
    db: Any,
    binding: CapabilityBinding,
    *,
    policy: dict[str, object] | None,
    actor: str | None = None,
) -> CapabilityBinding:
    """The named owner of `CapabilityBinding.policy_json`.

    `{"default": true}` marks the binding `dotmac_integration.selection` picks
    when a dispatch names no `capability_binding_id` and several bindings are
    enabled for one capability. It is a SELECTION decision, separate from
    whether a binding is enabled (`set_binding_enabled`) and from where its
    traffic lands (`establish_destination`).

    Deliberately does NOT invalidate activation, for the same reason as
    :func:`set_binding_scope`: selection is read live, per dispatch, and no
    connection check ever validated against it.
    """
    binding.policy_json = policy
    binding.updated_by = actor
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
        binding.enabled_at = None
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

#: Entering and leaving quarantine, unprefixed. Declared the same way and for
#: the same reason as the endpoint codes above.
#:
#: Quarantine is a CONTAINMENT decision — it stops an installation consuming the
#: outbound queue and answering ingress — so "who stopped trusting this, when,
#: and why" is the first thing an incident asks and the last thing that should
#: live only in a mutable `state_reason` column that the very next state change
#: overwrites. Both directions are declared: a release with no trail is how a
#: quarantine quietly stops meaning anything.
QUARANTINE_AUDIT_ACTIONS: Final[tuple[str, str]] = (
    "installation.quarantined",
    "installation.quarantine_released",
)


def _installation_audit(
    db: Any,
    installation: ConnectorInstallation,
    *,
    action: str,
    actor: str | None,
    reason: str,
    previous_state: str,
) -> None:
    """Record a containment decision about one installation.

    Same adapter shape as `_endpoint_audit`, and the same deferred import for
    the same reason: one platform ledger, reached only when something is
    actually written.

    `previous_state` is in the event because the column it came from is about to
    be overwritten. Without it the trail can say an installation was quarantined
    but not what it was doing beforehand — and "it was enabled and serving
    traffic" reads very differently from "it was already disabled".
    """
    from dotmac_integration.operations import record_operation

    record_operation(
        db,
        action=action,
        entity_type="connector_installation",
        entity_id=str(installation.id),
        details={
            "connector_key": installation.connector_key,
            "installation_name": installation.name,
            "previous_state": previous_state,
            "reason": reason,
            "actor": actor,
        },
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
    if revision is None:
        raise LifecycleError("installation's configuration revision is missing")
    manifest = _pinned_manifest(registry, installation)
    schema_errors = _schema_error_codes(
        manifest, _bound_capability_ids(db, installation), revision.config_json or {}
    )
    if schema_errors:
        revision.validation_status = "invalid"
        revision.validation_errors = list(schema_errors)
        installation.state = "draft"
        installation.state_reason = ",".join(schema_errors)
        installation.validated_at = None
        db.flush()
        raise LifecycleError(
            "installation static validation failed: " + ",".join(schema_errors)
        )
    try:
        diagnostics = plugin.validate_connection(
            config=revision.config_json or {},
            secrets=secrets or {},
        )
    except Exception:
        _record_validation_failure(
            db,
            installation,
            revision,
            codes=("connection_validation_failed",),
        )
        raise LifecycleError("connection validation failed") from None
    failures = [d for d in diagnostics if not d.ok]
    if failures:
        codes = tuple(diagnostic.code for diagnostic in failures)
        _record_validation_failure(db, installation, revision, codes=codes)
        raise LifecycleError(
            "connection validation failed: " + ",".join(codes)
        ) from None

    installation.state = "enabled"
    installation.state_reason = None
    installation.enabled_at = datetime.now(UTC)
    installation.validated_at = datetime.now(UTC)
    installation.updated_by = actor
    revision.validation_status = "valid"
    revision.validation_errors = None
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

    ## What quarantine does and does not do

    It stops this installation consuming the outbound queue —
    `admission.admit_installation` refuses every dispatch for it, so
    `dispatch.prepare` never claims another of its deliveries — and it stops it
    answering ingress (`ingress.HANDSHAKE_INSTALLATION_STATES`) and backing an
    activatable binding (`activation`).

    It DESTROYS NOTHING. Queued deliveries stay queued, leases run out normally,
    `next_attempt_at` is untouched, the configuration history is intact, and the
    inbox keeps every receipt. That is deliberate: an installation is
    quarantined precisely when someone is unsure what it did, and the moment
    containment starts deleting evidence it stops being containment.

    Scope is the INSTALLATION, and the reasoning for choosing that over a
    binding or a capability is in `admission`'s module docstring — it belongs
    next to the check that enforces it, not next to the setter.

    The exit is :func:`release_quarantine`, which is a separate, separately
    audited decision.
    """
    previous_state = installation.state
    installation.state = "quarantined"
    installation.state_reason = reason
    installation.updated_by = actor
    db.flush()
    _installation_audit(
        db,
        installation,
        action=QUARANTINE_AUDIT_ACTIONS[0],
        actor=actor,
        reason=reason,
        previous_state=previous_state,
    )
    return installation


def release_quarantine(
    db: Any,
    installation: ConnectorInstallation,
    *,
    reason: str,
    actor: str | None = None,
) -> ConnectorInstallation:
    """The explicit exit from quarantine. Lands in `disabled`, never `enabled`.

    A quarantine with no stated way out is not a state, it is a dead end — the
    installation sits there until someone edits a row by hand, which is both
    unaudited and exactly the operation you least want performed by hand on a
    connector nobody trusts. So there is one function, it requires a reason like
    every other repair command, and it writes its own audit event.

    It stops at `disabled` on purpose. "We have finished investigating" and "we
    trust this to talk to a provider again" are two decisions, and collapsing
    them would let a release skip `enable`'s live connection check — so an
    installation could come out of quarantine and start dispatching on
    credentials nobody re-verified. The operator's next step is `enable`, which
    proves the connection before anything is sent.

    Refuses anything not actually quarantined, rather than silently disabling
    it: a release aimed at the wrong installation should fail loudly, not turn
    a healthy integration off.
    """
    if installation.state != "quarantined":
        raise LifecycleError(
            f"installation {installation.name!r} is {installation.state!r}, not "
            "quarantined; there is nothing to release"
        )
    installation.state = "disabled"
    installation.state_reason = reason
    installation.updated_by = actor
    db.flush()
    _installation_audit(
        db,
        installation,
        action=QUARANTINE_AUDIT_ACTIONS[1],
        actor=actor,
        reason=reason,
        previous_state="quarantined",
    )
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
