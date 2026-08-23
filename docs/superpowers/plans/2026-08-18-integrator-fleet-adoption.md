# Integrator fleet adoption work packets

**Status:** execution plan; the checked-in adoption ledger and ADR-0024 remain
the authoritative gates.  
**Goal:** build the reusable connector and product-port seams for every
continuing application in parallel, then cut over one capability binding at a
time without disturbing billing or an untouched integration.

## The ordering rule

Application readiness may be built in parallel. Runtime cutover is sequential.
A capability callback or delivery route moves only after its own eight-part
migration packet, staging evidence, survivor canaries and rollback proof are
complete. Sub's seven shared integration tables stay until every production
inventory row is migrated or retired.

An application that is not yet deployed on Seabone does not block another
application's isolated capability rehearsal. It does block its own rehearsal
and the final fleet-retirement gate. This is why all application seams should be
built now without turning the first WhatsApp receive cutover into a big-bang
fleet deployment.

## Parallel work packets

### Packet A — fleet evidence and release gates (Starter)

Owns the machine-readable adoption ledger, six-application connector ratchet,
snapshot schema, CI gate and retirement refusal. Add one read-only capture
adapter per product repository; the product knows its legacy tables and config
surfaces, while Starter owns only the evidence contract.

Acceptance:

- Sub, ERP, CRM, Academy, Backoffice and Vendor CP each have one current source
  revision, static capability inventory and production-derived snapshot;
- all seven coverage categories are explicit booleans, never inferred from an
  empty result;
- each production capture matches the deployed source revision and explicitly
  accounts for every mapped source surface or declared capability; omitted
  rows and `unmeasured` observations refuse completeness;
- no snapshot contains configuration, payload, header, consequence, secret
  reference or secret value;
- an unknown live capability or missing application refuses retirement.

Current implementation status: Sub's product-owned capture adapter is built in
a clean worktree at `0d27ab91` and its read-only contract passed against a fully
migrated ephemeral PostgreSQL database on Seabone. It remains unmerged and
undeployed, so Sub production evidence is still correctly `unmeasured`.
Academy's seven-surface capture adapter is likewise built at `a5e25e4e`; its
exact image passed the full application suite and a staging-derived clone
migration on Seabone. It is also unmerged and production remains `unmeasured`.

### Packet B — Sub WhatsApp receive (current vertical)

Keep the existing `dotmac-connector-whatsapp` distribution and
`messaging.receive.v1` product port. Supply the missing mirror evidence, bind a
real destination revision and prove the named receipt-delivery reconciler.
WhatsApp send/templates do not move in this packet.

Acceptance:

- exact connector/module/client/assembly pins are installed in staging;
- the same signed fixture produces semantically equal legacy and Integrator
  observations, while provider event identity remains the deduplication key;
- Nextcloud Talk's live staging binding has a survivor canary before and after;
- the billing non-interference fingerprint and billing queue observation are
  unchanged;
- callback rollback restores only WhatsApp receive; no shared table is removed.

### Packet C — Academy seams

Inventory and classify all seven committed-source surfaces, including the two
operational endpoints the count ratchet originally missed:

- `app/services/erp_sync.py` — course-completion delivery;
- `app/services/erp_assessment_sync.py` and
  `app/api/erp_applicant_assessments.py` — assessment delivery/registration;
- `app/services/email.py` — SMTP delivery;
- `app/web/labs.py` — retain as an authenticated, product-local lab-console
  proxy unless a separate decision assigns it to Integrator;
- `app/observability.py` — retain GlitchTip error reporting as a product-local
  operational control; and
- `app/main.py` plus deployment settings — retain authenticated Prometheus
  metrics as product-local observability.

The three ERP exchanges remain versioned product-to-product APIs under the
application-independence rules in ADR-0024; converting them into external
connector plugins would move product data synchronization into the wrong
control plane. SMTP becomes the Academy external-transport packet. The console
proxy and observability endpoints remain product-local and are not silently
treated as connector debt merely because they use HTTP or an SDK.

Acceptance: one complete seven-row production census; typed ERP API contracts
and named local reconcilers remain current; an SMTP capability, connector,
product port, descriptor, destination, secret-name mapping, mirror proof,
rollback and retirement gate are complete; and the three product-local
operational surfaces retain their authentication and no-growth canaries.

### Packet D — ERP non-financial seams

Group by external authority, not by the 21 files the ratchet happens to count:

- communications: FCM (`app/services/push.py`), Nextcloud Talk and Mailcow;
- identity/security: Cloudflare Turnstile; the pinned ERP source explicitly
  says its unshipped OIDC implementation was removed, so OIDC is not an
  Integrator migration surface;
