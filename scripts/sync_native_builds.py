"""Generate standalone native hooks, or reject drift without changing files."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECIMAL_CORE = ROOT / "native/decimal-core"


def decimal_core_files(directory: Path) -> dict[Path, bytes]:
    """Complete source membership and bytes; compiler output is never a source."""
    return {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and "target" not in path.relative_to(directory).parts
    }


def sync_decimal_core(package: Path, *, check: bool) -> list[str]:
    expected = decimal_core_files(DECIMAL_CORE)
    if not expected:
        raise SystemExit("canonical native/decimal-core source is missing")
    destination = package / "native/decimal-core"
    actual = decimal_core_files(destination)
    drift = sorted(
        name
        for name in expected.keys() | actual.keys()
        if expected.get(name) != actual.get(name)
    )
    if check:
        return [str((destination / name).relative_to(ROOT)) for name in drift]
    for name in actual.keys() - expected.keys():
        (destination / name).unlink()
    for name in drift:
        if name in expected:
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected[name])
    return []


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
        data = tomllib.loads((package / "pyproject.toml").read_text())
        if data["tool"]["dotmac"]["native"].get("decimal-core", False):
            stale.extend(sync_decimal_core(package, check=args.check))
        expected = (
            template.replace("__DOTMAC_PACKAGE__", module)
            .replace("__DOTMAC_MATURIN__", pins["maturin"])
            .replace("__DOTMAC_RUST__", pins["rust"])
        )
        path = package / "build.py"
        if args.check:
            if not path.exists() or path.read_text() != expected:
                stale.append(str(path.relative_to(ROOT)))
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
            if data["tool"]["dotmac"]["native"].get("decimal-core", False):
                core = cargo["dependencies"].get("dotmac-decimal-core", {})
                if core != {"version": "=0.1.0", "path": "decimal-core"}:
                    stale.append(
                        str(package.relative_to(ROOT)) + ": decimal core dependency"
                    )
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
