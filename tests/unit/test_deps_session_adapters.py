"""Route dependencies defer DB construction without taking transaction ownership.

`deps.get_db`/`deps.get_platform_db` resolve the runtime through
`resolve_database_runtime()` (kernel-runtime-composition-seam), not by importing
`dotmac_kernel.db` directly. That is what lets a product install its own
`DatabaseRuntime` and have these two adapters serve it unchanged; a fake
runtime here proves the delegation without needing a real engine.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from dotmac_kernel import deps


class _FakeRuntime:
    """A minimal stand-in for `DatabaseRuntime`, carrying only the two
    generator methods these adapters call."""

    def __init__(self, *, request_session=None, platform_request_session=None):
        if request_session is not None:
            self.request_session = request_session
        if platform_request_session is not None:
            self.platform_request_session = platform_request_session


def test_tenant_dependency_delegates_when_the_request_resolves(monkeypatch) -> None:
    request = Mock()
    request.state.tenant = None
    session = object()
    events: list[object] = []

    def owned(tenant_id):
        events.append(tenant_id)
        yield session
        events.append("closed")

    monkeypatch.setattr(
        deps, "resolve_database_runtime", lambda: _FakeRuntime(request_session=owned)
    )

    dependency = deps.get_db(request)
    assert next(dependency) is session
    with pytest.raises(StopIteration):
        next(dependency)
    # No tenant on request.state -> None is passed through, same as the
    # reference runtime's own `get_db` did before the seam existed.
    assert events == [None, "closed"]


def test_tenant_dependency_passes_the_resolved_tenant_id(monkeypatch) -> None:
    request = Mock()
    request.state.tenant = Mock(id="tenant-123")
    session = object()
    seen: list[object] = []

    def owned(tenant_id):
        seen.append(tenant_id)
        yield session

    monkeypatch.setattr(
        deps, "resolve_database_runtime", lambda: _FakeRuntime(request_session=owned)
    )

    dependency = deps.get_db(request)
    assert next(dependency) is session
    assert seen == ["tenant-123"]


def test_platform_dependency_delegates_when_the_request_resolves(monkeypatch) -> None:
    session = object()
    events: list[str] = []

    def owned():
        events.append("opened")
        yield session
        events.append("closed")

    monkeypatch.setattr(
        deps,
        "resolve_database_runtime",
        lambda: _FakeRuntime(platform_request_session=owned),
    )

    dependency = deps.get_platform_db()
    assert next(dependency) is session
    with pytest.raises(StopIteration):
        next(dependency)
    assert events == ["opened", "closed"]


def test_web_auth_uses_the_same_dependency_override_identity() -> None:
    import dotmac_kernel.web_deps as web_deps

    assert web_deps.get_db is deps.get_db
