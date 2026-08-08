# ADR-0010: Adapters are thin, and an adapter must be identifiable

- **Status:** Accepted (2026-08-08)
- **Scope:** Fleet-wide. Every Dotmac repository.
- **Relates to:** ADR-0002 (feature structure), ADR-0003 (business rules in
  services), ADR-0008 (declaration registries)

## Context

The rule itself is old and uncontroversial, and already stated in the fleet
standard: *keep routes, web handlers, jobs, webhooks, commands, and delivery
integrations as thin adapters around the owner.* A decision belongs to one
service; an adapter validates, authorizes, delegates, and renders.

`dotmac_starter_mt` enforces it (hard rule 1,
`tests/architecture/test_thin_wrappers.py`). No other repository does, and the
reason is not negligence — it is that **the rule was unenforceable there**, for
a reason worth writing down.

Measured in `dotmac_erp` on 2026-08-08:

| Layer | Direct DB access | Files |
|---|---|---|
| `app/api` | 21 | 11 |
| `app/web` | 27 | 9 |
| `app/services/*web*.py` | **1223** | **83** |

115 web modules live inside `app/services/`. A rule scoped to the `app/web/`
DIRECTORY would have passed while missing 96% of the violations — and reported
compliance while doing it. That is worse than no rule, because it converts an
unknown into a false assurance.

## Decision

**Logic lives in services. Routes, web handlers, tasks, jobs, CLI commands and
webhook receivers validate, authorize, delegate and render — nothing else. They
issue no database query directly.**

And the part that makes it enforceable rather than aspirational:

**An adapter must be identifiable by a rule, not by inspection.** A repository
adopting this ADR declares how its adapters are recognised, by one of:

1. **Naming convention** — `router.py` / `web.py`, as the starter mandates.
   Simplest, and the reason its check is three lines of `rglob`.
2. **Registration** — a module that mounts a router, registers a task, or binds
   a CLI command IS an adapter, discovered from the registry rather than the
   filesystem. More robust where no convention exists or where renaming is
   impractical.

Directory location is **not** an acceptable identifier on its own. ERP is the
worked example of why.

## Enforcement

Each repository carries an architecture test that:

- **walks a set it asserts on.** A glob that silently matches nothing passes,
  and a vacuous check is indistinguishable from a clean one. The starter's
  `test_router_scan_is_not_vacuous` exists for this and every adopting repo
  needs its equivalent.
- **holds installed module packages to the same standard as the host.** A
  module that escaped the check would be held to a WEAKER standard than the
  assembly installing it, which is backwards: it is the less-reviewed code.
- **ratchets where debt exists.** A repository with existing violations adopts
  with an allowlist seeded at the current count that may only shrink — the shape
  `test_no_orphan_settings.py` uses. The value is not retiring the debt on day
  one; it is that new code cannot add to it.

## Consequences

- **Scope and ownership stop leaking into adapters.** ERP's settings work found
  six reads resolving a setting inside a web or api module, each also failing to
  state an organization. That is not a coincidence: an adapter reaching past its
  service has no natural place to get the scope from, so it improvises. Fixing
  the layering fixes the scoping, because a service method takes its owner
  explicitly.
- **A repository whose adapters cannot be identified must fix that first.**
  Renaming 83 modules, or building registration-based discovery, is the
  prerequisite — not a cleanup to do afterwards.
- **The allowlist is public debt.** Its size is a number in the repository that
  only goes down, which is a far better artefact than a wiki page nobody reads.
- Adopting repositories will find, as ERP did, that the count is concentrated:
  eight modules held 40% of ERP's violations. The tail is cheap; the head is
  where the work is.

## What this does not say

It does not say adapters are forbidden from touching the ORM at all — a route
may load the object it is about to render. It says a DECISION is not made there.
Where that line sits in a marginal case is a judgement for review; the check
catches the unambiguous form (a query in an adapter) and that is where nearly
all of the 1271 measured occurrences live.

It also does not mandate a directory layout. `app/services/x/web.py` is fine if
the repository's identification rule finds it. The starter's layout is one
answer, not the required one.
