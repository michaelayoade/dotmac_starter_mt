"""N2: a failing admission-lease release AFTER the send must never undo it.

Kept out of `tests/unit/test_delivery_smtp.py`, whose conserved fingerprint is
pinned. The network is not involved: `_admitted_send` is stubbed, because only
the lease handling around it is under test.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest
from dotmac_delivery_smtp import SmtpConfig, SmtpDeliveryProvider
from dotmac_kernel.delivery_providers import OutboundMessage, ProviderResult


def _provider(lease, admitted):
    cfg = SmtpConfig("127.0.0.1", 2525, True, "", "a@mail.example.test", True)
    p = SmtpDeliveryProvider(config=lambda: cfg, admission=lambda m: lease)
    p._admitted_send = admitted  # type: ignore[method-assign]
    return p


def _msg() -> OutboundMessage:
    return OutboundMessage(
        dispatch_id=uuid4(),
        channel="email",
        address="learner@recipient.example.test",
        body=None,
        category="academy.account",
        subject="Hi",
        text="body",
    )


class _Lease:
    def __init__(self, exit_raises: bool) -> None:
        self.exit_raises = exit_raises
        self.exit_args: tuple | None = None

    def __enter__(self):
        return None

    def __exit__(self, *args):
        self.exit_args = args
        if self.exit_raises:
            raise RuntimeError("gate unreachable: secret-token-123")
        return False


def test_a_failing_release_after_a_result_returns_the_result(caplog) -> None:
    lease = _Lease(exit_raises=True)
    result = ProviderResult(status="accepted", response_code="250")
    p = _provider(lease, lambda *a: result)
    with caplog.at_level(logging.ERROR):
        assert p.send(_msg()) is result
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in text and "secret-token" not in text


def test_the_lease_still_sees_an_exception_from_the_send() -> None:
    lease = _Lease(exit_raises=False)

    def boom(*a):
        raise ValueError("send failed")

    with pytest.raises(ValueError, match="send failed"):
        _provider(lease, boom).send(_msg())
    assert lease.exit_args is not None and lease.exit_args[0] is ValueError


def test_a_clean_release_is_called_once_with_no_exception() -> None:
    lease = _Lease(exit_raises=False)
    result = ProviderResult(status="accepted")
    assert _provider(lease, lambda *a: result).send(_msg()) is result
    assert lease.exit_args == (None, None, None)
