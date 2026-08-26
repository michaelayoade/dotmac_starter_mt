# Provider capability sources — connectors and conformance

**As of:** 2026-08-15

| Repository | Revision read | Note |
|---|---|---|
| `dotmac_starter_mt` | `e6ba2022f3d7` | working tree clean; branch `docs/adr-0030-cloud-commerce-composition` |
| `dotmac_sub` | `27c76aaeebb7` | clean |
| `dotmac_erp` | `0f4b1698ddbf` | 67 local paths present; every conclusion below is from `git grep`/`git show` at the pinned revision |
| `dotmac_crm` | `c64b5aa0f790` | 3 local paths present; revision-pinned reads only |
| `dotmac_vendor_control_plane` | `89848017d6b8` | the pin is a **descendant** of the checked-out `f9ca367c1161` — unmerged vendor-module adoption work, not a divergent branch; all VCP greps were run at the pin |
| `dotmac_integrator` | `d014116e63ad` | clean |

**Decision:** [ADR-0030](../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
§ 1 (registrar/panel facts are external observations), § 3 (provider names stay
in Integrator connector distributions), § 6 (connector distributions are **NOT
yet authorized** — § 5 merely sequences them at build-order step 15, after the
Integrator secret resolver and an explicit amendment naming them), § 2.6 (a
provider-free fake/conformance kit is part of the OWNING module's completion
gate), and § 8.2–8.3 (capability-ID ownership is split; capabilities are not
split per lifecycle verb). Under
[ADR-0024](../adr/0024-apps-compose-by-synchronizing-data.md) §§ 6–7,
[ADR-0023](../adr/0023-dual-plane-modules-declare-both-persistence-planes.md),
[ADR-0014](../adr/0014-at-most-once-execution-has-one-owner.md),
[ADR-0009](../adr/0009-secrets-are-held-not-dereferenced.md),
[ADR-0008](../adr/0008-manifest-declaration-registries.md),
[ADR-0018](../adr/0018-an-exemption-must-be-enforceable.md) and
[ADR-0017](../adr/0017-adoption-is-the-scarce-resource.md) with its 2026-08-12
amendment.

This audit settles what the connector layer already is, what the four cloud
provider capabilities must look like on top of it, and what would have to be
true before a single connector distribution is created.

**Companion documents this one extends rather than restates:**
[`cloud-commerce-owner-sources.md`](cloud-commerce-owner-sources.md) § 5,
[`integration-platform-sources.md`](integration-platform-sources.md),
[`external-connector-sources.md`](external-connector-sources.md),
[`payment-connector-sources.md`](payment-connector-sources.md),
[`payment-connector-extraction-dossier.md`](payment-connector-extraction-dossier.md),
[`whatsapp-connector-sources.md`](whatsapp-connector-sources.md), and the
PROPOSED contract spec
`docs/superpowers/specs/2026-08-14-payment-connector-and-settlement-contracts.md`.

---

## 1. Verdict

Five rulings, because "provider capabilities" is not one thing.

| Subject | Ruling | Mandatory source / evidence |
|---|---|---|
| **The connector framework itself** | **already built — do not restate** | `packages/dotmac-integration/` (version `0.1.0a2` in tree, `mod_intg`, product-first from Sub) plus the `dotmac_integrator` assembly. This dossier proposes no second registry, SPI, retry engine, checkpoint store or fake harness. |
| **PSP capability** | **product-first with mandatory sources; audit already complete** | Sub (Paystack + Flutterwave, capability-gated) and ERP (a second independent Paystack, plus Remita and Mono). Audited in `payment-connector-sources.md`; contract PROPOSED in the 2026-08-14 spec. ADR-0030 changes only the counterparty, not the shape. |
| **Registrar capability** | **greenfield-after-inventory** | no tracked file in any of the six repositories implements registrar I/O. § 4 lists the patterns searched and every false positive. |
| **DNS capability** | **greenfield-after-inventory** | same. The only real DNS-adjacent code in the fleet is FX-rate hosting and a CAPTCHA. |
| **Hosting-panel capability** | **greenfield-after-inventory** | same. Zero hits for every panel product name in five repositories. |

**Blesta does not exist in this fleet.** § 5.

**The gate is not open.** ADR-0030 § 6 lifts the ADR-0017 moratorium for ten
named packages — three enabling owners and seven business owners. Connector
plugin distributions are **not** among them: § 5 sequences them, § 6 does not
authorize them. ADR-0017's 2026-08-12 amendment separately keeps an inbound
receiver for payment-provider events under the moratorium. So this dossier
specifies contracts; it does not permit a package.

---

## 2. `dotmac-integration` — the base, and what it already decides

`packages/dotmac-integration/src/dotmac_integration/`, distribution
`dotmac-integration 0.1.0a2`, schema `mod_intg`, lineage prefix `ig`, manifest
`code="integration"`, `core=False`. It is the reusable engine and it is not a
Cloud business module. Fifteen modules, two migrations, and a `platform_tables`
declaration with an explicitly empty tenant `tables` tuple.

| Concern | Owner in the module | What proves it |
|---|---|---|
| Plugin discovery | `discovery.discover`, group `dotmac_integration.connectors` | `test_discovery_reads_package_metadata_and_names_no_provider` |
| Duplicate key / SPI range / undeclared capability — the three refusals | `discovery`, `spi`, `activation` | `test_integration_spi.py` (11 cases), `test_integration_bindings.py` (activation, 7 cases) |
| Installation, immutable config revision, capability binding | `models`, `lifecycle` | `test_integration_lifecycle.py` (21 cases) |
| Secret **references** and the refusal that keeps them references | `secret_refs.validate_config_revision` | `test_a_literal_secret_in_config_is_refused_at_any_depth` |
| Binding selection: enabled ≠ selected, fail closed in three directions | `selection.resolve_binding` | `test_integration_bindings.py` (10 cases) |
| Inbound receipt, binding-scoped dedupe, identity-collision refusal | `execution.receive_verified` | `test_the_same_event_id_with_different_content_is_refused`; live Postgres canary `tests/test_integration_isolation.py::test_inbox_deduplication_is_enforced_by_the_database` |
| Outbox, enqueue dedupe, worker lease, conditional settle | `execution`, `dispatch.prepare/invoke/settle` | live races in `tests/test_integration_isolation.py` (claim, settle, checkpoint) |
| Backoff, attempt cap, four outcome classes | `retry`, `policy.ExecutionPolicy` | `test_integration_execution.py` |
| At-most-once execution | **the kernel** — `idempotency.run_effect_once` adapts `dotmac_kernel.idempotency.execute_once_platform` | `test_the_module_owns_no_second_audit_ledger`, `test_integration_scopes_are_namespaced` |
| Polling cursor with an optimistic version | `execution.advance_checkpoint` | `test_only_one_session_can_advance_a_checkpoint` |
| Health (derived, never stored), audit (kernel ledger), repair | `operations` | `test_no_health_is_persisted_anywhere`, `test_every_audit_action_reaches_the_kernel_namespaced` |
| Fake-plugin conformance kit | `conformance` | shipped as library code, not test-tree code |

Three properties are load-bearing for everything in § 8 and are already
enforced, not aspirational:

1. **A connector receives no database.** `dispatch.invoke` takes no `db`
   parameter *by signature* — `test_invoke_cannot_be_given_a_database_session`
   asserts it by introspection. A plugin therefore cannot write a lifecycle
   field even if it wanted to.
2. **A raising or off-contract plugin is `RECONCILIATION_REQUIRED`, never
   retryable.** `dispatch.invoke`: *"a throw tells us nothing about whether the
   effect LANDED."* This is the single most important pre-existing decision for
   registrar and panel work, where a retried write buys a second domain-year or
   terminates an account twice.
3. **A connector classifies; it does not decide.** `retry.Outcome.error_code`
   is *"A CONNECTOR's vocabulary. Stored, never branched on"*, and the port
   deliberately dropped Sub's `error_code == "crm_customer_name_rejected"`
   branch — proven by `test_no_product_error_code_leaks_into_the_generic_claim`.

### 2.1 What the assembly does not yet have

Verified at `d014116e63ad`: eight Python modules under `src/dotmac_integrator/`,
eight routes (two probes, a composition report, a connector listing, a health
report, three operations endpoints).

- **No ingress route.** There is no `POST /ingress/...` of any shape.
- **No dispatch pump.** `worker.py` runs a lease sweep only, and says the pump
  *"cannot be written honestly until a real connector exists."*
- **No secret resolver.** `git grep -in secret` over the assembly returns one
  hit, in a test's list of module-owned function names. `dispatch.invoke`
  requires an injected `SecretResolver` and `lifecycle.enable` gates enablement
  on a live `validate_connection(config, secrets)`; **no connector of any kind
  can be enabled today.**

`tests/architecture/test_the_assembly_stays_thin.py` in that repository is the
strongest existing model for the guard proposed in § 8.6: an AST
docstring-stripping scan, word-boundary provider tokens, a forbidden
policy-vocabulary list, a redefinition check for module-owned functions, and
four sensitivity proofs including one asserting `\bmeta\b` does **not** match
`table.metadata.columns`.

### 2.2 The binding model against the destination-scope invariant

The confirmed fleet invariant (Knowledge `provider-metadata-never-selects-destination-scope`,
priority 98) is: *external-provider metadata is corroboration only; destination
scope comes from a trusted installation/operation binding and the originating
local intent.*

**The existing binding model enforces half of it, and the honest answer to the
other half is "not yet".**

| Half | Status | Evidence |
|---|---|---|
| A dispatch resolves exactly one enabled binding, or fails closed — absent, stale and ambiguous all refuse | **enforced** | `selection.resolve_binding`; ten cases in `test_integration_bindings.py`; the explicit `capability_binding_id` seam is refused rather than silently rerouted when stale |
| An inbound event is attributed to a binding, and a receipt cannot exist without one | **enforced structurally** | `InboxReceipt.capability_binding_id` is `NOT NULL`; dedupe is binding-scoped, so two bindings observing one upstream event are two receipts |
| The configuration a dispatch runs against is pinned at claim time, not re-read mid-flight | **enforced** | `dispatch.prepare` captures `config_revision_id` with the claim |
| **A binding names a DESTINATION — which application, which scope, which local intent** | **absent** | there is no destination column, no destination table and no product-intent reference anywhere in `models.py`. The only field shaped like one is `CapabilityBinding.scope_json`, and the module's own docstring says it is *"Parity with the source, where its only consumer DISPLAYS configured domains. Never in a uniqueness constraint and never read by routing"* — asserted by `test_scope_json_is_in_no_uniqueness_constraint` |

So today the invariant holds by absence: nothing routes on provider metadata
because nothing routes anywhere at all — there is no ingress path and no
destination binding. The moment an ingress route is added, the invariant becomes
a *design obligation* rather than a *proven property*. § 11 lists the proof.

The source shows exactly what happens without it: Sub reads the invoice a
payment will be allocated to out of PSP-controlled metadata
(`payment_webhook_commands.py:361-365`), which is safe there only because Sub
runs one operator tenant.

---

## 3. PSP — audited already; ADR-0030 changes only the counterparty

`payment-connector-sources.md` (890 lines) and
`payment-connector-extraction-dossier.md` are the audit. This dossier adds
three things and restates none of it.

**Confirmed at the pinned revisions.** Sub has one real PSP client
(`app/services/integrations/connectors/payment_gateway.py`, 403 lines,
Paystack + Flutterwave in one class with fourteen `if self.provider ==`
branches) behind a capability-gated control plane; ERP has a second,
independent Paystack stack (`paystack_client.py`, 1,164 lines) plus Remita
(poll-only, no webhook) and Mono (bank-statement aggregation, not a PSP); CRM
has provider *settings* and no client; Vendor CP and the Integrator have
nothing.

**Delta 1 — the counterparty moved.** The 2026-08-14 spec names Team 2's
billing profile contract as the consumer of `SettlementObservationV1`. Under
ADR-0030 § 1 the consumer is the `dotmac-billing` module's published port, and
the Cloud assembly is the translator. Open items D1 (`source_system` must be the
Integrator deployment, not the PSP) and D2 (where `tenant_id` comes from) are
now questions for the Billing owner's port, not for two specs to negotiate.

**Delta 2 — the capability names are already in production.** Sub's
`registry.py` `_DEFINITIONS` declares `payments.intent.v1`,
`payments.webhook.v1`, `payments.reconcile.v1` and `payments.refund.v1` against
live Paystack and Flutterwave installations. Three of the four carry through
unchanged; the spec's rename of `payments.webhook.v1` to
`payments.settlement.observation.v1` is correct and is the only vocabulary
delta. That is real product-first evidence for the naming convention in § 7.2.

**Delta 3 — a gate mismatch has closed.** The dossier's **G1** recorded that
`tests/architecture/test_product_first_extraction.py` had no classification
admitting a stateless connector distribution, and that `optional-module` was
*"a lie the gate currently forces."* At `e6ba2022f3d7` the vocabulary now
includes **`stateless-protocol-adapter`**, governed by
`stateless_adapter_violations()` — a pure function over a directory that
refuses persistence imports and lineage declarations, with a synthetic-package
sensitivity proof. `packages/dotmac-auth-oidc/EXTRACTION.toml` already uses it.
G1 is closed; G2 (no secret resolver), G3, G4, G5, G6 and G7 are not.

---

## 4. Registrar, DNS and hosting panel — the negative inventory

`cloud-commerce-owner-sources.md` § 4 reached this ruling with one sweep. This
is the wider, word-bounded re-run across six repositories, recorded so the
`greenfield-after-inventory` verdict is a measurement rather than a memory.

**Patterns searched, case-insensitive, word-bounded, excluding `.venv/`,
`node_modules/`, `site-packages/` and lockfiles:**

- registrar: `registrar`, `\bepp\b`, `epp_code`, `auth_code`, `whois`, `rdap`,
  `namecheap`, `godaddy`, `resellerclub`, `opensrs`, `\benom\b`, `nameserver`,
  `domain_transfer`, `domain_renewal`, `\btld\b`, `expiry_date`
- DNS: `dns_zone`, `dns_record`, `zone_file`, `cloudflare`, `route53`,
  `powerdns`, `bind9`, `dnspython`, and record-type handling (`A`, `CNAME`,
  `MX`, `TXT`) in code
- panel: `cpanel`, `\bwhm\b`, `directadmin`, `plesk`, `cyberpanel`,
  `virtualmin`, `hosting_account`, `hosting_package`, `suspend_account`,
  `unsuspend`, `terminate_account`, `disk_quota`, `bandwidth_usage`

**Result: zero implementations. Every hit is a false positive, and they are
named here because an unnamed false positive gets rediscovered as a source.**

| Hit | Where | What it actually is |
|---|---|---|
| `registrar` | `dotmac_sub/field_mobile/lib/core/push/push_registrar.dart` + `app.dart`, `main.dart`, `test/push_test.dart`, `docs/FCM_SETUP.md`; mirrored in `dotmac_crm/mobile/` | an FCM push-token registrar |
| `nameserver`, RADIUS vocabulary | `dotmac_sub/config/freeradius/radiusd.conf` | FreeRADIUS configuration |
| `expiry_date` (~60 files) | `dotmac_erp` HR certifications, fleet vehicle documents, inventory lots, corporate cards; `dotmac_sub/app/schemas/subscriber.py` | subscriber and asset expiry, not domain expiry |
| `cloudflare` | `dotmac_erp/app/services/careers/captcha.py`, `app/config.py`, `templates/careers/apply.html` | Cloudflare **Turnstile CAPTCHA** |
| `cloudflare` | `dotmac_erp/app/services/finance/platform/ecb_rate_fetcher.py`; `dotmac_sub/docs/storage_s3.md` | FX rates on Cloudflare Pages; R2 as an S3 backend |
| DNS wishlist prose | `dotmac_sub/docs/feature_improvements/07_maps_speedtest_dns.md`, `OUTSTANDING_FEATURES.md` | unchecked feature ideas |
| `unsuspend` (~40 files) | `dotmac_sub/app/services/account_status_commands.py`, `account_lifecycle.py`, `connection_type_provisioning.py`, `nas/vendor_adapter.py`, `nas/provisioner.py`, `app/web/admin/customers.py` | ISP subscriber lifecycle |
| `bandwidth_usage` | `dotmac_sub/app/services/usage_summary.py`, `customer_portal_flow_services.py`, `web_reports_extended.py`; `dotmac_crm/app/services/subscriber_reports.py` | RADIUS usage reporting |
| `TenantDomain` | `packages/dotmac-kernel/src/dotmac_kernel/models.py:103` | a **tenant hostname routing** row (`domain`, `verified_at`) for custom-domain request resolution. Not a registered domain, no registrar, no expiry, no nameservers. The closest name in the fleet to `dotmac-domains` and the one most likely to be mistaken for a source |

There is no registrar credential, no availability/registration/transfer/renewal
service, no DNS zone owner, no hosting account/package/suspension service, and
no parity test to port. What Vendor CP contributes is a *shape*, not an
implementation — see § 9.

---

## 5. Blesta — what is actually there

**Nothing.** `blesta`, `hostbill` and `clientexec` produce **no tracked file
match** in `dotmac_sub`, `dotmac_erp`, `dotmac_crm`,
`dotmac_vendor_control_plane`, `dotmac_integrator` or `dotmac_starter_mt` at the
revisions above. The only occurrences of the word "Blesta" in the fleet are in
`docs/adr/0030-*.md`, where it is discussed as a hypothetical replaceable
connector, and in the Knowledge entry summarising that ADR.

The nearest thing is **`whmcs` as a dropdown label in CRM**:
`app/models/subscriber.py:210` carries a free-text `external_system` column
whose comment reads `splynx, ucrm, whmcs`, echoed in `app/api/subscribers.py:204`,
`app/services/subscriber.py:5`, `app/web/admin/subscribers.py:70` and two
templates. There is no WHMCS client, credential, webhook or scheduled job. It
records where a subscriber came from.

Two consequences:

1. **A Blesta connector is greenfield in the strictest sense** — there is no
   API client, no field mapping and no parity test anywhere to port. Its
   dossier, if one is ever authorized, would be `greenfield-after-inventory`
   with this section as the evidence.
2. **The guard in § 8.6 is preventive, not corrective.** The count it protects
   is currently zero in all seven business packages, which is exactly when a
   ratchet is cheapest to install and, per ADR-0018, exactly when it is most
   likely to pass for the wrong reason — hence the mandatory sensitivity proof
   and the shrink-only "not yet created" set.

---

## 6. Do not port

Named so nobody reaches for them by reflex when the first connector is written.

1. **`PaymentGatewayRunner`'s conditional tree.** One class, two providers,
   fourteen `if self.provider ==` branches
   (`payment-connector-sources.md` § 2.1). Porting it whole relocates the tree
   into the Integrator — the alternative ADR-0024 § 7 rejects in terms. The
   extraction is a split into one distribution per provider.
2. **`ConnectorType` and `PaymentProviderType` as persisted enums.** Sub's
   `app/models/connector.py` declares
   `webhook|http|email|whatsapp|smtp|stripe|twilio|facebook|instagram|custom`;
   `PaymentProviderType` is a database `Enum` column with 34 references across
   12 modules including two dead members. ADR-0008 forbids the shape; retiring
   the existing ones is a migration, not a rename, and must never be bundled
   into a cutover.
3. **Provider names in persisted identity.** ERP's
   `payment_intent.paystack_reference` (unique),
   `payment_webhook.paystack_event_id`, `transfer_batch.paystack_batch_reference`,
   eight `bank_account.mono_*` columns and the `payments.remita_rrr` table. And
   Sub's `provider_event_id = f"{provider.value}-{identity}"`, which pushes a
   provider name into every downstream idempotency key.
4. **A currency default.** `config.get("default_currency") or "NGN"`
   (`payment_gateway.py:333`). Currency is required on every amount-bearing
   message and its absence is a refusal.
5. **Float money on the wire.** `float(Decimal(str(...)))` at `:331` and `:371`.
6. **A hardcoded minor-unit divisor.** `_money(..., divisor=Decimal(100))` per
   provider branch. `dotmac_kernel.money` carries `Currency.minor_units`.
7. **A provider error string branched on in shared code.** Sub's generic claim
   path tested `error_code == "crm_customer_name_rejected"`. The module already
   dropped it and has a test that keeps it dropped.
8. **A retry that re-executes a money handler without re-verification.** ERP's
   `webhook_service.retry_failed_webhook:454-500`. The module's
   `operations.replay_receipt` — authorized, audited, over an already-verified
   receipt — is the fix and is already built.
9. **Product-owned settings as connector configuration.** ERP holds nine
   `paystack_*` and four `mono_*` specs in `settings_spec.py`, and Remita's
   credentials are **process-global environment variables in a multi-tenant
   ERP**. Connector configuration lives on an immutable config revision with
   secret references.
10. **A second delivery ledger, retry engine, checkpoint store, health column
    or audit trail in a plugin.** All six have exactly one owner already.
11. **`if provider == "blesta"` in any form**, including a settings key, a
    column, an enum member and a template class name. § 8.6.

---

## 7. Shared contract

### 7.1 There is no plane to declare, and that is the answer

A connector distribution is **stateless**. It owns no table, no schema, no
migration lineage and no `mod_*` namespace, so ADR-0023's tenant/platform
declaration does not apply to it. The correct dossier field is
`planes = "none"`, written out rather than omitted so a reviewer sees the
statement instead of inferring it from an absence — the same discipline
`dotmac-integration` applies in the opposite direction with its explicitly empty
`TENANT_TABLES = ()`.

The plane that *does* exist belongs to `dotmac-integration`: `mod_intg` is
**platform-only**, and there the `REVOKE ALL` from the tenant app role is the
isolation. That is already proven against a real PostgreSQL in
`tests/test_integration_isolation.py::test_app_user_holds_nothing_on_any_table`.

An inbound receipt is a platform row **always**, even when the settlement,
renewal or suspension it reports will land in a tenant-plane record. The
Integrator holds transport evidence; the owner holds the state. No foreign key
crosses the boundary, and none could — the Integrator holds no product key at
all.

The correct classification for such a distribution is
**`stateless-protocol-adapter`** (§ 3, Delta 3).

### 7.2 What version one of the connector layer owns

- **Provider authentication** — bearer credentials, signing keys, rotation
  windows, all held as `<scheme>://<opaque>` references on an immutable config
  revision.
- **Ingress authentication** — signature verification over the exact raw bytes,
  constant-time, failing closed on a missing secret, **before** any parse.
- **Wire translation in both directions** — provider format ⇄ the owning
  module's published contract, carrying the provider's own status token
  verbatim and unmapped.
- **Provider I/O** — the HTTP/EPP/panel call, its timeout, and a typed
  `Outcome` classifying the attempt.
- **A deterministic provider event identity** — including for providers with no
  push channel, where the connector mints one from immutable provider fields and
  declares the derivation.
- **A declared capability set, SPI range and JSON-schema config contract**,
  published as package metadata.

It does **NOT** own:

- what any observation means; whether a transition is permitted; any Dotmac
  lifecycle state or status field;
- money arithmetic of any kind — no netting, no coverage, no allocation, no
  currency conversion, no acceptability judgement;
- the destination application, tenant, account, subscription, invoice, order or
  service record;
- the binding, the retry curve, the attempt cap, the lease, the checkpoint, the
  inbox, the outbox, the audit trail or the health report;
- scheduling — a connector cannot reschedule itself;
- price, term or eligibility for anything it registers, renews or provisions.

**Capability id vocabulary.** `domain.noun.vN`, validated by `spi._CAPABILITY_RE`
only. The version is part of the identity, so `.v1` and `.v2` are different
contracts one distribution may implement independently. The convention is
established by 20+ live ids in Sub's registry (`payments.intent.v1`,
`crm.ticket_observation.v1`, `erp.status.read.v1`, `messaging.receive.v1`,
`events.deliver.v1`) and by ADR-0024 § 7's own examples.

