"""Hermetic kernel settings for package-local test runs.

`dotmac_kernel.config` freezes `settings` at first import from the process
env, and the provisioning engine's submit path imports the kernel's eager
DB module for its savepoint helper. Mirror the root conftest's placeholder:
well-formed, unroutable, never connected to.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://unit-test:unit-test@127.0.0.1:59999/unit-test",
)
os.environ.setdefault(
    "PLATFORM_DATABASE_URL",
    "postgresql+psycopg://unit-test:unit-test@127.0.0.1:59999/unit-test",
)
