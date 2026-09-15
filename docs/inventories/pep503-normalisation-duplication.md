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

Entry 1 is the fleet's one canonical owner: ERP's own `scripts/dependency_bundle.py`
and `scripts/erp_lock.py` both **import** `normalise_name` from
`dependency_normalisation.py` rather than defining their own copy — ERP
consolidated this itself after discovering the two scripts had already
diverged on edge-separator handling (see `dependency_normalisation.py`'s own
module docstring, at the pinned commit below, for the full account).

Entry 2 is this repository's port. `scripts/bundle_envelope.py`'s module
docstring ("Slice 1a-iv") records why it is a port rather than an import:
nothing yet wires this repository's dependency-free `scripts/` file into
ERP's dependency graph, so there is no shared package for either side to
import from yet.

## Correction to an earlier claim

A prior description of this duplication (the packet that requested this
inventory) characterized it as a **third** copy, alleging `dotmac_erp`
carries independent copies in both `scripts/dependency_bundle.py` and
`scripts/erp_lock.py`. That is not the current state. At the pinned commit
this repository's docstring already names for byte-compatibility
(`b449c4d82fdb6c19d2c9e26eab8ef85ba50528ed`), `scripts/erp_lock.py` imports
`normalise_name`/`normalise_name_for_identity` from
`dependency_normalisation.py` — it holds no inline copy of its own. The
fleet-wide count today is **two** implementations of this semantics, not
three: ERP's single owner, and this repository's port.

## Parity requirement

Entries 1 and 2 must agree byte-for-byte on every input: a plan digest or
bundle manifest ERP computes must be reproducible by a Starter-side verifier
computing the same PEP 503 package-directory name from the same input (see
`scripts/bundle_envelope.py`'s module docstring, "Byte-for-byte compatibility
is the whole point"). `tests/architecture/test_pep503_normalisation_duplication.py`
pins entry 2 against the exact boundary cases entry 1's own docstring
documents having fixed (run-collapsing, input-charset refusal, edge-separator
refusal) — the closest thing to a cross-repository equality check available
from a single checkout.

## Retirement gate

This entry is removed in the SAME change that gives entry 2 a real shared
owner to import instead of port — not merely when one exists, but when a
consumer (ERP, at cutover, per `scripts/bundle_envelope.py`'s module
docstring) actually depends on it as published. Until then, this inventory
stays open, and its two-entry count is enforced two-directionally: a rise
means an undocumented third copy landed somewhere and nobody recorded it; a
fall without the named copy actually being deleted in the same change means
the detector stopped seeing a copy that still exists, which reads like
progress and is not.
