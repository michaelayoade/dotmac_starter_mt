# All 31 outstanding Starter PRs: integrated source

Scope authorized by Michael on 2026-10-29: all 31 open PRs in the captured inventory.
The integration merge base is `233e6cd579be7384f6e62ef3df4f0ab49e03762a`
(tree `a99da0e5103878c621dfcc14f49a1e30e2a0282f`). Older source slices
already present on that base are inherited rather than replayed over newer code.
The working branch is `integration/all-starter-module-prs-20261029`.
The refreshed merge base is `329a397bedd7de9f8583f648368b3ba3558a4191`
(tree `1e1d9e2ef112f280d9f86a00f9ea0e1b976666f1`): #943 has since landed
independently and is inherited. Its requirements-engine companion #911 remains
part of this aggregate.
This is source preparation, not publication, installation, adoption or deployment.

| PR | Captured source | Disposition | Integration evidence / resolution |
| --- | --- | --- | --- |
| [#640](https://github.com/michaelayoade/dotmac_starter_mt/pull/640) | [`8f02124f`](https://github.com/michaelayoade/dotmac_starter_mt/commit/8f02124f1f534bd427b84e3062312fc2f0c779ea) | Inherited | Main contains the implementation, golden fixture and stronger revision-8 design/tests; preserve those newer files. |
| [#682](https://github.com/michaelayoade/dotmac_starter_mt/pull/682) | [`f4f704ce`](https://github.com/michaelayoade/dotmac_starter_mt/commit/f4f704cea24e644f3806b249a6529fa13a3e96e1) | Included — sequencing hold | Exact compatibility source and the existing immutable workflow pin are included. The trusted runner deliberately refuses recorded ERP debt; the protected successor chain and actual 3/3 proof remain required. |
| [#780](https://github.com/michaelayoade/dotmac_starter_mt/pull/780) | [`4e40b54e`](https://github.com/michaelayoade/dotmac_starter_mt/commit/4e40b54ea8915c40e627cac0595882cea39f8bbf) | Included | Historical B5 scheduling evidence and the unadopted N3 proposal; current topology and admission restrictions are preserved. |
| [#804](https://github.com/michaelayoade/dotmac_starter_mt/pull/804) | [`872108ab`](https://github.com/michaelayoade/dotmac_starter_mt/commit/872108ab5aa10bf2556b9c02d13cb9b391f245d6) | Inherited | Current Billing/Payments source and retained multiline migration already incorporate the settlement and confirmation work. |
| [#805](https://github.com/michaelayoade/dotmac_starter_mt/pull/805) | [`293ed7e3`](https://github.com/michaelayoade/dotmac_starter_mt/commit/293ed7e348ce9523adc397e79a76e5e34e592da6) | Inherited | Current immutable settlement account/funding binding supersedes this earlier source slice. |
| [#837](https://github.com/michaelayoade/dotmac_starter_mt/pull/837) | [`b3c4ba41`](https://github.com/michaelayoade/dotmac_starter_mt/commit/b3c4ba41ac414af89215b06dff41b46d9771ce96) | Included | Router configuration decision and inventory; renumber the previously unmerged decision to ADR-0078 to preserve existing ADR-0074. |
| [#838](https://github.com/michaelayoade/dotmac_starter_mt/pull/838) | [`fbdbd5d4`](https://github.com/michaelayoade/dotmac_starter_mt/commit/fbdbd5d43bd4ab29499be40ef85e2ef37b3c937d) | Included, corrected | Stateless config-layers a1; approval classification now compares default-filled before/after values, with authored hosted regressions and explicit port deltas. |
| [#845](https://github.com/michaelayoade/dotmac_starter_mt/pull/845) | [`ccb9130d`](https://github.com/michaelayoade/dotmac_starter_mt/commit/ccb9130deb7822494a42efdbf7af9f74c1eddd59) | Included | Signed opening source/command provider source composed with current Billing a10. Production custody, enrollment and the separate inverse-verifier composition are not asserted. |
| [#854](https://github.com/michaelayoade/dotmac_starter_mt/pull/854) | [`b33baf28`](https://github.com/michaelayoade/dotmac_starter_mt/commit/b33baf28ed976dfe1a5f85e60da7e8bbcb610992) | Inherited | Current Billing account observation API and its tests. |
| [#855](https://github.com/michaelayoade/dotmac_starter_mt/pull/855) | [`19d96f1d`](https://github.com/michaelayoade/dotmac_starter_mt/commit/19d96f1da58237f720f7dbb160ec994f4f3e9a02) | Inherited | Current rated invoice authoring API and persisted line identities. |
| [#857](https://github.com/michaelayoade/dotmac_starter_mt/pull/857) | [`2ebde7f1`](https://github.com/michaelayoade/dotmac_starter_mt/commit/2ebde7f1bdab39075ba33365b2fed5a50991af42) | Inherited | Current bi_0010 composite line keys and later predecessor/catalog canaries. |
| [#858](https://github.com/michaelayoade/dotmac_starter_mt/pull/858) | [`3bd009da`](https://github.com/michaelayoade/dotmac_starter_mt/commit/3bd009da65156caf3a13de03805725970c86e266) | Inherited | Current nonempty opening rehearsal contracts, owner and evidence reporting. |
| [#859](https://github.com/michaelayoade/dotmac_starter_mt/pull/859) | [`c5cd2530`](https://github.com/michaelayoade/dotmac_starter_mt/commit/c5cd253022b0bbae4b939c40d3121646dd0afd2f) | Inherited | Current exact settlement allocation observation and stronger deallocation/reversal tests. |
| [#860](https://github.com/michaelayoade/dotmac_starter_mt/pull/860) | [`0c417648`](https://github.com/michaelayoade/dotmac_starter_mt/commit/0c41764892d34cf86e8eb344a70cf27a4f0fba29) | Inherited | Current immutable purchase-funding purpose and pm_0004 migration. |
| [#861](https://github.com/michaelayoade/dotmac_starter_mt/pull/861) | [`b71f9f6b`](https://github.com/michaelayoade/dotmac_starter_mt/commit/b71f9f6bbb55b7f15e4f9d8271d8bb39a4c3293b) | Inherited | Current ig_0016 receipt-origin contract, including later verification-config binding. |
| [#862](https://github.com/michaelayoade/dotmac_starter_mt/pull/862) | [`44e19f85`](https://github.com/michaelayoade/dotmac_starter_mt/commit/44e19f850309f1b2f6595a027220aa2845b4f04e) | Inherited | The provenance implementation is already on main; preserve its later workflow, tests and authority history. |
| [#875](https://github.com/michaelayoade/dotmac_starter_mt/pull/875) | [`9d06cc95`](https://github.com/michaelayoade/dotmac_starter_mt/commit/9d06cc959fa51750bac726511fbe9debb9ce2dca) | Consumed | Kernel a106 authorization is historical and consumed. Preserve current Kernel a107 and active=null; never reactivate the old release authorization. |
| [#878](https://github.com/michaelayoade/dotmac_starter_mt/pull/878) | [`6b60c143`](https://github.com/michaelayoade/dotmac_starter_mt/commit/6b60c143333b213f8f3c8ede6fcd71c208c6f76c) | Superseded | Current Integration a20 and Learning namespace allocation supersede the a19 reconciliation/allocation slice. Preserve published state; new dispatch source is a21. |
| [#879](https://github.com/michaelayoade/dotmac_starter_mt/pull/879) | [`1b0153ad`](https://github.com/michaelayoade/dotmac_starter_mt/commit/1b0153ad01b72816b7d70349ba2de69165b2c012) | Included / inherited | Keep current Learning inventory/dossier; retain the unique historical proposed immutable-lineage adoption design. |
| [#883](https://github.com/michaelayoade/dotmac_starter_mt/pull/883) | [`c2d0701d`](https://github.com/michaelayoade/dotmac_starter_mt/commit/c2d0701d66c5cbe5688a1338c1f815246302435b) | Included, reconciled | Dispatch snapshots follow released provisioning as ig_0018; include grant-bound PH ph_0003 and Foundation source. Preserve Foundation's spent identity and all released migrations. |
| [#886](https://github.com/michaelayoade/dotmac_starter_mt/pull/886) | [`27f25584`](https://github.com/michaelayoade/dotmac_starter_mt/commit/27f25584935997fa85b161e59a2cb7d1866646be) | Inherited | Merged integrated Learning extraction contains the result core, migration and tests. |
| [#887](https://github.com/michaelayoade/dotmac_starter_mt/pull/887) | [`80f6e5da`](https://github.com/michaelayoade/dotmac_starter_mt/commit/80f6e5da4a126dbbc7914f7e27be4fef7f29318b) | Inherited | Merged integrated Learning extraction contains curriculum/reading source, migration and tests. |
| [#888](https://github.com/michaelayoade/dotmac_starter_mt/pull/888) | [`e17f37e7`](https://github.com/michaelayoade/dotmac_starter_mt/commit/e17f37e711a9aa79254e3a25410919057ba6848d) | Inherited | Merged integrated Learning extraction contains participation/roster/exam source, migration and tests. |
| [#890](https://github.com/michaelayoade/dotmac_starter_mt/pull/890) | [`3298583d`](https://github.com/michaelayoade/dotmac_starter_mt/commit/3298583d7b10e0cd3a9c70752074df6c72ad595a) | Included, corrected | UI a9 dashboard primitives. Historical source anchors are preserved; the newer audit is recorded as revalidation. |
| [#911](https://github.com/michaelayoade/dotmac_starter_mt/pull/911) | [`7670b7eb`](https://github.com/michaelayoade/dotmac_starter_mt/commit/7670b7eb1eb9b29eaa2347ff852b1694a9a0e7f1) | Included | Requirements evaluation source and characterization tests join the unpublished Learning a4 candidate; no independent a3 release or application wiring is claimed. |
| [#935](https://github.com/michaelayoade/dotmac_starter_mt/pull/935) | [`55fa13ce`](https://github.com/michaelayoade/dotmac_starter_mt/commit/55fa13ce960d1eb23f7bbe9e338bf5f5e6b7859e) | Included | IPAM outbox relay and aggregate-import idempotency, retaining the current extraction and all predecessor bytes. |
| [#939](https://github.com/michaelayoade/dotmac_starter_mt/pull/939) | [`7d660766`](https://github.com/michaelayoade/dotmac_starter_mt/commit/7d66076652c0b0e7c6e0a8a4b9264217f0cd2ceb) | Included | Payroll a2 exact independent totals and net-only payment parity. |
| [#940](https://github.com/michaelayoade/dotmac_starter_mt/pull/940) | [`3a8e1ac1`](https://github.com/michaelayoade/dotmac_starter_mt/commit/3a8e1ac1c3c59827bd58f0918d86c9fec0f9c907) | Superseded by #944 | The complete immutable Tax evidence scope is included in Billing a10. The source-only a9 allocation is not a separate release. |
| [#942](https://github.com/michaelayoade/dotmac_starter_mt/pull/942) | [`b391df0f`](https://github.com/michaelayoade/dotmac_starter_mt/commit/b391df0fe83cc72e2db0c26a2d0064d8f7efc47a) | Included | Tax a7 dependency determinations and tx_0005, preserving published predecessors and source-only a6 history. |
| [#943](https://github.com/michaelayoade/dotmac_starter_mt/pull/943) | [`9249d5ba`](https://github.com/michaelayoade/dotmac_starter_mt/commit/9249d5ba99cc577c1b5c6ed40f20ef26dabdc0bd) | Inherited after refresh | Learning a4 caller-owned cohort intake successor and installed-probe expectations are now on main329. |
| [#944](https://github.com/michaelayoade/dotmac_starter_mt/pull/944) | [`c716d304`](https://github.com/michaelayoade/dotmac_starter_mt/commit/c716d30415d22795e86ac18bad64c3a4c7ed7b0f) | Included | Complete Billing a10 Tax-owned invoice/dependency evidence, historical reads and bi_0012. |

The added-path completeness audit found all 110 distinct added paths from the
superseded financial, Learning and prerequisite slices present in the integrated
tree. Later tests and contract extensions are preserved. On 2026-10-29, the
unchanged captured heads of #640, #804, #805, #854, #855, #857, #858, #859,
#860, #861, #862, #875, #878, #886, #887 and #888 were closed as already
incorporated, superseded or consumed on protected main. Their obsolete branches
were not merged. #943 had already merged independently. The aggregate must be
accepted before the remaining unresolved slices are closed. Payroll #939's
separate auto-merge has been disabled.

## Combined metadata and source identities

The integrated candidate declares Payroll a2, Tax a7, Billing a10, Learning a4,
UI a9, config-layers a1 and Integration a21 as unpublished source. Platform
Health remains its existing unpublished 0.2.0a1 successor with ph_0003 and a98
Kernel floor. IPAM remains its unpublished 0.2.0a1 extraction with ip_0003/4.
Published Integration a20 and its ig_0017 provisioning migration are unchanged;
new dispatch source uses ig_0018 and a21. No tag/publication is inferred from a
version declaration. Foundation 0.4.0a1 remains a spent, non-publishable identity;
its receipt, spent-identity record and window debt are preserved without a new
allocation or lifecycle disposition.

The global lock is regenerated with Poetry 2.4.1; incoming old locks and release
authority digests are not authoritative. The final authority history starts from
canonical main and appends only the combined candidate digest. Existing drift
dispositions and released migration digests remain unchanged. The released-source
census decreases for the five published declarations moved to unpublished
successors relative to the original base (Payroll, Billing, Learning, UI and Integration).
After #943 landed, current main has census76; four further declaration transitions
in this aggregate yield72. Both existing drift dispositions remain unchanged.

## Compatibility hold and merge acceptance

The aggregate retains `hold:sequencing` from #682. Its existing pinned workflow
calls the independently protected trusted runner, which explicitly expects ERP's
recorded six-site compatibility debt and returns a refusal. Running that workflow
cannot establish a green compatibility result. No skip, continue-on-error,
candidate-controlled replacement or false adoption claim resolves that condition.

ERP's protected revision `134abce8a74a205bd9636cafcf232178acb70874` already
contains the published Files/Tax repins. Its nine product-owned observations
reproduce byte-for-byte under their pinned `8b4b6d4` semantics, and all 15 checks
on that revision passed. No ERP evidence refresh or credential ceremony is a
prerequisite to using this existing proof.

The remaining closure sequence requires reviewed final gate/bindings bytes that
resolve actual published package sources; a protected trusted runner successor
binding those digests and the current ERP revision; then a protected
workflow/guard successor and the aggregate repin. The prospective Kernel subject
is explicit and distinct from observed product lock versions. Actual
three-product compatibility must execute with adoption `not_evaluated`; any
reached package debt remains a refusal requiring its own repair. Exact-head
review and every required current-base CI check must pass before the sequencing
hold is removed and this aggregate merges normally.

Other source reviews and static checks do not substitute for hosted tests. Tests
and dependency installations were not run locally. No production credential
custody, enrollment, live authority, worker switch, migration execution or
Seabone deployment follows from this source integration.
