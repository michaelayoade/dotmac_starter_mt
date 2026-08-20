# Vendor CP composition readiness — one owner per decision, end to end

**As of:** 2026-08-19
**Starter:** the ADR-0033 module stack, off `origin/main` `fb9aea0`
**Vendor CP:** `main` `2c4d88a`
**Decision record:** [ADR-0033](../adr/0033-the-vendor-control-plane-composes-existing-owners.md)
**Source evidence:** [`vendor-cp-gap-sources.md`](vendor-cp-gap-sources.md)

The completion artifact for the programme that recomposes the vendor control
plane as a thin assembly. It maps **every decision and transition in the Vendor
journey to exactly one owner**, and states what is still missing.

It is a readiness map, not a claim of readiness: four modules are built and
validated, **none is adopted**, and the composition itself is a later task.

## The journey, decision by decision

Each row names the ONE owner of that decision. Where a row has two entries, they
are different decisions that look like one — and the split is the point.

| # | Decision or transition | Owner | State |
|---|---|---|---|
| 1 | What was released, and is this release evidence real? | `dotmac-release-catalog` | **adopted** in Vendor production |
| 2 | What did we agree with this counterparty, and on what terms? | `dotmac-commercial-agreements` | **built**, PR #275 |
| 3 | Has the required set of eligible actors approved this exact content? | `dotmac-approvals` | **adopted** in Vendor production |
| 4 | Is this agreement ACTIVE — was the contracted activation rule satisfied? | `dotmac-commercial-agreements` | **built**, PR #275 |
| 5 | What does an activated agreement version entitle, exactly? | `dotmac-entitlement-allocation` | **adopted** in Vendor production |
| 6 | What signed, versioned, revocable authority does that entitlement become? | `dotmac-licensing` | **built**, PR #276 |
| 7 | Which brand does this deployment present? | `dotmac-brand-profiles` | **built**, PR #278 |
| 8 | What SHOULD this deployment be running, and is that plan approved? | `dotmac-deployment-control` | **built**, PR #277 |
| 9 | How do the bytes get there, and what happened on the wire? | `dotmac-integration` / the Integrator assembly | **adopted** (ADR-0024) |
| 10 | What IS the deployment running, and does it match what we sent? | `dotmac-deployment-control` | **built**, PR #277 |
| 11 | Did the deployment prove it is itself when it reported? | `dotmac_kernel.licensing` (ADR-0007) | shipped |
| 12 | What is owed, and has it been paid? | `dotmac-billing` / `dotmac-collections` | ADR-0020; not on `main` |
| 13 | A support request about any of the above | `dotmac-ticketing` | released |
| 14 | May this person act on that deployment right now? | `dotmac-approvals` + `dotmac-application-access` + the assembly | **ADR-0029; access module unbuilt** |

### Rows 3 and 4 are two decisions, not one

`dotmac-approvals` answers *were the required people satisfied by this exact
content*. `dotmac-commercial-agreements` answers *is this agreement now in
force*. ADR-0026 § 6 forbids the first owner performing the second's transition,
and the binding that makes the split safe is the digest: activation requires the
approval evidence whose `content_digest` equals the snapshot the agreement froze
itself. Change the terms after approval and the approval goes **stale rather
than transferable**.

### Rows 8 and 9 are two decisions, not one

Deployment control decides WHAT should be deployed and emits a provider-neutral
`DeliveryIntent`. The Integrator decides HOW to get it there and owns every piece
of transport evidence. Hard rule 28 states it; the module's guards enforce it by
scanning for endpoint, credential, retry and checkpoint identifiers.

### Row 11 is the kernel's, and deliberately not deployment control's

`verify_applied_state` and `verify_possession` live in `dotmac_kernel.licensing`.
Deployment control consumes the RESULT. A second verifier could disagree with the
first, and the disagreement would be invisible until it mattered.

## The three journey steps that have no unresolved owner but no code either

