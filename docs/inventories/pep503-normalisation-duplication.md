# PEP 503 name-normalisation: bounded local pattern inventory

**Status:** open. Starter's observed `scripts/` AST pattern has a source-derived
inventory; alternative implementations, other production roots and ERP are
unmonitored from this repository's CI. Enforced by
`tests/architecture/test_pep503_normalisation_duplication.py`.

## What is duplicated

The *validating* form of PEP 503 distribution-name normalisation: collapse
runs of `-`/`_`/`.` to a single `-`, lower-case the result, refuse any input
character outside `[A-Za-z0-9._-]`, and refuse a normalised result that
starts or ends with `-` (PEP 503 normalisation does not strip an edge
separator, and no valid distribution name can start or end with one).

| # | Repository | Location | Symbol |
|---|---|---|---|
| 1 | `dotmac_starter_mt` | `scripts/bundle_envelope.py` | `_normalise_pep503_name` |

The reported ERP owner, `scripts/dependency_normalisation.py::normalise_name`,
is **unmonitored** here. Candidate CI neither fetches nor holds an immutable
ERP source tree, so it cannot honestly assert a fleet-wide exact count or
ratchet. The pinned ERP provenance in `scripts/bundle_envelope.py` remains a
compatibility reference, not locally enforced inventory evidence.

Entry 1 is this repository's port. `scripts/bundle_envelope.py`'s module
docstring ("Slice 1a-iv") records why it is a port rather than an import:
nothing yet wires this repository's dependency-free `scripts/` file into
ERP's dependency graph, so there is no shared package for either side to
import from yet.

## Detection boundary

The architecture test parses Python files under `scripts/` and recognises
only the port's syntactic pattern: top-level `re.compile` literals for the
`[-_.]+` run and input alphabet, with direct function-body expressions for
collapse, lowercasing, and leading/trailing separator checks. A sensitivity
plant adds a second copy of that pattern and must be found; an adjacent
whitespace normaliser is not found. Equivalent alternate syntax, aliases,
nested/unreachable bodies, other Starter production roots, and ERP are
unmonitored. This is not a semantic or fleet-wide enumeration claim.

## Parity requirement

Starter's port and ERP's reported owner are intended to preserve valid-string
semantics: a plan digest or bundle manifest ERP computes must be reproducible
by a Starter-side verifier computing the same PEP 503 package-directory name
from the same valid string input. Starter additionally refuses non-string
input; that is an intentional typed-boundary difference, not a parity failure.
The local architecture test pins Starter against the boundary cases in its
checked-in contract (run-collapsing, input-charset refusal, edge-separator
refusal). This is not a cross-repository equality check.

## Retirement gate

This entry is removed in the SAME change that gives Starter a real shared
owner to import instead of port — not merely when one exists, but when a
consumer (ERP, at cutover, per `scripts/bundle_envelope.py`'s module
docstring) actually depends on it as published. The retirement seam is still
unresolved: the shared owner must be an importable package, while the action
that consumes the envelope remains SHA-pinned; neither packaging choice is
silently treated as solved here. Until then, this inventory stays open. An
additional `scripts/` implementation with the observed AST pattern fails the
local inventory test. ERP needs its own immutable-source CI gate (or a
separately verified cross-repository artifact) before any fleet-wide count
can be asserted.
