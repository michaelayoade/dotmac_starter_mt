# dotmac-billing

`dotmac-billing` is the single reusable owner of operational receivables. It
accepts immutable rated obligations and independently confirmed settlements,
owns invoice and credit-note meaning, posts immutable allocation/correction
effects, and derives separate per-currency receivable, available-credit and
prepaid-funding positions.

It is not a general ledger, PSP connector, subscription scheduler, collections
policy engine, renderer, file store, numbering engine or product consequence
owner. Applications install their own selected plane and synchronize through
the frozen V1 contracts; they never share `mod_billing` rows. Every public V1
command and fact is an immutable closed type, including its nested money,
party, address, payment, line, tax/FX and accounting-effect values. Boundary
canaries reject `Any`, `object` and unshaped dictionaries.

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

The first release is `0.1.0a1` and requires `dotmac-kernel>=0.1.0a75` plus
`alembic>=1.13` for its published migration/linking surface. Its wheel contains
`py.typed`, the manifest and the complete `bi` migration lineage.
