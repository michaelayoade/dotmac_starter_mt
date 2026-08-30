# Changelog — dotmac-deployment-foundation

## 0.3.0a1 — unreleased, and HELD

`PostgresRecoveryBundle.v1` — the artefact a database can actually come back
from, and the reason `Assurance.PROVED` is now reachable at all.

`pg_dump --dbname` captures GRANTs and RLS policies and never captures the roles
they name. A `pg_restore` of Vendor CP's newest production backup into a
disposable PostgreSQL 16 container exited 1 with 114 missing-role errors from a
TOC holding 55 ACL entries, 26 POLICY entries and zero role objects — and left
45 tables, 23 of 26 policies and 16 RLS-enabled tables behind. Under ADR-0023
the revocation IS the plane isolation, so that database reads as recovered and
is not.

`recovery.py` adds the bundle: thirteen required components, each with a digest
and a statement of what it covers — dump, role and membership closure DERIVED
from the source catalog, role attributes and PostgreSQL 16 per-membership
INHERIT, ownership, default/schema/object privileges, row- and column-level ACL
evidence, RLS ENABLE and FORCE, extensions, an explicit tablespace decision,
migration heads. `load_manifest` refuses a database-only dump on the artefact's
shape, before a target exists; that refusal catches a plausible WHOLE, which is
the shape that fools an operator.

No passwords, no hashes, no superusers: `RoleFact` has no field a verifier could
occupy and refuses `superuser`. `pg_dumpall --globals-only` is named as the
wrong flag — it emits SCRAM verifiers — and `--no-role-passwords` as the right
one.

`[database]` in the descriptor declares typed roles, expected schemas and
isolation invariants. A declaration is a CLAIM: nothing turns it into role DDL,
`restore_plan` refuses a bundle with no role closure even when the descriptor
names every role, and an AST guard proves the package contains no role DDL
anywhere.

`EffectivePrivilegeFact` — isolation is proven with `has_table_privilege` OR
`has_any_column_privilege` across all seven table privileges, in both
directions. `information_schema.table_privileges` sees only direct grants and
reports "fully revoked" for a role holding the privilege through PUBLIC, an
inherited membership, or a column.

`classify_invariant_breaches` — a rehearsal is a DRIFT DETECTOR as well as a
recovery proof. A restored copy violating a declared invariant has either been
restored unfaithfully or restored perfectly from a production database that is
already wrong, and those have opposite remedies. Comparing the restored copy
against the source catalogue separates them, which is nearly free because a
verification already holds both: a breach in the restored copy only is a
RESTORE DEFECT, a breach in both is SOURCE DRIFT. Both still fail the proof —
the label changes where the operator looks, never whether the receipt is PROVED,
because otherwise the cheap repair is to relax the bundle until it passes.
Measured instance: a Platform CP rehearsal found `platform_api` holding DELETE
on a delivery-target table, and production had the same permission — real drift,
a required revocation whose migration has never run.

Counts (`roles`, `privileges`, `policies`, `rls_tables`, …) are recorded in the
manifest as OBSERVATIONS and gated on by nothing. A grant matrix is a good
invariant and a poor assertion: `app_admin 315 / app_user 62 / platform_api 164`
changes with every migration, so the gate is the property, not the total.

`RESTORE_PROCEDURE` — ten ordered steps. `adjudicate_restore` DESTROYS a target
after any non-zero restore, and after a zero exit carrying missing-role errors.
`RecoveryReceiptV1` is value-free and carries the restore wall clock.

**Behaviour change.** `providers/compose_host.py` refuses its own historical
`("--no-owner", "--no-privileges")` default for any product declaring
`[database]`; that default gave every adopting product a dump with no ownership
and no grants in it. `BackupRecord` gains `artefact_class`, defaulting to
`data_export`, and a `data_export` may not claim RESTORABLE or PROVED.
`retention_keep` now also keeps an aged data export until a newer PROVED bundle
exists.

### Also in 0.3.0a1

