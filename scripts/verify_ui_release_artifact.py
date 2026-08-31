#!/usr/bin/env python3
"""Prove an INSTALLED `dotmac-ui` artifact carries the contract it declares.

Run this with the interpreter of a clean virtualenv that has `dotmac-ui`
installed — once against the freshly built wheel, once against the bytes
installed back from the registry. `release-ui.yml` does exactly that, and
`tests/architecture/test_ui_release_contract.py` is the guard that keeps it
doing it.

## Why a script and not two copies of an inline heredoc

The workflow used to carry the smoke twice, inline, and the architecture guard
compensated by counting seam strings. Two copies of a proof drift; the copy that
drifts is the one nobody reads. One file, invoked twice, cannot.

## What is actually proved, and why each half is load-bearing

1. **The subject is the INSTALLED package.** `dotmac_ui.__file__` must resolve
   inside this interpreter's own `site-packages`, never a source checkout.
   This is not hypothetical bookkeeping: a sibling extraction shipped a package
   that resolved its data relative to its SOURCE location, so every check
   passed in the repository and the same check silently proved nothing about
   the artifact a consumer installs. A smoke run from a checkout directory can
   import the checkout; then wheel-content defects are invisible.

2. **Every DECLARED component is proved, not one hand-picked one.** The
   component set is read from the installed `dotmac_ui.COMPONENTS`, so adding a
   component to the contract automatically extends this proof. `map_frame`
   existed on `main` for weeks while the release proof still only exercised
   `empty_state`; enumerating by hand is how that happens.

3. **Resolution goes through a HOST Jinja loader**, a bare `Environment` with
   `StrictUndefined` and none of the kernel's globals. Wheel-content inspection
   proves a file is in the archive; only a loader proves the installed layout
   is addressable at the published template path. Jinja is installed by the
   host, never by `dotmac-ui`.

4. **The rendered markup carries every class the contract declares.** A macro
   that renders but emits different classes is a broken contract that a
   "did it render?" check calls healthy.

5. **The published manifest matches the installed bytes.** `manifest.json` is
   a consumer-facing integrity claim (an air-gapped bundle check, a JS build,
   an nginx pipeline). A manifest whose digest describes a file the wheel does
   not contain is worse than no manifest, so digests, byte sizes and the
   package version are recomputed here from the installed files.
"""

from __future__ import annotations

import re
import sys
import sysconfig
from hashlib import sha256
from pathlib import Path

#: Every `.dmui-*` class token the rendered markup emits.
_CLASS_TOKEN = re.compile(r"\bdmui-[A-Za-z0-9_-]+")

#: Argument sets each component is rendered with. A component's declared class
#: set is proved against the UNION of its renders, because state modifiers
#: (`--loading`, `--error`, …) are mutually exclusive within one render.
#: Every key must be a declared parameter of that component; unknown keys fail.
_RENDER_CASES: dict[str, tuple[dict[str, object], ...]] = {
    "empty_state": (
        {
            "title": "No invoices",
            "message": "Create one to begin.",
            "action_label": "New invoice",
            "action_url": "/invoices/new",
        },
    ),
    "map_frame": tuple(
        {
            "canvas_id": "release-smoke-map",
            "label": "Release smoke map",
            "state": state,
            "status_title": "Map state",
            "status_message": "Rendered by the release artifact proof.",
        }
        for state in ("ready", "loading", "empty", "error")
    ),
}


def _fail(message: str) -> None:
    raise SystemExit(f"dotmac-ui release artifact proof FAILED: {message}")


def _assert_subject_is_the_installed_artifact(module: object) -> Path:
    """The import must have come from this interpreter's site-packages."""
    origin = getattr(module, "__file__", None)
    if origin is None:
        _fail("dotmac_ui has no __file__; it is not an installed package")
    package_dir = Path(str(origin)).resolve().parent

    candidates = {
        Path(path).resolve()
        for key in ("purelib", "platlib")
        if (path := sysconfig.get_paths().get(key))
    }
    if not candidates:
        _fail("this interpreter reports no site-packages path")
    if not any(package_dir.is_relative_to(root) for root in candidates):
        _fail(
            "dotmac_ui was imported from "
            f"{package_dir}, which is not inside this interpreter's "
            f"site-packages ({sorted(str(c) for c in candidates)}). "
            "The proof would be measuring a source checkout, not the artifact."
        )
    return package_dir


