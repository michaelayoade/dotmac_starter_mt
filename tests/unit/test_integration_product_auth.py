"""Parity for Integrator's extracted held-key product authenticator."""

from __future__ import annotations

import secrets
from dataclasses import FrozenInstanceError, dataclass
from uuid import UUID

import pytest
from dotmac_integration import (
    ProductAuthenticationFailed,
    ProductAuthenticator,
    ProductPrincipal,
)

REF = "file:///run/secrets/product-query/erp"
KEY = "synthetic-product-query-key"
BINDING_ID = UUID("00000000-0000-0000-0000-000000000001")
SCOPE = "integration:query"


@dataclass(frozen=True, slots=True)
class Caller:
    application: str = "dotmac_erp"
    api_key_ref: str = REF
    scopes: tuple[str, ...] = (SCOPE,)
    binding_ids: tuple[UUID, ...] = (BINDING_ID,)


def test_exact_held_key_authenticates_to_an_immutable_principal() -> None:
    caller = Caller()
    authenticator = ProductAuthenticator((caller,), secret_lookup=lambda _: KEY)

    principal = authenticator.authenticate(KEY, required_scope=SCOPE)

    assert isinstance(principal, ProductPrincipal)
    assert principal.application == "dotmac_erp"
    assert principal.scopes == frozenset({SCOPE})
    assert principal.binding_ids == frozenset({BINDING_ID})
    with pytest.raises(FrozenInstanceError):
        principal.application = "changed"  # type: ignore[misc]
    assert KEY not in repr(principal)
    assert REF not in repr(principal)
    repeated = authenticator.authenticate(KEY, required_scope=SCOPE)
    assert repeated == principal
    assert repeated is not principal


@pytest.mark.parametrize("presented", ["", "wrong-synthetic-key"])
def test_missing_or_wrong_key_has_one_opaque_refusal(presented: str) -> None:
    authenticator = ProductAuthenticator((Caller(),), secret_lookup=lambda _: KEY)

    with pytest.raises(ProductAuthenticationFailed) as exc:
        authenticator.authenticate(presented, required_scope=SCOPE)

    assert str(exc.value) == "product authentication failed"
    assert KEY not in str(exc.value)
    assert not presented or presented not in str(exc.value)


@pytest.mark.parametrize(
    "scopes", [(), ("*",), ("integration:*",), ("Integration:query",), ("query",)]
)
def test_a_valid_key_without_the_exact_scope_is_refused(
    scopes: tuple[str, ...],
) -> None:
    authenticator = ProductAuthenticator(
        (Caller(scopes=scopes),), secret_lookup=lambda _: KEY
    )

    with pytest.raises(ProductAuthenticationFailed, match="authentication failed"):
        authenticator.authenticate(KEY, required_scope=SCOPE)


def test_an_unheld_reference_fails_closed_without_naming_material() -> None:
    authenticator = ProductAuthenticator((Caller(),), secret_lookup=lambda _: None)

    with pytest.raises(ProductAuthenticationFailed) as exc:
        authenticator.authenticate(KEY, required_scope=SCOPE)

    assert str(exc.value) == "product authentication failed"
    assert KEY not in str(exc.value)
    assert REF not in str(exc.value)


def test_no_callers_refuses_without_consulting_the_lookup() -> None:
    def lookup(reference: str) -> str | None:
        raise AssertionError("an empty caller list has no material to look up")

    authenticator = ProductAuthenticator((), secret_lookup=lookup)

    with pytest.raises(ProductAuthenticationFailed, match="authentication failed"):
        authenticator.authenticate(KEY, required_scope=SCOPE)


@pytest.mark.parametrize("held", ["", " synthetic key "])
def test_exact_comparison_preserves_empty_and_whitespace_material(held: str) -> None:
    authenticator = ProductAuthenticator((Caller(),), secret_lookup=lambda _: held)

    principal = authenticator.authenticate(held, required_scope=SCOPE)
    assert principal.application == "dotmac_erp"


def test_scan_all_callers_compares_only_held_keys_and_last_match_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compared: list[tuple[str, str]] = []
    looked_up: list[str] = []
    compare_digest = secrets.compare_digest

    def record_compare(presented: str, held: str) -> bool:
        compared.append((presented, held))
        return compare_digest(presented, held)

    def lookup(reference: str) -> str | None:
        looked_up.append(reference)
        return None if reference == "unheld" else KEY

    monkeypatch.setattr(
        "dotmac_integration.product_auth.secrets.compare_digest", record_compare
    )
    authenticator = ProductAuthenticator(
        (
            Caller(api_key_ref="first"),
            Caller(api_key_ref="unheld"),
            Caller(application="last", api_key_ref="last"),
        ),
        secret_lookup=lookup,
    )

    principal = authenticator.authenticate(KEY, required_scope=SCOPE)

    assert principal.application == "last"
    assert looked_up == ["first", "unheld", "last"]
    assert compared == [(KEY, KEY), (KEY, KEY)]


def test_last_matching_caller_without_scope_refuses_an_earlier_grant() -> None:
    authenticator = ProductAuthenticator(
        (Caller(), Caller(application="last", scopes=("integration:observe",))),
        secret_lookup=lambda _: KEY,
    )

    with pytest.raises(ProductAuthenticationFailed, match="authentication failed"):
        authenticator.authenticate(KEY, required_scope=SCOPE)


def test_callers_are_captured_once_but_held_keys_are_looked_up_each_time() -> None:
    callers = [Caller()]
    held = {REF: KEY}
    authenticator = ProductAuthenticator(iter(callers), secret_lookup=held.get)
    callers.clear()

    principal = authenticator.authenticate(KEY, required_scope=SCOPE)
    assert principal.application == "dotmac_erp"
    held[REF] = "rotated-synthetic-key"
    with pytest.raises(ProductAuthenticationFailed):
        authenticator.authenticate(KEY, required_scope=SCOPE)
    assert (
        authenticator.authenticate(held[REF], required_scope=SCOPE).application
        == "dotmac_erp"
    )


@pytest.mark.parametrize("presented,held", [("non-ascii-é", KEY), (KEY, "held-é")])
def test_compare_digest_non_ascii_type_error_is_preserved(
    presented: str,
    held: str,
) -> None:
    authenticator = ProductAuthenticator((Caller(),), secret_lookup=lambda _: held)

    with pytest.raises(TypeError):
        authenticator.authenticate(presented, required_scope=SCOPE)


def test_lookup_errors_are_not_converted_to_authentication_failures() -> None:
    failure = RuntimeError("synthetic held-source failure")

    def lookup(reference: str) -> str | None:
        raise failure

    authenticator = ProductAuthenticator((Caller(),), secret_lookup=lookup)

    with pytest.raises(RuntimeError) as exc:
        authenticator.authenticate(KEY, required_scope=SCOPE)
    assert exc.value is failure