`DeploymentIdentity.v1` — every rendered Compose service now carries
`io.dotmac.deployment.*` labels: the product, the environment, the compose
service name and its KIND (`release` | `migration` | `dependency` |
`telemetry`), plus — on the services that run the product's own image — the
manifest digest, the configuration digest and the source revision.

A controller that cannot read a release's identity off the running object has
to infer it, and the two things available to infer from are both wrong. An
image TAG is mutable, which is the reason every other reference in this
facility is a digest. A compose PROJECT name is an operator's `-p` flag or the
directory the file happens to sit in. Neither is an authorized identity, and a
controller that reconciles against an inferred one reconciles the wrong release
confidently.

The configuration digest is the CANONICAL DESCRIPTOR DOCUMENT's digest — the
same value `dotmac-deployment-control` binds an approval to — so a controller
compares the label to an approved plan digest with `==` rather than translating
between two spellings of one value. It is taken over the descriptor as actually
rendered, image override included, so a rollback restamps it and a rollback to
a previously approved image reproduces that image's approved digest exactly. It
deliberately is NOT a digest of the rendered compose file: the label lives
inside the file it would describe.

Consequence worth knowing: the canonical document carries the exact facility
version, so a Foundation upgrade changes the configuration digest and therefore
the rendered bytes, and `render --check` fails until the assets are
re-rendered. That is intended — the version is in the digest precisely because
a renderer upgrade can change what a descriptor word MEANS.

`build_canonical_document` gains `refuse_resolved_material=False` for that one
caller. The flag changes no byte of the document — the refusal only ever
raises — but the refusal is a boundary check about what may be SENT to Control
rather than a canonicalization rule, and a descriptor it trips (an in-container
`uvicorn --host 0.0.0.0` is enough) must still be renderable. A container with
no identity is strictly worse than a descriptor Control would want edited.

Auxiliary services are labelled but NOT stamped with the release: a Redis or a
vendor collector is recreated on its own schedule and takes no part in the
release, so making one answer "yes" to "are you running release X" would be a
confident wrong answer rather than a missing one. The migration service IS
stamped — it runs the product image at this revision and is the release's first
effect on the database.

`IngressPolicy.v1` — the typed exposure contract, and the non-mutating
projection that makes an exposure authorizable.

`PortPublication` gains mandatory `exposure` (`none` | `loopback` | `private` |
`public`) and mandatory `address_family` (`ipv4` | `ipv6` | `dual_stack`). The
free-form `bind` is REMOVED and declaring it is fatal; the bind address is
DERIVED from the two declarations, so a loopback publication renders an explicit
`127.0.0.1` and/or `::1` while anything routable renders a required, no-default
promotion-time variable. `none` emits no publication at all. Compose renders in
LONG SYNTAX for roles and managed dependencies alike, one entry per declared
family, so `host_ip` is a field that cannot be omitted rather than a string
position that can.

`[ingress]` declares its own `exposure` and `address_family`, because an edge is
where a `public` exposure legitimately lives; a role may no longer also publish
a port its edge already routes to. Source policy is a NAMED source set that
`dotmac-deployment-control` resolves at authorization — no product IP literal is
accepted anywhere, `trusted_proxies` included. A provider capability matrix
fails a publication closed when it claims a control no available provider
enforces.

`ProductDeploymentSpec.to_canonical_document()` returns
`DeploymentDescriptorDocument.v1`, and `canonical_bytes()` / `sha256_digest()`
belong to that DOCUMENT rather than to the spec or a renderer — so there is one
answer to "what was signed" and a caller cannot reach the digest without
holding the bytes it was taken over. It is the missing hop between a parsed
descriptor and `dotmac-deployment-control`'s desired specification: the facility
previously had no canonical document at all, only digests of rendered bytes, so
no descriptor fact was inside any plan digest.

The document carries schema identity, the exact facility version, every default
materialized, the service roster and roles, exact image references, the ingress
and exposure policy, and the migration, backup, handoff and rollback
requirements. It EXCLUDES resolved endpoints, IP addresses, credential bindings
and secret values — Control binds this digest into an independently signed
authorization and resolves the private material separately, so a resolved
address reaching the digest would collapse the two owners into one. The
exclusion is enforced over the finished document, with a planted-address proof.

