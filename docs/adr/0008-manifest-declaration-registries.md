# ADR 0008 — Module-declared vocabularies, never host-enumerated lists

**Status:** Accepted — **fleet-wide**
**Date:** 2026-08-07
**Applies to:** every Dotmac repository, not only this one. Enforcement for other
repositories lands through the pinned Governance source
(`michaelayoade/dotmac_governance`, see hard rule 15); this ADR is the statement
of the rule and the reference implementation.
**Extends:** ADR-0003 (composable deployment profiles), ADR-0006 (white-label
product foundation)
**Owns:** the rule that a vocabulary is DECLARED by the modules that own its
members, never enumerated by the layer that hosts it — and the uniform registry
shape every such vocabulary uses
**Does not own:** what any individual vocabulary means (permissions, capabilities,
audit actions, feature flags and setting domains each keep their own semantics),
nor the module registry itself (`dotmac_kernel.modules`)

## Context

A kernel serves products it has never seen. Any list it hard-codes of values its
consumers will need is a list that will be wrong for the second consumer.

`SettingDomain` made this concrete. It was a five-member enum — `auth`, `audit`,
`branding`, `custom_fields`, `display` — and a `sa.Enum` CHECK constraint pinning
the column to those five. Those are *this repo's* domains. `dotmac_erp` runs
twenty-one and `dotmac_sub` twenty-eight. ERP could not
have adopted the kernel's settings subsystem without either abandoning its own
domains or landing a kernel migration per product — and "the kernel ships a
migration whenever a product invents a value" is not a foundation, it is a
bottleneck with a version number.

The same pressure had already been resolved four times, each time the same way
and each time as a local decision rather than a stated rule:

| Vocabulary | Declared as | Validated at |
|---|---|---|
| Permissions | `permissions=(PermissionSpec(...),)` | `create_app` boot + `require_permission` |
| Capabilities | `capabilities=(CapabilitySpec(...),)` | `require_capability` |
| Audit actions | `audit_actions=(...)` | `write_audit_event` |
| Feature flags | `feature_flags=(FeatureFlagSpec(...),)` | `resolve_flag` |
| Setting domains | `setting_domains=(...)` | the settings write path |

Four instances of a pattern nobody had written down is how the fifth came to be
built as an enum in the first place.

## Decision

**A vocabulary whose members belong to modules is declared by those modules and
validated by a registry. The layer that hosts the vocabulary never enumerates its
members.**

Stated for the kernel because that is where it bites hardest, but the rule is
fleet-wide: it applies wherever one layer holds a vocabulary another layer owns
members of — a kernel serving products, a product core serving its own modules,
or a shared package serving its consumers. "The kernel" below reads as "the
hosting layer" in any repository.

Every such registry has the same shape, and a new one should be a copy of
`dotmac_kernel.audit_actions` with the nouns changed:

1. **One field on both manifests.** `FeatureManifest` and `ModuleManifest` carry
   the declaration; `ModuleManifest.from_feature` passes it through. A module
   declares only what it owns.
2. **Construction is validation.** `from_manifests` raises on a member declared
   by two modules — a vocabulary member has exactly one owning module, so there
   is no merge semantics to get wrong later.
3. **Process-active install, from the INSTALLED set, not the enabled subset.**
   `create_app` installs it before anything mounts. Disabling a module must not
   turn a real member into an undeclared one for whatever is still running, and
   stored rows (a grant, an override, a setting) outlive any deployment's
   enabled set.
4. **Not-installed is a distinct state from installed-and-empty.** An empty
   registry means "this deployment declares nothing" and correctly rejects
   everything. No registry at all is a WIRING mistake — a worker, CLI or test
   that builds no app — and must say so, or it sends the reader hunting for a
   missing declaration instead of a missing install. The two defaults differ by
   what each one *does*: an uninstalled permission catalogue denies, which is the
   safe answer for an authorization check; an uninstalled audit or settings
   registry would reject writes inside the caller's transaction and turn a wiring
   mistake into a failed business operation, so it raises a named error instead.
5. **`require()` at the boundary that uses the member**, not at declaration time.
   Declarations are import-time and process-global; a registry belongs to one
   assembly. Validating at import would make one test's imports break an
   unrelated assembly's boot.
6. **The database column is a plain string.** A CHECK constraint or a native enum
   re-imposes exactly the closed list the registry exists to open, and costs a
   kernel migration per consumer. Correctness comes from the write boundary.
7. **Both directions are governed in CI**
   (`tests/architecture/test_manifest_declarations.py`): a declared member with
   no consumer is a dead declaration and fails; a consumed member nothing
   declares fails. Burn-down allowlists start empty and may only shrink.

### Consequences

- `SettingDomain` is an open `str` subclass, not an enum. Kernel-owned domains
  are bound as class attributes (`SettingDomain.branding`) so call sites read
  unchanged; a product constructs its own (`SettingDomain("payroll")`).
- Kernel migration `0014` drops `ck_domain_settings_domain` and widens
  `domain_settings.domain` to `String(120)`. Downgrade is lossy by necessity —
  rows outside the original five cannot satisfy a restored constraint.
- Adding a sixth vocabulary is a mechanical exercise against this list, and a
  reviewer has something concrete to check it against.
- A kernel change that adds a member to a fixed list — rather than a declaration
  point for it — is a defect under this ADR, whatever the list is made of.

## Known non-conformances at acceptance

Recorded so adoption is a schedule rather than a discovery. Neither is scheduled
here; each owning repository decides when, and neither blocks the kernel port.

| Where | What | Why it is a violation |
|---|---|---|
| `dotmac_erp` `app/models/domain_settings.py:37` | `SettingDomain(enum.Enum)`, 24 members, stored as a NATIVE Postgres enum (`Enum(SettingDomain)`) on `domain_settings.domain` | Adding a domain needs an `ALTER TYPE` migration. ERP's own `DomainSettingHistory.domain` is already a `String(50)`, so the conforming shape already exists on half of its settings tables. |
| `dotmac_sub` `app/models/domain_settings.py:22` | `SettingDomain(enum.Enum)`, 29 members, same native-enum storage | Same. |

**Explicitly NOT a violation:** `dotmac_erp`'s
`app/models/finance/audit/audit_log.py::AuditAction` (`INSERT`/`UPDATE`/`DELETE`).
That is a closed row-change vocabulary for a trigger-backed audit log — no module
owns a member of it, and no module will ever add one. A closed enum is the right
type for a genuinely closed set; this ADR constrains vocabularies whose members
belong to somebody else, not every enum.

## Alternatives rejected

**Keep the enum and widen it per product.** Every product's vocabulary would
enter the kernel's source, coupling the kernel to consumers it is supposed to be
independent of, and each addition would ship a migration to every deployment.

**No validation — accept any string.** The column is free text, so a typo
(`"brandng"`) silently creates a parallel domain no reader resolves; the setting
reverts to its default and the misconfiguration is invisible. This is the same
argument that put `write_audit_event` behind a registry: a trail missing events
you believe you are reading is worse than one that is obviously empty.

**Validate at `create_app` against the registered specs.** Attempted and
reverted. The spec registry is process-global while a domain registry belongs to
one assembly, so importing the settings feature anywhere made every synthetic
assembly fail to boot. The check moved to CI over the real assembly, which is
where an assembly-wide invariant belongs.
