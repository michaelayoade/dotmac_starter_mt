"""The legacy production compose is FROZEN, and the freeze is measured.

`docs/CONTROL_EXCEPTIONS.md` (2026-08-30) acknowledges the root
`docker-compose.yml` as a real production deployment declaration that sits
outside the governed ADR 0014 surface, because it resolves its image through a
deploy-time substitution rather than an immutable digest.

An acknowledgement with no measurement is an exemption, and ADR-0018 says an
exemption states an enforceable premise or the region is unmonitored rather
than exempt. **The premise here is that the legacy path does not grow.** This
file is what makes that checkable.

## The ratchet is two-directional, and the second direction is the point

It fails when a count RISES — the obvious direction — and it also fails when a
count FALLS without the baseline being lowered in the same reviewed change.

A one-directional ratchet silently absorbs improvement. Somebody removes a
caller, the number drifts below the baseline, and from then on the guard is
measuring a fleet it no longer describes: it would not notice a caller being
added back. Recording the improvement WHEN IT HAPPENS, by the person who made
it, keeps the baseline a description rather than a ceiling nobody has looked at.

## What is deliberately not checked

Whether the legacy path still WORKS, and whether parity with the governed
declaration has been proven. Those gate retirement (`EXTRACTION.toml`'s
`local_copy_retirement`) and neither is derivable from repository content. This
file measures shape, and says so rather than implying coverage it does not have.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE = PROJECT_ROOT / "docs" / "inventories" / "legacy-compose-baseline.json"
EXCEPTIONS = PROJECT_ROOT / "docs" / "CONTROL_EXCEPTIONS.md"

#: An image reference that is NOT an immutable digest. Both shapes count: a
#: mutable tag, and a deploy-time substitution — ADR 0014 refuses the second
#: for the same reason as the first, because a value resolved later cannot be
#: the value that was approved.
_IMAGE_LINE = re.compile(r"^\s*image:\s*(?P<value>\S.*?)\s*$", re.MULTILINE)
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")


def mutable_image_references(body: str) -> list[str]:
    """Every `image:` value in `body` that is not pinned by digest."""
    return [
        match.group("value")
        for match in _IMAGE_LINE.finditer(body)
        if not _DIGEST.search(match.group("value"))
    ]


def callers(root: Path, target: str) -> list[str]:
    """Every tracked file that names `target` as a compose file to run.

    Deliberately narrow: it matches the file being USED (`-f <target>`, or a
    variable defaulted to it), not every mention of the string. A prose
    reference in a document is not a caller, and counting one would make the
    baseline rise whenever somebody wrote about the problem.
    """
    patterns = (
        re.compile(rf"-f\s+{re.escape(target)}\b"),
        re.compile(rf'COMPOSE_FILE_PROD:?=\s*"?{re.escape(target)}'),
    )
    found: list[str] = []
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        if relative.startswith((".git/", "node_modules/", ".venv/", "docs/")):
            continue
        if candidate.suffix not in {".yml", ".yaml", ".sh", ""} and (
            candidate.name != "Makefile"
        ):
            continue
        try:
            body = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if any(pattern.search(body) for pattern in patterns):
            found.append(relative)
    return found


@pytest.fixture(scope="module")
def baseline() -> dict[str, object]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_the_legacy_compose_still_exists(baseline: dict[str, object]) -> None:
    """When it stops existing, this whole file and its exception row go with
    it — in the same change. A guard outliving its subject is the stale
    baseline problem in its purest form."""
    target = PROJECT_ROOT / str(baseline["path"])
    assert target.is_file(), (
        f"{baseline['path']} is gone. Delete this test, its baseline and the "
        "2026-08-30 row in docs/CONTROL_EXCEPTIONS.md in this same change"
    )


def test_mutable_image_references_have_not_risen(baseline: dict[str, object]) -> None:
    body = (PROJECT_ROOT / str(baseline["path"])).read_text(encoding="utf-8")
    found = mutable_image_references(body)
    assert len(found) <= int(baseline["mutable_image_references"]), (
        f"the legacy compose now has {len(found)} unpinned image reference(s) "
        f"({found}), above the frozen baseline of "
        f"{baseline['mutable_image_references']}. The legacy path is frozen: it "
        "may shrink, never grow"
    )


def test_mutable_image_references_have_not_silently_fallen(
    baseline: dict[str, object],
) -> None:
    """The second direction. An improvement is recorded by whoever made it."""
    body = (PROJECT_ROOT / str(baseline["path"])).read_text(encoding="utf-8")
    found = mutable_image_references(body)
    assert len(found) >= int(baseline["mutable_image_references"]), (
        f"the legacy compose now has only {len(found)} unpinned image "
        f"reference(s), below the baseline of "
        f"{baseline['mutable_image_references']}. Lower the baseline in this "
        "same change — a ratchet that absorbs improvement silently stops "
        "describing the thing it measures"
    )


def test_callers_have_not_risen(baseline: dict[str, object]) -> None:
    found = callers(PROJECT_ROOT, str(baseline["path"]))
    assert len(found) <= int(baseline["callers"]), (
        f"the legacy compose now has {len(found)} caller(s) ({found}), above "
        f"the frozen baseline of {baseline['callers']}. Point new work at "
        "deploy/product.toml; do not grow the path being retired"
    )


def test_callers_have_not_silently_fallen(baseline: dict[str, object]) -> None:
    found = callers(PROJECT_ROOT, str(baseline["path"]))
    assert len(found) >= int(baseline["callers"]), (
        f"the legacy compose now has only {len(found)} caller(s) ({found}), "
        f"below the baseline of {baseline['callers']}. Lower the baseline in "
        "this same change so the retirement is recorded where it happened"
    )


def test_the_recorded_callers_are_the_ones_actually_found(
    baseline: dict[str, object],
) -> None:
    """A count alone cannot show WHICH caller changed.

    Two callers swapping — one retired, one added — leaves the count identical
    and the estate different, and a numeric ratchet would report green.
    """
    assert sorted(callers(PROJECT_ROOT, str(baseline["path"]))) == sorted(
        str(item) for item in baseline["caller_paths"]
    )


def test_the_exception_is_recorded_and_names_retirement(
    baseline: dict[str, object],
) -> None:
    """The baseline is meaningless without the entry that explains it."""
    body = EXCEPTIONS.read_text(encoding="utf-8")
    assert "the legacy production compose is outside the governed surface" in body
    assert "No compliant twin" in body
    assert str(baseline["path"]) in body


# ── sensitivity: the measurements must react ────────────────────────────────


def test_a_planted_tag_is_counted_as_a_mutable_reference() -> None:
    planted = 'services:\n  app:\n    image: "postgres:16"\n'
    assert mutable_image_references(planted) == ['"postgres:16"']


def test_a_planted_substitution_is_counted_as_a_mutable_reference() -> None:
    """ADR 0014 refuses a deploy-time substitution for the same reason as a
    tag. If this stopped counting, the legacy file's single finding would
    vanish and the baseline would read zero for the wrong reason."""
    planted = "services:\n  app:\n    image: ${APP_IMAGE:?set it}\n"
    assert mutable_image_references(planted) == ["${APP_IMAGE:?set it}"]


def test_a_digest_pinned_image_is_NOT_counted() -> None:
    """The negative control. A detector that counted everything would make the
    baseline meaningless and the ratchet unfalsifiable."""
    planted = f'services:\n  app:\n    image: "repo/app@sha256:{"a" * 64}"\n'
    assert mutable_image_references(planted) == []


def test_a_prose_mention_is_not_a_caller(tmp_path: Path) -> None:
    """Counting mentions rather than uses would make the baseline rise
    whenever somebody documented the problem — which is the behaviour that
    teaches people to stop documenting it."""
    (tmp_path / "notes.sh").write_text(
        "# we should retire docker-compose.yml one day\n", encoding="utf-8"
    )
    assert callers(tmp_path, "docker-compose.yml") == []


def test_a_real_use_IS_a_caller(tmp_path: Path) -> None:
    (tmp_path / "run.sh").write_text(
        "docker compose -f docker-compose.yml up -d\n", encoding="utf-8"
    )
    assert callers(tmp_path, "docker-compose.yml") == ["run.sh"]
