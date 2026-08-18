"""`ig_0001`'s literal `depends_on` is a frozen deviation, and the last one.

## The defect, proved rather than asserted

`packages/dotmac-integration/.../ig_0001_connector_control_plane.py` sets

    depends_on = ("0001_initial_tenant_schema",)

ADR-0006 D1's amendment forbids exactly this: "A module lineage may **not**
name a foreign revision directly," because the edge is true only in the
assembly that wrote it. Host owners (`kernel`, `assembly`) keep literal edges;
an installable module does not.

## Legacy, not defiance — and the remedies differ

`ig_0001`'s own docstring still says *"Cross-lineage ordering is `depends_on` by
rule — a `down_revision` across owners would splice two independently released
lineages into one chain."* That WAS the rule when the file was written
(`Create Date: 2026-08-13`). The amendment landed in `2699b2b` (#147,
2026-08-14 05:16 +0100) and `ig_0001` merged in `02006c3` (#152, 07:06 +0100) —
one hour and fifty minutes later, on a branch cut before the rule moved. The
file therefore documents the superseded rule accurately and was never brought
forward.

That distinction is the reason this is a recorded deviation rather than a
defect report. "Someone broke the rule" is repaired by fixing the file; "the
rule changed under a frozen artifact" cannot be, because by the time anyone
noticed, the artifact was published. The remedy available is to declare the
EFFECT portably (done: `module_database_roles.v1`, on the manifest and in
`ig_0007`) and to stop the population growing (this file).

## What the deviation costs, and why no assembly can absorb it

Alembic resolves `depends_on` while building the revision map, so in any
composition that does not also carry kernel `0001_initial_tenant_schema` the
`ig` lineage cannot be READ — not `upgrade`, not `heads`, not `history`.
`test_the_literal_makes_the_lineage_unreadable_alone` demonstrates that against
the real files, and its twin shows the same lineage loading cleanly once the
edge is logical.

**A prerequisite binding cannot rewrite an edge a released migration hard-codes.**
`resolve_depends_on` is a function the migration calls; a literal tuple never
consults the binding registry, and there is no hook between the assembly and
`ScriptDirectory` that could substitute one. So the only way an assembly can
cope is to compose the lineage that actually contains the revision id
`0001_initial_tenant_schema` — which means an adopter that cannot run kernel
`0001` cannot install this module at all, at any version through `0.1.0a4`.
That is the thing the retirement gate below buys back, and it is strictly worse
than a lint violation: it is an installability bound.

## Why it is not repaired here

`ig_0001` shipped in `dotmac-integration-v0.1.0a1`, `-v0.1.0a2` and
`-v0.1.0a3`. `tests/architecture/test_released_migrations.py` records its
SHA-256 at each tag and cross-checks each against the blob git holds there;
"history is bytes" is that guard's premise and this change does not get to hole
it on the way past. Editing the file is therefore not available in `0.1.0a4`,
and no ADDITIVE revision can undo a `depends_on` belonging to a different one —
the attribute is read from `ig_0001`'s own module namespace.

So `0.1.0a4` declares the EFFECT the literal was standing in for
(`module_database_roles.v1`, on the manifest and in `ig_0007`), which is the
portable, statically enforced half, and leaves the physical edge frozen. The
literal is recorded here rather than exempted, because an unrecorded deviation
is indistinguishable from an oversight, and the next author reaching for
`depends_on = (` needs to meet a failing test rather than a precedent.

## The ratchet (ADR-0018)

Two-directional and population-exact. It fails when a SECOND literal appears,
and it fails when this one disappears without the record being lowered in the
same change — the second direction being what stops the guard from quietly
becoming an assertion about nothing. The premise is enforceable because
"literal" is a syntactic property of the assignment, read from source.

## Retirement gate

This entry is removed, and the population assertion flipped to zero, in the
change that rebases the `ig` lineage under a new root for a breaking version —
the only mechanism that can retire a hard-coded edge, since neither an additive
revision nor an assembly binding can reach it.

The gate that forces it: the first adopter that must install
`dotmac-integration` without running kernel `0001`. ADR-0024 §§ 6-7 names
exactly one composer today — `dotmac_integrator`, a thin assembly that pins the
kernel and does run `0001` — which is why this is a tracked bound rather than a
live outage, and why `0.1.0a4` is publishable with it. It is not a reason to
leave it: the bound is invisible from inside the one assembly that satisfies it,
which is precisely how it survived three releases.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_VERSIONS = sorted((REPO_ROOT / "packages").glob("*/src/*/migrations/versions"))

#: `(distribution directory name, revision file name, the literal it carries)`.
#: EXACTLY these, no more and no fewer.
FROZEN_LITERAL_DEPENDS_ON: dict[tuple[str, str], tuple[str, ...]] = {
    (
        "dotmac-integration",
        "ig_0001_connector_control_plane.py",
    ): ("0001_initial_tenant_schema",),
}


def _literal_depends_on(path: Path) -> tuple[str, ...] | None:
    """The `depends_on` value, when it is a literal tuple/list/str of names.

    Returns `None` for `depends_on = None`, for an absent assignment, and for
    `resolve_depends_on(...)` — a Call is precisely the compliant form, so the
    detector distinguishes on the NODE TYPE rather than on a text match.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == "depends_on"):
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return (value.value,)
            if isinstance(value, ast.Tuple | ast.List):
                names = [
                    element.value
                    for element in value.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                ]
                if names:
                    return tuple(names)
    return None


