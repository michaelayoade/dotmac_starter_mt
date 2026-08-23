"""The prose-only writer claim is frozen and may only be retired deliberately.

`[[product_writers]]` (#354) gave writer claims a typed channel, after two
Governance rationales were contradicted by the dossiers they cited — the cited
claim lived in `local_copy_retirement` prose, and a sentence is not something a
checker can compare.

Its validator deliberately allows the block to be ABSENT, because absence must
stay distinguishable from a claim of absence: a consumer that cannot find the
row it needs has to fail as UNKNOWN. That is the right call, and it means #354
is **migration support rather than a ratchet** — on its own it leaves the prose
channel live indefinitely for everything unmigrated.

At the freeze that was 303 dossier/product pairs across 86 dossiers, with
exactly ONE dossier fully typed. This module is the ratchet over that number.

Two directions, because they fail differently:

- **growing** — a new prose-only claim is fresh debt in a channel that has
  already produced one cross-repository contradiction;
- **shrinking silently** — a baseline that tracks reality without being
  regenerated stops describing anything (ADR-0018), and the count could return
  to 303 unnoticed.

And a dossier ABSENT from the baseline must be complete, which is what stops
86 becoming 87 as packages are added.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "product_writer_sweep.py"
BASELINE = PROJECT_ROOT / "docs" / "inventories" / "product-writer-baseline.json"


def _sweep():
    spec = importlib.util.spec_from_file_location("product_writer_sweep", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── The live state ──────────────────────────────────────────────────────────


def test_no_prose_only_claim_was_added_and_none_is_stale() -> None:
    """The ratchet itself, both directions at once."""
    sweep = _sweep()
    problems = sweep.compare(sweep.untyped_pairs(), sweep._baseline())
    assert not problems, "product-writer ratchet:\n" + "\n".join(
        f"  - {problem}" for problem in problems
    )


def test_the_baseline_is_not_empty() -> None:
    """A ratchet over an empty set passes for the wrong reason.

    If this ever legitimately reaches zero, delete the baseline AND this
    module and require completeness outright — do not leave an empty file
    that reads as enforcement.
    """
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["prose_only"]
    assert baseline, "the baseline is empty; see the docstring before 'fixing' this"
    assert sum(len(products) for products in baseline.values()) > 0


def test_the_baseline_excuses_only_real_packages_and_real_products() -> None:
    """An entry naming a package that no longer exists is an exemption for
    nothing, and it inflates the count the ratchet is measuring."""
    sweep = _sweep()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["prose_only"]
    packages = {path.parent.name for path in sweep._dossiers()}
    unknown = sorted(set(baseline) - packages)
    assert not unknown, f"baseline excuses non-existent package(s): {unknown}"

    for dossier, products in baseline.items():
        assert products, f"{dossier}: an empty list excuses nothing — remove the key"
        assert sweep.SELF not in products, (
            f"{dossier}: {sweep.SELF} is this repository, not a product whose "
            "writers could need retiring; it must never be counted"
        )


# ── Sensitivity: the ratchet has to bite in both directions ─────────────────


def test_it_fires_when_a_mid_migration_dossier_gains_a_claim() -> None:
    sweep = _sweep()
    live = {key: list(value) for key, value in sweep.untyped_pairs().items()}
    baseline = sweep._baseline()
    partial = next(name for name in sorted(live) if name in baseline)
    live[partial] = sorted({*live[partial], "dotmac_newly_declared"})

    problems = sweep.compare(live, baseline)
    assert any("NEW prose-only claim" in problem for problem in problems), problems


def test_it_fires_when_a_new_dossier_is_not_fully_typed() -> None:
    """The rule that stops the debt growing with the package count."""
    sweep = _sweep()
    live = {key: list(value) for key, value in sweep.untyped_pairs().items()}
    live["dotmac-brand-new"] = ["dotmac_sub"]

    problems = sweep.compare(live, sweep._baseline())
    assert any("not in the baseline" in problem for problem in problems), problems
    assert any("immediately" in problem for problem in problems), problems


def test_it_fires_when_a_retired_pair_is_still_excused() -> None:
    """The other direction. Typing a pair without regenerating leaves the
    baseline overstating the debt, and the count can drift back up unseen."""
    sweep = _sweep()
    live = {key: list(value) for key, value in sweep.untyped_pairs().items()}
    victim = sorted(live)[0]
    remaining = live[victim][1:]
    if remaining:
        live[victim] = remaining
    else:
        del live[victim]

    problems = sweep.compare(live, sweep._baseline())
    assert any("still excused here" in problem for problem in problems), problems


def test_an_unchanged_set_produces_no_findings() -> None:
    """SPECIFICITY for the three proofs above: it must object because something
    changed, not because it objects to everything."""
    sweep = _sweep()
    live = sweep.untyped_pairs()
    assert sweep.compare(live, live) == []
