#!/usr/bin/env python3
"""Prove the installed/source kernel app constructor is DB-runtime-free.

The caller removes ``DATABASE_URL``, ``PLATFORM_DATABASE_URL`` and
``PYTHONPATH`` before starting this process.  Importing ``create_app`` is a
composition operation, not a database operation: it must not enter the eager
reference-assembly runtime or import the consumer-owned PostgreSQL driver.

``KERNEL_IMPORT_EXPECTED_PREFIX`` is required so a green result also identifies
its subject.  The source canary points it at ``packages/dotmac-kernel/src``;
the wheel canary points it at the clean consumer virtualenv.  Without that
binding a subprocess could pass against whichever editable checkout happened
to be first on ``sys.path``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotmac_kernel import create_app as public_create_app
from dotmac_kernel.app_factory import create_app


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def main() -> None:
    expected_raw = os.environ.get("KERNEL_IMPORT_EXPECTED_PREFIX")
    if not expected_raw:
        raise SystemExit("KERNEL_IMPORT_EXPECTED_PREFIX is required")

    package = sys.modules.get("dotmac_kernel")
    package_file = getattr(package, "__file__", None)
    if not isinstance(package_file, str):
        raise SystemExit("dotmac_kernel has no concrete package file")

    expected = Path(expected_raw).resolve()
    observed = Path(package_file).resolve()
    if not _is_within(observed, expected):
        raise SystemExit(
            "dotmac_kernel resolved outside the named subject: "
            f"{observed} not under {expected}"
        )

    forbidden = sorted(
        name
        for name in sys.modules
        if name == "dotmac_kernel.db"
        or name == "psycopg"
        or name.startswith("psycopg.")
    )
    if forbidden:
        raise SystemExit(
            "create_app import entered the database runtime: " + ", ".join(forbidden)
        )
    if not callable(create_app):
        raise SystemExit("dotmac_kernel.app_factory.create_app is not callable")
    if public_create_app is not create_app:
        raise SystemExit("package-root create_app is not the app_factory constructor")

    print(f"PASS app_factory import boundary: {observed}")


if __name__ == "__main__":
    main()
