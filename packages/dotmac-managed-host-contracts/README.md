# dotmac-managed-host-contracts

Immutable, provider-neutral contracts for a constrained managed-host agent. The
wheel publishes three independently bindable capability families:

- `host.deployment-bundle.lifecycle.v1`
- `host.backup-restore.lifecycle.v1`
- `host.health-probe.lifecycle.v1`

Every family exposes Integration SPI 1.2's `plan`, `apply`, `observe`, and
`cancel` operations with exact canonical Draft 2020-12 JSON Schema bytes. The
contract code is unversioned; `schema_version` produces the public `.v1` id
declared by the Product Manifest.

The deployment surface is closed. A request selects one versioned bundle and
one typed operation from a fixed lifecycle: install, repair, suspend, resume,
upgrade, rollback or decommission. Upgrade/update semantics exist only as the
`upgrade` operation within that bundle lifecycle. No request, configuration,
endpoint, result or public API carries arbitrary shell text, argv, an SSH
command, a startup script, executable bytes or a generic file-and-run shape.

Backup/restore and health probes are separate bindings because their
credentials, schedules, approvals and failure boundaries differ from bundle
deployment. They return public object/version, restore-validation and health
facts. The agent endpoint, mutual identity, held credential reference, bundle
catalogue and backup storage are typed installation `config_fields`; signed
operation inputs never repeat them.

This package contains no agent, connector, provider branch, network client,
persistence, migration, scheduler, retry engine or secret material.

## Published data

- `PRODUCT_MANIFEST` — owner `dotmac-managed-host` and three public capability
  ids.
- `CAPABILITY_CONTRACTS` — immutable, canonically ordered snapshots.
- `CAPABILITY_SCHEMAS` — exact self-contained schema documents.
- `CAPABILITY_COMPOSITIONS` — empty; suite composition belongs to its owner.
- `COMPOSITION_DEPENDENCY_CONTRACTS` and
  `COMPOSITION_DEPENDENCY_SCHEMAS` — empty for this owner catalogue.
- `DEPLOYMENT_BUNDLE_LIFECYCLE`, `BACKUP_RESTORE_LIFECYCLE`, and
  `HEALTH_PROBE_LIFECYCLE` — named lifecycle snapshots.

See `COMPATIBILITY.md` for fixed meanings and `EXTRACTION.toml` for the
product-first inventory ruling.