- automation: generic service hooks;
- operations: object storage, OpenBao materialization and health probes;
- decision support: hosted LLM backends;
- internal application exchange: Sub, Academy and the retiring CRM;
- reference data: public exchange-rate observation.

For each group decide whether it is external transport, an internal typed app
port, or product-local infrastructure before authoring a connector. Do not
move Paystack, Mono, Remita, settlement observation, payment synchronization or
any billing consequence in this packet.

Acceptance: one owner and one typed port per surviving capability, no provider
branch in ERP, an exact connector distribution where transport is external,
and a survivor canary for every ERP integration left on the old path.

### Packet E — CRM retirement

CRM's category counts are not permission to recreate its 33 HTTP clients. Use
production traffic, scheduled-task and credential-name evidence to classify
each surface as:

- retire with CRM;
- move to an already-owned Sub/ERP/Academy product port; or
- retain temporarily behind a named exit gate.

Start with the provider clusters visible in `app/services/meta_*`,
`app/services/crm/inbox/**`, `app/services/chatwoot`, `genieacs`, `zabbix`,
Nextcloud, SMS and the ERP/ERPNext clients. A connector is built only when a
continuing product names the capability and destination.

Acceptance: zero-traffic proof per retired surface, no CRM-only plugin, and a
deletion/rollback window tied to the CRM retirement programme.

### Packet F — Backoffice and Vendor CP zero preservation

Both continuing applications currently measure zero. Keep them under the
ratchet. They publish provider-neutral product ports and application
descriptors as domain slices land, but never acquire provider credentials,
webhook verification, connector schedules, checkpoints or retry engines.

Acceptance: production-derived zero, no-growth canary and explicit staging
availability before either application's first binding is enabled.

### Packet G — financial integrations (held)

Paystack, Flutterwave, Mono, Remita, settlement observation and any route that
can change invoices, balances, allocations, payment status or collection state
remain on their current owners. Build inventories and contracts, but do not
switch transport while `financial_cutover_authorized` is false.

Acceptance later requires the separate billing/payment gates, finance approval,
reconciliation and rollback evidence. A communications cutover cannot open
this gate indirectly.

### Packet H — staging assembly and operations

The Integrator assembly owns exact released pins, ingress/egress routes,
secret-name resolution, destination installation and the receipt/delivery
pumps. Operations owns Seabone deployment, observability and rollback commands.

Acceptance:

- one immutable artifact is promoted; staging and later production never
  rebuild it;
- request-size, access-log redaction and credential non-escape proofs pass;
- each enabled binding has a destination and named product reconciler;
- disabled/unconfigured bindings fail closed without affecting other bindings;
- the old route remains independently restorable during the rollback window.

## Merge and cutover sequence

1. Land Packet A so every other change has a truthful scoreboard.
2. In parallel, complete B's staging evidence and the inventories/contracts in
   C, D, E and F. Packet G remains evidence-only.
3. Build connector distributions and product ports only after each capability
   name, owner and disposition are fixed.
4. Release the module/connectors once per coherent batch and exact-pin Packet H.
5. Deploy the exact artifact to Seabone with all bindings disabled or in mirror.
6. Rehearse one target capability; run billing and survivor canaries before and
   after; exercise rollback.
7. With an explicitly named production target, shadow and cut over that one
   binding. Observe through its rollback window before starting the next.
8. Retire product-local paths and lower the connector ratchet only with
   observed zero old-path traffic.
9. Remove Sub's shared control plane only after every application snapshot is
   complete, every row is migrated/retired, temporary retentions are closed,
   the tables are empty and all rollback windows have expired.

## Current Seabone consequence

Sub, ERP and CRM applications are running. Academy's application process is
not running, but its stopped staging database has now supplied an isolated,
exact-artifact clone rehearsal. Backoffice is absent, and no Vendor CP
application process was observed. Therefore:

- Sub WhatsApp receive may proceed to an isolated staging rehearsal once its
  three recorded blockers are cleared;
- Academy may claim candidate migration and census-shape evidence, but not a
  live binding rehearsal; Backoffice and Vendor CP cannot claim staging
  readiness;
- no fleet cutover or shared-plane retirement may be claimed.

The 2026-08-18 aggregate follow-up also found populated ERP and CRM integration
surfaces. ERP holds historical Paystack and sync evidence, so every
non-financial rehearsal needs a finance-state fingerprint and must leave its
payment routes and workers unchanged. CRM holds active provider configurations,
OAuth identities, SMTP, webhook deliveries and over 819,000 integration runs;
therefore CRM cannot be retired as one unit until each continuing capability
has moved and each retiring surface proves zero old-path traffic.