**A gap, and the ownership that resolves it.** There is no capability-id
*registry* anywhere in the fleet. The id is an open string checked by a regex,
with no declaration, no owner and no collision check across distributions.
Nothing today stops two owners minting `domains.registration.v1` with different
shapes. ADR-0008 says a new vocabulary is a declaration registry; this
vocabulary is open but unregistered.

ADR-0030 § 8.2 settles who owns what, and the answer is a **split** — the
capability id names a business contract, so the connector layer cannot own its
meaning:

| Concern | Owner |
|---|---|
| The capability ID and its typed semantic contract | the **business domain owner**. `dotmac-domains` owns the meaning of a domain-registration capability |
| Registry mechanics, installed-plugin declarations, binding validation, collision refusal | **`dotmac-integration`** |
| Fleet-wide uniqueness and declaration/consumer completeness | **Governance CI** |
| Implementing a declared capability at an accepted version | the **connector plugin**, which never mints authoritative meaning |

What remains open is mechanism, not ownership: whether registration is a
declaration on the owning module's `ModuleManifest` (the shape that already
exists and is already CI-checked in both directions) or a separate artefact, and
whether a connector declaring an unregistered id is refused at discovery, at
binding, or merely warned.

---

## 8. The four capability surfaces

Shapes and semantics only. Each surface is **published by the owning business
module** under ADR-0030 § 2.3 and § 2.6; a connector implements it. Nothing
below authors a port — § 8.7 is explicit about that boundary.

