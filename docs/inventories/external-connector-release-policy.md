# Connector release and conformance policy

**As of:** 2026-08-15
**Policy:** [`.github/release-connectors.json`](../../.github/release-connectors.json)
**Gate:** [`scripts/release_connector.py`](../../scripts/release_connector.py)
**Guards:** `tests/architecture/test_connector_release_policy.py`,
`tests/architecture/test_declared_publication.py`
**Decision:** ADR-0024 §§ 6–7 (the Integrator is the sole external connector
control plane); ADR-0018 (a guard exemption states an enforceable premise);
AGENTS.md rules 15, 22, 25, 28

Read under the same two cautions as every file in this directory
([README](README.md)): facts go stale, and **a row in a release allowlist is not
permission to extract, adopt or trust anything**.

Companion to [`external-connector-sources.md`](external-connector-sources.md),
which measures the surface products must retire. This file governs the other
end: what a connector distribution must satisfy before the Integrator may
install it.

## 1. A connector is a third shape

| | Module | Stateless protocol adapter | **Connector plugin** |
|---|---|---|---|
| Governed by | `release-modules.json` | `release-adapters.json` | `release-connectors.json` |
| `EXTRACTION.toml` classification | `optional-module` etc. | `stateless-protocol-adapter` | **`stateless-protocol-adapter`** (same) |
| Owns rows | one `mod_*` schema + lineage | none | none — state lives in `mod_intg` |
| Reached by | composed into an assembly | a product **calls** it | **discovered** by the control plane |
| Lives at | `packages/*` | `packages/*` | `packages/dotmac-connector-*` (enforced) |
| Floors on | a published kernel | (none) | a published `dotmac-integration` |
| Proved by | namespace registration | public surface on installed bytes | SPI conformance on installed bytes |

### `connector-plugin` is a release profile, not a classification

A connector needs a distinct release **profile**, not a fourth extraction
classification. Its dossier declares `stateless-protocol-adapter` like any other
distribution a product does not install, because the four properties that
classification governs — no `ModuleManifest`, no lineage, no ledger allocation,
no persistence import — are exactly the four a connector has. Promoting
`connector-plugin` to a dossier classification would mean amending ADR-0006 and
the global validator to describe the same properties twice, and would put the
same package in two vocabularies at once.

**So the classification check does not separate this lane from the adapter
lane.** It is a floor they share. What separates them is the strictness in § 2,
none of which the adapter lane asks for. That claim is checkable rather than
asserted: `test_a_real_adapter_with_the_same_classification_is_still_refused`
drives the connector gate with `dotmac-auth-oidc` — a genuine
`stateless-protocol-adapter` whose dossier passes the classification check
exactly — and requires a refusal.

The distinction that earns a third file is the middle row. An adapter is a
library: it is verified by its public surface and nothing else in the fleet has
an opinion about it. A connector is *discovered* — it registers in the
`dotmac_integration.connectors` entry-point group and is loaded at boot by
`dotmac_integration.discovery.discover`, which is **fail-closed as a set**: one
malformed connector refuses the whole registry rather than silently offering the
rest. So a connector that conforms only alongside its neighbours is not
independently releasable, and the gate must establish that before publication
rather than after.

Making the adapter lane's fields optional was rejected for the reason that lane
already records about the module lane: optionality is not scoped to the package
that needs it. Once `spi_range` and `connector_key` may be absent, a connector
whose entry-point registration was dropped in a bad merge stops being refused
and starts being treated as an adapter — its conformance proof skipped rather
than failed.

## 2. What earns an allowlist entry

Five obligations. Each is checked by `scripts/release_connector.py`, and each
check has a sensitivity proof that plants the violation rather than asserting
clean input passes.

1. **Classification and location.** `EXTRACTION.toml` declares
   `stateless-protocol-adapter`, read from the package and never trusted from
   the allowlist — what stops the lane becoming a way to publish a *stateful*
   module while skipping the namespace, lineage and dual-plane gates. And
   `package_dir` must start with `packages/dotmac-connector-`: first-party
   connectors are built, tested, versioned and published from Starter
   `packages/` as independent distributions, and neither `dotmac-integration`
   nor the Integrator assembly may import one — discovery stays exclusively
   through package metadata. The prefix is enforced rather than conventional so
   a connector cannot be released from a directory whose name does not announce
   what it is. *Later third-party connectors may live in separate repositories
   under the same governance profile; this lane governs only the first-party
   packages Starter builds.*
2. **Discovery.** Exactly one entry point in `dotmac_integration.connectors`,
   and its key equals the `connector_key` the allowlist and the manifest
   declare. Two makes "which one failed" unanswerable at boot; zero is invisible
   to the control plane the distribution was built for; a mismatch surfaces only
   when two connectors collide in a live registry, where the winner depends on
   which wheel was installed second.
