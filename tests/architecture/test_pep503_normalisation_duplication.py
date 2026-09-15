"""Exact, non-growing duplication inventory: PEP 503 name normalisation.

`dotmac_erp`'s `scripts/dependency_normalisation.py::normalise_name` is the
fleet's one canonical owner of the *validating* PEP 503 name-normalisation
semantics. `scripts/bundle_envelope.py::_normalise_pep503_name` in THIS
repository ports its valid-string behaviour while additionally refusing
non-string input (see that module's docstring, "Slice 1a-iv"). It is a port
rather than an import because nothing yet wires this
dependency-free `scripts/` file into ERP's dependency graph. That makes this
repository's copy a second, independently-maintained implementation of the
same semantics.

**Correction to an earlier claim.** A prior description of this duplication
characterized it as a THIRD copy, alleging `dotmac_erp` also carries an
inline copy in `scripts/erp_lock.py`. That is false at the pinned commit
this repository's own docstring already names for byte-compatibility
(`b449c4d82fdb6c19d2c9e26eab8ef85ba50528ed`): `erp_lock.py` imports
the total `normalise_name_for_identity` from `dependency_normalisation.py`
rather than defining its own — ERP already consolidated onto one owner after
its two scripts had
independently diverged (see `dependency_normalisation.py`'s own module
docstring for the account). The fleet-wide count today is TWO
implementations, not three. See
`docs/inventories/pep503-normalisation-duplication.md` for the full record.

**The ratchet runs both ways.** `KNOWN_PEP503_NORMALISATION_COPIES` below is
exact. A RISE means an undocumented third copy landed somewhere and nobody
recorded it. A FALL means an entry was deleted from this list without its
underlying copy actually being retired in the same change — which reads
exactly like progress and is not; the only sanctioned way to shrink this
list is to delete the copy it names in the SAME change (see the inventory
doc's "Retirement gate").
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_DOC = (
    PROJECT_ROOT / "docs" / "inventories" / "pep503-normalisation-duplication.md"
)

# The exact, named list of every known implementation of PEP 503's
# *validating* name-normalisation semantics (collapse runs of `-`/`_`/`.` to
# a single `-`, lower-case, refuse an input character outside
# `[A-Za-z0-9._-]`, refuse a normalised result with a leading/trailing `-`)
# across this repository and its sibling `dotmac_erp` checkout. Adding an
# entry here without a corresponding retirement plan is exactly the drift
# this inventory exists to catch.
KNOWN_PEP503_NORMALISATION_COPIES = (
    "dotmac_erp:scripts/dependency_normalisation.py:normalise_name",
    "dotmac_starter_mt:scripts/bundle_envelope.py:_normalise_pep503_name",
)


def _load_bundle_envelope():
    module_name = "bundle_envelope_pep503_dup_check"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, PROJECT_ROOT / "scripts" / "bundle_envelope.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_the_inventory_doc_exists_and_names_the_recorded_symbols() -> None:
    """The inventory doc and this list must reference the same two symbols
    — a doc that drifts from the enforced list would make the record and
    the gate disagree about what is actually tracked."""

    assert INVENTORY_DOC.is_file()
    text = INVENTORY_DOC.read_text(encoding="utf-8")
    assert "normalise_name" in text
    assert "_normalise_pep503_name" in text


def test_the_inventory_is_exactly_two_entries_today() -> None:
    """DESIGNED BREAK CONDITION (both directions). Breaks if a third copy is
    added to the list without a matching retirement (count rises to 3), and
    breaks if an entry is deleted from the list without this assertion being
    lowered in the same change (count falls below 2) — both read as drift,
    not as a legitimate shrink, unless the underlying copy was actually
    retired in the same diff."""

    assert len(KNOWN_PEP503_NORMALISATION_COPIES) == 2


def test_the_inventory_names_starters_own_copy_by_its_exact_symbol() -> None:
    """This repository's own entry is pinned to the exact function
    `build_local_index` calls — a silent rename here would desynchronise the
    inventory from the code it describes."""

    assert (
        "dotmac_starter_mt:scripts/bundle_envelope.py:_normalise_pep503_name"
        in KNOWN_PEP503_NORMALISATION_COPIES
    )


def test_starters_copy_still_exists_at_its_recorded_location() -> None:
    """Confirms the symbol the inventory names still exists, rather than
    having silently moved or been renamed out from under the inventory."""

    module = _load_bundle_envelope()
    assert hasattr(module, "_normalise_pep503_name")


def test_starters_copy_matches_the_known_pep503_boundary_cases() -> None:
    """Parity note made executable. `dependency_normalisation.py`'s own
    docstring documents ERP having already suffered a silent divergence
    between two of its own scripts on exactly this boundary (edge-separator
    stripping) before it consolidated onto one owner. This repository
    cannot import ERP's copy directly (separate checkouts/deployments), so
    this pins THIS repository's port against the same boundary cases
    instead: run-collapsing, case-folding, input-charset refusal, and
    edge-separator refusal on the normalised output.

    Breaks if `_normalise_pep503_name`'s collapsing, case-folding, charset
    check, or edge-separator check silently diverges from ERP's owner.
    """

    module = _load_bundle_envelope()
    normalise = module._normalise_pep503_name

    assert normalise("Dotmac_Kernel") == "dotmac-kernel"
    assert normalise("dotmac--kernel") == "dotmac-kernel"
    assert normalise("dotmac.kernel") == "dotmac-kernel"

    for invalid in ("-dotmac-kernel-", "dotmac/kernel", "dotmac kernel"):
        try:
            normalise(invalid)
        except ValueError:
            continue
        raise AssertionError(f"{invalid!r} should have been refused")