**One capability per independently bindable lifecycle boundary** — ADR-0030
§ 8.3. Per-verb capabilities are explicitly NOT the fleet convention: making
create, suspend, restore and terminate separately bindable would permit an
incoherent installation in which different providers claim different verbs for
one hosting account. A capability therefore declares its supported
**operations** internally, and a provider that cannot perform one reports that
operation unsupported.

The four families below are exactly the four ADR-0030 § 8.3 names: PSP
settlement/payment lifecycle, domain registrar lifecycle, DNS zone/record
lifecycle, and hosting account lifecycle. Each table that follows lists the
operations WITHIN one capability, not a set of separately bindable ids. Split a
family further only where there is a real reason to select different providers,
credentials, release cycles or failure domains — which is why DNS is its own
family rather than a registrar operation (§ 8.3).

Three further conventions apply to all four and are not repeated per surface:

- **Command capabilities** (mode `DELIVERY`) accept exactly one typed command
  carrying an **owner-minted opaque `operation_reference`**, and return one
  typed acknowledgement. The acknowledgement says what the provider *said*, not
  what is now true. `delivery_attempts.idempotency_key = operation_reference`;
  whether the effect *ran* at most once is the kernel ledger's answer under
  scope `integration.delivery`.
- **Observation capabilities** (modes `INGRESS`, `POLL`) emit immutable typed
  facts carrying the provider's own status token verbatim, an `observed_at`, an
  `arrival_mode` (`ingress` | `poll` | `operator_verify`), a
  `confirmation_evidence` of `connector_verified`, and a `receipt_id`. Dedupe is
  `(capability_binding_id, provider_event_id)`, a database constraint. A
  correction is a **new** fact carrying `relates_to`, never an edit.
