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
| Owns rows | one `mod_*` schema + lineage | none | none — state lives in `mod_intg` |
| Reached by | composed into an assembly | a product **calls** it | **discovered** by the control plane |
| Floors on | a published kernel | (none) | a published `dotmac-integration` |
| Proved by | namespace registration | public surface on installed bytes | SPI conformance on installed bytes |

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

1. **Classification.** `EXTRACTION.toml` declares `connector-plugin`, read from
   the package and never trusted from the allowlist. This is what stops the lane
   becoming a way to publish a stateful module while skipping the namespace,
   lineage and dual-plane gates the module lane performs.
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
4. **An installable floor.** `integration_floor` names the earliest
   `dotmac-integration` release whose SPI admits the connector, **and that
   release must be published**. See § 4 — this is the check with live teeth.
5. **No secret shape, no persistence.** A connector holds a *reference* to
   credential material (ADR-0024 § 7), never the value, and ships no migration
   lineage. The name-shape list is imported from the module lane rather than
   copied: two copies drift, silently and in the worst direction.

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
| `dotmac-imports` | `0.1.0a2` | — | **never-published** |
| `dotmac-auth-oidc` | `0.1.0a1` | — | never-published, *deliberate* |
| `dotmac-template-studio` | `0.2.0a3` | — | never-published, no lane |
| the other eight | — | = declared | published |

`dotmac-integration` is the programme's example and is recorded as **evidence**,
not repaired. `0.1.0a2` is the 2026-08-14 strictness fix; the only installable
release is `0.1.0a1`, which ships a `run_effect_once` that raises `TypeError` on
its first call. The release decision belongs to the release step — a guard that
"fixed" this by bumping a number would delete the evidence that decision needs.

`dotmac-imports` is the sharper case and was previously invisible: it is
release-allowlisted, so the allowlist reads as a catalogue of available modules,
while **no `dotmac-imports-v` tag has ever been written in any version**. Its own
changelog says `0.1.0a1 — unreleased`. See § 6.

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

## 6. Open — needs a ruling

1. **`dotmac-imports` has never been published.** It is release-allowlisted with
   `kernel_floor` `0.1.0a56` and has no tag in any version, so the ADR-0025
   import-run ledger is installable by nobody and no assembly can pin it. Either
   dispatch a release run or move the entry out of the allowlist. Leaving it is
   the state that makes an allowlist row read as availability.
2. **`dotmac-integration` 0.1.0a2.** Recorded, not fixed, per this task's
   instruction. It blocks any connector that needs the fix, per § 5.
3. **Does the connector lane get its own workflow?** There is deliberately none
   while `connectors` is empty: a lane whose gate exists and whose workflow does
   not cannot publish at all, which is a stronger closure than an empty
   allowlist behind a live workflow. The guard is two-directional — a workflow
   becomes *required* the moment an entry is added — so opening the lane is one
   complete, reviewable diff. The alternative (mirror `release-adapter.yml` now,
   shut) matches PR #180's precedent; this file takes the other option and the
   choice is worth confirming.
4. **Where do connector distributions live?** Nothing here assumes they are in
   `packages/`. If they are separate repositories, the classification and
   conformance checks move with them and this lane governs only the ones this
   repository builds.
