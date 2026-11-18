"""Settings specs: typed, UI-entered, no env bootstrap, no password spec."""

from __future__ import annotations

import pytest
from dotmac_delivery_smtp import SmtpConfig, SmtpConfigError, settings
from dotmac_delivery_smtp.manifest import module


def test_specs_typed_and_env_free() -> None:
    keys = {s.key for s in settings.ALL_SPECS}
    assert keys == {"host", "port", "starttls", "username", "from_address"}
    assert all(s.env_var is None for s in settings.ALL_SPECS)
    assert not any(s.is_secret for s in settings.ALL_SPECS)
    assert not any("pass" in s.key for s in settings.ALL_SPECS)
    assert settings.STARTTLS.default is True and settings.PORT.default == 587
    assert module.setting_domains == ("delivery_smtp",)


@pytest.mark.parametrize(
    "bad", ["not an address", "a@b", "x@y.example.test\r\nBcc: z@y.example.test"]
)
def test_from_validator_refuses_bad_values(bad: str) -> None:
    with pytest.raises(ValueError):
        settings._check_from(bad)


def test_from_validator_accepts_named_and_bare() -> None:
    settings._check_from("")
    settings._check_from("a@mail.example.test")
    settings._check_from("Academy <a@mail.example.test>")


@pytest.mark.parametrize(
    "kw",
    [{"host": ""}, {"port": 0}, {"from_address": ""}, {"starttls": False}],
)
def test_config_validate_refuses(kw: dict) -> None:
    base: dict = {
        "host": "smtp.example.test",
        "port": 587,
        "starttls": True,
        "username": "",
        "from_address": "a@mail.example.test",
        "production": True,
    }
    base.update(kw)
    with pytest.raises(SmtpConfigError):
        SmtpConfig(**base).validate()