- **Reconcile capabilities** (modes `POLL`, `DELIVERY`) answer "what does the
  provider currently believe about this reference?" and page a window from an
  engine-owned `polling_checkpoints` cursor. They are not optional extras: ERP's
  Remita proves a real production provider can have **no push channel at all**,
  and every one of these four domains has providers of that kind (EPP poll
  queues, panel APIs with no callbacks).

### 8.1 PSP

Already specified. `docs/superpowers/specs/2026-08-14-payment-connector-and-settlement-contracts.md`
§§ 1–5 defines the four shapes below and this dossier ratifies their SEMANTICS
unchanged.

**One reconciliation is outstanding.** That spec predates ADR-0030 § 8.3 and
declares its four shapes as four separately bindable capability ids. Under the
family rule they are four **operations** of one `payments.psp.v1` lifecycle
capability. This dossier does not unilaterally rewrite an accepted spec; the
reconciliation must land before a PSP connector distribution is authorized,
which § 6 of ADR-0030 already blocks. Nothing else in the spec changes — the
shapes, the modes and the "an acknowledgement is not a settlement" rule all
survive the regrouping.

| Operation | Modes | Carries |
|---|---|---|
| `payments.settlement.observation.v1` | INGRESS, POLL | verify signature over raw bytes; normalise one HTTP body into a **tuple** of `SettlementObservationV1` — exact `amount` + required `currency`, `provider_fee` as its own exact Money never netted, `provider_status` verbatim, a declared open `observation_kind` (`capture`, `capture_failed`, `refund`, `chargeback`, `chargeback_reversed`, `fee_adjustment`, `provider_correction`) |
| `payments.intent.v1` | DELIVERY | carry `PaymentIntentCommandV1` (`intent_reference`, amount, currency, opaque `payer_contact`, `return_url`, opaque `merchant_reference`, opaque `mandate_ref`) and return `PaymentIntentAcknowledgementV1` |
| `payments.refund.v1` | DELIVERY | carry a refund instruction; return the provider's acknowledgement |
| `payments.reconcile.v1` | POLL, DELIVERY | one reference, or a paged window from a checkpoint |

The one thing worth restating because the other three surfaces inherit it: **an
acknowledgement is not a settlement.** "The provider accepted my charge request"
and "money moved" are different facts with different identities, and conflating
them is how an invoice is marked paid on a pending checkout.

### 8.2 Registrar

**Capability: `domains.registrar.v1`** — one lifecycle boundary, eight declared
operations. A registrar that cannot serve one of them declares that operation
unsupported; the binding still covers the family, because a domain whose
registration and whose renewal came from different providers is not a coherent
installation.

A domain name is the only one of these four assets that a mistake makes
*unrecoverable* — a lapsed renewal enters redemption, a completed transfer-out
is gone, and both are visible to the customer within hours.

| Operation | Modes | Command / fact | Must never |
|---|---|---|---|
| `availability` | DELIVERY (read) | in: labels + TLD. out: `DomainAvailabilityFactV1{name, available: yes\|no\|unknown, provider_status verbatim, premium indicator, provider_quote: Money\|None, observed_at}` | reserve anything; decide a price; decide the customer may have it. `unknown` is a first-class answer — a registrar timeout is not "available" |
| `registration` | DELIVERY | in: `operation_reference`, name, term, `contact_set_ref`, `nameserver_set_ref`, privacy flag. out: acknowledgement + `provider_order_ref` + `provider_charge: Money` verbatim | report the domain as registered. Registration is confirmed by an observation, not by an acknowledgement |
| `renewal` | DELIVERY | in: `operation_reference`, name, term, the **currently observed expiry** the owner is renewing from | renew on its own initiative, or on a schedule of its own. Renewal is a decision the lifecycle owner makes against its own facts |
| `transfer` | DELIVERY | in: `operation_reference`, name, direction (`in`/`approve_out`/`cancel`), `auth_code_ref` — a **secret reference, never a literal** | approve a transfer-out. Only the lifecycle owner decides that, and only after checking non-financial holds |
| `contacts` | DELIVERY | in: `operation_reference`, name, desired contact set (opaque to the connector). out: acknowledgement | interpret a contact; store one; decide a jurisdiction |
| `nameservers` | DELIVERY | in: `operation_reference`, name, desired ordered nameserver set. out: acknowledgement | derive nameservers from a DNS provider it happens to also implement. § 8.3 |
| `observation` | INGRESS, POLL | `DomainObservationV1` — name; the registrar's own status tokens verbatim and unmapped (`clientTransferProhibited`, `pendingDelete`, …); `expires_at`; nameservers as seen; a digest of the contact set as seen; and an `observation_kind` from an open registry seeded with `registered`, `renewed`, `expiry_observed`, `transfer_requested`, `transfer_completed`, `transfer_rejected`, `redemption_observed`, `deleted`, `provider_correction` | map a status token to a Dotmac state; say the domain "is" anything |
| `reconcile` | POLL, DELIVERY | one name, or a paged portfolio window from a checkpoint | decide that a discrepancy is drift, or repair one |

