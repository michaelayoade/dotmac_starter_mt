"""Fixtures for the hermetic SMTP connector tests (see `smtp_sink_support`).

Two certificates exist so hostname verification is provable in both directions:
one for `mail.example.test` (the configured TLS host) and one for a different
name. The sink listens on loopback; tests pin the connection to it with the
`address` field and bind 127.0.0.0/8 as the network target. The off-target
tests use the documentation range 192.0.2.0/24, which is never connected to.
"""

from __future__ import annotations

import ssl
from collections.abc import Iterator

import pytest
from smtp_sink_support import OTHER_HOST, TLS_HOST, SmtpSink, make_certificate


@pytest.fixture(scope="session")
def good_cert(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, str]:
    return make_certificate(tmp_path_factory.mktemp("good"), "good", TLS_HOST)


@pytest.fixture(scope="session")
def other_cert(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, str]:
    return make_certificate(tmp_path_factory.mktemp("other"), "other", OTHER_HOST)


@pytest.fixture
def sink(good_cert: tuple[str, str]) -> Iterator[SmtpSink]:
    server = SmtpSink(*good_cert)
    yield server
    server.close()


@pytest.fixture
def wrong_name_sink(other_cert: tuple[str, str]) -> Iterator[SmtpSink]:
    server = SmtpSink(*other_cert)
    yield server
    server.close()


@pytest.fixture
def trusting(good_cert: tuple[str, str], other_cert: tuple[str, str]) -> ssl.SSLContext:
    """Trusts BOTH throwaway certificates, so only hostname verification can
    tell them apart (the chain is valid either way)."""
    context = ssl.create_default_context(cafile=good_cert[0])
    context.load_verify_locations(cafile=other_cert[0])
    return context
