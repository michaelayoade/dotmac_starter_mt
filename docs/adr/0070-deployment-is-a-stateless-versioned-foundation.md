# ADR-0070: Deployment is a stateless versioned foundation, not a module

- Status: Accepted
- Date: 2026-08-26
- Deciders: Michael
- Supersedes: none
- Extends: ADR-0003 (composable deployment profiles — this ADR supplies the
  concrete facility ADR-0003 § "Infrastructure provisioning and deployment
  execution" declared but never built), ADR-0006 (module/assembly ownership and
  the product-first extraction amendment), ADR-0024 (applications are
  independent), ADR-0062 (modules own definitions, assemblies own exporters)
- Related: ADR-0007 (authenticated applied state), ADR-0009 (a secret is held,
  never dereferenced), ADR-0018 (a guard exemption states an enforceable
  premise), ADR-0023 (dual-plane modules), `packages/dotmac-deployment-control`
  (desired deployment state), `packages/dotmac-platform-health`

## Context

Four Dotmac repositories each carry their own deployment infrastructure, and
the same seven mechanisms are implemented four times with four different sets
of defects.

`dotmac_sub` has the most mature engine: an exclusive deployment lock, exact
OCI digest and source-revision verification, backup before DDL, a migration
lock with bounded retries, a warm candidate that is readiness-gated before
Nginx hands traffic over, per-worker verification and a bounded stability
window before the rollback decision. It also carries a 22-service Compose
topology with genuinely privileged network roles, a second divergent copy of
its Nginx configuration, and host-side script drift that has caused two
recorded staging incidents.

`dotmac_integrator` has the best image and migration-ordering contract: a
non-root, read-only, capability-dropped runtime; a one-shot migration service
that must COMPLETE before any runtime container starts; an owner role that is
absent from the runtime roles; an audited image (`scripts/audit_image.sh`) that
carries its own sensitivity proof; and deployment thresholds that live in
`deploy/alerts/` rather than in the process. It has no host deployment engine,
no backup, and no ingress at all, and its Compose file and runbook disagree
about whether a digest or a mutable tag is deployed.

`dotmac_erp` has production-used migration-role preflight, backup-before-
migrate and static-asset synchronization — and, in the same files, production
source/template/static bind mounts, a `/health` gate that cannot fail, one
credential used for both migration and online access, and development OpenBao
and CSP defaults that reach production.

`dotmac_starter_mt` is the reference assembly and has the thinnest of all four.

Copying the best of these into the other three would not establish a standard.
It would establish four forks of one standard, and the next security fix would
have to be applied four times by four people who would each apply it slightly
differently. That is the failure ADR-0006's build-once rule exists to prevent,
applied to a surface — build and deployment — that ADR-0006 never named because
it is not a business capability and has no rows.

## Decision

### 1. A fourth destination exists, and it is stateless

`dotmac-build-once-reusable-belongs-in-kernel` resolves reusable code to the
kernel, `dotmac-ui`, or a `dotmac-<domain>` module. Deployment infrastructure
fits none of them, and forcing it into one is what has kept it unbuilt:

- It is not kernel. The kernel is imported by a running application process.
  A build runner that renders a Compose file has no database, no ORM, no web
  framework and no tenant, and must not acquire them in order to validate a
  descriptor.
- It is not a module. A module has a `ModuleManifest`, a `mod_<code>` schema, a
  migration lineage and rows. This facility has none of those and must never
  gain them: the thing that decides how a deployment is built cannot be a
  tenant-scoped table inside one of the deployments it builds.
- It is not an assembly overlay. That is precisely the copying this ADR ends.

`dotmac-deployment-foundation` is therefore created as a **universal facility**
— the classification `packages/dotmac-kernel/EXTRACTION.toml` already uses, and
the one `scripts/check_allocation_serialized.py` already exempts from the
migration-ledger gate because such a package legitimately owns no lineage.

It is housed in the Starter's `packages/` tree, for the same reason
`dotmac-application-directory` is: the Starter is where distributions are built
and released, and housing is not composition. The assembly does not install it
into the application image, `alembic.ini` gains no lineage, and no application
process imports it.

Concretely, and enforced:

- no `ModuleManifest`, no models, no migrations, no `short_code`, no
  `migration_prefix`, no tenant or business state;
- zero runtime dependencies — not `dotmac-kernel`, not SQLAlchemy, not FastAPI,
  not Jinja, not a YAML library, not a templating engine. Standard library
  only, exactly as `dotmac-ui` is dependency-free and for the same reason: a
  build runner adopts the facility without adopting a runtime;
- rendering is explicit Python that emits text, not a template engine, so
  `render --check` compares bytes and a reviewer reads a diff;
- consumed by CI, build runners, provisioning workers and deployment hosts —
  never by an application domain service. Two import-linter contracts say so in
  both directions.

Any concrete Python implementation of a kernel provider protocol — a
`ProvisioningProvider` for a dedicated VM, for instance — is a **stateless
protocol adapter** and is classified as one. It is not an optional module.

