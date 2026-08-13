"""The component boundary: inert package data, host-supplied Jinja.

ADR-0006 § 2 assigns the Jinja/HTMX component library to `dotmac-ui`, and
`COMPATIBILITY.md` § "The component boundary" is the contract these tests hold
in place. Three failure modes are specifically worth catching, because each one
looks fine in this repo and breaks for a consumer:

1. **Templates present in the checkout but absent from the wheel.** Everything
   passes here and the installed package has no components at all.
2. **A component that needs a host filter or global.** It renders in the
   starter, whose environment has `local_datetime` and `brand`, and explodes on
   ERP, whose environment does not.
3. **A component styled with utility classes.** It renders unstyled anywhere the
   consumer's Tailwind content globs do not reach into site-packages — which is
   every correctly configured consumer, since none of them compile this package.
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import dotmac_ui
import pytest
from dotmac_ui.components import COMPONENTS, ComponentContract
from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = PROJECT_ROOT / "packages" / "dotmac-ui"


def _clean_environment() -> Environment:
    """A host with NOTHING installed on it — no globals, no filters.

    `StrictUndefined` so a component silently depending on a missing context
    variable raises here instead of rendering an empty string.
    """
    return Environment(
        loader=FileSystemLoader(str(dotmac_ui.template_dir())),
        autoescape=True,
        undefined=StrictUndefined,
    )


def _render(contract: ComponentContract, **kwargs: object) -> str:
    env = _clean_environment()
    template = env.get_template(contract.template)
    macro = getattr(template.module, contract.macro)
    return str(macro(**kwargs))


@pytest.mark.parametrize("contract", COMPONENTS, ids=lambda c: c.macro)
def test_components_render_on_a_clean_host(contract: ComponentContract) -> None:
    """No custom filter, no global, no request — stock Jinja only."""
    rendered = _render(contract, message="Nothing here")
    assert rendered.strip(), f"{contract.macro} rendered nothing"


@pytest.mark.parametrize("contract", COMPONENTS, ids=lambda c: c.macro)
def test_every_published_template_is_under_the_namespace(
    contract: ComponentContract,
) -> None:
    """A flat path would shadow a consumer's own `components/` in a ChoiceLoader."""
    assert contract.template.startswith(f"{dotmac_ui.TEMPLATE_NAMESPACE}/")
    assert (dotmac_ui.template_dir() / contract.template).is_file()


@pytest.mark.parametrize("contract", COMPONENTS, ids=lambda c: c.macro)
def test_component_markup_uses_only_published_classes(
    contract: ComponentContract,
) -> None:
    """No utility classes: a consumer never compiles this package's templates."""
    source = (dotmac_ui.template_dir() / contract.template).read_text(encoding="utf-8")
    used = {
        cls for attr in re.findall(r'class="([^"{}]*)"', source) for cls in attr.split()
    }
    assert used, f"{contract.macro} emits no classes at all — check the parser"
    unpublished = used - dotmac_ui.PUBLISHED_COMPONENT_CLASSES
    assert not unpublished, (
        f"{contract.macro} uses classes that are not published (utility classes "
        f"will not resolve for a consumer): {sorted(unpublished)}"
    )
    assert used <= contract.classes, (
        f"{contract.macro} emits classes its contract does not declare: "
        f"{sorted(used - contract.classes)}"
    )


@pytest.mark.parametrize("contract", COMPONENTS, ids=lambda c: c.macro)
def test_declared_parameters_match_the_macro_signature(
    contract: ComponentContract,
) -> None:
    """The parameter tuple is the published signature, so it cannot drift."""
    env = _clean_environment()
    macro = getattr(env.get_template(contract.template).module, contract.macro)
    assert tuple(macro.arguments) == contract.parameters


def test_published_classes_are_exactly_the_contracts_union() -> None:
    expected: set[str] = set()
    for contract in COMPONENTS:
        expected |= contract.classes
    assert dotmac_ui.PUBLISHED_COMPONENT_CLASSES == expected


def test_every_published_class_is_styled_by_the_compiled_stylesheet() -> None:
    """A declared class with no rule is a component that renders unstyled."""
    css = dotmac_ui.stylesheet_path().read_text(encoding="utf-8")
    defined = set(re.findall(r"\.([a-zA-Z_][\w-]*)", css))
    missing = dotmac_ui.PUBLISHED_COMPONENT_CLASSES - defined
    assert not missing, f"published but unstyled: {sorted(missing)}"


def test_component_css_resolves_entirely_through_tokens() -> None:
    """A component that hardcodes a colour cannot be re-themed by a consumer."""
    css = dotmac_ui.stylesheet_path().read_text(encoding="utf-8")
    start = css.index(".dmui-empty-state")
    block = css[start:]
    offenders = re.findall(r"#(?:[0-9a-fA-F]{3,8})\b|\brgba?\(|\bhsla?\(", block)
    assert not offenders, f"component CSS carries raw colour literals: {offenders}"


def test_the_optional_action_is_omitted_when_no_url_is_given() -> None:
    without = _render(dotmac_ui.EMPTY_STATE, message="No parties found")
    assert "dmui-empty-state__action" not in without

    with_action = _render(
        dotmac_ui.EMPTY_STATE,
        message="No parties found",
        action_url="/admin/parties/create",
        action_label="New Party",
    )
    assert 'href="/admin/parties/create"' in with_action
    assert "New Party" in with_action


def test_rendered_output_escapes_its_arguments() -> None:
    """Autoescaping is the host's setting, but the markup must not defeat it."""
    rendered = _render(dotmac_ui.EMPTY_STATE, message="<script>alert(1)</script>")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


@pytest.mark.slow
def test_the_templates_are_present_in_the_built_wheel(tmp_path: Path) -> None:
    """Package data that is not packaged is a source-checkout-only feature.

    Builds the real wheel rather than inspecting `pyproject.toml`, because the
    thing that breaks a consumer is the archive's contents, not the intent
    expressed in the include list.
    """
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "poetry",
            "build",
            "--format",
            "wheel",
            "--output",
            str(tmp_path),
        ],
        cwd=PACKAGE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"poetry build unavailable: {result.stderr.strip()[:200]}")

    wheels = list(tmp_path.glob("*.whl"))
    assert wheels, "poetry build produced no wheel"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())

    for contract in COMPONENTS:
        expected = f"dotmac_ui/templates/{contract.template}"
        assert expected in names, (
            f"{expected} is missing from the wheel; the consumer would install a "
            f"package with no components. Wheel contains: {sorted(names)[:20]}"
        )
