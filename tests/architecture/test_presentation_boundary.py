"""The runtime-brand adapter is assembly composition, not a new owner.

One detector arm: a presentation module that reaches BOTH resolved branding
(`dotmac_kernel.branding`) and the design system (`dotmac_ui`) has become a
second brand authority. Exactly one module is allowed to — `service.py`.

**This file is ADR-0018's worked example for the three-leg standard** (ADR-0018
§ "Decision amendment — 2026-08-15"). The arm carries `test_leg_1_sensitivity`,
`test_leg_2_specificity` and `test_leg_3_liveness`, named so a reader can see
which legs exist rather than infer it.

The retrofit found a real defect, which is the argument for the third leg. The
arm matched module names by EXACT equality against two spellings, and the old
sensitivity proof used exactly those two. Three ordinary spellings escaped it:
`from dotmac_ui.brand import ...`, `from dotmac_kernel import branding`, and
`import dotmac_ui.tokens`. Submodule imports of `dotmac_ui` are already written
in this repository, so the arm was inert against the form the violation would
really arrive in — and both fixture legs were green throughout. Only driving
the arm over the real corpus showed it.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRESENTATION_ROOT = PROJECT_ROOT / "app" / "features" / "presentation"


def _imports(source: str) -> set[str]:
    """Every dotted module name an import statement could be naming.

    `from p import q` yields BOTH `p` and `p.q`, because `q` may be a submodule
    — `from dotmac_kernel import branding` is the ordinary spelling of the
    branding import and binds exactly the same authority as
    `from dotmac_kernel.branding import load_branding`. Recording only
    `node.module` made the two read differently, which is half of the blind
    spot `test_leg_3_liveness_...` found.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _reaches(imports: set[str], target: str) -> bool:
    """Does any import name `target` or something UNDER it?

    Prefix matching, bounded at the dotted separator. Exact equality was the
    other half of the blind spot: `from dotmac_ui.brand import BrandOverride`
    reaches `dotmac_ui` as surely as `import dotmac_ui` does, and it is the
    spelling this codebase already uses elsewhere. The `.` in the prefix is
    what keeps `dotmac_uikit` and `dotmac_kernel.branding_utils` out — see
    `test_leg_2_specificity_...`.
    """
    return any(
        module == target or module.startswith(f"{target}.") for module in imports
    )


def _authority_crossings(sources: dict[str, str]) -> set[str]:
    crossings: set[str] = set()
    for name, source in sources.items():
        imports = _imports(source)
        if _reaches(imports, "dotmac_kernel.branding") and _reaches(
            imports, "dotmac_ui"
        ):
            crossings.add(name)
    return crossings


def _presentation_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in PRESENTATION_ROOT.glob("*.py")
    }


def test_only_the_service_crosses_from_resolved_branding_to_dotmac_ui() -> None:
    assert _authority_crossings(_presentation_sources()) == {"service.py"}


def test_template_studio_is_not_a_presentation_projection_dependency() -> None:
    imported = {
        module
        for source in _presentation_sources().values()
        for module in _imports(source)
    }
    assert not any(module.startswith("dotmac_template_studio") for module in imported)


# ── The three legs (ADR-0018 amendment 2026-08-15) ──────────────────────────
#
# One arm — "this module reaches BOTH resolved branding and `dotmac_ui`" — and
# three named proofs for it. This file is the worked example the ADR points at,
# so the legs are spelled out rather than bundled.


def test_leg_1_sensitivity_the_boundary_detector_still_bites() -> None:
    """SENSITIVITY. A representative violation fires the arm.

    Every spelling below is one a reviewer would actually meet: the dotted
    `from` import, the bare `import`, the submodule import, and the
    `from <package> import <submodule>` form. They are listed individually
    because a detector proved on ONE spelling is proved on one spelling —
    which is exactly what the liveness leg below found.
    """
    violating = {
        "a.py": (
            "from dotmac_kernel.branding import load_branding\n"
            "from dotmac_ui import BrandOverride\n"
        ),
        "b.py": ("from dotmac_kernel.branding import get_brand\nimport dotmac_ui\n"),
        "c.py": (
            "from dotmac_kernel.branding import get_brand\n"
            "from dotmac_ui.brand import BrandOverride\n"
        ),
        "d.py": (
            "from dotmac_kernel import branding\nimport dotmac_ui.tokens as tokens\n"
        ),
    }
    assert _authority_crossings(violating) == {"a.py", "b.py", "c.py", "d.py"}
    assert "dotmac_template_studio" in _imports("import dotmac_template_studio\n")


def test_leg_2_specificity_a_near_miss_stays_silent() -> None:
    """SPECIFICITY. The near-misses the arm was narrowed against stay silent.

    The arm is about a module holding BOTH authorities at once. One of the two
    is the ordinary case — `web.py` renders, `service.py` resolves — and a
    detector that fired on either alone would flag the whole feature and be
    switched off within a week. The `_utils` cases are the other near-miss:
    a prefix match must stop at a dotted boundary, or `dotmac_uikit` becomes
    `dotmac_ui`.
    """
    quiet = {
        "branding_only.py": "from dotmac_kernel.branding import load_branding\n",
        "ui_only.py": "from dotmac_ui import BrandOverride\n",
        "neither.py": "from dotmac_kernel.models import Tenant\n",
        "sibling_package.py": (
            "from dotmac_kernel.branding_utils import x\nimport dotmac_uikit\n"
        ),
        "kernel_root_only.py": (
            "from dotmac_kernel import models\nfrom dotmac_ui import BrandOverride\n"
        ),
    }
    assert _authority_crossings(quiet) == set()


def test_leg_3_liveness_the_arm_classifies_real_presentation_source() -> None:
    """LIVENESS. The arm reaches, and correctly classifies, REAL corpus code.

    ADR-0018's third leg, and the one the two above cannot stand in for: they
    are statements about strings this file wrote, not about the subject. The
    zero baseline here is legitimate — one crossing exists and it is allowed —
    so liveness is proved by IN-SITU MUTATION of the real scan:

    * discovery is the real `_presentation_sources()`, asserted to have
      actually reached `web.py`;
    * the mutated bytes are the real `web.py` bytes, not a fixture;
    * the classifier is the real `_authority_crossings`;
    * the corpus on disk is never written to, so a run that dies mid-test
      cannot leave a dirty tree or a poisoned guard.

    This is the leg that caught the defect. `web.py` grown a second authority
    the way it would REALLY be spelled — `from dotmac_kernel import branding`
    plus a `dotmac_ui` submodule — was invisible to the arm, while the two
    spellings the old sensitivity fixture happened to use were caught. The
    submodule spelling is already written in this repository — see
    `from dotmac_ui.a11y import ...` in the UI component tests — so this was a
    live blind spot, not a hypothetical one.
    """
    corpus = _presentation_sources()
    assert "web.py" in corpus, "discovery no longer reaches the real web.py"
    assert _authority_crossings(corpus) == {"service.py"}, "corpus not clean to start"

    planted = (
        "from dotmac_kernel import branding\n"
        "from dotmac_ui.brand import BrandOverride\n"
    )
    mutated = dict(corpus)
    mutated["web.py"] = planted + mutated["web.py"]

    assert _authority_crossings(mutated) == {"service.py", "web.py"}, (
        "the boundary arm did not classify a planted crossing in the REAL "
        "web.py source — it is inert against the spellings this codebase "
        "actually uses (ADR-0018 leg 3)"
    )
    # And the real corpus is untouched by the mutation.
    assert _authority_crossings(_presentation_sources()) == {"service.py"}