def _found() -> dict[tuple[str, str], tuple[str, ...]]:
    found: dict[tuple[str, str], tuple[str, ...]] = {}
    for versions in MODULE_VERSIONS:
        distribution = versions.parents[3].name
        for path in sorted(versions.glob("*.py")):
            literal = _literal_depends_on(path)
            if literal is not None:
                found[(distribution, path.name)] = literal
    return found


def test_the_module_lineages_carry_exactly_the_recorded_literals() -> None:
    """Both directions. A new literal fails; a retired one fails until recorded."""
    assert MODULE_VERSIONS, "no module lineages found; the scan covers nothing"
    found = _found()

    appeared = sorted(set(found) - set(FROZEN_LITERAL_DEPENDS_ON))
    assert not appeared, (
        f"{appeared}: a module lineage names a foreign revision directly. "
        "Declare the EFFECT in the manifest's `requires` and call "
        "`resolve_depends_on(...)`; the assembly binds it (ADR-0006 D1)"
    )
    retired = sorted(set(FROZEN_LITERAL_DEPENDS_ON) - set(found))
    assert not retired, (
        f"{retired}: the literal is gone — lower the record in the SAME change, "
        "which is what makes this a ratchet rather than a claim about nothing"
    )
    assert found == FROZEN_LITERAL_DEPENDS_ON, "a recorded literal changed value"


def test_the_deviation_is_a_population_of_one() -> None:
    """Stated as a number so a second entry is a visible decision, not a diff hunk."""
    assert len(FROZEN_LITERAL_DEPENDS_ON) == 1


def test_the_detector_distinguishes_the_compliant_form(tmp_path: Path) -> None:
    """Sensitivity and specificity in one place.

    A detector that matched the text `depends_on = (` would fire on every
    compliant lineage; one that matched nothing would pass this whole file for
    the wrong reason. Both spellings are put in front of it.
    """
    literal = tmp_path / "xx_0001_literal.py"
    literal.write_text(
        'revision = "xx_0001"\n'
        "down_revision = None\n"
        'depends_on = ("0001_initial_tenant_schema",)\n',
        encoding="utf-8",
    )
    assert _literal_depends_on(literal) == ("0001_initial_tenant_schema",)

    logical = tmp_path / "xx_0002_logical.py"
    logical.write_text(
        'revision = "xx_0002"\n'
        'down_revision = "xx_0001"\n'
        'REQUIRES = ("module_database_roles.v1",)\n'
        "depends_on = resolve_depends_on(REQUIRES)\n",
        encoding="utf-8",
    )
    assert _literal_depends_on(logical) is None

    none = tmp_path / "xx_0003_none.py"
    none.write_text('revision = "xx_0003"\ndepends_on = None\n', encoding="utf-8")
    assert _literal_depends_on(none) is None


def _lineage_loads(versions: Path) -> str | None:
    """`None` when the `ig` lineage reads cleanly on its own; else the failure."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("version_locations", str(versions))
    try:
        ScriptDirectory.from_config(cfg).get_heads()
    except Exception as exc:
        # Broad on purpose: the failure IS the observation, and pinning the
        # class would make the test agree with today's Alembic internals rather
        # than with "the lineage did not load".
        return f"{type(exc).__name__}: {exc}"
    return None


@pytest.fixture(scope="module")
def integration_versions() -> Path:
    return (
        REPO_ROOT
        / "packages/dotmac-integration/src/dotmac_integration/migrations/versions"
    )


def test_the_literal_makes_the_lineage_unreadable_alone(
    integration_versions: Path,
) -> None:
    """The cost of the deviation, measured rather than described.

    An assembly that does not compose kernel `0001` cannot even INSPECT this
    lineage — the revision map is built before any command runs, so `heads`,
    `history` and `upgrade` fail identically. Asserted so the retirement gate
    above is backed by a reproducible failure instead of an argument.
    """
    failure = _lineage_loads(integration_versions)
    assert failure is not None, (
        "the lineage now loads standalone — if the literal was retired, lower "
        "FROZEN_LITERAL_DEPENDS_ON and delete this test in the same change"
    )
    assert "0001_initial_tenant_schema" in failure


def test_the_same_lineage_loads_once_the_edge_is_logical(
    integration_versions: Path, tmp_path: Path
) -> None:
    """Specificity: the literal is the cause, not some other property of `ig`.

    A copy of the real files with the one line replaced by the compliant form
    resolves to an empty edge (no bindings installed, which is the
    graph-inspection case `resolve_depends_on` documents) and walks all ten
    revisions. Without this, the test above would also pass if the lineage were
    broken for an unrelated reason.
    """
    copy = tmp_path / "versions"
    shutil.copytree(integration_versions, copy)
    root = copy / "ig_0001_connector_control_plane.py"
    source = root.read_text(encoding="utf-8")
    assert 'depends_on = ("0001_initial_tenant_schema",)' in source
    root.write_text(
        source.replace(
            'depends_on = ("0001_initial_tenant_schema",)',
            "from dotmac_kernel.prerequisites import resolve_depends_on\n"
            'REQUIRES = ("module_database_roles.v1",)\n'
            "depends_on = resolve_depends_on(REQUIRES)",
        ),
        encoding="utf-8",
    )
    assert _lineage_loads(copy) is None
