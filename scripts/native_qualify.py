"""Hosted standalone native artifact qualification; never installs the assembly."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*arguments: str, cwd: Path = ROOT) -> None:
    subprocess.run(arguments, cwd=cwd, check=True)


def packages() -> list[tuple[Path, str]]:
    result = []
    for path in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        data = tomllib.loads(path.read_text())
        native = data.get("tool", {}).get("dotmac", {}).get("native")
        if native:
            result.append((path.parent, native["python-package"]))
    return result


def verify_wheel(wheel: Path, module: str) -> None:
    with zipfile.ZipFile(wheel) as archive:
        libraries = [
            name
            for name in archive.namelist()
            if name.endswith((".so", ".pyd", ".dll", ".dylib"))
        ]
        if len(libraries) != 1 or not libraries[0].startswith(module + "/_native."):
            raise RuntimeError(f"wrong native payload: {wheel}: {libraries}")
        (metadata,) = (
            name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")
        )
        contents = archive.read(metadata).decode()
        if (
            "Root-Is-Purelib: false" not in contents
            or "-abi3-" in contents
            or "-none-any" in contents
        ):
            raise RuntimeError(f"wrong native tags: {wheel}: {contents}")
        if module == "dotmac_inventory":
            if not any(
                name.endswith("LICENSE.mpdecimal.txt") for name in archive.namelist()
            ):
                raise RuntimeError(
                    "inventory wheel must ship the upstream Decimal license"
                )
            with tempfile.TemporaryDirectory() as temporary:
                library = Path(temporary) / "_native.so"
                library.write_bytes(archive.read(libraries[0]))
                needed = subprocess.check_output(
                    ["readelf", "-d", str(library)], text=True
                )
                symbols = subprocess.check_output(
                    ["nm", "-D", "--defined-only", str(library)], text=True
                )
                if "libmpdec" in needed or re.search(r"\b(?:mpd_|dm_)\w*", symbols):
                    raise RuntimeError(
                        "inventory Decimal must be statically linked "
                        "with private symbols"
                    )


def unpack_sdist(sdist: Path, destination: Path, module: str) -> Path:
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        if any(
            name.endswith((".so", ".pyd", ".dll", ".dylib")) or "/target/" in name
            for name in names
        ):
            raise RuntimeError(f"binary payload in source archive: {sdist}")
        expected = [
            "build.py",
            "native/Cargo.toml",
            "native/Cargo.lock",
            "native/pyproject.toml",
            "native/src/lib.rs",
        ]
        if module == "dotmac_inventory":
            expected += [
                "native/build.rs",
                "native/decimal_shim.c",
                "native/decimal_shim.h",
                "native/vendor/UPSTREAM.toml",
                "native/vendor/LICENSE.mpdecimal.txt",
            ]
            if not any(
                "/native/vendor/" in name and name.endswith(".tar.gz") for name in names
            ):
                raise RuntimeError(
                    "inventory source archive lacks vendored Decimal sources"
                )
        for suffix in expected:
            if not any(name.endswith("/" + suffix) for name in names):
                raise RuntimeError(f"source archive missing {suffix}: {sdist}")
        archive.extractall(destination, filter="data")
    (source,) = destination.iterdir()
    return source


def verify_artifacts(artifacts: Path) -> None:
    for package, module in packages():
        prefix = package.name.replace("-", "_") + "-"
        for directory in (artifacts, artifacts / "rebuilt"):
            wheels = list(directory.glob(prefix + "*.whl"))
            if len(wheels) != 1:
                raise RuntimeError(
                    f"expected one {module} wheel in {directory}: {wheels}"
                )
            verify_wheel(wheels[0], module)
        (sdist,) = artifacts.glob(prefix + "*.tar.gz")
        with tempfile.TemporaryDirectory() as temporary:
            unpack_sdist(sdist, Path(temporary), module)


def standalone(artifacts: Path) -> None:
    artifacts.mkdir(parents=True)
    pins = tomllib.loads((ROOT / "scripts/native-build.toml").read_text())
    run(
        sys.executable,
        "-m",
        "pip",
        "install",
        f"poetry-core=={pins['poetry-core']}",
        f"maturin=={pins['maturin']}",
    )
    # Constraints pin every resolved runtime/test dependency to source-controlled
    # assembly versions without asking Poetry to install an incompatible host.
    lock = tomllib.loads((ROOT / "poetry.lock").read_text())
    versions: dict[str, str] = {}
    for entry in lock["package"]:
        if entry.get("source"):
            continue
        name = entry["name"].lower().replace("_", "-")
        if name in versions and versions[name] != entry["version"]:
            raise RuntimeError(f"ambiguous root lock pin: {name}")
        versions[name] = entry["version"]
    constraints = artifacts / "runtime-constraints.txt"
    constraints.write_text(
        "".join(f"{name}=={version}\n" for name, version in sorted(versions.items()))
    )
    runtime = artifacts / "runtime"
    runtime.mkdir()
    run(
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-build-isolation",
        "--no-deps",
        "--wheel-dir",
        str(runtime),
        str(ROOT / "packages/dotmac-kernel"),
    )
    for package, module in packages():
        run(
            "git",
            "ls-files",
            "--error-unmatch",
            str(package.relative_to(ROOT) / "native/Cargo.lock"),
        )
        run(
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(artifacts),
            str(package),
        )
        # Call the declared backend's source hook; wheels use pip's PEP 517 path.
        run(
            sys.executable,
            "-c",
            "from poetry.core.masonry.api import build_sdist; "
            "import sys; build_sdist(sys.argv[1])",
            str(artifacts),
            cwd=package,
        )
        (sdist,) = artifacts.glob(package.name.replace("-", "_") + "-*.tar.gz")
        target = artifacts / "extracted" / module
        target.mkdir(parents=True)
        source = unpack_sdist(sdist, target, module)
        run(
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(artifacts / "rebuilt"),
            str(source),
        )
    verify_artifacts(artifacts)
    (kernel,) = runtime.glob("*.whl")
    run(
        sys.executable,
        "-m",
        "pip",
        "install",
        "--constraint",
        str(constraints),
        f"{kernel}[testing]",
        f"pytest=={versions['pytest']}",
        "psycopg[binary]",
        *map(str, artifacts.glob("*.whl")),
    )
    exemptions = {"pip", "setuptools", "wheel", "maturin", "poetry-core"}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"].lower().replace("_", "-")
        if name.startswith("dotmac-") or name in exemptions:
            continue
        if versions.get(name) != distribution.version:
            raise RuntimeError(
                f"runtime dependency is not root-locked: {name}=={distribution.version}"
            )
    run(sys.executable, "-m", "pip", "check")
    # Keep every native/parity test intact, outside the assembly's conftest and
    # pytest pythonpath. These files own their local fixtures and primitive data.
    with tempfile.TemporaryDirectory(prefix="native-parity-") as temporary:
        tests = Path(temporary)
        for _, module in packages():
            test = (
                ROOT
                / "tests/unit"
                / ("test_" + module.removeprefix("dotmac_") + "_native.py")
            )
            shutil.copy2(test, tests / test.name)
        os.environ["DATABASE_URL"] = (
            "postgresql+psycopg://unit-test:unit-test@127.0.0.1:59999/unit-test"
        )
        for distribution in [artifacts, artifacts / "rebuilt"]:
            run(
                sys.executable,
                "-m",
                "pip",
                "install",
                "--force-reinstall",
                "--no-deps",
                *map(str, distribution.glob("*.whl")),
            )
            modules = [module for _, module in packages()]
            run(
                sys.executable,
                "-c",
                "import importlib, sys; "
                "extensions=[importlib.import_module(name+'._native') "
                "for name in sys.argv[1:]]; "
                "assert all('site-packages' in str(ext.__file__) "
                "for ext in extensions), [ext.__file__ for ext in extensions]",
                *modules,
                cwd=tests,
            )
            run(
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--confcutdir",
                str(tests),
                str(tests),
                cwd=tests,
            )
    hashes = []
    for path in sorted(artifacts.rglob("*")):
        if path.is_file() and path.name.endswith((".whl", ".tar.gz")):
            hashes.append(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
                f"{path.relative_to(artifacts)}\n"
            )
    (artifacts / "SHA256SUMS").write_text("".join(hashes))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["standalone", "verify"])
    parser.add_argument("artifacts", type=Path)
    args = parser.parse_args()
    artifacts = args.artifacts.resolve()
    if args.command == "standalone":
        standalone(artifacts)
    else:
        verify_artifacts(artifacts)


if __name__ == "__main__":
    main()
