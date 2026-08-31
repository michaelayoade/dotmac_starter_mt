# Release record — `dotmac-kernel` 0.1.0a99 and `dotmac-ui` 0.1.0a8

As-of **2026-08-31**, taken at source revision
`8c92943062dbfe7f17bfa35264243709ec3d92c3` (protected `main`).

This record states that two artifacts were **published**. It makes no claim that
any product consumes them. Publication and adoption are different questions and
only the first one is settled here — see AGENTS.md rule 22 ("a pin is
installation, not adoption") and Governance ADR 0013 (a release claim needs an
oracle carrying immutable coordinates).

## What was published

Both releases ran from the same protected-main revision. Neither lane rebuilds
inside `publish`: the bytes `build` inspected and smoked are the bytes uploaded,
proven by an artifact-bundle digest that is identical on upload and download.

| | `dotmac-kernel` | `dotmac-ui` |
|---|---|---|
| Version | `0.1.0a99` | `0.1.0a8` |
| Source revision | `8c92943062dbfe7f17bfa35264243709ec3d92c3` | same |
| Tag (annotated) | `dotmac-kernel-v0.1.0a99` | `dotmac-ui-v0.1.0a8` |
| Tag peels to | `8c92943062dbfe7f17bfa35264243709ec3d92c3` | same |
| Release run | `33361078471` | `33361250324` |
| Jobs | build, publish, verify — all `success` | build, publish, verify — all `success` |
| Artifact-bundle sha256 (build → publish, identical) | `170a8cfa41ed45756dbfda02a7cf3cf1ec4ad7c5d368fdfdb20df106974711d0` | `1997fe0ca07c058056cefc6ef8a01d4355c4c9c95d678e251b4b2ae08b8abd24` |
| Files uploaded | `dotmac_kernel-0.1.0a99-py3-none-any.whl`, `dotmac_kernel-0.1.0a99.tar.gz` | `dotmac_ui-0.1.0a8-py3-none-any.whl`, `dotmac_ui-0.1.0a8.tar.gz` |
| Registry | Forgejo private index, `registry.dotmac.io/api/packages/dotmac/pypi` | same |

**Recorded gap, not an omission:** neither lane prints a per-file sha256 for the
wheel and sdist individually. The byte chain is proven at bundle granularity
(upload digest == download digest) plus a registry read-back and an install. A
future lane change should emit per-file digests so a consumer can pin one.

## Registry read-back and clean install

Each lane's `verify` job is a separate job that uploads nothing. It polls the
Forgejo simple index for the version, installs **from the index** into a clean
virtualenv, and re-runs the proof there.

- **kernel** — `scripts/consumer_boot_check.sh --from-registry "0.1.0a99"`, after
  the same script ran in `build` against the local wheel.
- **dotmac-ui** — `available on the Forgejo index`, then a clean-venv install and
  `scripts/verify_ui_release_artifact.py`, with the resolved version required to
  equal the dispatched one.

## Installed-artifact canary — what it actually measured

The `dotmac-ui` proof refuses to measure a source checkout: it asserts
`dotmac_ui.__file__` resolves inside the running interpreter's own `sysconfig`
`purelib`/`platlib`. Both stages named their subject explicitly:

```
build  (freshly built wheel)
  verifying INSTALLED dotmac-ui 0.1.0a8 at /tmp/uismoke/lib/python3.12/site-packages/dotmac_ui
verify (installed back FROM the registry)
  verifying INSTALLED dotmac-ui 0.1.0a8 at /tmp/uiverify/lib/python3.12/site-packages/dotmac_ui
  packaged asset OK  dotmac-ui-1.css
  packaged asset OK  tailwind-preset.js
  manifest OK        manifest.json for 0.1.0a8
  component OK       empty_state (1 render(s), 7 classes)
  component OK       map_frame (4 render(s), 12 classes)
dotmac-ui 0.1.0a8 release artifact proof PASSED
```

The component set is READ from the installed `dotmac_ui.COMPONENTS`, never
enumerated in the workflow, so `map_frame` is covered by construction. Its
markup was checked for its exact declared class set across all four states
(ready/loading/empty/error), not merely for rendering non-empty.

### Why this record can be trusted

`0.1.0a7` was published, verified and tagged while exposing only `EMPTY_STATE`,
because the release proof enumerated its subject by hand and executed for the
first time *during* the irreversible publish. That is now closed structurally
rather than by discipline: the `ui-artifact` job runs the **same** proof script
on every pull request, against a wheel installed into a clean virtualenv. The
evidence above is therefore a re-run of a proof already exercised pre-merge, not
a first execution at the point of no return.

`0.1.0a8` is a NEW version, not a re-publication of `a7`. `map_frame` reached
`main` after `a7` was tagged, so the tree and the published `a7` artifact
disagreed. Editing a published version would make one version name two
contracts (AGENTS.md rule 34).

## What this record does NOT establish

- **No adoption.** No product pins `dotmac-kernel==0.1.0a99` or
  `dotmac-ui==0.1.0a8` as a result of this record, and none is modified or
  counted as a consumer. Sub's contract-v2 facet and ERP's follow later and
  independently, each needing its own composition or cutover evidence.
- **No retirement.** Nothing here retires an executor; `retired_total` remains
  `0` and `executor-retirement-baseline.json` is untouched.
- **No Foundation movement.** The frozen `0.3.0a2` candidate is unaffected:
  source `e930f878…`, artifact `9740182233`, the canonical descriptor digest,
  the three rendered-asset digests and the eight `io.dotmac.deployment.*` label
  keys were all verified unchanged on each rebased tree and on merged `main`.

## Ledger effect

`docs/inventories/declared-publication-baseline.json` loses its `dotmac-ui` row
in this change. The sweep's oracle is a git tag, `dotmac-ui-v0.1.0a8` now
exists, so the row would otherwise assert an unpublished state the oracle
contradicts.

The `dotmac-kernel` row was already removed by PR #536 (`46239044`), which
recorded the a99 publication in the ledger but recorded no coordinates. This
document supplies them, so kernel a99 appears here for evidence rather than for
a second ledger edit.