### 2. The boundary, in four lines

- **`dotmac-kernel`** owns universal in-process contracts and mechanics:
  session factories and transaction boundaries, tenant/organization priming,
  conflict savepoints and public error contracts, product/module manifest
  contracts, migration graph orchestration, the standard liveness/readiness and
  telemetry *interfaces*, and the provisioning/profile protocols and their
  reusable conformance kits. It never renders infrastructure.
- **`dotmac-deployment-foundation`** owns build- and deploy-time execution:
  the versioned deployment descriptor, the renderer, the hardened image
  contract and its audit, the Compose baseline, the deployment state machine,
  backup/restore and their evidence, ingress providers, the telemetry
  collector profile and resource attributes, and the common infrastructure
  alert catalogue. It decides HOW, on one host, for one release.
- **`dotmac-deployment-control`** owns durable fleet intent: desired state,
  immutable plans, approvals, rollout decisions and attempts, authenticated
  acknowledgements and drift. It decides WHAT to deploy and to which target.
  It gains no Docker, Nginx, SSH, cloud-provider, migration, backup or
  monitoring implementation — its `EXTRACTION.toml` contract already says so
  and this ADR does not widen it.
- **The product assembly** owns declarative, product-specific input only:
  identity, manifest reference, process roles and commands, worker and queue
  topology, image-specific system packages, migration command, readiness
  dependencies, exposed domain ports, product preflight/postflight commands,
  product metrics and domain alert rules, and explicitly justified capability
  or resource exceptions.

`dotmac-platform-health` is untouched by this ADR. It may own authenticated,
normalized health *observations* and their projection. It owns no raw logs,
metrics or traces, no deployment decision, and no monitoring infrastructure.

### 3. Variation enters through a typed descriptor, never a branch

No shared execution path in the foundation contains `if product == "erp"`, a
product enum, a product-keyed behaviour dict, or a provider conditional. Every
difference between ERP, Sub, Integrator and Starter is a value in
`ProductDeploymentSpec.v1` or a declared extension point. This is ADR-0024 § 4
applied to infrastructure, and it is checked the same way: a static guard over
the foundation's own source, with a sensitivity proof.

### 4. The descriptor holds names, never secrets

`ProductDeploymentSpec.v1` declares *material names* and approved OpenBao
pointers. It may not contain a credential, token, DSN with embedded
credentials, private key or any other secret value — ADR-0009's rule, restated
for a file that is checked into a product repository and rendered into
configuration a host reads. The loader refuses such a value at parse time
rather than at review time, and that refusal carries a sensitivity proof.

### 5. Nginx is the first dedicated-VM ingress provider, not the architecture

Ingress is a replaceable provider. Nginx is the first one implemented because
Sub's warm-candidate handoff is the proven production behaviour and it is
Nginx-shaped. It owns no tenant, domain, TLS or business lifecycle decision:
it renders an upstream pair, a candidate handoff and the timeout/size envelope
the descriptor declares, installs the result atomically, verifies with
`nginx -t`, rolls back on failure and reports a config-digest drift.

Static Nginx serves a *known* set of hosts. Dynamic customer domains are a
different problem and are explicitly out of scope here: they need a
domain/DNS/TLS reconciler, and Caddy, Traefik, cert-manager or a managed load
balancer remain available as later providers for that profile. Host bootstrap
is a typed provider (Ansible or cloud-init); an imperative "apt install and
hand-edit" script is never retained as the authority.

### 6. Build once, promote a digest

One image is built once and the exact same OCI digest is promoted through
test, staging and production. No environment rebuilds. The deployment engine
verifies the digest and the source revision it was built from before it mutates
anything, and refuses a dirty or source-mounted production state outright.

### 7. The standard is enforced at three levels, or it is not a standard

A template that is copied is not a standard; it is a fork with good intentions.
Enforcement is:

1. **Build time** — one renderer, one image contract, one release workflow.
2. **CI time** — a reusable cross-repository conformance workflow pinned by
   immutable commit, required by the Governance profile in every product, plus
   sensitivity tests proving each detector fails when a temporary violation is
   introduced (ADR-0018), and two-directional ratchets for temporary
   deviations.
3. **Runtime** — `render --check` against the committed assets, image audits,
   disposable migration and boot tests, and a running drift check comparing the
   image digest, configuration digest and product-manifest digest with the
   approved deployment plan.

## Consequences

- ERP is the first full adopter and the highest-value one, because it holds the
  most defects. Starter becomes the minimal reference adopter. Integrator
  adopts the same descriptor and its Compose/runbook contradiction is corrected
  in the same change. Sub adopts last: its 22-service topology and privileged
  network roles need explicit, justified overlays, and its duplicated
  deployment and Nginx scripts are retired only after parity is proven, never
  before.
- Four repositories gain a `deploy/product.toml` and lose an engine. The
  engine's behaviour is extracted product-first from Sub, Integrator and ERP as
  rule 24 requires, with the defect list recorded in the dossier so that a
  reader can see what was deliberately not copied.
