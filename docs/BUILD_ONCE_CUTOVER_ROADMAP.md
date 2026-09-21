# Build-once cutover roadmap: Foundation → Platform CP → ERP → Sub

- **Status:** Draft execution roadmap, 2026-09-20. Michael Ayoade owns go/no-go
  decisions. This document grants no release, host, secret, migration, production
  deployment, or risk-acceptance authority.
- **Baseline:** `dotmac_starter_mt` `d346d94f474088bdaf8b860809a3c425e9410543`;
  Platform CP PR #187 `a514c41c8552301b69a486ef231273e38719ed76`;
  local CP ADR-0017 amendment `2fead5f` (not pushed). Refresh these coordinates
  before acting. Repository documents do not establish current host state.
- **Owning contracts:** Starter [ADR-0070](adr/0070-deployment-is-a-stateless-versioned-foundation.md),
  `AGENTS.md` rules 24, 30, 31, 32 and 41; Platform CP ADR-0013 and ADR-0017;
  Governance ADR-0014 § 6. This roadmap orders work; it does not amend those
  contracts. A conflict is a stop requiring an owning ADR amendment.

**Execution decision:** close the issuer contract and source/runner readiness
(gates 0–1) now, then build one successor and rehearse it before publication.
Do not deploy the currently refusing CP issuer, repin #187 for appearance, or
allocate another candidate while its proof path is not ready. The next review
should ask which gate's refusal was removed and where its evidence lives.

## Outcome, not activity

The first programme cutover is **not** a Foundation pin, a green render, an
issuer deployment, a published wheel, or one signed receipt. It is Platform CP
executing a deployment and a redeployment through the admitted Foundation path,
proving recovery and the authorized read-backs, then retiring or positively
fencing its former deployment executor with a product retirement receipt. The
two runs must be controller-owned and distinguishable; a rehearsal against a
disposable target is a publication gate, not that production adoption.

After the CP control-plane path is proved, ERP takes the first full data-plane
adoption slice, then Sub. Each product owns its own exact pin, retained assets,
rehearsal, switch and old-writer retirement. No neighbour's receipt proves its
adoption. ERP and Sub do not wait to inventory their sources, but neither may
claim a cutover from CP's evidence.

The original ADR-0070 consequence calls **ERP the first full adopter**. The
current programme queues CP first. This roadmap interprets CP as the required
control-plane bootstrap and first executor cutover, and ERP as the first full
data-plane adopter. Before anyone describes CP as the first *full* adopter,
Michael must settle that wording in ADR-0070; this roadmap cannot silently
reverse an accepted ADR.

## Measured starting line

| Surface | Evidence at the baseline | Consequence |
| --- | --- | --- |
| Foundation candidate | `pyproject.toml` declares `0.4.0a1`; `tests/architecture/candidate_window_baseline.json` records artifact `9954731961` as `unrecorded` and `drifted`. No successor is allocated. | `0.4.0a1` is spent. Never relabel, rebuild, publish, or rehearse it as a fresh candidate. |
| CP topology | Starter PR #723 merged a CP-shaped V3 diagnostic fixture, not an accepted CP descriptor or consuming deploy workflow. CP #188 merged backup-first ordering. | Finish the production input/consumer separately; source parity is not adoption. |
| CP PR #187 | Draft at `a514c41`: `check`, `kernel-pin`, `image`, `postgres` and Governance-pin succeed; Engineering Standards fails on schema-11 `deployment_artefact_surfaces`. Five module pins are validation only; kernel a101 remains admitted. | Do not merge, declare adoption, or bump to a104 for appearance. |
| CP issuer | The generated Lane 3 status records no authorization run ID and **0/16** `executed_passed`. PR #187's `deployment propose` and `deployment authorize` deliberately refuse pending an immutable Foundation candidate and custody-approved signer; current production workflow only format-checks `authorization_ref`. | Deploying these source bytes cannot issue the needed authorization. Source composition and live issuer admission are separate gates. |
| CP bootstrap | CP's checked-in descriptor-reconciliation evidence says the single-use persistence bootstrap already ran; ADR-0013 prohibits replacing the application during bootstrap and requires later bootstrap-call-site retirement. | Reconcile that receipt and define the production issuer's in-place activation; do not assume the one-time operation can be replayed. |
| Foundation admission/recovery | `IntegrationSurfaceAbsenceProofV1` and 13 concern slots exist. Starter #735 (`8404473c`) proves the generic verifier for every slot with distinct **synthetic** positive, missing-implementation and wrong-version cases; concrete assembly bindings are not proven. Starter #737 (`d346d94f`) adds `admit_host_source` for a verified candidate/installed-host attestation pair and interpreter reading, but its trace has no consumer and neither mutating executor calls it. `RecoveryExecutor.run::real-ten-step-sequence` remains open; both executors still call `require_host_source(receipt=None)`. | Generic slot verification and the pure admission seam are real progress, not evidence of a trusted mutating execution or real recovery. |
| Lane 3 | `docs/inventories/deployment-exposure-rehearsal-status.md` says 0/16, with hand measurements, blocked, unexecuted and vacuous rows. | Publication requires a real receipt with **16/16 `executed_passed`**, not a converted tally or a shell transcript. |