**Two obligations specific to this surface.**

- **Auth codes are secret material carried per operation.** The module's
  `secret_refs` live on a *config revision*, so every reference it can
  materialize is installation-scoped and long-lived. A transfer auth code is
  neither: it belongs to one name, for one operation, for a few days. This is a
  real gap in the base (§ 10, defect 4), not something a connector may work
  around by putting the code in `payload_json` — which is persisted on
  `delivery_attempts` and ends up in every backup.
- **Registrars with no webhook are the norm, not the exception.** EPP's poll
  message queue maps exactly onto `polling_checkpoints` keyed
  `(capability_binding_id, job_key)` with its optimistic `version`. A connector
  for such a registrar declares `modes = frozenset({POLL, DELIVERY})` and mints
  its `provider_event_id` deterministically from the EPP message id.

### 8.3 DNS

**Capability: `dns.authoritative.v1`** — one independently bindable lifecycle.

Separate from the registrar **family**, and the separation is the point. This
is the one place ADR-0030 § 8.3's "split only where there is a real reason to
select different providers" bites: registrar and authoritative-DNS providers
are commonly replaced independently, so they are two families even though most
registrars also run nameservers. The first implementation of both may
legitimately be one distribution — a distribution declaring two capabilities is
still two independently bindable capabilities, and that is what lets DNS later
move to a dedicated provider with **no change in any business module**.

DNS is the one surface that is genuinely **desired-state**, not command/event.
The table below predates Integration SPI 1.2 and names the three DNS RESOURCE
KINDS the owner schema must express. They are not connector-engine operation
codes. The released provisioning engine invokes the canonical `plan`, `apply`,
`observe`, and `cancel` operations for this capability; each request identifies
one of these resource kinds.

| Operation | Modes | Shape |
|---|---|---|
| `zone` | DELIVERY | create/delete a zone. out: `provider_zone_ref` + the nameservers the provider assigned, as a fact |
| `recordset` | DELIVERY | apply a desired record set for one zone. **plan / apply / observe**, with a stable `plan_hash` over the canonicalised desired state, an `operation_reference` that is the resume token, and a `PARTIAL` result naming exactly the outstanding records |
| `observation` | INGRESS, POLL | the zone and records as the provider currently holds them, so a resolver can derive drift |

**Reuse the shape, not the seam.** `dotmac_kernel.providers.provisioning`
already encodes precisely this contract —
`plan → apply → observe → cancel`, `plan_hash` stability, `operation_id`
idempotency with terminal results frozen and `PARTIAL` resumable,
`outstanding_steps`, and a `retryable`/terminal error hierarchy. Its semantics
are the product-first source for `dns.recordset.v1`'s **semantics**. Its
`Protocol` is **not** the seam: a connector's contract is the Integrator SPI,
and a plugin implementing a second in-process provider protocol at the same
boundary is exactly the parallel framework this dossier refuses. Copy the state
machine; keep one transport.

A DNS record set contains customer-visible routing. A connector never decides
that a record should exist, never merges its own view with the desired one, and
never deletes a record it did not plan.

### 8.4 Hosting panel

**Capability: `hosting.account.v1`** — one lifecycle boundary, six declared
operations.

An earlier draft of this dossier split these six into six separately bindable
capabilities. ADR-0030 § 8.3 rejects that: separately bindable verbs would
permit an installation where one provider creates the account and another
suspends it, which is not a coherent hosting account. A panel that can create
accounts but cannot terminate them declares `termination` **unsupported within
the capability**, and `ConnectorManifest.require_declares` still refuses a
binding that does not cover the family **at the write** (`lifecycle.add_binding`)
rather than at the first call. That refusal already exists and is tested; what
changes is its unit — the family, not the verb.

| Operation | Modes | Shape | Note |
|---|---|---|---|
| `provision` | DELIVERY | in: `operation_reference`, opaque `package_ref`, primary domain, opaque owner contact. out: acknowledgement + `provider_account_ref` | never chooses a package; never generates or returns a password in a fact — a credential handoff is a secret channel, not an observation field |
| `package` | DELIVERY | in: `operation_reference`, account ref, target `package_ref` | never decides that an upgrade is warranted or affordable |
| `suspension` | DELIVERY | in: `operation_reference`, account ref, action (`suspend`/`restore`), opaque `reason_ref` | never suspends on its own signal — usage, non-payment and abuse are all owner decisions. ADR-0030 § 1: Collections may only *request*; the lifecycle owner locks, revalidates and decides |
| `termination` | DELIVERY | in: `operation_reference`, account ref, opaque `approval_ref` | **the one irreversible operation.** It must never be auto-retried, must never be enqueued without an approval reference the owner minted, and a lost acknowledgement resolves through `reconcile`, never through a second attempt |
| `observation` | INGRESS, POLL | account state as the panel holds it, plus resource usage: disk, bandwidth, mailbox and database counts, each with its unit and the provider's own period boundary verbatim | never converts a usage number into an overage, a charge or a threshold breach |
| `reconcile` | POLL, DELIVERY | one account, or a paged account list from a checkpoint | never repairs |

**Usage observations are facts with a period, not meters.** Metering, rating and
overage are `dotmac-subscriptions` and `dotmac-billing` decisions under ADR-0030
§ 1. A connector that emitted "over quota" would have made the first of them.

### 8.5 Rule 1 demonstrated — mixed providers, zero business-module change

The requirement: Dotmac must be able to use Blesta for hosting while using a
direct registrar and a direct PSP, with no code change in any business module.
This is not a new mechanism; it is the existing schema read correctly.

Concretely, four rows in `mod_intg.connector_installations`, each with its own
config revision and secret references, and **one enabled binding per lifecycle
family** — which under ADR-0030 § 8.3 is one binding each, not one per verb:

| `connector_key` | installation | enabled binding |
|---|---|---|
| `psp_direct_a` | `psp-live` | `payments.psp.v1` (operations: settlement observation, intent, refund, reconcile — pending the § 8.1 reconciliation) |
| `registrar_direct_b` | `registrar-live` | `domains.registrar.v1` (eight operations) |
| `panel_bridge_c` (a Blesta profile) | `hosting-live` | `hosting.account.v1` (six operations) |
| `dns_direct_d` | `dns-live` | `dns.authoritative.v1` (three operations) |

The family model makes this demonstration stronger, not weaker: because a
binding covers a whole lifecycle, there is no way to express the incoherent
installation in which Blesta creates a hosting account and a direct panel
suspends it.

Why this works with what is already built:

1. `CapabilityBinding` is unique on `(installation_id, capability_id)` and
   **`capability_id` alone is deliberately not unique** — asserted by
   `test_a_capability_alone_is_not_a_uniqueness_constraint`. Many installations
   may implement one capability.
2. Selection is per dispatch, not per schema. `resolve_binding` takes an
   explicit `capability_binding_id` (the primary, unambiguous path), or narrows
   by `connector_key`, or requires exactly one `policy_json.default` — and
   refuses otherwise, naming the colliding installations.
3. Moving hosting from the Blesta profile to a direct panel is: draft a new
   installation, add a config revision, bind `hosting.account.v1`, enable, flip
   the destination binding, disable the old one. **No import changes, no
   migration, no release of any business module.**
4. If the registrar distribution also implements `dns.authoritative.v1`,
   binding DNS to it is one more row — and unbinding it later is one row back.
   The family stays separate precisely so that swap is a row and not a refactor.

The one thing missing is the row that says *which application and scope this
installation delivers to* (§ 2.2). Until that exists, "zero code change" is true
of the connector layer and unproven end to end.

### 8.6 Rule 2 demonstrated — the guard that keeps Blesta one profile

