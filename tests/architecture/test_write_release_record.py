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
- it enrols every migration-history map on a distribution's first release;
- it moves incremental migrations from ``UNRELEASED`` into the immutable tag;
- it validates both record files before writing either one.

The digest half is proven against a real published tag rather than a fixture:
a hand-built record already in `RELEASED_TAGS` is the oracle, and the writer
has to reproduce it exactly.
"""

from __future__ import annotations

import ast
import difflib
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


def _mapping_keys(source: str, name: str) -> set[str]:
    """Literal string keys from one top-level dictionary assignment."""
    tree = ast.parse(source)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == name
    )
    assert isinstance(assignment.value, ast.Dict)
    return {ast.literal_eval(key) for key in assignment.value.keys if key is not None}


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


def _without_tags_after(text: str, distribution: str, tag: str) -> str:
    """Drop every recorded tag of `distribution` newer than `tag`."""
    keep = int(tag.rsplit("a", 1)[1])
    for name in re.findall(rf'"{re.escape(distribution)}-v[^"]+"', text):
        if int(name.rsplit("a", 1)[1].rstrip('"')) <= keep:
            continue
        entry = re.search(rf"\n    {re.escape(name)}: \(.*?\n    \),", text, re.S)
        assert entry is not None, name
        text = text[: entry.start()] + text[entry.end() :]
    return text


def _unreleased_filenames(text: str, distribution: str) -> set[str]:
    row = re.search(
        rf'^    "{re.escape(distribution)}": frozenset\((.*?)\),$',
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert row is not None, distribution
    payload = row.group(1).strip()
    return set() if not payload else ast.literal_eval(payload)


def _with_unreleased_filenames(
    text: str, distribution: str, filenames: set[str]
) -> str:
    row = re.search(
        rf'^    "{re.escape(distribution)}": frozenset\((.*?)\),$',
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert row is not None, distribution
    values = ", ".join(repr(filename) for filename in sorted(filenames))
    payload = f"{{{values}}}" if values else ""
    replacement = f'    "{distribution}": frozenset({payload}),'
    return text[: row.start()] + replacement + text[row.end() :]


def test_a_partial_incremental_record_retires_the_released_migration() -> None:
    """The a14 failure: adding the digest without clearing UNRELEASED is not a
    record, because the same file then reads as immutable and editable at once.

    Reconstruct that historical partial state from the corrected record: the
    automatic writer had added the a14 tag row while ``ig_0012`` survived in
    the editable set. Re-running the writer must converge by clearing it.
    """
    writer = _writer()
    tag = "dotmac-integration-v0.1.0a14"
    distribution, commit, digests = _recorded_entry(writer, tag)
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")

    current_unreleased = _unreleased_filenames(text, distribution)
    text = _with_unreleased_filenames(
        text,
        distribution,
        current_unreleased | {"ig_0012_delivery_evidence.py"},
    )
    assert "ig_0012_delivery_evidence.py" in _unreleased_filenames(text, distribution)
    # Reconstruct the state AS OF a14. `add_released_tag` treats every other
    # recorded tag of the distribution as "previously released", without regard
    # to order, so a later tag that re-lists ig_0012 — a15 does, having shipped
    # no new migration — would leave nothing for this repair to retire.
    text = _without_tags_after(text, distribution, tag)
    assert '"dotmac-integration-v0.1.0a15"' not in text

    updated, changed = writer.add_released_tag(text, tag, distribution, commit, digests)

    assert changed
    assert _unreleased_filenames(updated, distribution) == current_unreleased

    unchanged, changed_again = writer.add_released_tag(
        updated, tag, distribution, commit, digests
    )
    assert not changed_again
    assert unchanged == updated


def test_a_new_migration_absent_from_unreleased_is_refused() -> None:
    """Sensitivity proof: the retirement check must fail closed, not merely
    rewrite whatever happens to be present in the editable set."""
    writer = _writer()
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    assert "ig_9999_missing_declaration.py" not in _unreleased_filenames(
        text, "dotmac-integration"
    )
    _, _, prior = _recorded_entry(writer, "dotmac-integration-v0.1.0a13")
    digests = {**prior, "ig_9999_missing_declaration.py": "a" * 64}

    with pytest.raises(writer.ReleaseRecordError) as refusal:
        writer.add_released_tag(
            text,
            "dotmac-integration-v0.1.0a99",
            "dotmac-integration",
            "abcdef12",
            digests,
        )

    assert "not declared in UNRELEASED" in str(refusal.value)
    assert "ig_9999_missing_declaration.py" in str(refusal.value)


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
    # Byte-level: exactly ONE contiguous deletion of the five lines a row
    # occupies, and no other edit anywhere in the file.
    #
    # Expressed as a diff rather than by filtering, because the obvious filter
    # is wrong in a way that looks right: a row's closing `},` and its
    # `"state"` line recur in every other row, so "keep the lines of `before`
    # that appear in `after`" keeps those duplicates and the comparison
    # inflates. The shape of the edit is the claim, so the diff is what states
    # it.
    diff = difflib.SequenceMatcher(
        None, before.splitlines(), after.splitlines(), autojunk=False
    )
    edits = [op for op in diff.get_opcodes() if op[0] != "equal"]
    assert len(edits) == 1, f"expected one edit, got {edits}"
    tag, start, end, _, _ = edits[0]
    assert tag == "delete", f"expected a deletion, got {tag!r}"
    assert end - start == 5, f"a ledger row is five lines; removed {end - start}"


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


# ── The wrapper fails loudly ────────────────────────────────────────────────


def test_an_unopened_record_fails_the_run_rather_than_warning() -> None:
    """The correction that made #357 worth having.

    The first version of `open_release_record_pr.sh` exited 0 and printed a
    `::warning::` when it could not open the record, reasoning that the
    artifact is already published so the run should not report a successful
    publication as failed.

    That reasoning was wrong, and it rebuilt the exact failure class the
    script exists to close: correctness went back to depending on somebody
    READING a warning inside a green run — and a green run with no record
    looks, at a glance, exactly like a green run with one. "Tag exists,
    record missing" has to be visible where people already look.

    Asserted on the source rather than by running the script, because every
    `give_up` path needs a network, a token and a real tag. The properties
    are textual and this is what makes them stay true.
    """
    script = (PROJECT_ROOT / "scripts" / "open_release_record_pr.sh").read_text(
        encoding="utf-8"
    )
    body = script.split("give_up() {", 1)
    assert len(body) == 2, "give_up() is gone; this guard needs rewriting"
    # Split on the closing brace at column 0, NOT the first `}` — the body is
    # full of `${TAG}` and `${MANUAL}`, and splitting on those truncated the
    # handler to nothing while every assertion below still "passed".
    handler = body[1].split("\n}", 1)[0]

    assert "exit 1" in handler, (
        "give_up must FAIL the run. Exiting 0 puts correctness back on somebody "
        "noticing a warning in a green run, which is the failure this closes"
    )
    assert "exit 0" not in handler
    assert "::warning::" not in handler, "a warning is what did not work"

    # It must also say the artifact is fine, or the loud failure invites the
    # one genuinely harmful reaction: re-running the publish.
    assert "DO NOT RE-RUN THE PUBLISH" in handler
    assert "already" in handler and "published" in handler

    # ...and name the manual repair, since that is what the reader must do.
    assert "${MANUAL}" in handler

    # ...and link the pull-request page for the branch it already pushed. The
    # proven instance (kernel a92, run 32617583628) failed at exactly this
    # step: the edits were correct and the branch was pushed, so the gap was
    # one click wide and nothing said where to click.
    assert "${COMPARE_URL}" in handler


def test_the_only_successful_exits_are_a_record_opened_or_already_complete() -> None:
    """Two success paths, both meaning the record EXISTS. Any third would be a
    way for the run to go green with the ledger still stale."""
    script = (PROJECT_ROOT / "scripts" / "open_release_record_pr.sh").read_text(
        encoding="utf-8"
    )
    exits = [
        line.strip() for line in script.splitlines() if line.strip().startswith("exit ")
    ]
    assert (
        exits.count("exit 0") == 1
    ), f"expected exactly one exit 0 (the already-complete path), found {exits}"
    # `exit 2` is argument misuse, which is a failure like any other.
    assert set(exits) <= {"exit 0", "exit 1", "exit 2"}, exits


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


def test_a_first_release_enrols_every_migration_guard_map() -> None:
    """The release workflow must be able to record a module's first tag.

    Appending only ``RELEASED_TAGS`` is not enough: the guard then rejects the
    unknown owner and still leaves main red. First-release enrolment is one
    mechanical operation over all four owner maps plus the immutable tag map.
    """
    writer = _writer()
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    distribution = "dotmac-nonesuch"
    tag = "dotmac-nonesuch-v1.0.0"
    assert distribution not in _mapping_keys(text, "DISTRIBUTIONS")

    updated, added = writer.add_released_tag(
        text,
        tag,
        distribution,
        "abcdef12",
        {"ns_0001_nonesuch.py": "a" * 64},
        package_dir="packages/dotmac-nonesuch",
        import_name="dotmac_nonesuch",
    )

    assert added
    ast.parse(updated)
    for mapping in ("DISTRIBUTIONS", "LINEAGE_GLOBS", "TAG_PREFIXES", "UNRELEASED"):
        assert distribution in _mapping_keys(updated, mapping)
    assert tag in _mapping_keys(updated, "RELEASED_TAGS")
    assert '"dotmac-nonesuch": "ns_*.py"' in updated
    assert '"dotmac-nonesuch": "dotmac-nonesuch-v"' in updated
    assert "packages/dotmac-nonesuch" in updated
    assert "dotmac_nonesuch/migrations/versions" in updated

    unchanged, added_again = writer.add_released_tag(
        updated,
        tag,
        distribution,
        "abcdef12",
        {"ns_0001_nonesuch.py": "a" * 64},
    )
    assert not added_again
    assert unchanged == updated

    long_entry = writer._first_release_entries(
        distribution="dotmac-operational-escalations",
        tag="dotmac-operational-escalations-v0.1.0a1",
        commit="abcdef12",
        digests={"oe_0001_escalation_policy.py": "b" * 64},
        package_dir="packages/dotmac-operational-escalations",
        import_name="dotmac_operational_escalations",
    )["DISTRIBUTIONS"]
    assert max(map(len, long_entry.splitlines())) <= 88
    assert '/ "packages/dotmac-operational-escalations"' in long_entry
    assert '/ "src/dotmac_operational_escalations/migrations/versions"' in long_entry


def test_a_refused_first_enrolment_does_not_partially_remove_the_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both record files change together only after every premise validates."""
    writer = _writer()
    ledger = tmp_path / "declared-publication-baseline.json"
    released = tmp_path / "test_released_migrations.py"
    ledger.write_text(
        json.dumps(
            {
                "unpublished": {
                    "dotmac-nonesuch": {
                        "declared": "1.0.0",
                        "reason": "synthetic first-release atomicity fixture",
                        "state": "never-published",
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    released.write_text(
        writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    ledger_before = ledger.read_text(encoding="utf-8")
    released_before = released.read_text(encoding="utf-8")

    monkeypatch.setattr(writer, "LEDGER", ledger)
    monkeypatch.setattr(writer, "RELEASED_TAGS_MODULE", released)
    monkeypatch.setattr(writer, "tag_commit", lambda _tag: "abcdef12")
    monkeypatch.setattr(
        writer,
        "migration_digests",
        lambda *_args: {
            "ns_0001_nonesuch.py": "a" * 64,
            "wrong_0002_second_lineage.py": "b" * 64,
        },
    )

    with pytest.raises(writer.ReleaseRecordError) as refusal:
        writer.write_record(
            distribution="dotmac-nonesuch",
            version="1.0.0",
            tag="dotmac-nonesuch-v1.0.0",
            package_dir="packages/dotmac-nonesuch",
            import_name="dotmac_nonesuch",
        )

    assert "one migration prefix" in str(refusal.value)
    assert ledger.read_text(encoding="utf-8") == ledger_before
    assert released.read_text(encoding="utf-8") == released_before
