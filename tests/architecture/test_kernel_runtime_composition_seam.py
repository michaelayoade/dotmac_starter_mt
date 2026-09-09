"""Kernel-owned paths run through a product-supplied `DatabaseRuntime` with
the reference runtime (`dotmac_kernel.db`) genuinely unavailable.

This is the closing half of the direct-import retirement: a product whose
direct `dotmac_kernel.db` import count is zero can still have the KERNEL
itself reach the eager reference runtime transitively, at boot or on every
authenticated request, through `dotmac_kernel.deps`, `middleware.tenant`, and
the `app_factory` startup checks. "No direct import" cannot detect that — the
only proof that means anything blocks the reference runtime from ever being
importable and then drives real work through the seam
(`ProductAssemblySpec.database_runtime` -> `session_runtime
.install_database_runtime` -> `session_runtime.get_database_runtime`).

`scripts/check_kernel_runtime_composition_seam.py` is that probe: it sets
``sys.modules["dotmac_kernel.db"] = None`` before importing anything else (so
any later import of it anywhere in the process raises `ImportError`), then
drives boot, an authenticated request (`platform_auth` + `deps`), a
CLI-shaped entry point, and a worker path (`messaging.worker.run_once`)
through a `DatabaseRuntime` it constructs and installs itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KERNEL_SOURCE = ROOT / "packages" / "dotmac-kernel" / "src"
PROBE = ROOT / "scripts" / "check_kernel_runtime_composition_seam.py"

_EXPECTED_STAGES = (
    "PASS boot: create_app + startup validation ran on the product runtime",
    "PASS authenticated request: platform login+logout resolved "
    "deps.get_platform_db through the product runtime",
    "PASS CLI entry point: upsert ran through get_database_runtime()",
    "PASS worker path: run_once reached the product runtime's session",
    "PASS dotmac_kernel.db stayed unimported for the whole probe",
)


def _run_probe(tmp_path: Path, probe: Path = PROBE) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "PLATFORM_DATABASE_URL", "PYTHONPATH"}
    }
    program = (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(KERNEL_SOURCE)!r}); "
        f"runpy.run_path({str(probe)!r}, run_name='__main__')"
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", program],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_every_named_path_runs_on_the_product_runtime_while_db_is_unimportable(
    tmp_path: Path,
) -> None:
    result = _run_probe(tmp_path)
    assert result.returncode == 0, result.stderr
    for stage in _EXPECTED_STAGES:
        assert stage in result.stdout, (
            f"expected stage missing from probe output: {stage!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def test_the_probe_genuinely_blocks_the_reference_runtime(tmp_path: Path) -> None:
    """Sensitivity (near-miss half): if the block itself were a no-op, a bare
    `import dotmac_kernel.db` would succeed silently and this probe would be
    proving nothing. Confirm the block bites on its own, isolated from the
    seam under test."""
    program = (
        "import sys; sys.modules['dotmac_kernel.db'] = None; "
        f"sys.path.insert(0, {str(KERNEL_SOURCE)!r}); "
        "import dotmac_kernel.db"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "PLATFORM_DATABASE_URL", "PYTHONPATH"}
    }
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", program],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "ImportError" in result.stderr


def test_the_seam_bites_a_reintroduced_transitive_reach(tmp_path: Path) -> None:
    """Plant half: reintroduce the exact regression this seam retired
    (`dotmac_kernel.deps.get_db` importing `dotmac_kernel.db` directly again)
    in a copied source tree, and observe the probe fail against it — proving
    the guard is the LIVE BEHAVIOUR, not the absence of a grep hit.
    """
    import shutil

    planted_root = tmp_path / "planted-src"
    shutil.copytree(KERNEL_SOURCE, planted_root)
    deps_path = planted_root / "dotmac_kernel" / "deps.py"
    source = deps_path.read_text()
    anchor = "    runtime = get_database_runtime()\n"
    assert source.count(anchor) == 1, "deps.get_db no longer has the expected shape"
    deps_path.write_text(
        source.replace(
            anchor,
            "    from dotmac_kernel.db import get_db as _ref  # noqa: F401\n" + anchor,
            1,
        )
    )

    probe_source = PROBE.read_text()
    planted_probe = tmp_path / "planted_probe.py"
    planted_probe.write_text(probe_source)

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "PLATFORM_DATABASE_URL", "PYTHONPATH"}
    }
    program = (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(planted_root)!r}); "
        f"runpy.run_path({str(planted_probe)!r}, run_name='__main__')"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", program],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, (
        "the probe passed after deps.get_db was made to import dotmac_kernel.db "
        "again — the guard did not bite a real transitive reach"
    )
    assert "ImportError" in result.stderr or "ImportError" in result.stdout


def test_a_docstring_mention_of_the_reference_module_is_not_flagged(
    tmp_path: Path,
) -> None:
    """Near-miss half: a comment/docstring naming `dotmac_kernel.db` (there are
    dozens, by design — see `session_runtime.py`'s module docstring) must not
    trip the same probe that catches a real import."""
    import shutil

    planted_root = tmp_path / "near-miss-src"
    shutil.copytree(KERNEL_SOURCE, planted_root)
    deps_path = planted_root / "dotmac_kernel" / "deps.py"
    source = deps_path.read_text()
    anchor = "    runtime = get_database_runtime()\n"
    assert source.count(anchor) == 1
    deps_path.write_text(
        source.replace(
            anchor,
            "    # See dotmac_kernel.db for the reference assembly's own instance.\n"
            + anchor,
            1,
        )
    )

    probe_source = PROBE.read_text()
    planted_probe = tmp_path / "near_miss_probe.py"
    planted_probe.write_text(probe_source)

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "PLATFORM_DATABASE_URL", "PYTHONPATH"}
    }
    program = (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(planted_root)!r}); "
        f"runpy.run_path({str(planted_probe)!r}, run_name='__main__')"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", program],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for stage in _EXPECTED_STAGES:
        assert stage in result.stdout