The status row is a dated repository observation. Before a production action,
the responsible operator re-reads the live source revision, CI, release
coordinates and named host; the table is not a substitute for those oracles.

## The one critical path

Each gate below has an owner, an artifact and a refusal. **A gate advances only
when its evidence is addressable by immutable coordinates and the owning check
accepts it.** Source PRs may run in parallel; the irreversible candidate and
production steps stay serial.

| Gate | Owner and next deliverable | Pass evidence | Stop / authority boundary |
| --- | --- | --- | --- |
| **0. Settle the bootstrap contracts** | CP + Control + Foundation: review the pre-publication rehearsal issuer, immutable candidate reference, custody-approved signer, lease and controller identity. Specify how the five ADR-0013 A6.4 plan inputs are derived without an operator-supplied image. Reconcile the existing single-use persistence-bootstrap receipt with the no-application-replacement rule. | Accepted design and source-level refusal tests for wrong candidate, signer, target, profile, image and plan; protected rehearsal workflow prepared. The issuer path must be implementable once a candidate exists, but cannot produce a candidate-bound authorization yet. | PR #187's current refusal is honest. Do not patch around it with flags, a free-text `authorization_ref` or a fake receipt. Recommend a protected disposable issuer for rehearsal only; production issuer activation remains a separate gate. Any departure from ADR-0013 needs its owner's amendment, not just host approval. |
| **1. Make source and runner ready** | Foundation wires trusted candidate + installed-host admission through both executors; prepares concrete assembly binding and absence-evidence collection for the declared concerns beyond #735's synthetic verifier tests; fixes recovery's per-step adjudication; prepares a protected, real ten-step recovery and Lane 3 runner. Control prepares enrolment/revocation, challenge and signed authorization/grant semantics. | Source-level negative tests for same-author, wrong-artifact, wrong-host, revoked-key, replay and `receipt=None`; a `Lane3RunnerCapability.v1` check proves the runner can produce the required evidence before allocation. Positive candidate admission and live ten-step recovery are **not** claimed at this gate. | `_do_restore_objects` currently overwrites `outcome.attempt` from `_do_restore_roles` before adjudication: add roles-fail/objects-succeed refusal. No second CP renderer or Control decision engine. Do not require a candidate-bound proof before the candidate exists. |
| **2. Freeze and build one successor** | Starter release captain freezes an exact protected-main source revision, disposes of spent `0.4.0a1`, allocates fresh `0.4.0a2`, and builds wheel and sdist once. Reconcile ADR-0070's “sign, then commit receipt” ordering with `foundation-candidate-attestation.yml`, which requires an **already committed** `CandidateArtifact.v1`; distinguish that artifact record from the later signed attestation and review any owning ADR amendment before executing the sequence. | Protected candidate run; committed artifact record with source, wheel/sdist digests, run/artifact IDs and observed expiry; exact bytes re-fetched and verified with the required 30-day retention margin; independent signed envelope and its reviewed record at the sequence the reconciled contract requires. | **Not published or tagged yet.** Missing/expired artifacts or source-tree drift under the same version spend the candidate; disposition and a new version are required. An evidence-only main commit does not automatically void the window. Release-ancestry and freeze guards must still pass. Do not allocate before issuer design and runner readiness are reviewed. |
| **3. Rehearse exact bytes before publication** | CP/Control activate the gate-0 **rehearsal** issuer on a specifically authorized disposable target. Foundation installs the gate-2 wheel as an exact verified file, never a name-only resolution. Exercise candidate/host admission, real ten-step recovery and Lane 3 with the lease, controller identity and actual source-set probes. Retain the Lane 3 receipt as its workflow artifact; do not move the release revision after that run. | Issuer authorization run ID; signed candidate/host and recovery receipts; Lane 3 `RehearsalReceipt.v1` with **16/16 `executed_passed`**, including provoked rollback and non-vacuous running-service negatives. The rehearsal run binds the **later release revision** that contains the committed candidate record; that revision separately binds the candidate source tree and artifact digest. | Human names the exact SSH endpoint and action before host work. `vendor-cp-prod` is a logical host ID, not an inferred SSH target. A disposable issuer's identity is not production authority. No positive admission on editable CI or ordinary main. |
| **4. Publish the rehearsed candidate** | Starter release captain publishes the **same wheel and sdist bytes** from the rehearsed candidate, reads them back from the registry, verifies digests, tags and merges the truthful release record under the short named freeze. | Protected release run, immutable tag/peeled candidate-source commit, registry read-back, install-back of exact files and green release-record main. The release workflow separately checks candidate source, release revision and rehearsal-runner revision. | No rebuild, mutable tag, name-only install, skipped gate, or use of the spent a1 artifact. Publication still does not transfer a product executor. |
| **5. Land CP's consuming source** | CP implements ADR-0017's concrete design only with Michael's separate implementation go-ahead and the topology-complete published Foundation pin. First, pre-merge CI renders and byte-compares the effective `docker-compose.production.yml` **without authorization or an independent image slot**. Reconcile #187 and schema 11. After merge, publish a CP image and verify its release receipt and registry read-back; only then derive `admit_candidate_image` and let Control bind that image, retained render, private inventory, target and approval. | Required hosted checks green at the final PR head; retained asset hash and consuming workflow; negative tests for edited bytes, `COMPOSE_FILE` diversion, image injection, wrong authorized inputs and missing receipted rollback asset. Accepted descriptors remain until their specified successful migration/runtime promotion. | A green render is neither image admission nor Control authorization. #187 stays draft until its real Governance gate is green. Do not let a pre-merge candidate authorize production or retroactively mutate `deploy/product.toml` or its ledger. |
| **6. Activate production issuer; execute and retire CP** | On Michael's exact production-host authorization, activate the **production** issuer with approved invocation, signer custody, persistence and authorization path **without application replacement**. CP then performs an authorized Foundation deployment **and redeployment**, verifies image/profile/catalog/asset and runtime read-backs, proves recovery and observes the displacement window. Retire direct-Compose **and** the bootstrap mutation path. | Production issuer admission; two distinct controller-owned deploy-run IDs and signed receipts, including the CP-authorized second-deployment oracle and image digest; bootstrap receipt supersession, checked-in launcher/call-site deletion to zero, old-executor zero-surface proof and product retirement receipt. | Reconcile the already-run single-use bootstrap; never replay it by assumption. A rollback needs a separately receipted previous asset and authorization; V3 currently reports it unavailable. If either ordinary or bootstrap legacy path can still write, cutover is not complete. |
| **7. Repeat by product** | ERP, then Sub: each chooses a coherent legacy-executor slice, makes its own descriptor/asset/authorization binding, rehearses its topology, executes and retires its writer. | Product-local deploy/redeploy, recovery, observation and retirement receipts. | CP evidence cannot close ERP or Sub debt. ERP's finance/data cutover obligations remain separately gated. |

