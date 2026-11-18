"""Per-operation timeout budget (kept out of test_delivery_smtp.py, whose whole
module is fingerprint-pinned by the standards profile).

The DATA reply gets the long budget; everything after it (QUIT) runs under the
short one, so a server that 250s and then stalls cannot hold the attempt past
the relay lease. Plain (non-TLS) loopback server, non-production config.
"""

from __future__ import annotations

import socket
import threading
import time
from uuid import UUID

import pytest
from dotmac_delivery_smtp import SmtpConfig, SmtpConfigError, SmtpDeliveryProvider
from dotmac_delivery_smtp.provider import (
    DEFAULT_DATA_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
)
from dotmac_kernel.delivery_providers import OutboundMessage
from dotmac_kernel.messaging.relay import RelayPolicy


class StallOnQuit:
    """Accepts a message, then never answers QUIT."""

    def __init__(self, quit_stall: float) -> None:
        self.quit_stall = quit_stall
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(2)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        f = conn.makefile("rwb", buffering=0)
        try:
            f.write(b"220 stall\r\n")
            while True:
                line = f.readline()
                if not line:
                    return
                verb = line.decode().split(" ", 1)[0].strip().upper()
                if verb == "EHLO":
                    f.write(b"250 stall\r\n")
                elif verb in ("MAIL", "RCPT"):
                    f.write(b"250 ok\r\n")
                elif verb == "DATA":
                    f.write(b"354 go\r\n")
                    buf = b""
                    while not buf.endswith(b"\r\n.\r\n"):
                        buf += f.readline()
                    f.write(b"250 2.0.0 queued\r\n")
                elif verb == "QUIT":
                    time.sleep(self.quit_stall)
                    return
        except OSError:
            return
        finally:
            conn.close()

    def close(self) -> None:
        self.sock.close()


def _message() -> OutboundMessage:
    return OutboundMessage(
        dispatch_id=UUID("11111111-2222-3333-4444-555555555555"),
        channel="email",
        address="learner@recipient.example.test",
        body="hello",
        category="academy.account",
        subject="Hi",
    )


def test_stalled_quit_after_acceptance_runs_under_the_short_timeout() -> None:
    server = StallOnQuit(quit_stall=6.0)
    try:
        cfg = SmtpConfig(
            host="127.0.0.1",
            port=server.port,
            starttls=False,
            username="",
            from_address="Academy <academy@mail.example.test>",
            production=False,
        )
        provider = SmtpDeliveryProvider(
            config=lambda: cfg, timeout=0.5, data_timeout=30
        )
        started = time.monotonic()
        result = provider.send(_message())
        elapsed = time.monotonic() - started
    finally:
        server.close()
    assert result.status == "accepted"
    assert elapsed < 4.0  # not held for the 30 s DATA budget by the stalled QUIT


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf"), 0, -1, True, "5", None]
)
@pytest.mark.parametrize("name", ["timeout", "data_timeout"])
def test_non_finite_or_non_positive_timeouts_are_refused(
    name: str, bad: object
) -> None:
    with pytest.raises(SmtpConfigError):
        SmtpDeliveryProvider(config=lambda: None, **{name: bad})  # type: ignore[arg-type,return-value]


def test_default_budget_is_below_the_relay_lease_policy() -> None:
    operations = 9  # greeting, EHLO, TLS, EHLO, AUTH, MAIL, RCPT, DATA go-ahead, QUIT
    budget = DEFAULT_DATA_TIMEOUT_SECONDS + operations * DEFAULT_TIMEOUT_SECONDS
    assert budget < RelayPolicy().stale_lease_seconds
