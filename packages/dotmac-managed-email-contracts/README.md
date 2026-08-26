# dotmac-managed-email-contracts

Immutable, provider-neutral contracts for managed email. The wheel describes
one independently bindable capability family: `email.lifecycle.v1`, covering
application, domain, mailbox, alias, quota, delivery, app-password and DKIM
desired state.

Every family exposes Integration SPI 1.2's `plan`, `apply`, `observe`, and
`cancel` operations with exact canonical JSON Schema bytes. The contract code
inside each `CapabilityContractSnapshot` is unversioned; its `schema_version`
produces the public capability id declared by the Product Manifest.

The lifecycle family is intentionally coherent. One binding owns a managed
email application's application/domain/mailbox/alias/quota/delivery,
app-password and DKIM resources, so a deployment cannot accidentally create a
mailbox through one provider and mutate its quota through another. Resource
kinds are typed schema data, never engine operations or separately selectable
capabilities.

Backup/restore and update belong to the separate managed-host owner because they
run through a constrained host-agent boundary and have different failure,
approval and credential scopes. A later suite composition may require those
host capabilities for a managed email offer; this catalogue does not redeclare
them.

This package contains no connector, provider branch, network client,
persistence, migration, retry engine or secret material. Only the provider
administrative credential is held installation configuration. Mailbox and app
password material stays with the owning user/product; lifecycle operations can
disable or revoke it but never accept, generate or return it. DKIM private
material remains provider-owned while the public DNS record is evidence.

## Published data

- `PRODUCT_MANIFEST` — owner `dotmac-managed-email` and the versioned public
  capability id.
- `CAPABILITY_CONTRACTS` — immutable, canonically ordered snapshots.
- `CAPABILITY_SCHEMAS` — exact self-contained Draft 2020-12 documents.
- `CAPABILITY_COMPOSITIONS` — empty; cross-owner evidence flow belongs to the
  managed-suite catalogue.
- `COMPOSITION_DEPENDENCY_CONTRACTS` and
  `COMPOSITION_DEPENDENCY_SCHEMAS` — empty for this owner catalogue.
- `EMAIL_LIFECYCLE` — the named lifecycle snapshot.

See `COMPATIBILITY.md` for supported identifiers and `EXTRACTION.toml` for the
product-first inventory ruling.