def _assert_manifest_matches_the_installed_bytes(module: object) -> None:
    from dotmac_ui.assets import asset_manifest, manifest_path, static_dir

    manifest = asset_manifest()
    declared_version = manifest.get("version")
    actual_version = module.__version__
    if declared_version != actual_version:
        _fail(
            f"manifest declares version {declared_version!r} but the installed "
            f"package reports {actual_version!r}"
        )

    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        _fail("the published manifest lists no assets")

    for entry in assets:
        relpath = str(entry["path"])
        target = static_dir() / relpath
        if not target.is_file():
            _fail(f"manifest names {relpath}, absent from the installed wheel")
        payload = target.read_bytes()
        digest = sha256(payload).hexdigest()
        if digest != entry["sha256"]:
            _fail(
                f"{relpath}: manifest sha256 {entry['sha256']} != installed "
                f"bytes {digest}"
            )
        if len(payload) != entry["bytes"]:
            _fail(
                f"{relpath}: manifest byte size {entry['bytes']} != installed "
                f"{len(payload)}"
            )
        print(f"  manifest asset OK  {relpath}  sha256={digest[:12]}…")

    print(f"  manifest OK        {manifest_path().name} for {actual_version}")


def _assert_every_declared_component_renders(module: object) -> None:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    env = Environment(
        loader=FileSystemLoader(module.template_dir()),  # type: ignore[attr-defined]
        autoescape=True,
        undefined=StrictUndefined,
    )

    components = tuple(module.COMPONENTS)  # type: ignore[attr-defined]
    if not components:
        _fail("the installed package declares no components")

    for component in components:
        cases = _RENDER_CASES.get(component.macro)
        if cases is None:
            _fail(
                f"{component.macro} is published by the installed artifact but "
                "this proof has no render case for it — add one rather than "
                "letting a component ship unproven"
            )
        macro = getattr(env.get_template(component.template).module, component.macro)

        emitted: set[str] = set()
        for kwargs in cases:
            unknown = set(kwargs) - set(component.parameters)
            if unknown:
                _fail(
                    f"{component.macro}: render case passes {sorted(unknown)}, "
                    f"not in the declared signature {list(component.parameters)}"
                )
            rendered = str(macro(**kwargs))
            if not rendered.strip():
                _fail(f"{component.macro} rendered empty markup")
            emitted |= set(_CLASS_TOKEN.findall(rendered))

        missing = set(component.classes) - emitted
        if missing:
            _fail(
                f"{component.macro} never emitted its declared classes "
                f"{sorted(missing)}"
            )
        undeclared = emitted - set(component.classes)
        if undeclared:
            _fail(f"{component.macro} emitted undeclared classes {sorted(undeclared)}")
        print(
            f"  component OK       {component.macro} "
            f"({len(cases)} render(s), {len(component.classes)} classes)"
        )


def main() -> int:
    import dotmac_ui

    package_dir = _assert_subject_is_the_installed_artifact(dotmac_ui)
    print(f"verifying INSTALLED dotmac-ui {dotmac_ui.__version__} at {package_dir}")

    from dotmac_ui.assets import stylesheet_path, tailwind_preset_path
    from dotmac_ui.theme import bootstrap_script

    for label, path in (
        ("stylesheet", stylesheet_path()),
        ("tailwind preset", tailwind_preset_path()),
    ):
        if not path.is_file():
            _fail(f"{label} missing from the installed artifact ({path})")
        print(f"  packaged asset OK  {path.name}")
    if not bootstrap_script():
        _fail("the theme bootstrap script is empty")

    _assert_manifest_matches_the_installed_bytes(dotmac_ui)
    _assert_every_declared_component_renders(dotmac_ui)

    print(f"dotmac-ui {dotmac_ui.__version__} release artifact proof PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
