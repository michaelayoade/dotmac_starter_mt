"""The reference assembly's two release-version surfaces must stay aligned.

``scripts/bump_version.py`` owns both the root ``VERSION`` file and the root
``pyproject.toml`` declaration.  A partial bump makes deployment tooling and
Python package metadata report different assembly releases, so compare the
checked-in sources directly rather than relying on installed metadata.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_VERSION_FILE = _ROOT / "VERSION"
_PYPROJECT = _ROOT / "pyproject.toml"


def _version_file_value() -> str:
    return _VERSION_FILE.read_text(encoding="utf-8").strip()


def _pyproject_version() -> str:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["tool"]["poetry"]["version"]


def test_root_version_surfaces_match() -> None:
    version_file = _version_file_value()
    pyproject = _pyproject_version()

    assert version_file == pyproject, (
        f"VERSION declares {version_file!r} but pyproject.toml declares "
        f"{pyproject!r} — update both through scripts/bump_version.py"
    )