- A deployment defect is now fixed once and propagates through an exact version
  pin and an update pull request, exactly as ADR-0003 § "Cross-project reuse
  and release model" already requires for the kernel.
- **Observability is definition-only.** The facility renders a collector
  configuration and a 64-alert catalogue, and nothing consumes either: no
  collector is deployed, no metric is scraped, no rule is loaded, no annotation
  is emitted, and several catalogue alerts name metrics no Dotmac process
  produces. The catalogue is a SPECIFICATION of what must be emitted, not a
  monitoring system, and it must not be described as one — a directory of 64
  well-formed rules reads as 64 alerts to anyone who does not check. The
  sequence for making it real is in
  `docs/inventories/deployment-foundation-rehearsal.md`.
- The facility is unadopted until a product exact-pins a released version and
  its rendered assets pass `render --check` in that product's CI. Until then it
  is built and validated, and must not be described as in production
  (`AGENTS.md` rule 30).

### Amendment — 2026-08-31: database changes promote a declared result

A database-advancing operation carries a pre-authored descriptor transition:
target, plan digest, starting descriptor digest and result descriptor digest.
Authorization binds the result, plan and target; the starting digest is the
independently observed live-state and compare-and-swap precondition. A running
database is never used to author its descriptor.

The operation either commits in one database transaction or declares an
ordered descriptor for every durable checkpoint, including the final state. A
partially committing operation with only a final candidate is refused because
it leaves recovery with an undeclared intermediate state.

PostgreSQL and the accepted descriptor registry are separate transaction
domains, so their movement is not described as literally atomic. After the
database result is observed and before the accepted descriptor is promoted,
the transition is explicitly `promotion_pending`. Promotion is an idempotent,
durably recorded compare-and-swap from the starting digest to the result digest
keyed by transition id. Recovery re-drives that same operation and must recover
the original promotion event; observing that the pointer already equals the
result is not itself promotion evidence. A terminal receipt binds both
descriptors, the plan and target, the database postcondition, and the promotion
event.

The corresponding drift comparison runs in both directions at preflight,
postflight and recovery. A live fact missing from the descriptor is as much a
failure as a declared fact missing from the catalogue. Effective-privilege
coverage comes from an independently selected audit universe rather than from
the descriptor's invariants, because the omitted invariant is the Platform CP
incident shape. A report cannot expose a matched descriptor digest until the
whole selected universe was answered.

This amendment also records a boundary rather than hiding it.
`ProductDeploymentSpec.v1` is already published and is not redefined to accept
a new key. An independent `DatabaseDescriptorCatalogBindingV1` sidecar can bind
canonical database-catalogue schema/path/digest coordinates to its descriptor
digest. Each coordinate names either one complete MODULE schema or every
expected schema as a PRODUCT catalogue. A sidecar is not a descriptor fact and
can never enable v1's whole-descriptor matched digest.

The kernel module and product v1 schema identifiers are paired exactly with
MODULE and PRODUCT scope respectively; a future schema needs a new explicit
registration. A product coordinate binds catalogue product code and version to
the exact descriptor product and requires a decision reference when those codes
are intentionally aliases.

`ProductDeploymentSpec.v2` is the explicit successor rather than a silent v1
expansion. The v1 parser still refuses `database.catalogs` and its canonical
bytes omit the version-gated field. V2 requires at least one coordinate whenever
`[database]` exists, embeds it in the canonical descriptor, and may reach a
whole-descriptor match only after recognized witnesses cover every expected
schema.

Foundation owns neither catalogue grammar nor live collection. Structural
acceptance must invoke an integrated verifier over the held catalogue and
observation bytes, not trust a freely constructed result because its comparator
and observer strings look known. The result and `ObservedDatabaseState` bind
the same immutable observation identity and PostgreSQL major, report changed
attributes as both set-difference directions, and require explicit complete
schema, table and column extent. Foundation invokes that dependency-inverted
verifier with the held payloads and rechecks its factory-only result's contract
id, fact scope, digests, PostgreSQL major, declaration schema/scope/complete
schemas and product code/version before normalizing its typed facts.

Scoped evidence stays scoped. The Deployment Control catalogue can prove the
seven tables and 95 columns in `mod_deploy` without waiting for every module,
but that witness cannot expose the whole descriptor's matched digest while
`public` or another declared schema lacks structural coverage. Current
`CatalogEvidence` cannot create the witness because it carries no table or
column inventory. A product must compose the kernel-owned observer and pass the
matching module or product verifier into Foundation; a schema name or contract
id alone is not an adapter and proves nothing. Neither a schema name nor an
Alembic head is substituted for structural proof.

## What this ADR does not decide

- It does not authorize a production deployment, a host, or an SSH session.
- It does not name a target for any environment.
- It does not retire any product's existing deployment path. Retirement is a
  separate change per product, gated on proven parity.
