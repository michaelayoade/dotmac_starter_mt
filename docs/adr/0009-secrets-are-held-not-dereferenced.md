# ADR-0009: Secrets are held, not dereferenced

- **Status:** Accepted (2026-08-08)
- **Scope:** Fleet-wide. The kernel's contract; every product built on it.
- **Relates to:** ADR-0003 (declared extension points), ADR-0008 (declaration
  registries)

## Context

Three postures exist across the fleet for getting a secret into an application:

- the starter kernel encrypts secret settings at rest, with keys from the
  environment and no network fetch;
- `dotmac_sub` stores `bao://secret/settings/<domain>#<key>` as a setting's
  *value* and resolves it against OpenBao on the read path;
- `dotmac_erp` did both, plus a bootstrap-key fallback that silently
  substituted a default when a fetch failed.

These get argued as a transport question — *may the kernel read over the
network?* — which skips the question that actually decides the design: **for a
given secret, which system is authoritative, and when is the value obtained?**

The kernel is the strategic foundation for products that do not exist yet. It
will meet compliance regimes, secret stores and deployment topologies nobody
has described. Whatever it decides here has to survive that.

## Decision

**A secret is held, never dereferenced. Nothing in the kernel resolves a secret
from a network store while handling a request.**

A value that cannot be held is not a setting. If it must live in a store, the
product reads it and installs it — via
`dotmac_kernel.secret_sources.SecretSource` for named material, or
`dotmac_kernel.settings_crypto.KeyProvider` for settings encryption keys — or
seeds it as a real setting. It never enters `domain_settings` as a reference the
kernel dereferences.

A row whose value merely *looks* like a reference (`bao://…`) resolves to that
string. The kernel does not recognise the scheme, does not fetch it, and does
not fail; it is simply a value.

Both installation seams share one set of semantics, all of them consequences of
"held, not fetched":

- loaded **once**, at install, so a lookup is an in-memory read;
- rotation is an **explicit refresh**, never a TTL — a rotation takes effect
  when an operator says so, not when a timer fires;
- a **failed refresh keeps the working set**, so a store briefly unreachable
  during a rotation leaves a working process working;
- a **failing source raises at install**, and there is no degraded-start option:
  that is the flag switched on during an incident and never switched back;
- a source **raises when its store is unreachable** and never returns empty,
  which is indistinguishable from "nothing is configured";
- **values are never logged, repr'd, or quoted in an exception.** Only names —
  even a wrapped exception is reported by type alone, because a store client's
  own error can quote the payload it choked on.

The kernel ships no provider, no source and no store client. The dependency on
OpenBao, a cloud secret manager or a mounted file stays in the product.

## Why this rather than a resolution seam

The rejected alternative was for the kernel to offer resolution *timing* as a
contract — boot-loaded and read-time paths — with product-declared secret
classes mapping onto them. It is more general, and that is the problem.

**It would bake policy into the kernel.** A class taxonomy (say
key / issued / third-party-copy) is one organisation's way of reasoning about
where secrets may live. A different deployment — OEM, on-premise, a customer
under its own regime — carves it differently and then has to fight the kernel's
categories. That is exactly the mistake `SettingDomain` made as an enum, and
ADR-0008 exists because of it: a kernel encoding its first consumer's vocabulary
as though it were universal.

**It would make an operational property negotiable.** Under the alternative,
whether a store sits on the per-request read path depends on how a product
declares things. Under this decision it never does, and that is testable. A
property that holds by construction does not erode under pressure from the next
product with a deadline.

**It builds nothing speculatively.** A general secret-resolution framework
designed with no product attached is how the kernel's original settings module
got weak enough to need replacing.

The cost is real: store integration lives in products, and without a shared
shape each would reinvent it. `dotmac_kernel.secret_sources` and `KeyProvider` are that
shape — the pattern without the dependency.

## Consequences

- **The kernel's read path can never be taken down by a secret store.** Not by
  an outage, and not by latency, which is the worse failure: a slow store puts
  its latency on every request that reads any setting, and presents as the
  application being slow rather than the store being down.
- **Provenance stays honest.** `resolve_with_source` reports what produced the
  value in effect. Under dereferencing it could say a row won but not what the
  row meant, making the settings screen a liar.
- **A compliance ruling is no longer a kernel input.** Whether a given secret
  may live in the application database encrypted at rest becomes a per-secret
  product question — *is this a setting, or a reference my product resolves?* —
  answerable either way, and changeable in three years, with no kernel change
  and nothing blocked waiting on it.
- **Key material stays out of the database it protects.** A key that encrypts
  rows must not live beside them; `KeyProvider` and `SecretSource` are how it
  gets in without doing so.
- **Products with existing reference-valued settings must migrate.** Either the
  value becomes a real setting (encrypted at rest) or it stops being a setting
  and is installed at boot. `dotmac_sub` has ~13 such specs; three of them are
  keys protecting data in the same database and must take the second path.

## Enforcement

- `tests/unit/test_secret_sources_no_network.py` — resolution of a real encrypted
  secret, and of a bulk read, completes with `socket.socket` and
  `socket.create_connection` patched to raise. Includes a sensitivity proof
  that the patch fires, so the suite cannot pass vacuously.
- `tests/architecture/test_secrets_are_held.py` — no module on the resolution
  path imports anything that could open a socket (`secret_sources.py` included: the
  module that holds secrets must not also be able to fetch them). Includes a
  sensitivity proof that the detector catches a planted import.
- Neither check alone is sufficient: the runtime proof catches a dereference
  however it is spelled but only on paths a test drives; the static check covers
  every path but only the spellings it knows.