**Proposed file:** `tests/architecture/test_no_provider_reaches_a_business_module.py`,
beside `test_external_connector_ratchet.py` and `test_product_first_extraction.py`.

**Governed set:** the seven ADR-0030 § 6 business distributions —
`dotmac-billing`, `dotmac-subscriptions`, `dotmac-orders`, `dotmac-domains`,
`dotmac-hosting`, `dotmac-collections`, `dotmac-fulfillment`. The tuple is
asserted to have exactly seven members and each name is asserted to appear in
the ADR text, so the list cannot silently shrink. Packages not yet created live
in a `NOT_YET_CREATED` set that is asserted to only shrink — the two-directional
discipline of hard rule 25, and the reason a check over an empty `packages/`
directory cannot pass for the wrong reason.

**Detector:** one pure function, `provider_leak_violations(package_dir) -> list[str]`,
over a directory rather than a package name — the shape
`stateless_adapter_violations()` already uses, chosen so the sensitivity proof
can build a synthetic package in `tmp_path` and watch the checker fire. Source
is parsed with `ast`, docstrings stripped and re-unparsed before any text scan,
copying `test_the_assembly_stays_thin.py::_source_without_docstrings` — these
packages will discuss providers in prose precisely because they contain none,
and a scan that cannot tell explanation from implementation reports the
explanation.

Six violation classes, each named in its own failure message:

| Class | Greps for | Catches |
|---|---|---|
| `IDENTIFIER_LEAK` | any `ast.Name`, `arg`, attribute, keyword, class, function or assignment target whose snake/camel-split words intersect `PROVIDER_TOKENS` | `blesta_client_id`, `BlestaService`, `blesta_status` |
| `LITERAL_LEAK` | `ast.Constant` strings, word-bounded | `"blesta"`, `Enum` member values, `__tablename__`, template class names |
| `BRANCH_LEAK` | `ast.Compare` and `ast.match_case` where one operand is a `PROVIDER_TOKENS` string constant | `if provider == "blesta"` — reported separately so the message can name ADR-0024 § 7 |
| `SETTING_LEAK` | a `SettingSpec(...)` call, or any `key=`/`code=`/`domain=` keyword, whose value matches a token | a Blesta setting |
| `DDL_LEAK` | the same token scan over `migrations/versions/*.py` raw text, with adjacent string-literal concatenation undone and whitespace collapsed | a provider-named column or table, the class the payment audit found in ERP |
| `CONTROL_PLANE_IMPORT` | any import of `dotmac_integration` or `dotmac_integrator` | a business module reaching for the connector control plane instead of publishing a port (ADR-0024 § 6: *"Products do not each compose this module"*; ADR-0030 § 3) |

`PROVIDER_TOKENS`: `blesta`, `whmcs`, `hostbill`, `clientexec`, `paystack`,
`flutterwave`, `stripe`, `paypal`, `remita`, `monnify`, `interswitch`, `mono`,
`cpanel`, `whm`, `directadmin`, `plesk`, `cyberpanel`, `virtualmin`,
`namecheap`, `godaddy`, `resellerclub`, `opensrs`, `enom`, `cloudflare`,
`route53`, `powerdns`.

**Sensitivity proof, both directions.** The token list contains short and
substring-prone members, which is how a provider scan quietly becomes either a
false-alarm generator or a no-op:

- plant each violation class in a synthetic package and assert the function
  reports exactly it;
- assert the near-misses do **not** match — `metadata` (not `meta`),
  `monotonic` and `money` (not `mono`), `phenomenon` (not `enom`),
  `whm` inside no ordinary word — modelled on the integrator suite's
  `test_the_provider_detector_bites`, which already carries the `metadata` case;
- assert the scan actually read files: scanned file count equals package file
  count minus a named, commented exclusion set.

**What this test deliberately does not see**, stated because ADR-0018 requires
it: a provider name reached through a variable resolved at runtime; a provider
name in a data row rather than in code; and a provider name in the *assembly*,
which is governed by the integrator repository's own thin-assembly suite. The
Cloud assembly will need the equivalent check when it exists.

### 8.7 Rule 3 demonstrated — a connector transports, it never decides

Four properties, three already enforced and one still to build.

| Property | Status |
|---|---|
| A plugin cannot write anything: `dispatch.invoke` accepts no session by signature | **enforced**, `test_invoke_cannot_be_given_a_database_session` |
| A plugin's `Outcome` classifies but does not schedule; only `retry.next_state` and the engine decide what happens next, and a plugin cannot reschedule itself | **enforced**, `retry.py` + `test_integration_execution.py` |
| A plugin's `error_code` is stored and never branched on in shared code | **enforced**, `test_no_product_error_code_leaks_into_the_generic_claim` |
| A plugin holds no state: `planes = "none"`, no table, no lineage, no persistence import | **enforceable today** via `classification = "stateless-protocol-adapter"` and `stateless_adapter_violations()` |
| **A callback never assigns a Dotmac lifecycle state** | **not yet provable** — there is no ingress path. The receipt's `consequence_json` is filled by the *caller*, and nothing today constrains what that caller may write |

The last row is the design obligation the ingress work inherits: an inbox
receipt's consequence is *publishing a versioned observation to the owning
module's port*, and the owning module's own service is the only thing that may
then change state. ADR-0030 § 1 states the chain — collector records, resolver
derives drift, lifecycle owner decides consequence — and § 11 lists the proof
that must accompany the first ingress route.

---

## 9. Kernel floor

Capabilities the connector layer consumes today, so the floor can later be
proven both sufficient and necessary.

| Kernel capability | Used for | Consumed where |
|---|---|---|
| `dotmac_kernel.idempotency.execute_once_platform` | at-most-once execution, the tenant-free contract | `integration.idempotency.run_effect_once`, scopes `integration.*` |
| `dotmac_kernel.audit.write_platform_audit_event` | the one platform audit ledger | `integration.operations.record_operation`, three declared actions |
| `dotmac_kernel.db` | transaction authority — the module mutates and flushes, never commits, never opens a session | every function in `lifecycle`, proven by `test_the_service_never_commits_or_opens_a_session` |
| `dotmac_kernel.models` (`Base`, `TimestampMixin`, `uuid_pk`) | ORM base | `integration.models` |
| `dotmac_kernel.namespaces` (`module_schema`, `schema_table_args`, the migration-owner ledger) | the `mod_intg` allocation | `integration.models`, `integration.manifest` |
| `dotmac_kernel.modules.ModuleManifest` | composition, D1 identity, `platform_tables` | `integration.manifest` |
| `dotmac_kernel.planes` | the platform-plane declaration and its live-catalog gate | `tests/test_integration_isolation.py` |
| `dotmac_kernel.money` | **required by every one of the four surfaces** for provider quotes, charges and settlement amounts — exact, never float, currency always explicit | not yet consumed by the module; consumed by the first connector |
| `dotmac_kernel.secret_sources` / `install_secret_source` | the deployment-side materialization of `secret_refs`, held not fetched (ADR-0009) | **not consumed on this path by anything** — the module deliberately takes an injected `SecretResolver`, and no assembly supplies one (§ 2.1) |

**Two floor gaps, named now so the "sufficient and necessary" proof is possible
later:**

- **There is no scheduler.** `dotmac_kernel.durable_timers` does not exist —
  ADR-0030 § 4 names it as a prerequisite the Cloud programme must deliver. Every
  reconcile capability in § 8 is a POLL, and `ConnectorMode.POLL` is decorative
  (§ 10, defect 1). Without a generation-safe wake-up, the four surfaces have no way
  to run their reconcile leg, which is the leg that resolves every
  provider-succeeded-but-we-never-heard case.
- **There is no per-operation secret channel.** ADR-0009's model is a secret
  held per installation; a transfer auth code is per command (§ 8.2, and
  § 10 D4).

---

## 10. Known defects and deltas

Numbered so a later change can close one by number.

