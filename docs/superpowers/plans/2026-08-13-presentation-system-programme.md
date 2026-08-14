# Presentation system — delivery programme

**Status:** foundation delivery complete as of 2026-08-14; ongoing work is
demand-gated or ratcheted maintenance. `docs/superpowers/plans/` is
non-authoritative (see `CLAUDE.md`'s docs hierarchy). The decisions this plan
executes live in ADR-0006's 2026-08-13 amendment; where the two disagree, the
ADR wins.

**Date:** 2026-08-13.

The presentation foundation was delivered through **adoption-led releases**.
Each train below is ordered by the value and risk of what it retires, not by the
order ADR-0006 § 1 happens to list concepts in.

## Trains

| # | Outcome | Gate |
|---|---|---|
| 0 | Recover existing work | Fresh branches from current `origin/main` |
| 1 | First component **candidate** released | `empty_state` published as an audit-complete candidate — no reuse claim |
| 1b | Component slice **reuse-proven** | that candidate adopted by two independent products, local copies retired |
| 2 | Safe branding | `custom_css` rejected and never rendered |
| 3 | Real runtime branding | Same-origin generated token stylesheet |
| 4 | Palette convergence | Debt falls through coherent surface slices |
| 5 | Component expansion | Two consumers and local-copy retirement per component |
| 6 | Theme contract | Only after proven non-token structural demand |
| 7 | List surface | Only after ADR-0017 permits the kernel contract |

## Foundation closure

Trains 0, 1, 1b, 2 and 3 are complete. ERP and Sub independently consume the
released `empty_state` contract on their default branches, so both the token and
component slices are `reuse-proven`. Train 4 continues as an enforced
two-directional maintenance ratchet with a baseline of 851; it is not a reason
to keep a component-foundation project open. Trains 5–7 are demand gates, not a
pre-committed backlog.

**Train 2 precedes Train 3 deliberately.** Retiring tenant-supplied raw CSS is a
security correction to an existing surface, so it is not gated by ADR-0017 and
must not wait behind the feature that replaces it.

## Which products, and why the choice is a plan rather than a decision

`dotmac-ui` is **dependency-free**, so adopting it requires no kernel adoption
and is **not** gated by ADR-0017's migration-lineage moratorium. That applies to
every product, `dotmac_sub` included — an earlier draft of this programme
claimed otherwise and was wrong.

Sequencing is therefore about readiness, not permission:

- **`dotmac_erp` first** — completed on its default branch. Its production macro
  and direct macro test supplied the qualifying product-first source; the local
  macro now preserves legacy aliases as a thin adapter over the canonical
  package renderer.
- **`dotmac_sub` second** — completed on its default branch through promotion PR
  #2380. Its six live includes now pass through a thin include-context adapter,
  and its product-owned architecture test resolves the released package
  component through the real loader. ERP + Sub establish `reuse-proven`.
- **`dotmac_crm`** — technically eligible but explicitly not an active priority.
  Its remaining local template is optional cleanup rather than a programme gate.
- **`dotmac_academy_app` later** — already a token consumer and eligible, but it
  has no measured local `empty_state` owner to retire, so it is weaker evidence
  for this extraction than the three products above.

Any two independent products move a component slice to `reuse-proven`.
Consumption by the starter is reference proof, recorded separately in the
dossier, and never counts.

### Breaking the circularity

An earlier draft of this plan gated the *release* on two consumers, which cannot
happen: a product cannot adopt a component that has never been published. The
two states are distinct and the dossier now types them per slice:

- **Candidate (`audit-complete`)** — published so a product CAN adopt it. It
  carries no reuse claim, its `contract_consumers` list is empty, and the
  package headline drops to match, because a package is only as proven as its
  least-proven published contract. Publishing a candidate is permitted by the
  ownership map, not by consumer count.
- **Reuse-proven** — two independent products are on the released contract and
  their local copies are deleted.

Shipping the candidate is therefore not "extraction on thin evidence": it is the
only move that makes the evidence obtainable, and the dossier states plainly
that the evidence is not yet there.

The candidate has now completed that path: ERP and Sub consume released
0.1.0a7 on their default branches, retain only product-seam adapters, and carry
independent adoption tests. The component slice and package headline are
therefore `reuse-proven`.

The candidate itself is deliberately narrower than ERP's whole macro. The
shared contract is `title`, optional `message`, and an action label/URL pair,
with a package-owned decorative visual. Raw SVG input, host `/static/` paths,
English-title illustration inference and ERP's alias set stay out: copying those
would move product compatibility policy into the design-system owner rather
than extract the independently shared behaviour.

## Train 5 — candidate rulings

Extraction requires two live independent consumers, identical inputs/semantics/
failure behaviour, a product-first source, clean-host rendering, token-native
CSS, accessibility and browser-behaviour tests, exact released pins, and deletion
of the superseded local copies.

- `alert` and `modal` stay local until two LIVE consumers exist; CRM's copies
  have no callers.
- `form-validation.js`, `repeatable-fields.js` and `unsaved-changes.js` need
  exercised DOM/event/failure canaries before extraction — byte-identity is not
  behavioural evidence.
- `csv-parser.js` is import/data behaviour, not automatically a design-system
  asset.
- Recent activity, ledger, triage, kanban, gantt and topology stay product
  composites.
- Charts share `--dmui-chart-*` roles only; chart engines and currency/locale
  behaviour stay product-owned.

## Train 3 — response policy for the brand stylesheet

Same-origin endpoint (e.g. `/branding/theme.css`), loaded after the base UI
stylesheet and any trusted theme stylesheet. `Content-Type: text/css`;
`Cache-Control: private, no-store`; no inline `<style>`; no raw user CSS; a
generic fallback if generation fails; never log a complete rendered brand
payload. Logos accept validated same-origin packaged paths only until an
authoritative storage facility exists.
