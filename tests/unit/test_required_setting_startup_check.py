"""A configuration defect fails startup; only an unreachable store is excused.

`_required_setting_errors` once wrapped the seed and `validate_required_settings`
in one `except Exception`, logged a warning and returned no errors. Its
docstring justified a single case — a database this cannot reach — but the
handler caught every case, so a deployment whose encryption keys were
unreadable, whose settings table was missing a column, whose role lacked a
privilege, or whose seed itself was broken started in production with
required-setting validation silently skipped. The check that exists to stop a
misconfigured deployment serving traffic was the one thing a misconfiguration
could switch off (ADR-0011, amended 2026-08-20).

The split is the point: connection-level failure says nothing about whether a
setting is configured, and refusing to start over it would be fatal for the
wrong reason. Everything else IS an answer, and the answer is "we do not know",
which in production must not be survivable.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from dotmac_kernel import app_factory
from sqlalchemy.exc import (
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)
from sqlalchemy.exc import TimeoutError as SQLTimeoutError

#: A secret's value, as it would appear in a seed's bound parameters.
SECRET_VALUE = "s3cr3t-smtp-password-do-not-log"


class _KeyringError(Exception):
    """Stands in for `dotmac_kernel.settings_crypto.KeyringError`."""


def _raise(exc: BaseException):
    """A `platform_session` whose body raises when the seed runs."""

    class _Session:
        pass

    @contextmanager
    def _resolver():
        yield _Session()

    def _seed(_db: object) -> None:
        raise exc

    return _resolver, _seed


@pytest.fixture
def patched(monkeypatch):
    """Drive `_required_setting_errors` with an injected failure.

    Patches the two names the function imports lazily, via their real modules,
    so the production import path is exercised rather than bypassed.
    """

    def _install(exc: BaseException):
        resolver, seed = _raise(exc)
        import dotmac_kernel.db as db_mod
        import dotmac_kernel.settings_resolver as sr

        monkeypatch.setattr(db_mod, "platform_session", resolver, raising=True)
        monkeypatch.setattr(sr, "seed_settings_from_env", seed, raising=True)
        monkeypatch.setattr(
            sr, "validate_required_settings", lambda _db: [], raising=True
        )
        return app_factory._required_setting_errors()

    return _install


def _dbapi(cls, message: str, params: dict | None = None):
    """Build a SQLAlchemy DBAPI error the way SQLAlchemy itself would."""
    return cls(message, params, Exception(message))


# -- the store is genuinely unreachable: excused ---------------------------


@pytest.mark.parametrize(
    "exc",
    [
        _dbapi(OperationalError, "could not connect to server"),
        _dbapi(InterfaceError, "connection already closed"),
        SQLTimeoutError("QueuePool limit reached"),
    ],
    ids=["operational", "interface", "pool-timeout"],
)
def test_an_unreachable_store_yields_no_errors(patched, exc) -> None:
    assert patched(exc) == []


# -- anything else is a configuration defect: fatal in production ----------


@pytest.mark.parametrize(
    "exc",
    [
        _KeyringError("SETTINGS_ENCRYPTION_KEYS is not valid JSON"),
        _dbapi(ProgrammingError, 'column "scope_kind" does not exist'),
        _dbapi(ProgrammingError, "permission denied for table domain_settings"),
        ValueError("the seed itself is broken"),
    ],
    ids=["crypto", "schema", "permission", "seed-defect"],
)
def test_a_configuration_defect_is_reported_not_swallowed(patched, exc) -> None:
    errors = patched(exc)
    assert errors, (
        f"{type(exc).__name__} produced no error, so production would start "
        "with required-setting validation skipped — the exact fail-open this "
        "test exists to prevent"
    )
    assert "configuration defect" in errors[0]


def test_the_report_never_renders_a_bound_parameter(patched) -> None:
    """A seed's parameters can be a secret's value; the report must not carry it.

    `StatementError.__str__` appends the failing SQL and its bound parameters.
    An `IntegrityError` raised while seeding an encrypted setting therefore
    stringifies with the secret in it, and that string would land in the log and
    in the `RuntimeError` that stops production — ADR-0009 logs names, never
    values.
    """
    exc = _dbapi(
        IntegrityError,
        "duplicate key value violates unique constraint",
        {"value_text": SECRET_VALUE},
    )
    # The premise: SQLAlchemy really does render the parameter. If this stops
    # being true the test below passes for the wrong reason.
    assert SECRET_VALUE in str(exc)

    errors = patched(exc)
    assert errors
    assert SECRET_VALUE not in errors[0], (
        "the startup error rendered a bound parameter — a secret setting's "
        "value would reach the logs and the startup failure message"
    )
    assert (
        "IntegrityError" in errors[0]
    ), "redaction went too far: the defect must still be identifiable by type"


def test_a_clean_run_returns_the_validators_findings(monkeypatch) -> None:
    """The happy path is unchanged: the seed runs, then validation answers."""
    order: list[str] = []

    class _Session:
        def commit(self) -> None:
            order.append("commit")

    @contextmanager
    def _resolver():
        yield _Session()

    import dotmac_kernel.db as db_mod
    import dotmac_kernel.settings_resolver as sr

    monkeypatch.setattr(db_mod, "platform_session", _resolver, raising=True)
    monkeypatch.setattr(
        sr, "seed_settings_from_env", lambda _db: order.append("seed"), raising=True
    )
    monkeypatch.setattr(
        sr,
        "validate_required_settings",
        lambda _db: ["smtp/password is required and not set"],
        raising=True,
    )

    assert app_factory._required_setting_errors() == [
        "smtp/password is required and not set"
    ]
    # Seed BEFORE validate, or a setting configured by environment reads as
    # unconfigured on first boot.
    assert order == ["seed", "commit"]