The descriptor half is derived by walking `dataclasses.fields` rather than by a
hand-written serializer, because a hand-written one is a field allow-list: the
next field somebody adds stays out of the digest silently. `build_edge_plan()`
and `build_firewall_plan()` are provider-neutral; the firewall plan is derived
defense-in-depth and never a substitute for a correct socket binding.
`dotmac-deploy ingress-policy` prints all of it and mutates nothing.

Three measured facts are encoded rather than described. An `ip6tables`
`DOCKER-USER` rule for a published port is INERT — that chain is jumped only
from `FORWARD` while an IPv6 publish terminates on `INPUT` in `docker-proxy` —
so rules derive into `INPUT` on IPv6 and `DOCKER-USER` on IPv4, and emitting v6
into `DOCKER-USER` is refused. The IPv4 rule matches `--ctorigdstport` because
the packet there is already DNATed and its `--dport` is the container port. And
every allowlist ends in a terminal DROP, because one whose last rule is an
ACCEPT enforces nothing.

### Lane 3 is a runner and a receipt, not a fixture and a table

`scripts/exposure-rehearsal/` held a descriptor and some recorded bytes that no
test and no workflow consumed, and the sixteen gate items lived in a
hand-maintained markdown table whose header once read "14 of 16 CLOSED" while
its own rows recorded four `partial` and one `n/a`.

`scripts/exposure_rehearsal_runner.py` executes the lane through
`ExposureTransaction` over `ComposeHostExposureEffects` — snapshot, plan, apply,
re-observation, external probes, rollback, exact restoration comparison — and
emits `RehearsalReceipt.v1`. Every input is required and none has a default: the
protected-main revision, the wheel candidate digest, the Platform CP
authorization run, the signed authorization document, the controller identity,
the lease, the probe identity and the exact fixture. A rehearsal missing one is
not a partial rehearsal, it is a different activity.

`require_rehearsal.py` now reads **Lane 3**, not Lane 2. Lane 2 proves a real
engine, database, handoff and restore loop; it never watches an IPv6 socket
refuse the internet, which is what this release is named after. Two oracles,
because they fail differently: the Actions API says a run succeeded on this
exact SHA, and the receipt says what it established. A green run whose receipt
is full of `blocked` rows is exactly what the pair catches.

**Only `executed_passed` satisfies publication.** `hand_measured` and `vacuous`
are their own statuses because the old count folded them into "closed". The
status document is now GENERATED (`make rehearsal-status-check`), so a heading
cannot contradict its own table.

### The fixture can now fail the checks it feeds

The old one published only `loopback` and `none`, so `build_firewall_plan`
returned EMPTY and gate item 6 passed without observing anything. The new one
adds a `private`, dual-stack, source-scoped publication REMAPPED from 19001 to
8080 — the only shape that proves original-destination matching, because with
host == container a wrong `--dport` rule and a correct `--ctorigdstport` rule are
indistinguishable. It derives four rules: `--ctorigdstport` in `DOCKER-USER` on
IPv4, `--dport` in `INPUT` on IPv6, each ending in a terminal DROP.

### `ObservedProxy` carries the pid

Gate item 5 is "the `docker-proxy` pid is NEW" — a surviving pid means the
container was never recreated and the apply proved nothing. The parser discarded
the pid entirely, so that item could only ever be closed by a human reading `ps`
output. It is now captured (both `ps -eo pid,args` and `ps aux` shapes), and
`None` when the listing carried no pid column, so a caller must refuse rather
than compare sentinels.

### A typed `Digest`, and `HostLease.v1`

`Digest` replaces raw string comparison: bare and prefixed spellings parse to one
value, uppercase is accepted on input and lowercased on output, and unknown
algorithms, wrong lengths and malformed values are refused.
`require_same_digest` takes a NAMED mapping so a refusal says which term
disagreed, and refuses fewer than two terms so a three-term gate cannot be
weakened by passing fewer.

