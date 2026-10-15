"""Generate standalone native hooks, or reject drift without changing files."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configured_packages() -> list[tuple[Path, str]]:
    result = []
    for manifest in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        data = tomllib.loads(manifest.read_text())
        native = data.get("tool", {}).get("dotmac", {}).get("native")
        if native:
            result.append((manifest.parent, native["python-package"]))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--list", action="store_true", help="List native package directories"
    )
    args = parser.parse_args()
    pins = tomllib.loads((ROOT / "scripts/native-build.toml").read_text())
    template = (ROOT / "scripts/native_build_template.py").read_text()
    stale = []
    for package, module in configured_packages():
        if args.list:
            print(package.relative_to(ROOT))
            continue
        expected = (
            template.replace("__DOTMAC_PACKAGE__", module)
            .replace("__DOTMAC_MATURIN__", pins["maturin"])
            .replace("__DOTMAC_RUST__", pins["rust"])
        )
        path = package / "build.py"
        if args.check:
            if not path.exists() or path.read_text() != expected:
                stale.append(str(path.relative_to(ROOT)))
            data = tomllib.loads((package / "pyproject.toml").read_text())
            required = [
                f"poetry-core=={pins['poetry-core']}",
                f"maturin=={pins['maturin']}",
            ]
            if data["build-system"]["requires"] != required:
                stale.append(str(package.relative_to(ROOT)) + ": backend pins")
            mini = tomllib.loads((package / "native/pyproject.toml").read_text())
            if mini["build-system"]["requires"] != [f"maturin=={pins['maturin']}"]:
                stale.append(str(package.relative_to(ROOT)) + ": maturin pin")
            if mini["tool"]["maturin"]["module-name"] != f"{module}._native":
                stale.append(str(package.relative_to(ROOT)) + ": native module")
            cargo = tomllib.loads((package / "native/Cargo.toml").read_text())
            pyo3 = cargo["dependencies"]["pyo3"]
            if pyo3["version"] != "=" + pins["pyo3"] or any(
                feature.startswith("abi3") for feature in pyo3.get("features", [])
            ):
                stale.append(str(package.relative_to(ROOT)) + ": PyO3 pin or abi3")
        else:
            path.write_text(expected)
    if stale:
        raise SystemExit(
            "native build drift; run scripts/sync_native_builds.py: " + ", ".join(stale)
        )


if __name__ == "__main__":
    main()
