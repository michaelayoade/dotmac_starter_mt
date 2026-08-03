"""`dotmac_kernel.testing` — the kernel's supported test kit (Task 5).

A consumer assembly builds its unit tests on this instead of hand-rolling a
harness: the in-memory-SQLite engine + savepoint-isolated session + TestClient
wiring (`harness`), deterministic fakes for the kernel's provider seams
(`fakes`), and a fake ProvisioningProvider plus a reusable provider contract
(`provisioning`). This IS public, supported API (see `COMPATIBILITY.md`).
"""

from __future__ import annotations

from dotmac_kernel.testing.fakes import (
    FakeClock,
    FakeSeeder,
    InMemoryRateLimitStore,
    fake_branding,
)
from dotmac_kernel.testing.harness import (
    assembly_test_client,
    create_test_engine,
    isolated_session,
)
from dotmac_kernel.testing.licensing import (
    FakeDeploymentSigner,
    FakeLicenceSigner,
)
from dotmac_kernel.testing.provisioning import (
    FakeProvisioningProvider,
    check_provisioning_provider_contract,
)

__all__ = [
    # harness
    "create_test_engine",
    "isolated_session",
    "assembly_test_client",
    # fakes
    "FakeClock",
    "FakeSeeder",
    "InMemoryRateLimitStore",
    "fake_branding",
    # provisioning
    "FakeProvisioningProvider",
    "check_provisioning_provider_contract",
    # licensing (WS8) — the class imports lazily; INSTANTIATION needs the
    # `cryptography` package (`licensing`/`testing` extra)
    "FakeDeploymentSigner",
    "FakeLicenceSigner",
]