`HostLease.v1` records exclusive use of a shared target. Measured 2026-08-30,
the rehearsal host had no lease mechanism at all while eleven agents' worktrees
shared it. A lease **cannot be self-granted**: `authorization_run_id` is
mandatory, and a holder that writes its own lease has proved only that it can
write a file.

### `VantageQualification` — positive proofs, not an absent NIC

The external vantage's second NIC is gone, which removed the risk AND removed
the discrimination control that depended on it. A check that stops
discriminating because the thing it discriminated against was removed has been
lost, not passed. Qualification is now seven positive proofs, and the last —
that the TARGET observed the expected source address — is the only one a vantage
cannot fake about itself.

### `DeploymentProvenance.v1` — binding what ran to what was authorized

`build_provenance()` binds six facts into one canonical, digest-bearing record:
the descriptor digest, one sha256 per rendered asset (the Compose hashes among
them), the per-role image digest, the source revision, the service roster, and
the authorization receipt.

**Digests, never tags.** An image reference with no `@sha256:` is refused: a tag
is a mutable pointer that can be moved to different bytes after the approval and
before the deploy, and the record would still read as true. A branch name or an
abbreviated commit is refused for the same reason — neither identifies the
source months later.

**The receipt is bound by VALUE, never by import.** `dotmac-deployment-control`
owns plans, approvals, attempts and receipts; this facility owns rendering and
execution and declares ZERO runtime dependencies. So `AuthorizationReceipt` is a
typed input the caller constructs from Control — the same shape as `ProbeResult`
and `ProbeVantage` — and `provenance.py` imports nothing outside the standard
library, which is asserted from its own AST.

What the module does with it is a pure equality check: the digest the receipt
cites must equal the digest of the descriptor in hand. That is deliberately NOT
an authorization decision. Foundation never judges whether an approval should
have been granted; it refuses to execute something OTHER than what was
authorized, which is its own business. A structurally incomplete receipt is
refused as a malformed input; an unfavourable one is not evaluated at all.

`normalize_digest()` resolves a real trap between the two owners: Control's
`plan_digest` is BARE hex and this facility emits the `sha256:`-prefixed form,
compared with a raw `!=`. Unnormalized, a mismatch reads as a security refusal
while actually being a formatting bug — the kind of alarm that gets suppressed
rather than investigated. Both sides are normalized before comparison.

### The preservation property moved to the TRANSACTION

`OWNERSHIP_PREFIX` and `ownership_comment()` moved from
`providers/exposure_host` to `exposure`, and `ExposureTransaction` now MEASURES
what the provider previously only promised. Before applying it records the rules
in the shared chains it does not own; after the apply, and again after any
rollback, it looks and refuses if one of them has vanished.

The asymmetry is deliberate. A foreign rule that VANISHED was deleted by us —
the data-loss bug wearing the word *restore*, and exactly what replaying a
captured chain does. A foreign rule that APPEARED was written by somebody else
while we held the lock, and refusing on it would reject the correct behaviour of
preserving a rule that arrived mid-transaction. An unlabelled rule on a port we
publish is treated as ours rather than as unrelated host state, because calling
it foreign would make a correct rollback of our own leftover look like data loss.

This is a guarantee of the SEAM, not of one implementation: a second provider,
or a regression in this one, cannot quietly lose it.

### A real `ExposureEffects`, and the preservation property

`providers/exposure_host.ComposeHostExposureEffects` implements
`ExposureEffects` against a host, so apply, snapshot, re-observation and
rollback run through `ExposureTransaction` rather than through an operator's
hands. `dotmac-deploy exposure-apply` is the entry point, DRY RUN unless
`--execute`.

**A rollback restores what that transaction changed and nothing else.** The
obvious implementation — snapshot the chain, flush it, replay the snapshot —
destroys any rule another process added while the transaction was running, and
both chains this facility writes into (`DOCKER-USER` on IPv4, `INPUT` on IPv6)
are shared with everything else on the host. On a host carrying other work
that is a data-loss bug wearing the word *restore*.