| Step | Owner named | Why it is not built here |
|---|---|---|
| billing and collections | `dotmac-billing`, `dotmac-collections` (ADR-0020) | Deferred by Michael's standing "billing remains deferred" ruling. Both exist on unmerged branches; neither is on `main`. |
| temporary support access | `dotmac-application-access` (ADR-0029) | Deferred by ADR-0021 § 5 until the kernel has a generic signed-document mechanism, and by ADR-0017. Building a `dotmac-support-access` would have created a **fourth** owner of a decision ADR-0029 already gave three. |
| notification delivery | kernel `consent`/`channel_policy`/`delivery` + `dotmac-template-studio` + `dotmac-integration` | Already built and shipped. ADR-0006 § 5c decomposed it and three dossiers placed every piece. |

## What each new module needs from the assembly

None of the four talks to another. Every coupling is a value the assembly passes.

```
release-catalog ──(release_ref: str)──────────────┐
                                                  ▼
approvals ──(ApprovalEvidence)──► commercial-agreements
                                                  │
                                     agreement.activated.v1
                                                  ▼
                              entitlement-allocation (ContractSnapshot)
                                                  │
                                        (LicensableGrant)
                                                  ▼
brand-profiles ──(brand_profile_ref: str)──►  licensing
                                                  │
                                        (envelope, licence_ref)
                                                  ▼
                                        deployment-control
                                                  │
                                         (DeliveryIntent)
                                                  ▼
                                            Integrator
                                                  │
                                    (verify_applied_state result)
                                                  ▼
                                   deployment-control (ObservedState)
```

Seven typed seams the Vendor assembly must implement:

1. **`CapabilityCatalogueReader`** → agreements, from the kernel's capability
   catalogue for the named product.
2. **`ApprovalEvidence`** → agreements and deployment control, from
   `dotmac-approvals`' decision record. Two separate adapters; each module
   checks the digest against its own frozen content.
3. **`LicensableGrant`** → licensing, assembled from the agreement's activation
   fact plus the allocation's staged entitlement. This is the seam that reads
   `allocation_product()` rather than accepting a fresh product code — the
   relabelling path `dotmac-entitlement-allocation` records.
4. **`LicenceSigner`** → licensing, over the product's OpenBao-materialised key
   files. The module ships no implementation; ADR-0009 puts custody here.
5. **`DeliveryIntent` → Integrator**, and the Integrator's outcome back as a
   settle call.
6. **`ObservedState`** → deployment control, from
   `dotmac_kernel.licensing.verify_applied_state`. The adapter must pass the
   RESOLVED identity, never the report's own claim — the module raises if a
   caller passes `signature_status="valid"` with no authenticated ref.
7. **`BrandOverride`** ← brand profiles, mapped through the module's published
   `BRAND_OVERRIDE_INPUTS` allowlist. ADR-0033 § 2a assigns this mapping to the
   assembly explicitly: `dotmac-ui` owns the vocabulary, projection and contrast;
   the module owns the values, provenance, precedence and locks; the assembly
   joins them. Driving the mapping through the allowlist rather than hard-coded
   field names is what makes it break loudly if `dotmac-ui` grows a third input.

## Counterparty identity: one rule, no conflict

Every module takes an **opaque counterparty reference** and none defines a
counterparty master. The Vendor assembly binds it to `vendor_accounts.id`.

ADR-0019 § 1 / § 5b / § 6, ruling A3 and ADR-0024's correlation-only rule were
checked against each other and **agree**. There was no conflict to stop on, and
no module in this programme creates, owns or extends counterparty master data — a
speculative counterparty owner is exactly what the programme was told not to
build.

## Planes, and why three of four are platform-only