3. **Conformance.** `assert_plugin_conforms` passes against the **installed
   bytes**. It subsumes `assert_connector_conforms`, so metadata and
   executability are proved together — a distribution that declares a capability
   it cannot hand back a handler for passes every metadata check and fails at
   the first dispatch. The source tree and the wheel are different objects and
   only one of them gets published, so the static half (`conformance`) and the
   executable half (`verify-wheel`) both exist.
4. **An installable floor, EXECUTED.** `integration_floor` names the earliest
   `dotmac-integration` release whose SPI admits the connector, **and that
   release must be published**. See § 4 — this is the check with live teeth.

   Published was, until 2026-08-26, the *only* thing proved about it.
   `_refuse_unpublished_floor` established that a release tag exists; nothing
   installed it. Both smokes resolved `dotmac-integration` the ordinary way —
   the build job from the in-tree `packages/dotmac-integration`, the registry
   job by letting pip satisfy the connector's own `>=<floor>` — and pip takes
   the NEWEST release. So every run certified the current control plane twice
   and the declared floor zero times. The divergence was real, not theoretical:
   `dotmac-connector-linkedin` and `-mono` declare `0.1.0a11` and the other
   five declare `0.1.0a14`, while published `dotmac-integration` had reached
   `0.1.0a16`. A connector that would fail against its own stated minimum
   would have shipped green.

   The lane now runs conformance on **both legs, in both jobs**: the exact
   floor, built from its release tag before publication and pinned with
   `dotmac-integration==<floor>` on the registry install afterwards, and
   separately the current release. Both precede `git tag`, so a floor failure
   refuses the tag rather than being discovered after it. A declared floor is
   now a claim the lane executes, not one it merely reads.
5. **No secret shape, no persistence, no private retry/checkpoint engine.** A
   connector holds a *reference* to credential material (ADR-0024 § 7), never
   the value, and ships no migration lineage. It also may not carry its own
   delivery-retry or feed-checkpoint machinery: those are two of the six
   categories [`external-connector-sources.md`](external-connector-sources.md)
   is ratcheting *out* of products and into the control plane, and a connector
   rebuilding them locally moves the duplication rather than retiring it —
   invisibly, since connectors are not in the sweep's `RUNTIME_ROOTS`. The check
   is on *ownership*, not on the word: importing `dotmac_integration.retry` is
   correct and does not trip it; declaring a `retry.py` of your own does. The
   secret name-shape list is imported from the module lane rather than copied —
   two copies drift, silently and in the worst direction.

## 3. What an entry does **not** prove

Restated from the module catalogue because a connector is the shape most likely
to be misread as an endorsement of a provider integration.

- **Not that a version was published.** A row says the workflow *may* publish.
  Publication is tracked in
  [`declared-publication-baseline.json`](declared-publication-baseline.json),
  deliberately a separate file because it answers a separate question.
- **Not that anything adopted it.** Adoption is `EXTRACTION.toml`'s
  `contract_consumers`, which counts real cutovers. A release moves neither it
  nor `status`.
- **Not that the provider works.** Conformance is proved with no network and no
  credentials — deliberately, so an author's first experience of the SPI is not
  a secrets problem. A connector can conform perfectly and be misconfigured
  against a live provider; that is what the control plane's health and
  quarantine paths are for.

## 4. Declared but unpublished

`test_module_version_sync.py` proves a module's three version surfaces *agree*.
It cannot prove the agreed version *exists*. Three surfaces reading `0.1.0a2` in
unison say nothing about whether `0.1.0a2` was ever built, uploaded and
verified — internal consistency and publication are different questions, and
only the first one had a guard.

**Detection** is `scripts/declared_publication_sweep.py`. It reads every
`packages/*/pyproject.toml` (discovery, not enumeration — a new distribution
enrols by existing) and compares the declared version against the release tags.

**The oracle is a git tag**, because the release workflows write one only after
`verify-registry` has installed the exact published version from the private
index and registered its manifest. A tag is therefore this repository's own
assertion that a version is *installable*, which is stronger than "an upload
succeeded". Querying the index directly was rejected: it needs an authenticated
URL, which makes the check un-runnable at PR time and offline, and a gate that
cannot run is not a gate. The cost is stated rather than hidden — a publication
whose tag step failed reads here as unpublished, which is exactly the state
`recover-module-release.yml` exists to repair.

### The live state, 2026-08-15

| Distribution | Declares | Newest tag | State |
|---|---|---|---|
| `dotmac-integration` | `0.1.0a2` | `0.1.0a1` | **declared-unpublished** |
| `dotmac-imports` | `0.1.0a2` | — | never-published, *intended* (allowlist row removed) |
| `dotmac-auth-oidc` | `0.1.0a1` | — | never-published, *deliberate* |
| `dotmac-template-studio` | `0.2.0a3` | — | never-published, no lane |
| the other eight | — | = declared | published |

`dotmac-integration` is the programme's example and is recorded as **evidence**,
not repaired. `0.1.0a2` is the 2026-08-14 strictness fix; the only installable
release is `0.1.0a1`, which ships a `run_effect_once` that raises `TypeError` on
its first call. The release decision belongs to the release step — a guard that
"fixed" this by bumping a number would delete the evidence that decision needs.

