# Rust computation implementation inventory

Recorded 2026-10-15 against Starter `7a133895d611d9043eff10dc1b8271bdda493ff2`.
This inventory authorizes no publication, adoption claim or deployment.

The implementation direction is domain-owned native computation behind existing
Python contracts. Authorization, tenant selection, sessions, persistence, audit,
outbox, signing and release verification retain their existing owners. No new
domain vocabulary or migration lineage is introduced by changing the computation
language. The user directed implementation after the earlier Rust deferral.

## Product evidence

The read-only product audit inspected ERP
`93d7e111d9278b8377bac311e78308c99f51da79`, Sub
`c7707880232c9298ef89c24a1aea66d35bd3a1e6` and Data
`2c96c426f08f525992560df405507935543c499f`. These are checkout-bounded
observations, not claims about current production. No checked-in Cargo manifest,
Rust source or Rust lockfile was found in those trees. No product working tree
was modified. Existing module dossiers remain the authority for product source
qualification, published contracts and product cutover; this inventory does not
replace them or infer that the lack of Rust permits new business behavior.

| Area | Product evidence | Source retained for the native port |
| --- | --- | --- |
| Imports | ERP `app/services/finance/import_export/base.py`; Sub durable-import sources and tests already enumerated by `packages/dotmac-imports/EXTRACTION.toml` and `docs/inventories/imports-sources.md` | Existing extracted `dotmac_imports.tabular` and partitioning contracts, with `tests/unit/test_imports.py` and the module's existing tests |
| Positioning | Sub `app/services/field/geofence.py` implements Haversine; its direct behavior-test coverage was not established by this audit. The existing positioning dossier also inventories CRM's tested geometry source. | Existing extracted `dotmac_positioning.service` geometry and `tests/unit/test_positioning.py`; preserve fence selection, shape errors and transitions |
| Graphs | Sub `app/services/network_graph.py`, `network_explorer.py`, `customer_network_path.py` and `tests/test_network_explorer.py` describe owner-provided paths, not a replacement generic inference engine | Existing topology Dijkstra, fiber continuity BFS and media hierarchy cycle behavior and their module tests |
| Analytics | ERP `app/services/analytics/computers/cash_flow.py` and `revenue.py` have focused product-metric tests. Sub KPI computation returns an empty list. Data `app/services/data_management/quality.py` has cleaning/profile/pipeline tests. None supplies Starter's first-party session/funnel semantics. | Existing web-analytics projection algorithms, declaration contracts and `tests/unit/test_web_analytics_engine.py` |
| Financial calculations | ERP `app/services/finance/posting/residue.py` and `tests/unit/test_posting_residue_and_balance.py` establish exact-rounding behavior; Sub fiber-cost formulas are product-specific | Existing extracted finance, inventory, subscription and tax calculations and their exact-decimal tests; do not import unrelated pricing or posting authority |
| Surveys/forms | Existing module source qualification remains in each `EXTRACTION.toml` | Existing answer validation and cohort metrics; bulk computation changes no submission or lifecycle ownership |

## Implementation and retirement contract

Native functions consume owned primitive snapshots or bounded partitions and
return positional computation results. They do not acquire database state or
cache tenant data. Batching must include input conversion and output construction;
moving one scalar operation per call is not the implementation target.

Tests are written before replacing each algorithm. Independent known-answer,
ordering, refusal and boundary cases accompany the existing module suites. A
Python reference may exist temporarily in tests for differential coverage, never
as a silent runtime fallback or second business owner. The owning runtime path
switches to the native result and the displaced Python algorithm is removed.

Native build inputs must be included in each owning distribution's source
archive. Hosted CI must build and install the exact wheel, rebuild from the
source archive, and retain artifact identities. Missing native code is a build or
installation error. Published module identity, manifest digests and verification
ledgers remain governed by their existing release facilities; this source work
does not fabricate release receipts.

All workloads and dependency installation run on Git-hosted CI, as required by
AGENTS.md. Local inspection, editing, formatting and pinned static checks provide
no test or performance evidence.

## 2026-10-15 qualification and release continuation

Michael directed completion of the remaining native releases and clarified:
"Qualify and release; adopt where a matching use exists." This supersedes the
source implementation's publication deferral above. It does not add product
capabilities to manufacture consumers or transfer product data/writer authority.
The ten engines merged in PR #843 at
`008ccaeaebda6855ba34b8e70740e3859954a37b`. Imports `0.2.0a1` was subsequently
published and verified by release run `38726218479`; ERP's bounded customer
preview adoption merged in PR #715 at
`d1c1c7975eead7cb716fc4d590dabe97b6eebcdd` after its installed-native canary and
all required checks passed.

The remaining nine `0.2.0a1` distributions retain their existing public module
APIs and private Rust implementations. Hosted qualification and the release lane
must inspect the exact
wheel and source archive, verify supported interpreters and compiler-free
CPython 3.12 Debian Bookworm installation, publish those bytes, install back from
the registry, prove native loading, tag and reconcile the generated release
record. Each release has its own run and named freeze through truthful green
protected main. Qualification or a package pin alone is not product adoption.

| Native successor | Existing use to assess | Product boundary |
| --- | --- | --- |
| Surveys | Sub's survey metric projections through public `calculate_metrics` | Prove score eligibility, empty populations, Decimal/rounding and output parity; keep product queries, rows and transactions. Full survey persistence cutover remains separate. |
| Finance | ERP fixed-asset depreciation through public `calculate_depreciation` | Prove the supported three-method, period, rounding and final true-up contract. Unsupported product methods and accounting consequences retain their owners. |
| Inventory | No compatible computation-only public caller established | Private native totals remain inside module projection services. Organization-to-Tenant mapping, stock replay, shadow and writer retirement precede full adoption. |
| Forms | No compatible computation-only public caller established | Private native review remains inside the submission owner. Seven-table backfill, immutable versions, refusal/isolation parity and generic-writer retirement precede full adoption. |
| Positioning | No compatible computation-only public caller established | Geometric membership does not replace product distance/transition semantics. Accuracy and purpose/expiry/revocation grant decisions precede Sub's full adoption. |
| Network Topology | No compatible computation-only public caller established | Dijkstra does not replace owner-declared forwarding paths. ADR-0038's coordinated suite, reconciliation and one-writer gates remain. |
| Fiber Plant | No compatible computation-only public caller established | Strand/splice continuity does not replace product endpoint/segment ambiguity rules. CRM outside-plant consolidation and coordinated suite gates remain. |
| Media Observations | Mkt hierarchy is qualifying source; no bounded public computation adoption established | Production-writer audit, hierarchy/metric shadow and reconciliation remain prerequisites to cutover. No attribution or transport ownership moves. |
| Web Analytics | No matching first-party event consumer established | Financial KPIs and provider aggregates are outside the contract. Qualified release does not create a website/funnel product feature. |

No remaining native successor is claimed published by this continuation. Exact
publication evidence belongs in the existing release-verification records.
Any bounded product slice must exercise the installed extension through the
public owner, replace its displaced arithmetic, and pass meaningful parity and
ownership checks plus the complete hosted CI set. Computation adoption is not a
claim of module database cutover or production deployment, and no measured
speedup is claimed.
