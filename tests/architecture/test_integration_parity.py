"""Parity with `dotmac_sub` is reconciled by BEHAVIOUR, and recorded.

A product-first extraction is only product-first if someone can say what
happened to each thing it claims to have ported. Nine suites cover Sub's
integration platform; mechanically copying all nine would carry Sub's product
decisions into a shared engine, and would port a secret-VALUE surface the new
contract deliberately replaced.

So each suite carries an explicit disposition in `EXTRACTION.toml`, and this
file makes that machine-checkable: a suite cannot be quietly dropped, a
disposition cannot be invented, and anything marked `port` must actually be
ported.

The fresh invariant tests in `tests/unit/test_integration_*.py` are hardening.
They SUPPLEMENT parity; they do not stand in for it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOSSIER = PROJECT_ROOT / "packages" / "dotmac-integration" / "EXTRACTION.toml"
FLEET_ROOT = PROJECT_ROOT.parent
SUB = FLEET_ROOT / "dotmac_sub"

#: Every disposition the dossier may use. A CLOSED set: "we ported some of it"
#: is not a disposition, and inventing a new word to describe an awkward suite
#: is how a parity table stops meaning anything.
VALID_DISPOSITIONS = frozenset(
    {
        "port",
        "port-with-fake-plugin",
        "port-service-only",
        "keep-in-sub",
        "exclude-retiring",
        "exclude-superseded",
        "move-to-assembly",
    }
)

#: Dispositions that oblige this repository to hold a ported test.
_PORTING = frozenset({"port", "port-with-fake-plugin", "port-service-only"})


def _dossier() -> dict:
    return tomllib.loads(DOSSIER.read_text(encoding="utf-8"))


def _parity() -> dict[str, str]:
    return {
        suite: disposition
        for suite, disposition in _dossier()["parity"].items()
        if isinstance(disposition, str)
    }


def test_the_dossier_records_a_disposition_for_every_source_suite() -> None:
    """Nine suites, nine answers."""
    assert len(_parity()) == 9


def test_every_disposition_is_from_the_closed_set() -> None:
    unknown = {d for d in _parity().values() if d not in VALID_DISPOSITIONS}
    assert not unknown, (
        f"unrecognised disposition(s) {sorted(unknown)}. Inventing a word to "
        "describe an awkward suite is how a parity table stops meaning anything"
    )


def test_every_disposition_used_is_explained() -> None:
    """A one-word verdict with no stated reason cannot be reviewed."""
    reasons = _dossier()["parity"]["reasons"]
    for disposition in set(_parity().values()):
        assert disposition in reasons, disposition
        assert len(reasons[disposition]) > 40, disposition


def test_preserved_tests_are_exactly_the_ported_suites() -> None:
    """`preserved_tests` is the promise; the parity table is the reasoning.

    They drift the moment someone adds a suite to one and not the other, and a
    dossier that promises to port a suite it never listed is the failure this
    catches.
    """
    promised = {
        entry.split(":", 1)[1]
        for entry in _dossier()["preserved_tests"]
        if entry.startswith("dotmac_sub:")
    }
    porting = {
        suite for suite, disposition in _parity().items() if disposition in _PORTING
    }
    assert promised == porting


def test_excluded_suites_are_not_quietly_preserved() -> None:
    """Specificity for the test above, from the other direction."""
    promised = {e.split(":", 1)[1] for e in _dossier()["preserved_tests"]}
    for suite, disposition in _parity().items():
        if disposition not in _PORTING:
            assert (
                suite not in promised
            ), f"{suite} is {disposition!r} but appears in preserved_tests"


@pytest.mark.parametrize("suite", sorted(_parity()))
def test_each_named_suite_exists_in_the_source(suite: str) -> None:
    """A disposition about a file that does not exist is a stale claim.

    Skips when the fleet is not checked out beside Starter — asserting against
    a repository we cannot see would be inventing evidence.
    """
    if not SUB.is_dir():
        pytest.skip("dotmac_sub is not checked out beside Starter")
    assert (SUB / suite).is_file(), f"{suite} no longer exists in dotmac_sub"


def test_the_secret_value_suites_are_excluded_for_a_contract_reason() -> None:
    """The two exclusions worth defending explicitly.

    `auth encryption` and `header masking` cover storing and masking secret
    VALUES. This contract stores REFERENCES only, so the behaviour they assert
    cannot occur here — porting them would assert a property the design
    forbids, which is worse than not porting them at all.
    """
    parity = _parity()
    assert parity["tests/test_connector_auth_config_encryption.py"] == (
        "exclude-superseded"
    )
    assert parity["tests/test_connector_header_masking.py"] == "exclude-superseded"

    # And the module must actually enforce references, or the exclusion is
    # unjustified.
    from dotmac_integration import SecretValueError, validate_config_revision

    with pytest.raises(SecretValueError):
        validate_config_revision({}, {"token": "a-literal-secret"})


def test_the_fresh_invariant_tests_supplement_rather_than_replace_parity() -> None:
    """They are hardening, and the dossier says so.

    If the parity table were ever emptied and the fresh tests left standing,
    this repository would claim a product-first extraction it had not done.
    """
    unit = PROJECT_ROOT / "tests" / "unit"
    assert (unit / "test_integration_spi.py").is_file()
    assert (unit / "test_integration_bindings.py").is_file()
    assert (unit / "test_integration_execution.py").is_file()
    assert (unit / "test_integration_operations.py").is_file()
    assert _parity(), "parity dispositions must not be emptied"
