# Changelog — dotmac-deployment-foundation

## 0.3.0a1 — unreleased, and HELD

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