1. **`modes` is decorative across the whole SPI.** `ConnectorMode` declares
   `INGRESS`, `POLL` and `DELIVERY`, and re-verified at `e6ba2022f3d7`: no
   reference in `dispatch.py`, `execution.py`, `selection.py`, `lifecycle.py` or
   `activation.py`. Only `conformance.assert_plugin_conforms` looks at it, and
   only to check it is non-empty. Consequences: ingress cannot run, poll cannot
   run, and dispatch calls `handler_for` on a plugin that never declared
   `DELIVERY`. Recorded first in `whatsapp-connector-sources.md`; unchanged.
2. **`DispatchRequest` carries no raw body and no headers**, so signature
   verification — which the module's own docstring assigns to the connector —
   has nowhere to happen. Every one of the four surfaces signs or authenticates
   raw bytes. This blocks all four, hardest for PSP and panel, whose primary
   mode is ingress.
3. **The ingress URL shape is unresolved.** `/ingress/{connector_key}/{capability_id}`
   is ambiguous when two installations serve one pair — the normal case, not an
   edge case. `whatsapp-connector-sources.md` argues the URL must resolve one
   stable binding identifier. That argument is correct and applies with more
   force here: two registrar accounts or two panel servers under one connector
   is the ordinary deployment.
4. **`secret_refs` is installation-scoped only.** There is no channel for
   per-operation secret material. EPP transfer auth codes and panel credential
   handoffs both need one, and neither may travel in `payload_json`, which is
   persisted on `delivery_attempts` and on `inbox_receipts`.
5. **The assembly has no secret resolver** (§ 2.1). `lifecycle.enable` cannot
   run for any connector; no installation can reach `enabled`.
6. **Capability ids are an unregistered open vocabulary** (§ 7.2). Regex-checked,
   owner-less, collision-free by luck.
7. **A binding names no destination** (§ 2.2). The invariant that provider
   metadata never selects scope currently holds by absence.
8. **`scope_json` is a field that looks like routing and is not.** It is
   documented, tested and asserted to be display-only. A future reader adding
   scoped routing to it would be adding JSON-equality overlap semantics the
   module explicitly refuses.
9. **`test_external_connector_ratchet` abstains in CI** and, per
   `whatsapp-connector-sources.md`, currently fails locally at
   `dotmac_sub.sync_checkpoint: 9 > baseline 8`. Live drift under an unmonitored
   guard. Any provider ratchet added by § 8.6 must not inherit that abstention
   pattern — the Starter's own `packages/` tree is always present, so the new
   guard has no reason to skip.
10. **There is no payment ratchet baseline**, and per
    `payment-connector-extraction-dossier.md` § 7.4 it must not inherit
    `external-connector-baseline.json`, whose `webhook_surface` detector keys on
    `webhook|callback|/hooks|ipn` and therefore misses Sub's
    `/payment-events/…` routes.
11. **Two independent Paystack clients run in the fleet** with two rate-limit
    budgets, two backoff policies and two answers to "did this charge run?"
    (`payment-connector-sources.md` § 1). Unchanged at the pinned revisions.

---

## 11. Fresh proof required

The tenant-plane rows are missing from this list on purpose: a connector
distribution has no plane, and `mod_intg` is platform-only with its isolation
already proven live.

1. **Platform-plane isolation stays proven as the schema grows.** `app_user`
   holds no privilege on any `mod_intg` table or column; the online platform
   role reaches every table through schema `USAGE` plus row DML. Already proven
   at `tests/test_integration_isolation.py`; must be re-run for any table the
   ingress work adds.
2. **A destination binding is the only source of scope.** With provider metadata
   naming application B and the binding naming application A, the observation
   is routed to A, and a disagreement is recorded and **fails closed** rather
   than being reconciled toward the payload.
3. **No receipt row exists before verification succeeds.** Unknown connector
   key, unknown installation, unbound capability, disabled binding and invalid
   signature each leave the receipt count unchanged; each returns 404 (or 400
   for a bad signature) so an unauthenticated caller cannot enumerate what
   exists.
4. **A tampered body fails verification, and a missing signing secret is an
   explicit refusal**, not an accident of a truthiness guard.
5. **Duplicate delivery.** Same `provider_event_id`, same `payload_digest` →
   one receipt, the recorded `consequence_json` returned, no second
   consequence. Same id, different digest → `ProviderEventIdentityCollision`,
   escalation not retry, original content preserved.
6. **Out-of-order delivery.** A refund observation before its capture; an
   expiry observation before its renewal confirmation; a suspension observation
   before its suspend acknowledgement. Each is accepted as an immutable fact
   with its own identity, nothing is buffered or reordered, and the owner's
   resolver produces the same end state regardless of arrival order.
7. **Lost callback.** The provider performed the effect and never called back.
   Only `*.reconcile.v1` discovers it, and the fact it produces carries the same
   identity a callback would have produced — so the owner sees one fact, not
   two.
8. **Provider-succeeded-but-we-never-heard.** A write whose acknowledgement was
   lost settles to `reconciliation_required` with `next_attempt_at IS NULL`,
   nothing picks it up automatically, and no automatic retry reaches the
   provider. Proven separately for a domain registration, a renewal and an
   account termination, because those are the three where a second attempt is
   not idempotent.
9. **Concurrency.** Two workers, one delivery: exactly one claim
   (`rowcount == 1`), and the loser's `settle` raises `LostClaim` rather than
   overwriting. Two workers, one checkpoint: the stale write is refused. Both
   already proven live for the generic engine; both must be re-proven for the
   ingress path.
10. **Rollback with the consuming transaction.** A provider batch that produces
    one collision rolls back the **whole** batch, so a retry is correct and no
    partially-recorded batch exists that the provider believes it delivered.
11. **Idempotent replay and fingerprint conflict.** A replayed observation
    re-emits with a byte-identical identity, so the owner sees a replay and not
    a second fact; a replay whose fingerprint differs escalates to a human
    rather than silently superseding.
12. **Drift and reconciliation.** Diverge the provider from Dotmac's desired
    state — a nameserver changed at the registrar, a record removed at the DNS
    provider, an account suspended in the panel — and prove the resolver
    detects it, the owner decides the consequence, and repair converges without
    the connector deciding anything.
13. **No secret value reaches a log or a row.** A caplog sweep across the whole
    ingress and dispatch path finds no planted secret, no prefix, no length and
    no hash of one; after a full dispatch no materialized value appears in any
    `mod_intg` row. With the deliberate-violation canary that proves the sweep
    bites.
14. **The provider-leak guard bites** (§ 8.6), in both directions, including the
    near-miss cases.

---

## 12. Adoption and retirement

**Nothing here is authorized yet.** ADR-0030 § 6 does not name a connector
distribution, and ADR-0017's 2026-08-12 amendment independently holds an inbound
payment-provider receiver. The ordering below is what should happen when those
gates open, not a plan that may start.

**Prerequisites, in order, none of them provider work:**

1. Close the SPI gap in `dotmac-integration` — the base protocol plus
   mode-specific executable protocols, the ingress hook carrying raw bytes and
   headers, a binding-addressed ingress route, mode-checked dispatch, and a
   per-operation secret channel — and release it as its own alpha. Defects
   1–4, 8.
2. Give the `dotmac_integrator` assembly a secret resolver. Defect 5. Sub's
   `app/services/secrets.py` (462 lines) is the product-first source; whether
   its TTL cache survives ADR-0009's held-not-fetched posture is a design
   decision, not a defect.
3. Deliver `dotmac_kernel.durable_timers`, without which no reconcile leg runs.
4. Prove the whole chain on the **cheapest possible payload first**: the
   ingress-only Meta/WhatsApp capability that `packages/dotmac-integration/EXTRACTION.toml`
   already names as `first_cutover`, and which says in terms *"deliberately NOT
   payments."* That ordering is right and this dossier does not reopen it.

**Then, and only against frozen owner ports:**

5. **PSP first**, ingress-only, one provider, `payments.settlement.observation.v1`
   alone, shadowed beside Sub's existing receiver until there is zero
   unexplained drift over a full billing cycle including one refund and one
   redelivery. The egress capabilities follow, because outbound is where a
   mistake takes money twice.