### Parallel work that starts now

1. **CP/Control authority PRs:** prepare `propose` and `authorize` for
   immutable-candidate derivation and custody-approved signing; implement A6.4
   reference resolution and its negative tests. Candidate-bound success is
   proven at gate 3, after a real artifact exists. Do not merge a
   raw-image or operator-shaped authorization escape. CP ADR-0017's accepted
   *concept* should be pushed as a docs-only PR when GitHub write is authorized.
2. **Foundation PRs:** trusted-provenance consumption in both executors;
   concrete assembly binding/evidence beyond #735's synthetic slot tests;
   restore-attempt overwrite repair and real ten-step recovery runner; Lane 3 capability and runner fixes
   whose retained evidence, not merely a pass counter, proves all sixteen
   subjects at gate 3.
3. **Evidence and operations:** reconcile the CP bootstrap runbook with ADR-0013
   (already-run one-time in-place operation; no app replacement during
   bootstrap); inventory egress, PostgreSQL, verified backup/restore, signing custody, lease,
   controller and host-engine versions. This is read-only preparation until
   Michael names and authorizes an exact host. Prepare the old-executor
   retirement diff before, not after, the production switch.

These lanes meet at gate 2. A ninth unrelated merge or another pin-only PR
does not move this roadmap. Every work item must name the gate, its before/after
evidence and the refusal it removes.

## Decision and evidence discipline

- **Now:** Michael decides whether the pre-publication rehearsal uses a
  protected disposable CP issuer (recommended) or whether a production
  in-place issuer is contractually necessary. That decision must identify the
  signer/trust domain and the distinction between rehearsal and production
  authorization. He also ratifies CP ADR-0013 A6.4's replacement text before
  its immutable-reference rule is treated as accepted implementation authority;
  the current amendment calls that ratification pending. The ADR-0070 versus
  attestation-workflow ordering conflict needs an owning contract correction
  before the successor build. None of these decisions authorizes SSH or deployment.
- **Before host work:** Michael supplies the exact SSH endpoint, environment,
  action and scope. ADR-0013 names `vendor-cp-prod` as a logical target; a
  historical address in a runbook is not current permission. No broad Docker
  run is inferred from approval of a read-only preflight.
- **Before allocation/publication:** Starter's release captain records the
  exact source, candidate coordinates, disposition, freeze and normal release
  checks. The release policy in `AGENTS.md` rule 31 is machine-authorized on
  protected `main`; production deployment remains a separate human gate.
- **Before calling any gate closed:** update this roadmap with an immutable
  receipt/run/commit coordinate and the owning check that accepted it. Do not
  replace `blocked` with `passed` on a prose assertion, a local green test,
  or an agent's risk acceptance. Reconcile the canonical debt register and
  product cutover record in the same change.

The immediate executable package is therefore **gates 0–1's contract and
source-readiness PRs**, plus resolution of the signing-order conflict—not an
issuer deployment or a new Foundation build. The next joint checkpoint is a
reviewed rehearsal-issuer design, a capable Lane 3 runner and a protected
Foundation source tree whose admission and recovery paths can be exercised
against one exact built candidate. Only then does the short, irreversible
candidate window begin.
