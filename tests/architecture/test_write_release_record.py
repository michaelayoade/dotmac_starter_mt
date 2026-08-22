"""The post-release record is written by a script, not by remembering.

`scripts/write_release_record.py` exists because between 2026-08-21 and
2026-08-22 a publication left `main` red FOUR times — twice for the same
distribution, one version apart. The rule is stated verbatim in the tests that
catch it (`test_the_ledger_holds_no_stale_absolution`,
`test_the_tag_oracle_is_usable_and_complete`) and nothing enforced it, so the
only thing between a release and a red `main` was somebody remembering.

These tests hold the writer to the properties that make it safe to run from a
release workflow immediately after tagging:

- it never writes a record for a tag that does not exist;
- it never removes a row describing a DIFFERENT version;
- it is idempotent, so a re-run after a partial repair converges;
- it edits the JSON as text, so the ledger's prose survives untouched.

The digest half is proven against a real published tag rather than a fixture:
a hand-built record already in `RELEASED_TAGS` is the oracle, and the writer
has to reproduce it exactly.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "write_release_record.py"

#: A real, published, already-recorded release — the oracle for the digest path.
KNOWN_TAG = "dotmac-integration-v0.1.0a12"
KNOWN_DISTRIBUTION = "dotmac-integration"
KNOWN_PACKAGE_DIR = "packages/dotmac-integration"
KNOWN_IMPORT_NAME = "dotmac_integration"


def _writer():
    spec = importlib.util.spec_from_file_location("write_release_record", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recorded_entry(writer, tag: str):
    """The committed `RELEASED_TAGS` entry for `tag`, as data."""
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    block = re.search(r"RELEASED_TAGS: [^=]+= (\{.*?\n\})\n\n", text, re.DOTALL)
    assert block is not None, "RELEASED_TAGS is no longer a literal this can read"
    return ast.literal_eval(block.group(1))[tag]


def _ledger_text(writer) -> str:
    return writer.LEDGER.read_text(encoding="utf-8")


# ── The digest path, against a real tag ─────────────────────────────────────


def test_digests_read_from_the_tag_reproduce_a_hand_built_record() -> None:
    """The writer must agree with a record a human built and CI accepted.

    This is the property that matters: the digests are read from the TAG, not
    from the working tree. A digest taken from the tree would agree with an
    edit made after publication, which is precisely what the immutability gate
    exists to refuse — so a writer that reads the tree would pass its own tests
    while defeating the thing it feeds.
    """
    writer = _writer()
    distribution, commit, digests = _recorded_entry(writer, KNOWN_TAG)
    assert distribution == KNOWN_DISTRIBUTION

    assert writer.tag_commit(KNOWN_TAG) == commit
    assert (
        writer.migration_digests(KNOWN_TAG, KNOWN_PACKAGE_DIR, KNOWN_IMPORT_NAME)
        == digests
    )


def test_an_entry_renders_in_the_shape_the_map_already_uses() -> None:
    """A generated entry must be indistinguishable from a hand-written one, or
    the map becomes two styles and the next reader cannot tell which is which.
    """
    writer = _writer()
    _, commit, digests = _recorded_entry(writer, KNOWN_TAG)
    rendered = writer._rendered_entry(KNOWN_TAG, KNOWN_DISTRIBUTION, commit, digests)
    # Round-trips: what it renders parses back to what it was given.
    parsed = ast.literal_eval("{" + rendered.rstrip().rstrip(",") + "}")
    assert parsed == {KNOWN_TAG: (KNOWN_DISTRIBUTION, commit, digests)}


def test_a_second_write_of_the_same_tag_changes_nothing() -> None:
    """Idempotence. A workflow may re-run, and a hand repair may race it."""
    writer = _writer()
    _, commit, digests = _recorded_entry(writer, KNOWN_TAG)
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    unchanged, added = writer.add_released_tag(
        text, KNOWN_TAG, KNOWN_DISTRIBUTION, commit, digests
    )
    assert not added
    assert unchanged == text


# ── The ledger path ─────────────────────────────────────────────────────────


def test_removing_a_row_touches_only_that_row() -> None:
    """The ledger is edited as TEXT.

    A `json.loads`/`json.dumps` round-trip escapes every em-dash in the
    `$comment` prose to `\\u2014`, turning a five-line removal into a fifteen-
    line diff over paragraphs the change has no business touching. That
    happened three times in one session before the rule was written down.
    """
    writer = _writer()
    before = _ledger_text(writer)
    victim = sorted(json.loads(before)["unpublished"])[0]

    after, removed = writer.remove_ledger_row(before, victim)
    assert removed

    parsed = json.loads(after)
    assert victim not in parsed["unpublished"]
    assert parsed["$comment"] == json.loads(before)["$comment"]
    assert set(json.loads(before)["unpublished"]) - {victim} == set(
        parsed["unpublished"]
    )
    # Byte-level: the ONLY lines that disappear are the five the row occupies,
    # and every other line survives verbatim. Comparing the surviving text to
    # the original with the row's span excised is what makes "only that row"
    # a checked claim rather than a hopeful one.
    assert len(before.splitlines()) - len(after.splitlines()) == 5
    surviving = [line for line in before.splitlines() if line in after.splitlines()]
    assert surviving == after.splitlines()


def test_removing_the_last_row_leaves_valid_json() -> None:
    """The final entry carries no trailing comma, so the naive pattern misses
    it and a second pattern has to take the PRECEDING comma instead. Getting
    this wrong produces a file that parses nowhere and fails five gates at
    once."""
    writer = _writer()
    before = _ledger_text(writer)
    last = list(json.loads(before)["unpublished"])[-1]

    after, removed = writer.remove_ledger_row(before, last)
    assert removed, f"{last} is the final row and was not matched"
    parsed = json.loads(after)
    assert last not in parsed["unpublished"]


def test_removing_an_absent_row_is_a_no_op_not_a_failure() -> None:
    """Convergence: re-running the record after a partial repair must settle,
    not refuse. A workflow that fails on its own second run is one nobody
    re-runs."""
    writer = _writer()
    before = _ledger_text(writer)
    after, removed = writer.remove_ledger_row(before, "dotmac-not-a-real-package")
    assert not removed
    assert after == before


# ── Refusals ────────────────────────────────────────────────────────────────


def test_a_record_is_refused_before_its_tag_exists() -> None:
    """The record describes a publication. Writing it first would remove a live
    exemption for a version that may never ship."""
    writer = _writer()
    with pytest.raises(writer.ReleaseRecordError) as refusal:
        writer.write_record(
            distribution=KNOWN_DISTRIBUTION,
            version="9.9.9",
            tag="dotmac-integration-v9.9.9-never-tagged",
            package_dir=KNOWN_PACKAGE_DIR,
            import_name=KNOWN_IMPORT_NAME,
        )
    assert "does not resolve to a commit" in str(refusal.value)


def test_a_row_describing_a_different_version_is_refused() -> None:
    """The sharpest refusal. A row excusing `0.1.0a5` is a LIVE exemption when
    `0.1.0a4` is published; deleting it would silently promise a version nobody
    can install — the exact defect the ledger exists to prevent, committed by
    the tool meant to maintain it."""
    writer = _writer()
    ledger = json.loads(_ledger_text(writer))["unpublished"]
    distribution, row = next(iter(sorted(ledger.items())))
    with pytest.raises(writer.ReleaseRecordError) as refusal:
        writer.write_record(
            distribution=distribution,
            version=row["declared"] + "-not-this-one",
            tag=KNOWN_TAG,  # resolves, so the version check is what fires
            package_dir=None,
            import_name=None,
        )
    assert "describes a different" in str(refusal.value)


def test_an_unanchorable_distribution_refuses_rather_than_guessing() -> None:
    """A distribution with no existing entry has no established position in the
    map. Appending it somewhere arbitrary would make the file's ordering an
    accident, so the first entry stays a human decision."""
    writer = _writer()
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    with pytest.raises(writer.ReleaseRecordError) as refusal:
        writer.add_released_tag(
            text, "dotmac-nonesuch-v1.0.0", "dotmac-nonesuch", "abcdef12", {"a.py": "x"}
        )
    assert "anchor" in str(refusal.value)