6. **Registrar and DNS after `dotmac-domains` freezes its command and
   observation contracts**, availability first (a read that changes nothing),
   then observation, then renewal, then registration, then transfer. Transfer
   last because a completed transfer-out is irreversible.
7. **Hosting panel after `dotmac-hosting` freezes its contracts**, observation
   first, then provision, then package change, then suspension, and
   **termination last and separately**.

**What retires, and when.** Sub's Paystack and Flutterwave clients, its two
`/payment-events/*` routes, its verifier and its registry manifests; ERP's
`paystack_client.py`, `paystack_sync.py`, `webhook_service.py`'s verification
and dispatch, its webhook route and its two provider-named Celery tasks. Each
retirement lowers a two-directional ratchet **in the same change**, so it is
reviewable as a diff. Ratchet **R6** in
`payment-connector-extraction-dossier.md` § 7.1 — the provider-named persisted
identifiers — is the exception: it is an expand/contract migration with a data
backfill and must never be bundled into a cutover, per the confirmed fleet rule
`provider-name-column-migration-never-inside-a-money-cutover`.

Registrar, DNS and panel have **nothing to retire**: there is no local writer,
which is why they are the cleanest adopters and also why their first cutover has
no shadow control to compare against. That absence is the risk, and the answer
is the conformance kit, not a shadow.

**A green test suite is not a cutover.** A connector distribution is complete
when its manifest, its conformance run and its scans pass. It is *adopted* only
when a real application runs the exact released version, a measured
shadow/cutover switches authority, and the displaced local writer is deleted in
the same change that lowers its baseline.

---

## 13. The provider-free fake and conformance kit

ADR-0030 § 2.6 places the kit **inside the owning module's completion gate**: an
owner ships *"a provider-free fake/conformance kit for every port it publishes
or consumes."* This section defines the **common shape** and the
**connector-side obligations**. It does **not** author the ports — Billing,
Domains and Hosting each publish their own, and each ships its own kit.

### 13.1 Two halves, and both already have a precedent

| Half | Owner | Precedent that exists today |
|---|---|---|
| **SPI conformance** — is this a well-formed connector at all? | `dotmac-integration` | `conformance.assert_connector_conforms(manifest)` and `assert_plugin_conforms(plugin)`, shipped as **library code, not test-tree code**, precisely so the distribution being certified can import them |
| **Port conformance** — does this connector honour the owning module's contract? | each business module, per ADR-0030 § 2.6 | `dotmac_kernel.providers.provisioning` + Vendor CP's `LaboratoryProvisioningProvider` — a deterministic, side-effect-free provider with failure-injection knobs (`fail_plan`, `fail_apply`, `partial_first_apply`) and a contract suite in `tests/unit/test_provisioning_contract.py` |

One placement rule, learned from Vendor CP and worth carrying: the kit ships
**assertions and a reference fake**; a product that wants a runtime simulator
writes its own and owns it. Vendor CP asserts exactly this
(`test_runtime_laboratory_provider_is_vendor_owned` — *"The shipped lab must not
execute a helper from the kernel test kit"*), and it is right. A fake that is
both the certification oracle and a production code path has stopped being
either.

### 13.2 What every fake must simulate

Seven behaviours, each a knob rather than a subclass — a frozen dataclass with
settable outcomes, so a test cannot reshape the contract it is meant to be
checking (the shape `conformance.FakePlugin` already uses, with its comment
saying exactly that).

| # | Behaviour | The fake does | The connector must produce |
|---|---|---|---|
| 1 | **Success** | returns the provider's own success token | `SUCCEEDED`; a typed acknowledgement or fact; the provider status verbatim |
| 2 | **Refusal** | returns a business decline — insufficient funds, name taken, transfer locked, package unavailable | `TERMINAL`. Retrying a decline is the provider's decision to repeat, not the engine's |
| 3 | **Timeout** | raises a socket timeout mid-write | `RECONCILIATION_REQUIRED`, `next_attempt_at IS NULL`, **no automatic retry** |
| 4 | **Duplicate delivery** | replays an identical event body | one receipt, `is_new=False`, the recorded consequence returned, no second effect |
| 5 | **Out-of-order delivery** | emits the correction before the fact it corrects | both accepted as immutable facts with their own identities; the connector reorders nothing and buffers nothing |
| 6 | **Lost callback** | performs the effect and never calls back | nothing changes until `*.reconcile.v1` runs; the reconcile fact carries the identity the callback would have carried |
| 7 | **Provider succeeded, we never heard** | accepts a write, then fails the response | `RECONCILIATION_REQUIRED`; a subsequent reconcile finds the effect landed; **no second write reaches the fake** — asserted by counting the fake's calls, which is why the fake records every request it saw |

Plus three cross-cutting knobs: a batch body carrying several events (so
`normalize` returning a tuple is exercised — a single-event signature silently
drops all but the first); an identity collision (same event id, different
payload); and a rotation window (a body signed with the previous secret).

### 13.3 How a connector proves conformance

Its own test suite, in its own distribution, calling library code from two
owners:

```
from dotmac_integration.conformance import assert_connector_conforms, assert_plugin_conforms
from dotmac_domains.conformance import assert_registrar_conforms   # the OWNER's kit

def test_conforms() -> None:
    assert_connector_conforms(MANIFEST)
    assert_plugin_conforms(PLUGIN)
    assert_registrar_conforms(PLUGIN)   # drives all seven behaviours
```

Plus per-distribution scans, each with a planted-violation proof:

- exactly one `connector_key`, matching the distribution name;
- no comparison against a sibling provider's name;
- constant-time signature comparison — a plain `==` on the comparison path
  fails;
- the emitted message's field names intersect the owner's forbidden list at
  zero (no product identifier, no lifecycle field, no netted amount);
- no `float(` on a money path; no `Decimal` money without a currency; no
  currency default;
- no `backoff`/`tenacity`/`celery`/`apscheduler` import, no `while True` retry
  loop, no declared table, cursor or watermark;
- no import of any product package and no SQLAlchemy import — which
  `classification = "stateless-protocol-adapter"` and
  `stateless_adapter_violations()` already check generically.

**Nothing in the kit reaches a network.** The whole
installation → configuration → binding → dispatch → settle slice must be
provable without a provider, because a kit that needed credentials would make
every connector author's first encounter with the SPI a secrets problem. That is
`conformance.py`'s stated reason for existing, and it applies unchanged to all
four surfaces.

---

## Dated update 2026-08-17 — exact managed-service authorization

The statements above that no connector distribution was authorized were true at
this inventory's 2026-08-15 revision and remain the history of the gate.
[ADR-0033](../adr/0033-exact-managed-service-connectors-are-authorized.md) now
amends ADR-0030 section 6 for exactly seven managed-service distributions:
`dotmac-connector-contabo`, `dotmac-connector-keycloak-admin`,
`dotmac-connector-mailcow`, `dotmac-connector-nextcloud`,
`dotmac-connector-dotmac-erp`, `dotmac-connector-dotmac-academy`, and
`dotmac-connector-dotmac-host-agent`. It grants no wildcard and does not waive
owner contracts, provider-free conformance, SPI conformance, release, adoption,
or cutover evidence.

The managed-service Rule-24 evidence and per-provider rulings are recorded in
[`managed-service-connector-sources.md`](managed-service-connector-sources.md).
For DNS, that newer inventory carries forward this dossier's authoritative
family unchanged: `dns.authoritative.v1`. Integration SPI 1.2 supplies the
engine operations `plan`, `apply`, `observe`, and `cancel`; `zone`, `recordset`,
and `observation` are typed resource kinds inside those operation schemas. A
distribution may implement IaaS and DNS/PTR, but their capability bindings
remain independent.

The earlier secret-resolver blocker is also closed in the current Integrator
lineage: `dotmac_integrator` revision `783baf23cbf5` contains
`src/dotmac_integrator/secret_loading.py`,
`src/dotmac_integrator/secret_resolver.py`, and
`tests/unit/test_secret_resolver.py`. That closes one prerequisite; it does not
substitute for SPI 1.2 publication, an owner contract, or connector conformance.