So nothing is ever flushed. Every inserted rule carries
`-m comment --comment dotmac-exposure:<product>`, every removal is a targeted
`-D` of a rule bearing it, and a rule without it is never touched — whenever it
appeared. Ownership lives in the rule rather than in a diff against a snapshot,
because a diff cannot tell "someone else added this" from "we failed to record
adding this", and those need opposite handling. Deletes replay the rule's own
arguments, never an index, because an index shifts the moment anything else in
the chain changes.

The port match is re-derived from `FirewallRule.render()` rather than
re-implemented, so the rule the provider inserts cannot drift from the rule the
plan describes — the drift that put a `--dport` on a remapped publish.

Proven, not intended: a foreign rule is planted and asserted to survive,
including one that appears MID-TRANSACTION, and the suite is checked against
two sabotaged implementations (flush-and-replay, and index-based deletes) to
confirm it goes red for both.

### Execution and proof

`exposure.py` applies the plan and then goes and looks. `ExposureTransaction`
takes the product's exclusive deployment lock, SNAPSHOTS the host, applies,
RE-OBSERVES and verifies — rolling back to the observed snapshot on any
refusal, because a rollback that restores only what it thinks it changed cannot
repair what it did not notice changing.

`verify_exposure()` compares declared bindings against real sockets,
`docker-proxy` processes and firewall chains, in both directions: a declared
family that is not bound is `socket_missing`, and a bound port the descriptor
never mentions is `undeclared_socket` — the port that stays open is the one
nothing declares. `dotmac-deploy exposure-verify` runs the same verifier over
RECORDED command output, so an incident's pasted `ss` and `iptables-save` can be
replayed months later with no host present.

Three refusals encode what a naive check misses. `refuse_non_recreating_apply`
refuses an apply that cannot change a binding, because `docker compose restart`
reuses the container it already has and a plain `up -d` will not recreate an
unchanged image — a correct Compose diff plus a restart leaves the OLD binding
live and the NEW one believed. `conclude_binding` returns INCONCLUSIVE rather
than "closed" when the only evidence is an external probe against a host that
silently DROPs, because loopback-bound and wildcard-bound-and-dropped are
indistinguishable from outside. And `accept_public_exposure_evidence` refuses a
probe whose vantage is inside — or is not KNOWN to be outside — a source set
the plan accepts: on 2026-08-29 two agents independently connected to "public"
ports from a workstation sitting inside this fleet's own allowlisted range, and
each escalated a P0 that did not exist.

MAJOR-shaped, released as a pre-release, and the warning-phase deviation is
recorded with its four premises in `COMPATIBILITY.md`. **Publication is HELD**
while OpenBao containment and credential rotation settle — see
`docs/inventories/declared-publication-baseline.json`.

## 0.2.0a2 — unreleased

Make the strict image-audit filesystem collector run as an inspection-only
uid/gid 0 while continuing to validate the image's configured runtime
`Config.User` as numeric and non-root. A failed filesystem walk now refuses
the gate explicitly and preserves its partial output and diagnostics instead
of truncating the listing to empty evidence. Add executable negative controls
and one planted failure for every hardened-image rule.

## 0.2.0a1 — 2026-08-28

Normalize the Nginx renderer to exactly one trailing newline. The first ERP
adopter proved that `end-of-file-fixer` rewrites the 0.1 output while
`render --check` requires those original bytes, making the consumer's two gates
mutually exclusive. This is a minor release because rendered bytes are public
contract even when the behavioral configuration is unchanged.

## 0.1.0a1 — 2026-08-28

First cut. `ProductDeploymentSpec.v1`, the deterministic renderers, the
hardened image contract and its audit, the deployment state machine as data
with its executor, backup/restore assurance levels, drift comparison, the
64-alert common catalogue, the telemetry resource-attribute stamp, the
conformance kit, and the `dotmac-deploy` CLI.

Extracted product-first from three qualifying sources — `dotmac_sub`'s
deployment state machine, `dotmac_integrator`'s image and migration-ordering
contract, `dotmac_erp`'s migration-role preflight and backup-before-migrate —
with eighteen defects recorded as deliberate non-goals in `EXTRACTION.toml` and
the inventory.

Published through the protected facility lane after an exact-main disposable
host rehearsal completed with 34 passes, zero failures and zero skips.