| Module | Tenant | Platform | Reason |
|---|---|---|---|
| commercial-agreements | — | ✅ | No tenant data plane holds a vendor↔operator agreement. Sub sells ISP service to subscribers, a different subject with a different owner (ruling A2(a)). |
| licensing | — | ✅ | **A security boundary**: issuance must not live inside the deployment it authorises. The receiving half verifies offline through the kernel. |
| deployment-control | — | ✅ | Close to tautological: a module that decides what a FLEET runs cannot live inside one of those deployments. |
| brand-profiles | ✅ | ✅ | **Genuinely dual-plane**, with a real consumer on each side today: Sub (tenant, the 897-LOC extraction source) and Vendor (platform, the OEM case with host bindings). |

No plane was declared because it might be useful later. ADR-0023 requires a named
assembly on each declared side, and each of the four rows above names one.

## Adoption state — nothing is adopted

| Module | Dossier status | `adoption_evidence` | First adopter |
|---|---|---|---|
| commercial-agreements | `audit-complete` | empty | Vendor CP (authority transfer from `vendor_cp.contracts`) |
| licensing | `audit-complete` | empty | Vendor CP (authority transfer, issuer half only) |
| deployment-control | `audit-complete` | empty | Vendor CP (greenfield composition; nothing to transfer) |
| brand-profiles | `audit-complete` | empty | Sub (tenant plane), then Vendor CP (platform plane) |

Each dossier records what its cutover owes. Three obligations are worth naming
here because they are easy to discover too late:

- **Licensing envelopes migrate BYTE-FOR-BYTE.** Re-serialising an envelope
  changes its digest, which invalidates the signature and turns every deployed
  licence into one the receiver rejects. And the revocation list's cumulative
  superset rule **spans the cutover** — the fleet's imported state does not reset
  when the issuer's code does.
- **Agreements must preserve every `content_hash` unchanged**, map
  `pending_approval` → `proposed`, and synthesise history from the platform audit
  log rather than inventing a clean one.
- **Brand profiles change the colour PIPELINE, not just the storage.** Sub's own
  `brand_theme.py` generator is superseded by `dotmac-ui`'s, so the same values
  render differently. The cutover renders both and diffs the output rather than
  assuming parity.

## Open decisions this programme surfaced and did not take

1. **~~Sub's `semantic_colors` quintet~~ — RULED 2026-08-19, closed.** Not
   carried. The objection is ownership, not safety: `dotmac_ui.SEMANTIC_INTENTS`
   publishes those five names as tokens with built-in ramps that
   `render_brand_css` does not seed, so a per-profile override would be a second
   authority over a published token. Every affected value is reported by
   `translate_legacy_brand_values()` with
   `Disposition.OWNED_BY_PUBLISHED_TOKEN`. ADR-0033 § 2a records the amended
   presentation boundary.
2. **The two abandoned Vendor V6 branches must be deleted** once deployment
   control is composed — `feat/v6-slice1-deployment-credentials` and
   `feat/v6-slice2-applied-state-admission`. Their migration slots have been
   reused on Vendor `main`, so leaving them is both a misleading second
   implementation and a live rebase hazard.
3. **ERP's `app/licensing/` remains a second incompatible licence format.** This
   programme built the issuer and did not retire that receiver; the obligation is
   against `dotmac_kernel.licensing` and is recorded so it is not mistaken for
   solved by the arrival of an issuer.
4. **The Integrator does not yet own licence delivery.** `dotmac-licensing`
   deliberately left `transport.py`, `delivery_models.py` and the delivery half
   of `projection.py` (~1,600 LOC) in the Vendor repository. Moving them is a
   separate ADR-0024 slice; conflating it with the issuer retirement would strand
   delivery with no owner.

## What "composition-ready" does and does not mean here

**Does mean:** every decision in the journey has exactly one named owner; four
new owners are built, validated on Postgres, and independently releasable; no
module imports a sibling; the typed seams the assembly needs are specified; and
every conditional gap has a checked-in reuse-or-build disposition.

**Does not mean:** anything runs. No product pins these modules, no migration has
executed outside a scratch database, and the Vendor assembly that composes them
does not exist yet. That work is the next task, and it is where the obligations
above become real.
