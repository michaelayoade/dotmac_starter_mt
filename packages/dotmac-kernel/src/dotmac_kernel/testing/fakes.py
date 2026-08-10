"""Deterministic fakes for the kernel's provider seams (kernel-boundary Task 5).

Fakes for the seams that EXIST today (per the contracts-not-implementations
rule): a controllable clock, a recording seed hook, the in-memory rate-limit
store, and a fake branding loader. The `FakeProvisioningProvider` lives in the
sibling `provisioning` module. No fakes for protocols that do not yet exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from dotmac_kernel.middleware.rate_limit import MemoryStore

# The shipped in-memory RateLimitStore, re-exported under a test-facing name so
# a consumer's tests use the SAME store the kernel ships (not a divergent fake).
InMemoryRateLimitStore = MemoryStore

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class FakeClock:
    """A deterministic, advanceable clock — inject where code takes a `now`
    callable so time-dependent behavior is reproducible."""

    _now: datetime = field(default_factory=lambda: _EPOCH)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def set(self, when: datetime) -> None:
        self._now = when


@dataclass
class FakeSeeder:
    """A recording feature-seed hook. Pass `.hook` as a `FeatureManifest.seed`;
    it records each call, or raises when `fail=True` to exercise the
    deferred-non-fatal seed path."""

    name: str = "fake"
    fail: bool = False
    calls: list[str] = field(default_factory=list)

    def hook(self) -> None:
        if self.fail:
            raise RuntimeError(f"fake seed failure for {self.name!r}")
        self.calls.append(self.name)


def fake_branding(**overrides: str) -> dict[str, str]:
    """A fixed brand dict for tests that render branded templates without
    reading `brand.json` or the environment. Override any key by kwarg."""
    brand = {
        "name": "Test Brand",
        "tagline": "",
        "logo_url": "",
        "primary_color": "#000000",
        "accent_color": "#000000",
        "support_email": "test@example.com",
        "app_url": "http://testserver",
    }
    brand.update(overrides)
    return brand


__all__ = [
    "FakeClock",
    "FakeSeeder",
    "InMemoryRateLimitStore",
    "fake_branding",
]
