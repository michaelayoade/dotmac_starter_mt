# dotmac-party

Optional tenant module for Party **business context**. The kernel remains the
identity owner: `Party` answers who exists, credentials prove that Party, and
RBAC grants answer what an authenticated Party may do. This package answers:

- in which concurrent, temporal capacity does the Party relate to the product;
- how do two exact capacities relate to one another;
- in which organization context may a Person Party act;
- how can the Party be reached, with verification and consent kept separate;
- which legacy/external identifiers were observed during migration.

It deliberately owns no `customers`, `contacts`, `parties`, accounts,
credentials, sessions, permission grants, leads, subscribers, invoices,
subscriptions, tickets, conversations, or delivery transport.

## Archetype

```text
kernel Party ──< mod_party.PartyRole ──< Account (product-owned)
                         │
                         └── role-to-role PartyRelationship

Person Party ── PartyMembership ── Organization Party
Party ──< PartyContactPoint
Party ──< PartyExternalReference
```

`PartyRelationship` uses PartyRole endpoints rather than bare Party endpoints.
That resolves ADR-0019's recorded ambiguity: “account manager for Acme” can now
name Acme-as-customer independently of Acme-as-vendor.

## Open vocabularies

Role types, relationship types, membership types, and contact channels are
declared by an adopting product through `PartyVocabularyRegistry`. Their
database columns are plain strings and carry no enum/CHECK value list. The
registry gives each member one owner and refuses unknown or duplicate codes.
Relationship declarations also name their permitted subject/object role types,
so `billing_contact_for` cannot silently reverse its direction or connect the
wrong capacities. Membership declarations bound the keys allowed in
`access_scope`; an undeclared scope is refused rather than becoming an implicit
permission vocabulary.

Lifecycle states are different: their semantics belong to this module, so they
are closed `StrEnum` contracts and database CHECK constraints. Role,
relationship and membership transitions, contact active/primary selection,
verification and consent all go through the module service.

The current reference assembly still treats kernel `Party.email` as its single
email/login authority; this uncomposed candidate changes none of those readers
or writers. After an adopter's explicit cutover, shared reachability addresses
and phones belong in `PartyContactPoint` and cannot be projected into the
unique login locator. They are deliberately not unique across Parties.

## Transaction and tenancy contract

Every service requires an explicit `TenantScope`, mutates and flushes inside
the caller's transaction, and never commits or rolls back. All five tables have
`tenant_id UUID NOT NULL`, tenant-composite foreign keys and uniqueness, and
ENABLEd + FORCEd RLS in `pt_0001_party_context`.

The package is `audit-complete`, not adopted or reuse-proven. It must not be
released or composed into Sub until the dossier's backfill, shadow, reader,
writer-retirement, and lineage gates are met.

## Integration checklist

- Pin `dotmac-party` in the consuming assembly and register `dotmac_party.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`, `PARTY_PERSON_CATALOG_V1.name`.
- Add `dotmac_party.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: customer and contact are concurrent roles not identity tables](../../tests/unit/test_party_service.py#L160). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_party_service.py::test_customer_and_contact_are_concurrent_roles_not_identity_tables -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
