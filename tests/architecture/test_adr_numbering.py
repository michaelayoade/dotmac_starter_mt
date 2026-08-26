"""ADR numbers are unique claims in the merged catalogue.

A number written on a branch is not a reservation.  The guard runs over the
catalogue GitHub presents to the pull-request test job, so a later claim must
renumber when an earlier claim has already reached ``main``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIRECTORY = REPO_ROOT / "docs" / "adr"
ADR_FILENAME = re.compile(r"^(?P<number>\d{4})-.+\.md$")


class DuplicateAdrNumberError(ValueError):
    """Raised when more than one ADR file claims the same number."""


def assert_unique_adr_numbers(adr_directory: Path) -> None:
    """Refuse two ADR filenames that claim one four-digit number."""

    claims: dict[str, list[str]] = defaultdict(list)
    for path in sorted(adr_directory.glob("*.md")):
        match = ADR_FILENAME.fullmatch(path.name)
        if match is not None:
            claims[match.group("number")].append(path.name)

    duplicates = {
        number: filenames for number, filenames in claims.items() if len(filenames) > 1
    }
    if duplicates:
        detail = "; ".join(
            f"ADR-{number}: {', '.join(filenames)}"
            for number, filenames in sorted(duplicates.items())
        )
        raise DuplicateAdrNumberError(
            "ADR numbers are claimed only once in docs/adr; " + detail
        )


def test_every_adr_number_is_unique() -> None:
    """The checked-in ADR catalogue has no ambiguous number."""

    assert_unique_adr_numbers(ADR_DIRECTORY)


def test_duplicate_adr_number_detector_is_sensitive(tmp_path: Path) -> None:
    """ADR-0018: prove the guard fails against a planted collision."""

    (tmp_path / "0069-first-decision.md").write_text("first\n")
    (tmp_path / "0069-second-decision.md").write_text("second\n")

    with pytest.raises(
        DuplicateAdrNumberError,
        match=r"ADR-0069: 0069-first-decision\.md, 0069-second-decision\.md",
    ):
        assert_unique_adr_numbers(tmp_path)
