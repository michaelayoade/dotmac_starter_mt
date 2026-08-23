# Domain-service sources

**As of:** 2026-08-15
**Starter:** `e6ba2022f3d7` (branch `docs/adr-0030-cloud-commerce-composition`, clean)
**Sub:** `27c76aaeebb7`
**ERP:** `0f4b1698ddbf` (revision-pinned `git grep`/`git show`; worktree had local changes)
**CRM:** `c64b5aa0f790` (revision-pinned reads)
**Vendor CP:** `f9ca367c1161` — the brief pinned `89848017d6b8`; that commit exists
but is **not an ancestor of the current HEAD**, so every Vendor CP citation below
is at `f9ca367c1161` and is stated as such.
**Integrator:** `d014116e63ad`
**Also swept (no matches, see § 2):** `dotmac_workspace` `f63e6d4`,
`dotmac_academy_app` `40423a0`, `dotmac_governance` `e494dca`,
`dotmac-integration-client` `4714d94`, `dotmac-academy` `71b87b2`
**Decision:** [ADR-0030](../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
§ 1 names `dotmac-domains` the sole owner of the Dotmac domain-service lifecycle;
§ 5c rules it, and build-order step 10 sequences it after Subscriptions; § 6 carries the ADR-0017 exception that
authorizes the package; § 7 makes it Cloud-only at first.

This audit settles two things and nothing else: whether the
`greenfield-after-inventory` ruling in
[`cloud-commerce-owner-sources.md`](cloud-commerce-owner-sources.md) § 4
survives a wider and more specific search, and where the boundary runs between
the kernel's existing `TenantDomain` routing catalogue and a registered domain
sold as a product.

## 1. Verdict

`dotmac-domains` is **greenfield-after-inventory**, confirmed.

### Correction 2026-08-19 — release is not a ninth registrar operation

The first implementation pass exposed an internal contradiction in this dossier:
§ 7 described a generic guarded release command, while the accepted
`domains.registrar.v1` family deliberately contains exactly eight operations and
no release/delete delivery operation. A command that changed local state and
emitted a ninth operation would have no conforming connector and would stick.

For V1, intentional relinquishment is only a guarded transfer-out through
`transfer(direction=approve_out)`. Provider deletion is an immutable observation
that can confirm `released` only after expiry or redemption. `release` and
`allow_lapse` remain typed consequence requests that Domains refuses; approving
lapse is deferred until Domains owns an explicit renewal-disposition contract
and cancellation/scheduling semantics. This correction supersedes the generic
release-command wording below wherever the two disagree.

### Correction 2026-08-19 — registrar delivery is self-contained

The first implementation also exposed a cross-application boundary error in
the original registration shape: `contact_set_ref` and `nameserver_set_ref`
could only identify rows in Domains' own database. The independently deployed
Integrator is forbidden to read those rows, so a conforming connector could not
turn either reference into a provider request.

Registration and contact delivery now carry closed, versioned contact snapshots
(`postal address -> contact role -> contact set`) with source authority,
source reference and source version. Domains computes the content digest from
that canonical snapshot; callers cannot supply it. Registration additionally
carries the actual ordered nameservers. No provider-bound registrar command may
carry a Domains intent/service row reference. Transfer-in itself is deferred
from V1 until the shared per-operation secret channel exists. Domains therefore
exposes no auth-code literal or arbitrary secret reference in local evidence or
provider delivery; `approve_out` and `cancel` remain the only V1 directions.

The same independence rule applies to desired nameserver, DNS-zone and
DNS-recordset delivery: each event is the exact typed provider request with the
actual desired values, not a generic envelope containing an `intent_id` or
`domain_service_id`.

No Fulfillment `participant_code` is declared in this slice. Fulfillment has
not yet published the participant contract or capability-id registry that would
give that value meaning, and a zero-consumer local vocabulary would violate the
same composition rules this correction is enforcing.

This necessary snapshot includes contact personal data. Domains currently keeps
it in immutable command/intent evidence and its kernel outbox, while
Integrator's retention sweep redacts inbound `InboxReceipt` content but not
outbound `DeliveryAttempt.payload_json`. Cloud adoption is therefore blocked
until an approved retention/redaction design covers Domains' local evidence and
outbox plus completed Integrator deliveries. An owner API or cross-database
lookup is not an acceptable workaround.

### Correction 2026-08-19 — observations and terminal provider outcomes

The original scope remains authoritative for DNS, but the first implementation
did not persist DNS observations. V1 now stores immutable, binding-scoped actual
nameservers and canonical recordsets, refuses future facts and evidence-key
content conflicts, and derives DNS drift only from the active binding. Renewal
must name the latest recent registrar POLL observation from that same binding;
the safety window authorizes re-verification, not a commercial grace policy.

Terminal registration failure moves the aggregate to `registration_failed` so
a fresh aggregate may safely retry the name; terminal transfer-out failure
returns to `active`; terminal cancel failure leaves the transfer pending. Each
writes typed failure/attention evidence. Service identity is immutable, desired
intent advances `row_version`, and an active hold is identified by hold code,
source owner and source reference. The earlier transfer-in scope and pilot row
are superseded until the shared operation-secret channel is accepted.

No qualifying production implementation of domain registration, transfer,
renewal, expiry/redemption, contact/nameserver desired state, or DNS-record
intent exists anywhere in the inspected fleet. There is no registrar client, no
EPP or RDAP code, no availability check, no auth-code handling, no transfer or
registry lock, and no test to preserve. The version-one contract must therefore
be derived from the lifecycle and failure model in § 6, not from a copied
provider API — and, per ADR-0006's extraction rule, the absence of a source is
not a licence to invent kernel behaviour either.

Two negative findings are worth stating as facts rather than as an absence:

- The fleet has **no DNS client at all**. `dnspython` appears in six
  `poetry.lock` files (`dotmac_sub:poetry.lock:1090`, and the equivalents in
  ERP, Vendor CP, CRM, Integrator and the starter) purely as a transitive
  dependency of `email-validator` (`dotmac_sub:poetry.lock:1197-1209`). Nothing
  imports it.
- The starter's ADR-0003 DNS/TLS/ingress design exists as **three provider seam
  names and zero implementations** — see § 4.

### 1.1 What was searched, and where

Every sweep was `git grep -i` at the pinned revision of each repository, over
tracked content, across all six brief repositories plus the five additional
fleet repositories listed in the header.

| Sweep | Terms | Result |
|---|---|---|
| Registry protocol | `registrar`, `EPP`, `epp_`, `whois`, `rdap` | Sub/ERP/CRM false positives only (§ 2); zero in Vendor CP, Integrator, starter code |
| DNS | `nameserver`, `name_server`, `dns_zone`, `zone_file`, `glue_record`, `dnssec`, `ds_record`, `cname`, `soa`, `punycode`, `idna`, `dnspython`, `bind9`, `route53` | one FreeRADIUS comment; `dnspython` in lockfiles only |
| Domain lifecycle | `domain_transfer`, `transfer_domain`, `domain_renewal`, `renew_domain`, `domain_availability`, `domain_registration`, `register_domain`, `domain_name`, `domain_order`, `domain_service`, `domain_product`, `tld`, `sld`, `second_level_domain` | Sub SOT-registry DDD helpers and one ERP settings-export key (§ 2) |
| Transfer/hold semantics | `auth_code`, `authcode`, `transfer_lock`, `registry_lock`, `clientHold`, `serverHold`, `pendingDelete`, `autoRenewPeriod`, `redemption` | password-reset "capability redemption" and one Mono OAuth `auth_code` (§ 2) |
| Provider brands | `namecheap`, `godaddy`, `resellerclub`, `opensrs`, `enom`, `icann`, `cpanel`, `directadmin`, `plesk`, `virtualmin`, `cyberpanel`, `whmcs`, `blesta` | CRM's `whmcs` external-system label (§ 2, § 5); ADR/inventory prose in the starter |
| Ownership proof / TLS | `letsencrypt`, `certbot`, `acme`, `dns-01`, `http-01`, `tls_cert`, `certificate_authority`, `ingress_provider`, `custom_domain`, `tenant_domain`, `domain_verification`, `verify_domain`, `domain_ownership` | the starter's `TenantDomain` read model and profile seam strings only (§ 4) |

## 2. What the search found instead

None of the following is a source. Each is recorded so a later reader does not
re-litigate the same hit.

- **`redemption` is capability-token redemption, not domain redemption.**
  `dotmac_sub:app/services/auth_flow.py:1857`,
  `app/services/web_auth.py:562`, `app/api/auth_flow.py:463`,
  `app/models/access_invitation.py:7` and six lines in
  `app/services/sot_registry/domains/authorization_control_plane.py` all describe
  redeeming a password-reset capability. Registrar redemption-grace-period is a
  different concept that shares one English word.
- **`nameserver` is a FreeRADIUS comment.**
  `dotmac_sub:config/freeradius/radiusd.conf:249` —
  `"request to the nameserver. Enabling hostname_lookups will also"`. Vendor
  configuration boilerplate.
- **`domain_*` in Sub is DDD vocabulary.**
  `dotmac_sub:app/services/sot_registry/registry.py:164-180` defines
  `domain_order()`, `domain_relationship(domain_name)` and
  `services_for_domain(domain_name)` over Sub's source-of-truth registry.
  `domain_name` there is a business-domain label such as `financial` — never a
  hostname.
- **ERP's `domain_name`** at `app/services/settings_api.py:439` is a key in a
  settings-export payload shape, naming a settings domain.
- **ERP's `auth_code`** at `app/api/finance/banking.py:854` is a Mono bank-widget
  OAuth code.
- **`registrar` in the mobile apps** — `dotmac_sub:field_mobile/lib/core/push/push_registrar.dart`
  and `dotmac_crm:mobile/lib/core/push/push_registrar.dart` are FCM push-token
  registrars.
- **Vendor CP's approvals/licensing hits** are `approval`/`revocation` word
  overlap; `git grep -E 'registrar|EPP|whois|rdap|nameserver|redemption|tld'`
  over `src/**` and `tests/**` at `f9ca367c1161` returns **zero** lines.
- **Integrator returns zero matches in tracked source** at `d014116e63ad` for
  every sweep in § 1.1.
- **`hosting` does not exist in Sub.** `git grep -il '\bhosting\b|web_hosting|shared_hosting|vps'`
  over `dotmac_sub:app/**` returns nothing, and `ServiceType`
  (`dotmac_sub:app/models/catalog.py:27-29`) is `residential | business` — an
  ISP access vocabulary with no domain or hosting member.

## 3. The one adjacent capability worth naming: Integrator

`dotmac-integration` is not a source for domain lifecycle, but it is the
declared home of every registrar wire call, and its contract already exists.
`packages/dotmac-integration/src/dotmac_integration/spi.py` defines
`ConnectorManifest` with a stable `connector_key`, an `spi_range` and declared
`capabilities` keyed `domain.noun.vN` — a *contract* name whose version is part
of its identity. The module deliberately cannot enumerate connectors and forbids
`if provider == ...` (its module docstring cites ADR-0024 § 7). Capability
declarations are checked at discovery, at startup **and** at activation.

`packages/dotmac-integration/src/dotmac_integration/operations.py` derives health
rather than storing it, and its six signals — `in_flight_expired`,
`retryable_overdue`, `dead_letter`, `reconciliation_required`,
`receipts_unprocessed`, `checkpoints_stale` — are the existing fleet answer to
"is anything silently stuck?". Invariant 3 in § 7 (a paid renewal that failed at
the registrar must not silently resolve) needs exactly that shape, and must not
grow a second one inside Domains.

`packages/dotmac-kernel/src/dotmac_kernel/providers/provisioning.py` is the only
provider Protocol the kernel holds. Its `plan/apply/observe/cancel` cycle,
`operation_id`-as-idempotency-key rule, PARTIAL result and
retryable-versus-terminal error classification are a usable **shape reference**
for a registrar capability contract. It is not a domain model and must not be
subclassed into one.

## 4. The `TenantDomain` boundary, settled

These are two different objects that happen to hold a hostname string. The
distinction is not stylistic — it is enforced by a Postgres grant.

**`TenantDomain` is platform routing state.**
`packages/dotmac-kernel/src/dotmac_kernel/models.py:103-122` — table
`tenant_domains`, columns `id`, `tenant_id` (FK `tenants.id` ON DELETE CASCADE),
`domain String(253)`, `verified_at`, with a global unique on `domain`. Its
docstring is one line: *"Custom-domain mapping. Subdomain on
platform_root_domain works without a row here."*
`TenantResolverMiddleware._resolve()` reads it first, accepting an exact match
only where `verified_at IS NOT NULL`
(`docs/ARCHITECTURE.md:1419`, `packages/dotmac-kernel/src/dotmac_kernel/middleware/tenant.py`).

**It lives in the platform plane and `app_user` cannot write it.**
`packages/dotmac-kernel/src/dotmac_kernel/migrations/versions/20260504_0001_initial_tenant_schema.py:430`
grants `SELECT ON tenants, tenant_domains TO app_user, platform_api`, and `:437`
grants `INSERT, UPDATE, DELETE ON tenants, tenant_domains TO platform_api` only.
`tests/test_rls_catalog.py:42` classifies `tenant_domains` in
`_PLATFORM_READABLE` — a platform catalogue table under no RLS, "readable but
never writable by `app_user`".

**How they differ.**

| | `TenantDomain` (kernel) | `dotmac-domains` (proposed) |
|---|---|---|
| Question answered | which tenant does this inbound `Host:` header route to | what is the state of a registered name Dotmac sells and renews on a customer's behalf |
| Plane | platform catalogue; `platform_api` is the only writer | tenant (ADR-0030 § 7), `tenant_id NOT NULL`, FORCEd RLS |
| External authority | DNS, a CA and an ingress provider | a registry, via a registrar connector |
| Proof of control | a random TXT challenge the platform itself sets and checks | a registry-issued transfer auth code and registry-of-record status |
| Lifecycle | `requested → pending_dns → verified → pending_tls → active` (design intent, `docs/ARCHITECTURE.md:1440-1447`) | registration/transfer/renewal/expiry/redemption/release |
| Commercial | none; ADR-0003's WS10 says "billing and subscriptions remain unrelated unless the selected product commercially prices that capability" | the sold thing; priced by `dotmac-subscriptions` |
| Built today | read model only — "There is currently no mutation API for `TenantDomain`, DNS ownership verification, ingress reconciler, certificate lifecycle, or dynamic production `TRUSTED_HOSTS` policy" (`docs/ARCHITECTURE.md:293-296`) | nothing |

**Neither can become a second writer of the other, and the proof is not a
convention.** A tenant-plane module runs on `DATABASE_URL` as `app_user`, which
holds `SELECT` and nothing else on `tenant_domains`; a `dotmac-domains` write to
the routing catalogue fails at the database. In the other direction,
`dotmac-domains` owns tables in its own `mod_*` schema
(`packages/dotmac-kernel/src/dotmac_kernel/namespaces.py` D1: `public` is
reserved for the kernel and the one host assembly, and an installable module may
never write it), so the platform domain reconciler ADR-0003 plans cannot reach
into them either. Add one test asserting the grant, and one asserting the
namespace — do not restate the rule in prose only.

The same customer name may legitimately exist on both sides: Dotmac Cloud
registers `example.com` for a customer *and* that customer points it at a Dotmac
tenant. Those are two facts with two owners and two proofs. `dotmac-domains`
must not auto-create a `TenantDomain` row, and `TenantDomain.verified_at` must
never be inferred from registrar data.

**Reuse assessment of the ADR-0003 DNS/ingress/TLS shape.**

- *Not reusable, because it does not exist.* The three seams are three `str`
  fields on `DeploymentProfileSpec`
  (`packages/dotmac-kernel/src/dotmac_kernel/profiles.py:46-48`:
  `ingress_provider`, `dns_verification_provider`, `tls_provider`), validated
  only by name membership in `available_providers`
  (`profiles.py:validate`). `tests/unit/test_profiles.py:43-45` passes the
  strings `"nginx_static"`, `"manual_txt"`, `"customer_pki"`. There is no
  Protocol, no implementation and no reconciler anywhere in the repo.
- *Reusable as requirements, and must be honoured.* ADR-0003's rules —
  lower-case/IDNA normalization, port and trailing-dot stripping, reserved-name
  rejection, "a random DNS TXT challenge, not CNAME presence alone", never issue
  for an arbitrary first-request `Host`, an idempotent reconciler comparing
  desired to observed with drift/retry/repair — are the correct discipline and
  Domains should follow the normalization and reconciler rules for its own
  records.
- *Must stay where it is.* Custom-domain ownership proof, certificate issuance
  and ingress binding remain WS10 of
  `docs/superpowers/plans/2026-07-18-deployment-profiles-commercial-platform.md`
  (lines 389-420), executed by "a platform-authorized reconciler" that performs
  "all `TenantDomain`, DNS, certificate, and ingress writes; `app_user` never
  receives platform-table mutation grants". Absorbing that into `dotmac-domains`
  would make a tenant-plane commerce module the platform's routing authority.
  If DNS-record *intent* is in scope for a registered domain (§ 6), it is intent
  about the customer's zone at their registrar, expressed as desired state and
  reconciled through a connector — not the platform's ingress.

## 5. Do not port

There is no code to port. These are the anti-patterns the sweep surfaced in
neighbouring code, recorded so version one refuses them by construction.

- **A provider name in a schema column.**
  `dotmac_crm:app/models/subscriber.py:210` —
  `external_system: Mapped[str | None] = mapped_column(String(60))  # splynx, ucrm, whmcs`
  — with the value branched on at `app/web/admin/subscribers.py:70-71`
  (`if value == "whmcs": return "WHMCS"`) and enumerated in
  `templates/admin/subscribers/form.html:71`. No registrar, panel, PSP or
  billing-platform name may appear in a `dotmac-domains` column, enum, setting,
  capability id or template. A provider is an Integrator connector binding.
- **A closed lifecycle enum.** Registry status vocabularies (`clientHold`,
  `pendingDelete`, `redemptionPeriod`) are *observations from a registry*, not
  Dotmac states, and TLD policy varies. Both the observation kind and the
  Dotmac consequence must be open registered strings under ADR-0008, as
  ADR-0026 § 4 does for `policy_code`: a new registry status must not require a
  release. Contrast `dotmac_sub:app/models/catalog.py:27` `ServiceType` — a
  two-member enum in a database column that a product cannot extend.
- **Product-specific vocabulary.** Nothing named `subscriber`, `ISP`, `RADIUS`,
  `OLT`, `plan`, `organization_id` or `blesta_client_id`. Nothing named for a
  particular TLD's rules.
- **Registrar status as Dotmac status.** ADR-0030 § 1 is explicit: "A provider
  callback never assigns a Dotmac service status directly." The registry's
  status is an observation column on a mirror row; the Dotmac state is a
  separate column written only by the lifecycle engine.
- **Service-level `commit()` and framework exceptions.** ADR-0026 § 7 excludes
  both from `dotmac-approvals` for the same reason; hard rule 8 gives
  `dotmac_kernel.db` transaction authority. Services mutate and flush.
- **Silent fallback.** A missing registry observation, an unparseable expiry
  date, or an unconfigured connector fails closed. A domain whose registrar
  state is unknown is *unknown*, never assumed active and never assumed expired.
- **A stored `health` column.** `dotmac_integration/operations.py` already
  argues this: derived health "is slower and it cannot lie". Domains derives
  drift at read time from the observation mirror plus desired state.
- **Reserving before the effect.** `dotmac_kernel/idempotency.py`'s docstring
  records ERP's `202 "Request in progress"` placeholder as the defect class; a
  domain command must not write a reservation row before the registrar call.

## 6. Known defects/deltas

There is no source implementation, so these are the design hazards this owner
inherits from the problem rather than from a codebase. Each must be answered by
the version-one contract, and each is numbered so a reviewer can check it off.

1. **Two clocks, and the fleet has no timer.** Renewal wake-up needs
   generation-safe due work, which ADR-0030 § 4 assigns to
   `dotmac_kernel.durable_timers`. That module **does not exist** — the kernel
   package has no `durable_timers.py`, and the candidate source is still
   `dotmac_sub:app/services/runtime_durable_timers.py` (325 L) plus
   `app/models/durable_timer.py` (114 L). Domains must not ship a private
   scheduler ledger; if the timer owner is not ready, the renewal-due wake-up
   slice waits.
2. **Naive datetimes against timezone-aware columns.** The recorded fleet defect
   (`numbering-sources.md` § "ERP source" defect 3,
   `last_used_at = datetime.now()`) is the same class that corrupts a registry
   expiry comparison. Every date on a domain — registry expiry, commercial
   renewal date, redemption end — is timezone-aware and the registry's date is
   stored exactly as received.
3. **Registry expiry versus commercial renewal date.** These are two facts with
   two owners (invariant 4 in § 7). The hazard is a reconciler that "repairs"
   one from the other. Model them as separate immutable observations and derive
   disagreement as drift, never as a correction.
4. **A callback that arrives before the command commits.** Registrar callbacks
   are out-of-order by nature. An observation collector that requires a matching
   local command row will drop the first callback for a slow registration.
   Observations are deduplicated on the provider's own event identity and stand
   alone; correlation is a later join.
5. **A lost callback is the normal case, not the exception.** The renewal path
   must be repairable from a poll of registry state alone. No terminal Dotmac
   state may be reachable only via a callback.
6. **Money must not enter the lifecycle.** ADR-0026 § 7a's reasoning applies
   directly: an approval module that knew about currency "would be one schema
   change away from owning pricing". `dotmac-domains` knows a renewal *term*
   and a renewal *outcome*; price and saleability are `dotmac-subscriptions`.
   If any monetary value is unavoidable it uses `dotmac_kernel.money` and is
   never a `float`.
7. **The greenfield hazard itself.** With no source, there are no parity tests,
   so § 8's fresh proofs are the *only* evidence this owner will ever have.
   A thin test suite here is not a smaller risk than elsewhere — it is the whole
   risk.

## 7. Shared contract

Version one **owns**:

- the **domain service aggregate** — one row per (tenant, registered name),
  with one canonical writer for its lifecycle state;
- **registration request and confirmation** as separate facts, idempotent by a
  caller-supplied command key with a request fingerprint;
- **transfer-in and transfer-out** lifecycle, including the auth-code exchange
  as an opaque secret reference and the transfer-out release decision;
- **renewal request, confirmation and failure** as three distinct outcomes;
- **expiry and redemption observations** — typed, deduplicated, immutable;
- **desired contacts and nameservers**, captured as immutable, source-versioned
  snapshots and reconciled toward, never as a live foreign-row lookup or a
  write-through to a provider;
- **DNS intent where applicable** — desired records for the registered name's
  own zone, expressed as desired state with drift detection;
- the **registrar observation mirror** — a rebuildable projection of registry
  facts, with provenance (connector key, capability id, received-at, provider
  event id) on every row;
- the **drift resolver and reconciliation** — derived at read time from desired
  state plus the observation mirror, able to rebuild after missed delivery;
- **guarded transfer-out policy** — the refusal is Domains' own, and it refuses
  by default; generic release/allow-lapse requests remain refused in V1;
- typed **commands, facts, observations, outcomes and stable error classes**,
  emitted as outbox events; and
- a **provider-free fake/conformance kit** for the registrar port it consumes.

Version one does **NOT** own:

- **pricing or saleability** — `dotmac-subscriptions` owns the offer, the
  immutable price version, the term and the recurring charge occurrence;
- **collections policy** — `dotmac-collections` may send a typed delinquency
  consequence *request*; Domains locks, revalidates its own facts, applies or
  refuses, and returns a receipted outcome (ADR-0030 § 1);
- **registrar credentials or wire calls** — an Integrator connector holds the
  credential reference, the endpoint, the retry checkpoint and the wire payload
  (§ 3);
- **hosting lifecycle** — `dotmac-hosting`, a peer, never an import;
- **GL accounting** — Dotmac ERP;
- **invoicing, receivables or settlement** — `dotmac-billing`;
- **the cross-owner saga** — `dotmac-fulfillment` correlates; Domains answers
  one command at a time;
- **`TenantDomain`, DNS ownership challenges, certificates or ingress** — § 4;
  and
- **the approval decision itself** — see invariant 6 below.

### Invariants, as testable canaries

Each is stated as the test it becomes. None may be satisfied by documentation.

1. **A hosting suspension can never expire or release a domain.** No inbound
   command from any peer can reach the expiry or release transition. The canary
   drives every published inbound command against an `active` domain and asserts
   the lifecycle state is unchanged; a new inbound command that is not in the
   asserted set fails the test rather than silently joining the surface.
   Structurally this is guaranteed by ADR-0030 § 3 — Domains imports no sibling
   — but the guarantee needs the sensitivity proof ADR-0018 demands: a
   deliberately mis-wired command must make the canary fail.
2. **Payment does not mean renewal succeeded.** A settlement/coverage fact from
   the assembly may only move the domain to *renewal requested*. The canary
   feeds a paid coverage fact and asserts the registry expiry observation is
   untouched and the state is not `renewed`.
3. **A PAID renewal that then FAILS at the registrar raises an operational
   alert and does not silently resolve.** The failure is recorded as a durable
   `reconciliation_required`-class condition that a later successful poll does
   *not* clear on its own; only an explicit resolution or a confirmed renewal
   observation clears it. The canary: pay, fail at the registrar, run the
   reconciler twice, assert the condition is still open and visible.
4. **Registrar expiry date and commercial renewal date are separate facts that
   may legitimately disagree; neither overwrites the other.** Two columns, two
   writers, one derived `disagrees` predicate. The canary sets them apart,
   runs the resolver, and asserts both original values survive byte-for-byte
   and drift is reported.
5. **A provider callback never assigns Dotmac lifecycle state directly.** The
   collector's write path has no access to the lifecycle column. The canary
   delivers a callback claiming `expired` on an `active` domain and asserts the
   Dotmac state is unchanged while a typed observation exists.
6. **Intentional transfer-out is guarded.** `dotmac-approvals` is the **right
   collaborator, with two conditions.**
   - It is right because ADR-0026 § 1 scopes it to exactly the question a
     destructive transfer-out needs — "has the required set of eligible actors
     approved *this exact content* under *this exact policy revision*" — and
     its content-digest binding (§ 2) is what stops an approval of
     "transfer out `a.example` on 2026-09-01" from authorising a different name or a
     changed date. Its policy revisions are immutable and fail closed (§ 3), and
     it is dual-plane with a named tenant surface (§ 5).
   - **Condition one: Domains does not import it.** ADR-0026 § 6 delivers
     approval state as an outbox event that the consuming product turns into a
     call on its own authoritative service. So `dotmac-domains` publishes a
     transfer-out *request* and accepts a typed approval receipt
     (`policy_code`, `policy_version`, `content_digest`, decision, decided-at)
     as an input to the transfer command, verifying the digest against the
     transfer-out it is about to perform. The assembly is the wire. Domains has no
     Python dependency on `dotmac_approvals` and declares its own subject type
     on its own manifest (ADR-0026 § 4).
   - **Condition two: the refusal is Domains' own and survives approvals being
     absent.** ADR-0026's scope line makes the module optional. If the guard
     were "transfer unless approvals refused", an uninstalled approvals module
     would fail open. The canary: with no approval receipt supplied at all, the
     transfer-out command refuses.
   - The digest must also be re-verified at apply time, not only at request
     time: an approval of a stale desired state is stale, not transferable.

## 8. Kernel floor

Consumed capabilities, named now so the floor can later be proven both
sufficient and necessary. Kernel head at the revisions read is `0.1.0a63`
(`docs/MODULE_CATALOG.md`).

| Capability | Module | Why this owner needs it |
|---|---|---|
| Transaction authority | `dotmac_kernel.db` | hard rule 8; services receive a `Session`, flush, never commit |
| Idempotency | `dotmac_kernel.idempotency` (`execute_once`) | every domain command is replay-safe by `(tenant_id, scope, key)` with a fingerprint column; nothing reserved before the registrar effect (ADR-0014) |
| Inbox | `dotmac_kernel.messaging.inbox.process_once` | registrar callbacks and assembly-relayed commands arrive with a transport `command_id`; the adapter, not a second ledger |
| Outbox | `dotmac_kernel.messaging.outbox.enqueue_event` | every non-transactional effect — a registrar command, an alert, a lifecycle fact for the assembly — leaves this way |
| Audit | `dotmac_kernel.audit.write_audit_event` + declared `audit_actions` | transfer-out and manual drift resolution are operator-visible; hard rule 12 fails the write for an undeclared action |
| Planes | `dotmac_kernel.planes` / ADR-0023, ADR-0028 | declares **tenant only**; the assembly's plane selection must say so explicitly, and omission must fail |
| Namespaces | `dotmac_kernel.namespaces` (D1) | one immutable `mod_<short>` schema, one migration prefix, one branch label — allocated in the package-creation change, not here |
| Prerequisites | `dotmac_kernel.prerequisites` + `app/migration_bindings.py` | declares the database effects it needs; the assembly binds effect→revision (hard rule 14) |
| Settings | `dotmac_kernel.settings_resolver` + a declared `SettingDomain` | operator-tunable reconcile cadence, grace windows and drift thresholds; every registered spec needs a real reader (hard rule 10) |
| Secret references | `dotmac_kernel.secret_sources` / `dotmac_integration.secret_refs` | a transfer auth code and a registrar credential are *held or referenced*, never dereferenced on a request path (ADR-0009) |
| Money | `dotmac_kernel.money` | only if a monetary value proves unavoidable; the contract's intent is that it is not (§ 6 defect 6) |
| Durable timers | `dotmac_kernel.durable_timers` — **does not exist** | renewal-due and redemption-end wake-ups; ADR-0030 § 4 and build-order step 6 make this a prerequisite, and § 6 defect 1 records that the module is unbuilt |

The floor is a *consumption* claim. Each row must later be shown to be both used
(necessary) and enough (sufficient) — a row nothing calls is a false floor.

## 9. Fresh proof required

Every one of these is new. There are no ported tests and no parity ledger.

1. **Tenant RLS isolation** on live PostgreSQL: tenant A cannot read, update or
   delete tenant B's domain, observation or command rows; `tenant_id NOT NULL`,
   composite uniques and ENABLE + FORCE RLS created in the same migration
   (hard rule 11, `tests/test_rls_catalog.py`).
2. **Plane declaration is honest**: the module declares `tenant` only; an
   assembly that omits the selection fails (ADR-0028 § 2). *Not applicable:*
   platform-plane revocation, because there is no platform table. Adding one
   later re-opens this item.
3. **The `tenant_domains` boundary is grant-enforced** (§ 4): as `app_user`, a
   write to `tenant_domains` is refused by PostgreSQL; and a domain row's
   lifecycle never reads or writes `TenantDomain.verified_at`.
4. **Concurrency**: two concurrent renewal commands for the same domain produce
   one registrar request and one outcome; two concurrent transfer-outs cannot
   both pass the release guard. Under row locks against real PostgreSQL, not
   mocks — `numbering-sources.md` § "ERP source" defect 2 is the fleet's
   standing example of a suite that proved nothing because it mocked the
   database.
5. **Rollback with the consuming transaction**: a domain command whose caller's
   transaction rolls back leaves no idempotency record, no outbox row and no
   lifecycle change — the effect and the ledger commit together or not at all.
6. **Idempotent replay and fingerprint conflict**: same key + same fingerprint
   replays the original outcome without re-calling the connector; same key +
   changed fingerprint is a typed conflict, never a silent second registration.
7. **Out-of-order delivery**: an expiry observation timestamped *before* an
   already-recorded renewal confirmation does not regress the derived state;
   observation order and arrival order are independent.
8. **Lost callback**: with the registrar's callback dropped entirely, a poll of
   registry state plus the reconciler reaches the same terminal state as the
   callback path would have (§ 6 defect 5).
9. **Duplicate callback**: the same provider event id delivered twice yields one
   observation row.
10. **Drift and reconciliation**: after the observation mirror is truncated, the
    reconciler rebuilds it and the derived state from registry facts alone; a
    deliberately divergent registry expiry surfaces as drift and is not
    auto-corrected (invariant 4).
11. **The six invariants in § 7** each as a named canary, with the ADR-0018
    sensitivity proof for invariant 1: mis-wire the guard and the test must fail.
12. **Public-surface, import-independence and packaging verification**: no
    import of a sibling business module, no provider name anywhere in the public
    surface, wheel builds, manifest registers, migration lineage runs on live
    PostgreSQL.
13. **Registrar-port conformance against a provider-free fake**, including the
    partial/resumable outcome and the retryable-versus-terminal error split
    (§ 3).

## 10. Adoption and retirement

**First adopter: Dotmac Cloud, and only Dotmac Cloud.** ADR-0030 § 7 is explicit
that `dotmac-domains` is Cloud-only at first because no other application has
that lifecycle. Sub does not install it; if Sub shows a customer's portfolio it
does so through a rebuildable Cloud projection or a portal link, never by
installing the module to render navigation and never by reading Cloud's tables.
ERP, CRM, Vendor CP and the Integrator adopt none of it.

**There is no local writer to retire.** This is the defining consequence of the
greenfield ruling and it cuts both ways: the cutover carries no data migration
and no shadow comparison, because there is nothing to shadow. That removes the
usual safety net. Cloud's first domain is the first domain, so the cutover
evidence must be operational — a real registration, a real renewal, a real
failed renewal and a real refused generic release/allow-lapse consequence against
a registrar sandbox connector — rather than a diff against a prior implementation.

**How the cutover is sliced.** One command family at a time, each ending at its
own gate:

1. registration request → confirmation, with the observation mirror and the
   reconciler, against a sandbox connector;
2. renewal request → confirmation → failure, including invariants 2 and 3 —
   this slice is gated on `dotmac_kernel.durable_timers` existing (§ 6 defect 1);
3. expiry and redemption observations, and the derived drift against the
   commercial renewal date (invariant 4);
4. contacts, nameservers and DNS intent as desired state;
5. transfer-in;
6. guarded transfer-out, plus refused generic release/allow-lapse consequences,
   gated on the approval-receipt seam in invariant 6.

The command surface must stabilize before `dotmac-fulfillment` is allowed to
depend on it (ADR-0030 § 5c); a saga wired against a moving surface hardcodes
guesses about it.

**Gates before any code.** ADR-0030 § 6 removes the moratorium for this name
only. It does not relax the rest: `packages/dotmac-domains/EXTRACTION.toml` with
`source_mode = "greenfield-after-inventory"` and this audit as its evidence must
exist before behaviour code; namespace, migration prefix and branch label are
allocated in the same change that creates the stateful package, not reserved
ahead of it; and the live PostgreSQL migration and plane gates apply unchanged.

**A green test suite is not a cutover.** Under `docs/MODULE_CATALOG.md`'s own
vocabulary, this package can reach `audit-complete` on the strength of this
document and reach a passing contract, and still have zero proven consumers.
`adopted` requires Dotmac Cloud running the exact released version in
production; `reuse-proven` requires a second independent application, which
ADR-0030 § 7 says does not exist yet and may never.
