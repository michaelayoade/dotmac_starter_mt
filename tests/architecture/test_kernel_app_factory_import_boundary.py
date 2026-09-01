"""The public app constructor must not enter the assembly DB runtime on import.

The source proof runs in an isolated subprocess with both database URLs and
``PYTHONPATH`` removed.  The wheel half is executed by
``scripts/consumer_boot_check.sh``: it installs only the kernel and its declared
dependencies, runs the same probe against that installed artifact, and installs
the consumer-owned psycopg driver only afterwards.

This is the a100 regression: ``middleware/tenant.py`` imported
``dotmac_kernel.db`` at module scope, so importing ``create_app`` constructed
the reference assembly's engine and required a driver the kernel correctly does
not own.  The repair is a lazy adapter, not a metadata expansion.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KERNEL_SOURCE = ROOT / "packages" / "dotmac-kernel" / "src"
PROBE = ROOT / "scripts" / "check_kernel_app_factory_import.py"
CONSUMER_BOOT = ROOT / "scripts" / "consumer_boot_check.sh"


def _run_source_probe(
    tmp_path: Path, probe: Path = PROBE
) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "PLATFORM_DATABASE_URL", "PYTHONPATH"}
    }
    env["KERNEL_IMPORT_EXPECTED_PREFIX"] = str(KERNEL_SOURCE)
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


def test_source_create_app_import_does_not_enter_database_runtime(
    tmp_path: Path,
) -> None:
    result = _run_source_probe(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "PASS app_factory import boundary" in result.stdout


def test_import_canary_fails_on_a_planted_eager_db_import(tmp_path: Path) -> None:
    """Sensitivity: change only the guarded import edge and observe refusal."""
    source = PROBE.read_text()
    anchor = "from dotmac_kernel import create_app as public_create_app\n"
    assert source.count(anchor) == 1
    planted = source.replace(
        anchor,
        "import dotmac_kernel.db\n" + anchor,
        1,
    )
    planted_probe = tmp_path / "planted_probe.py"
    planted_probe.write_text(planted)

    result = _run_source_probe(tmp_path, planted_probe)
    assert (
        result.returncode != 0
    ), "the app-factory canary passed after an eager dotmac_kernel.db import"


def test_wheel_consumer_proves_boundary_before_installing_driver() -> None:
    """The artifact lane exercises the same probe before consumer-only deps."""
    script = CONSUMER_BOOT.read_text()
    boundary = script.index(
        'echo "==> [4/8] Prove importing create_app does not enter the DB runtime"'
    )
    driver = script.index(
        'echo "==> [5/8] Install the consumer-owned DB driver and test transport"'
    )
    assert boundary < driver
    boundary_block = script[boundary:driver]
    assert "check_kernel_app_factory_import.py" in boundary_block
    assert (
        "env -u DATABASE_URL -u PLATFORM_DATABASE_URL -u PYTHONPATH" in boundary_block
    )
    assert 'KERNEL_IMPORT_EXPECTED_PREFIX="$VENV"' in boundary_block
    assert "psycopg[binary]" not in boundary_block
