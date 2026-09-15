# PEP 503 name-normalisation: exact duplication inventory

**Status:** open, exact, two entries. Enforced by
`tests/architecture/test_pep503_normalisation_duplication.py`.

## What is duplicated

The *validating* form of PEP 503 distribution-name normalisation: collapse
runs of `-`/`_`/`.` to a single `-`, lower-case the result, refuse any input
character outside `[A-Za-z0-9._-]`, and refuse a normalised result that
starts or ends with `-` (PEP 503 normalisation does not strip an edge
separator, and no valid distribution name can start or end with one).

| # | Repository | Location | Symbol |
|---|---|---|---|
| 1 | `dotmac_erp` | `scripts/dependency_normalisation.py` | `normalise_name` |
| 2 | `dotmac_starter_mt` | `scripts/bundle_envelope.py` | `_normalise_pep503_name` |

Entry 1 is the fleet's one canonical owner: ERP's own
`scripts/dependency_bundle.py` **imports** the validating `normalise_name`
from `dependency_normalisation.py`, while `scripts/erp_lock.py` imports the
total `normalise_name_for_identity` form at immutable commit
`b449c4d82fdb6c19d2c9e26eab8ef85ba50528ed`; neither script defines an inline
copy. ERP consolidated these roles after discovering that edge-separator
handling differed (see `dependency_normalisation.py`'s own module docstring
at that pinned commit for the full account).

Entry 2 is this repository's port. `scripts/bundle_envelope.py`'s module
docstring ("Slice 1a-iv") records why it is a port rather than an import:
nothing yet wires this repository's dependency-free `scripts/` file into
ERP's dependency graph, so there is no shared package for either side to
import from yet.

## Correction to an earlier claim

A prior description of this duplication (the packet that requested this
inventory) characterized it as a **third** copy, alleging `dotmac_erp`
carries independent copies in both `scripts/dependency_bundle.py` and
`scripts/erp_lock.py`. That is not the current state. At the pinned commit,
the source confirms the two ERP callers use the canonical helpers for
byte-compatible semantics:
(`b449c4d82fdb6c19d2c9e26eab8ef85ba50528ed`), `scripts/erp_lock.py` imports
`normalise_name_for_identity` from `dependency_normalisation.py` — it holds
no inline copy of its own. The
fleet-wide count today is **two** implementations of this semantics, not
three: ERP's single owner, and this repository's port.

## Parity requirement

Entries 1 and 2 must preserve valid-string semantics: a plan digest or bundle
manifest ERP computes must be reproducible by a Starter-side verifier
computing the same PEP 503 package-directory name from the same valid string
input. Starter additionally refuses non-string input; that is an intentional
typed-boundary difference, not a parity failure.
`tests/architecture/test_pep503_normalisation_duplication.py` pins entry 2
against the exact boundary
cases entry 1's own docstring documents as fixed (run-collapsing,
input-charset refusal, edge-separator refusal) — the closest thing to a
cross-repository equality check available from a single checkout.

## Retirement gate

This entry is removed in the SAME change that gives entry 2 a real shared
owner to import instead of port — not merely when one exists, but when a
consumer (ERP, at cutover, per `scripts/bundle_envelope.py`'s module
docstring) actually depends on it as published. The retirement seam is still
unresolved: the shared owner must be an importable package, while the action
that consumes the envelope remains SHA-pinned; neither packaging choice is
silently treated as solved here. Until then, this inventory stays open, and
its two-entry count is enforced two-directionally: a rise means an
undocumented third copy landed somewhere and nobody recorded it; a fall
without the named copy actually being deleted in the same change means the
detector stopped seeing a copy that still exists, which reads like progress
and is not.
