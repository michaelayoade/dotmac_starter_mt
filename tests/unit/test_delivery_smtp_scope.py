"""F1: SMTP settings are read at platform scope only.

A tenant admin can write a tenant row for any registered spec through the
generic settings editor. If honoured, `host` could be pointed at a server the
tenant controls and the platform SMTP password would be sent to it.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from dotmac_delivery_smtp import SmtpDeliveryProvider, resolve_config, settings
from dotmac_delivery_smtp.manifest import module
from dotmac_kernel import secret_sources
from dotmac_kernel.models import Tenant
from dotmac_kernel.setting_domains import (
    SettingDomainRegistry,
    active_setting_domains,
    install_setting_domains,
)
from dotmac_kernel.settings_resolver import upsert_by_key

from tests.unit.test_delivery_smtp import (
    FakeSmtp,
    _Src,
    certpair,  # noqa: F401 - fixture
    msg,
    trusting,  # noqa: F401 - fixture
)


@pytest.fixture(autouse=True)
def _domain() -> Iterator[None]:
    previous = active_setting_domains()
    install_setting_domains(
        SettingDomainRegistry([("delivery_smtp", settings.DELIVERY_SMTP_DOMAIN.value)])
    )
    yield
    install_setting_domains(previous)


@pytest.fixture
def tenant(db) -> Tenant:
    row = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(row)
    db.flush()
    return row


def test_tenant_rows_are_inert_and_the_password_never_reaches_a_tenant_host(
    db,
    tenant,
    certpair,  # noqa: F811
    trusting,  # noqa: F811
) -> None:
    good = FakeSmtp(*certpair)
    attacker = FakeSmtp(*certpair)
    secret_sources.install_secret_source(_Src({"smtp_password": "pw-ok"}))
    try:
        for key, value in (
            ("host", "127.0.0.1"),
            ("port", good.port),
            ("starttls", True),
            ("username", "mailer"),
            ("from_address", "Academy <a@mail.example.test>"),
        ):
            upsert_by_key(db, settings.DELIVERY_SMTP_DOMAIN, key, value, tenant_id=None)
        # A tenant admin's rows, written through the generic editor's path.
        for key, value in (
            ("host", "127.0.0.2"),
            ("port", attacker.port),
            ("username", "stolen"),
            ("from_address", "evil@attacker.example.test"),
        ):
            upsert_by_key(
                db, settings.DELIVERY_SMTP_DOMAIN, key, value, tenant_id=tenant.id
            )
        cfg = resolve_config(db, production=True)
        assert (cfg.host, cfg.port, cfg.username) == ("127.0.0.1", good.port, "mailer")
        assert cfg.from_address == "Academy <a@mail.example.test>"

        provider = SmtpDeliveryProvider(
            config=lambda: resolve_config(db, production=True),
            ssl_context=trusting,
            timeout=3,
        )
        assert provider.send(msg()).status == "accepted"
        assert good.auth_seen and len(good.messages) == 1
        assert attacker.commands == [] and not attacker.auth_seen
    finally:
        secret_sources.clear_secret_source()
        good.close()
        attacker.close()


def test_resolve_config_takes_no_tenant_argument() -> None:
    import inspect

    assert "tenant_id" not in inspect.signature(resolve_config).parameters
    assert module.setting_domains == ("delivery_smtp",)
