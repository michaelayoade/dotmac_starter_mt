# dotmac-billing

`dotmac-billing` is the single reusable owner of operational receivables. It
accepts immutable rated obligations and independently confirmed settlements,
owns invoice and credit-note meaning, posts immutable allocation/correction
effects, and derives separate per-currency receivable, available-credit and
prepaid-funding positions.

Subscriptions alone owns `RatedObligationOutputV1`; Billing neither defines nor
names that producer contract. Billing owns only its distinct
`AcceptRatedObligationV1` command, and an assembly maps between them.

It is not a general ledger, PSP connector, subscription scheduler, collections
policy engine, renderer, file store, numbering engine or product consequence
owner. Applications install their own selected plane and synchronize through
the frozen V1 contracts; they never share `mod_billing` rows. Every public V1
command and fact is an immutable closed type. Every amount uses the
kernel-owned `dotmac_kernel.money.Money`; Billing does not publish a second
money type. `ReceivablePositionV1` is the account/currency aggregate with three
separate financial lanes. `ReceivableExposureV1` is the invoice-grained fact
with subject/service, service-period and due-date provenance that an assembly
may translate for Collections. Billing owns financial state at both grains;
reversal, refund and chargeback remain immutable movements that may reopen a
fact, not steady states. Its nested party, address, payment, line, tax/FX and
accounting-effect values are typed as well. Boundary
canaries reject `Any`, `object` and unshaped dictionaries.

Billing records one official artifact structurally per fact version and media
type. Physical repair is a separate typed command that names the exact current
relation, preserves the semantic digest, appends the replacement opaque file
relation and retains supersession evidence. Billing never stores or dereferences
file bytes. Artifact creation locks the invoice lifecycle and refuses a new
medium after cancellation; a pre-existing idempotent command still replays its
historical relation.

The database enforces Billing's internal relationship graph on both planes:
obligations and settlements belong to their account, document lines belong to
the same obligation/document pair, posting and allocation effects cannot cross
accounts, financial facts point to their source posting group, and artifact
repairs remain inside one semantic fact and media type. A rated-obligation
correction may append one same-account successor only before the obligation is
documented; after issuance, correction is a credit note.

The tenant plane requires `TenantScope`, `tenant_id NOT NULL` and forced RLS.
The platform plane requires `PlatformScope`, has no tenant column or RLS, is
revoked from `app_user`, and is reachable by `platform_api`.

Adopters relate their own subjects to Billing accounts with the public
`link_tenant_billing_account` or `link_platform_billing_account` migration
helper. The plane is named by the function, never selected by a boolean. The
tenant helper emits a composite Billing-account reference plus forced RLS; the
platform helper emits no tenant column or RLS and revokes all table and column
privileges from `app_user`.

Services accept a caller-owned SQLAlchemy session, mutate and flush only.
`dotmac_kernel.db` remains transaction authority.

Every Billing event code passed to the kernel outbox is declared on the module
manifest, including rated-obligation acceptance, document facts and artifact
relations, settlement acceptance, accounting facts, aggregate positions and
invoice-grained exposures. An architecture canary derives the emitted set from
the service and requires an exact match, so a new routing code cannot bypass
the manifest-owned vocabulary.

The first release candidate is `0.1.0a1` and requires
`dotmac-kernel>=0.1.0a89` plus
`alembic>=1.13` for its published migration/linking surface. Its wheel contains
`py.typed`, the manifest and the complete `bi` migration lineage.