`dotmac-imports` was the sharper case and is now **resolved — in the opposite
direction from the one the report suggested.** It was release-allowlisted with no
`dotmac-imports-v` tag in any version, so the allowlist read as a catalogue of
available modules. The anomaly turned out to be the *allowlist row*, not the
missing tag: the package's own `EXTRACTION.toml` `next_action` says to keep the
module audit-complete and unreleased until a real first adopter is ready, and to
publish just in time for that cutover.

**Ruling (2026-08-15): remove the row, do not publish.** The entry is gone from
`release-modules.json`, `release-module.yml` and `recover-module-release.yml`;
the package keeps its declared `0.1.0a2`, its kernel floor, its dossier and its
catalogue entry (which now reads `not allowlisted`). The row returns with ERP's
adoption proof — cutover 1, gated behind the E8 Organization-to-Tenant and
composed-lineage decision.

The general lesson is worth more than the case: **a declared-but-unpublished
finding does not imply the repair is a release.** Sometimes the wrong half is the
permission, not the artifact. The ledger therefore distinguishes *intended*
unpublished states (`dotmac-auth-oidc`, `dotmac-imports`) from *outstanding*
ones (`dotmac-integration`), and the reason text is where that difference lives —
which is why the guard demands a premise rather than a label.

### Two directions, both failures (ADR-0018)

- A distribution **entering** the unpublished state without a ledger row fails —
  that is a version silently promised to consumers.
- A row whose distribution has **since been published** also fails and must be
  removed in the same change as the release. A ledger that only grows stops
  describing anything.
- A **bump that outruns its row** fails too: a recorded excuse is about a
  specific version, and leaving the new version excused by a reason written for
  the old one is the drift that makes an exemption meaningless while it still
  looks reviewed.

## 5. This lane's link to the floor rule

`release-modules.json` already states the rule for kernels: *"A floor naming an
unpublished version cannot be resolved by an installer, so it is not a floor at
all."* Written down, but nowhere enforced. The connector gate enforces it, using
the same tag oracle: `resolve` refuses an `integration_floor` with no release
tag.

It bites today. The first connector may floor at `0.1.0a1` — inheriting a
published control plane with a known-broken public function — or wait for
`0.1.0a2` to be released. It may **not** floor at `0.1.0a2`, because nothing can
install it. A gate that allowed it would produce a wheel whose dependency
resolution fails for every consumer, discovered at install time by someone who
did not write it.

This is the concrete consequence of the a2/a1 gap, and it is why the gap is a
governance question rather than a version-bump question: **the connector
programme cannot ship a connector on the fixed control plane until the fixed
control plane is released.**

## 6. Rulings, 2026-08-15

| Question | Ruling | Where it lives now |
|---|---|---|
| Is `connector-plugin` a classification? | **No** — a release profile only. Dossiers stay `stateless-protocol-adapter`; ADR-0006 and the global validator are not amended. The stricter checks are what distinguish the lane. | § 1, `release-connectors.json` `conformance.classification` |
| `dotmac-imports` | **Remove the allowlist row, do not publish.** Its dossier gates release on ERP's first-adopter cutover; the row was the wrong half. | § 4, `release-modules.json` header |
| Connector workflow | **Land it now, shut**, per PR #180. Free-text input while the allowlist is empty; every dispatch fails in `resolve`; the input becomes an exact choice list on the first entry. | § 7 |
| Where connectors live | **Starter `packages/dotmac-connector-<provider>/`**, enforced by the gate. Third-party connectors may later live in their own repositories under the same profile. | § 2.1 |

`dotmac-integration` `0.1.0a2` remains as recorded unpublished evidence — not
published, not renumbered — and the floor guard stays exactly as strict: the
first connector must floor on a *verified tag*, which today means neither `a1`
by preference nor unpublished `a2` at all.

## 7. The workflow is not the lock

`.github/workflows/release-connector.yml` is merged and **shut**. The lock is the
empty `connectors` object in `release-connectors.json`; the workflow's existence
is not authorization and must not be read as any.

It lands early on purpose. Publish permissions, freshness checks and artifact
handling are a different review from the first provider implementation — different
reviewers, different failure modes — and bundling them means whichever is more
urgent carries the other through. The one that gets waved past in that trade is
the security sequence, the half nobody can see working until it is too late.

While the allowlist is empty the `connector` input is **free text**, because a
`workflow_dispatch` choice must offer at least one option and there is nothing to
offer. The enforced layer was never the input: it is `resolve`, re-run on the
publish side after the approval wait.
`test_the_allowlist_is_the_lock_and_every_dispatch_fails_today` drives the gate
with four plausible values — including two real packages — and requires all four
to be refused. When the first connector is authorized, the input becomes an exact
`choice` list matching the allowlist, enforced two-directionally so neither a
missing option nor a stale one can survive.
