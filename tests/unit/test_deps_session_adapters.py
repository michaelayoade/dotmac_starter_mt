"""Route dependencies defer DB construction without taking transaction ownership."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from dotmac_kernel import deps


def test_tenant_dependency_delegates_when_the_request_resolves(monkeypatch) -> None:
    import dotmac_kernel.db as transaction_owner

    request = Mock()
    session = object()
    events: list[object] = []

    def owned(request_arg):
        events.append(request_arg)
        yield session
        events.append("closed")

    monkeypatch.setattr(transaction_owner, "get_db", owned)

    dependency = deps.get_db(request)
    assert next(dependency) is session
    with pytest.raises(StopIteration):
        next(dependency)
    assert events == [request, "closed"]


def test_platform_dependency_delegates_when_the_request_resolves(monkeypatch) -> None:
    import dotmac_kernel.db as transaction_owner

    session = object()
    events: list[str] = []

    def owned():
        events.append("opened")
        yield session
        events.append("closed")

    monkeypatch.setattr(transaction_owner, "get_platform_db", owned)

    dependency = deps.get_platform_db()
    assert next(dependency) is session
    with pytest.raises(StopIteration):
        next(dependency)
    assert events == ["opened", "closed"]


def test_web_auth_uses_the_same_dependency_override_identity() -> None:
    import dotmac_kernel.web_deps as web_deps

    assert web_deps.get_db is deps.get_db
