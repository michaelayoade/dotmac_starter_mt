"""Unit coverage for `app.core.identity` (Task 5) — the single-owner
implementations of the invariants the two `Party` writers
(`app.features.auth.service.register` and `app.features.parties.service`'s
create/update paths) must preserve identically: email normalization and the
person `display_name` projection.
"""

from __future__ import annotations

from app.core.identity import normalize_email, person_display_name


def test_normalize_email_lowercases() -> None:
    assert normalize_email("MiXeD@ExAmPlE.com") == "mixed@example.com"


def test_normalize_email_already_lowercase_is_unchanged() -> None:
    assert normalize_email("plain@example.com") == "plain@example.com"


def test_person_display_name_joins_first_and_last() -> None:
    assert person_display_name("Ada", "Lovelace") == "Ada Lovelace"
