"""Generated portable Poetry hook; canonical source: scripts/native_build_template.py.

This file is embedded into each owning source archive. It must not import
repository helpers: an extracted sdist is a complete build input.
"""

# The canonical scripts directory exempts S603; generated package hooks need it.
# ruff: noqa: RUF100
from __future__ import annotations

import importlib.machinery
import importlib.metadata
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

PACKAGE = "dotmac_fiber_plant"
MATURIN_VERSION = "1.14.1"
RUST_VERSION = "1.90.0"


def build_native() -> None:
    """Build locked Rust sources and install only the owning extension."""
    root = Path(__file__).resolve().parent
    native = root / "native"
    if not (native / "Cargo.lock").is_file():
        raise RuntimeError("native/Cargo.lock must be committed before building")
    if importlib.metadata.version("maturin") != MATURIN_VERSION:
        raise RuntimeError(f"native build requires maturin=={MATURIN_VERSION}")
    compiler_path = shutil.which("rustc")
    if compiler_path is None:
        raise RuntimeError(f"native build requires rustc {RUST_VERSION}")
    compiler = subprocess.check_output(  # noqa: S603 - resolved fixed compiler query
        [compiler_path, "--version"], text=True
    ).split()
    if len(compiler) < 2 or compiler[1] != RUST_VERSION:
        raise RuntimeError(f"native build requires rustc {RUST_VERSION}")
    destination = root / "src" / PACKAGE
    suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    with tempfile.TemporaryDirectory(prefix="dotmac-native-") as temporary:
        work = Path(temporary)
        environment = os.environ.copy()
        environment["CARGO_TARGET_DIR"] = str(work / "target")
        subprocess.run(  # noqa: S603 - active interpreter, fixed maturin arguments
            [
                sys.executable,
                "-m",
                "maturin",
                "build",
                "--release",
                "--locked",
                "--interpreter",
                sys.executable,
                "--out",
                str(work / "wheels"),
            ],
            cwd=native,
            env=environment,
            check=True,
        )
        wheels = list((work / "wheels").glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("maturin must produce exactly one wheel")
        with zipfile.ZipFile(wheels[0]) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(set(names)) != len(names):
                raise RuntimeError("duplicate native wheel entries")
            extensions = []
            metadata = []
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise RuntimeError("unsafe native wheel path")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise RuntimeError("native wheel symlinks are forbidden")
                if member.is_dir():
                    continue
                if len(path.parts) < 2 or not (
                    path.parts[0] == PACKAGE or path.parts[0].endswith(".dist-info")
                ):
                    raise RuntimeError(f"unrelated native wheel payload: {path}")
                if path.name == "METADATA" and path.parts[0].endswith(".dist-info"):
                    metadata.append(member)
                # Reject bundled dependencies, libraries and unrelated extensions.
                if path.name.endswith((".so", ".pyd", ".dll", ".dylib")):
                    if (
                        len(path.parts) != 2
                        or path.parts[0] != PACKAGE
                        or not any(
                            path.name == "_native" + suffix for suffix in suffixes
                        )
                    ):
                        raise RuntimeError(f"unrelated native library: {path}")
                    extensions.append(member)
            if len(metadata) != 1 or BytesParser().parsebytes(
                archive.read(metadata[0])
            ).get_all("Requires-Dist"):
                raise RuntimeError("build-only native wheel must have no dependencies")
            if len(extensions) != 1:
                raise RuntimeError(
                    "native wheel must contain exactly one own _native extension"
                )
            extension = extensions[0]
            destination.mkdir(parents=True, exist_ok=True)
            for existing in destination.glob("_native*"):
                if existing.name.endswith((".so", ".pyd", ".dll", ".dylib")):
                    existing.unlink()
            with (
                archive.open(extension) as source,
                (destination / PurePosixPath(extension.filename).name).open(
                    "wb"
                ) as output,
            ):
                shutil.copyfileobj(source, output)


if __name__ == "__main__":
    build_native()
