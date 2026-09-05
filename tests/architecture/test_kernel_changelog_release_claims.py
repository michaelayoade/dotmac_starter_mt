"""A tagged changelog heading may only claim modules that tag actually carries.

The defect this exists for is not hypothetical and not old. `main` merged
`dotmac_kernel.request_evidence` and filed it under `## 0.1.0a101 — Unreleased`
— a heading that had already been published and tagged. Every downstream reader
of that changelog, and every person who checked the source rather than the
artifact, concluded the capability shipped. It did not: `dotmac-kernel-v0.1.0a101`
peels to a tree with no `request_evidence.py`, and the platform pinning a98 could
not have consumed it under any version. "Source-complete" was read as
"installable".

The tag is the oracle here, deliberately. A changelog claim is repository-local
prose; whether a version contains a module is an immutable coordinate that only
the annotated tag can answer, so this test asks the tag rather than the tree,
and treats an unreadable tag set as a FAILURE rather than a skip — a tag-blind
run would compare every section against nothing and report green.

Scope, stated so the unmonitored part stays visible: this checks module NAMES
against a tag's source listing. It does not check that a section describes the
behaviour correctly, and it says nothing about a heading that carries no
`dotmac_kernel.` reference at all.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "packages" / "dotmac-kernel" / "CHANGELOG.md"
SRC_PREFIX = "packages/dotmac-kernel/src/dotmac_kernel/"
TAG_PREFIX = "dotmac-kernel-v"

#: `dotmac_kernel.<name>` — only the first component, because a claim about
#: `dotmac_kernel.middleware.tenant` is a claim about the `middleware` package
#: being present, and submodule layout inside it is not this test's question.
_REFERENCE = re.compile(r"\bdotmac_kernel\.([A-Za-z_][A-Za-z0-9_]*)")
_HEADING = re.compile(r"(?m)^## (0\.1\.0a\d+) ")


def _git(*args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _tags() -> frozenset[str]:
    tags = frozenset(
        line for line in _git("tag", "--list", f"{TAG_PREFIX}*").split() if line
    )
    assert tags, (
        "no dotmac-kernel release tag is visible, so every section would be "
        "compared against nothing and this test would pass by seeing nothing. "
        "Fetch tags (`git fetch --tags`) — a tag-blind run is a hole, not a pass"
    )
    return tags


def _sections() -> dict[str, str]:
    text = CHANGELOG.read_text(encoding="utf-8")
    starts = [(match.start(), match.group(1)) for match in _HEADING.finditer(text)]
    assert starts, "no versioned changelog heading was parsed"
    bounds = [*(position for position, _ in starts), len(text)]
    return {
        version: text[bounds[index] : bounds[index + 1]]
        for index, (_, version) in enumerate(starts)
    }


def _modules_at(tag: str) -> frozenset[str]:
    listing = _git("ls-tree", "-r", "--name-only", tag, "--", SRC_PREFIX)
    names = set()
    for path in listing.split():
        head = path.removeprefix(SRC_PREFIX).split("/")[0]
        names.add(head.removesuffix(".py"))
    assert names, f"{tag} lists no kernel source; the comparison would be empty"
    return frozenset(names)


def claims_absent_from_tag(
    sections: dict[str, str], tags: frozenset[str]
) -> dict[str, list[str]]:
    """Every (version -> modules) claim a published tag does not support."""

    offences: dict[str, list[str]] = {}
    for version, body in sections.items():
        tag = f"{TAG_PREFIX}{version}"
        if tag not in tags:
            # Unreleased or skipped. Nothing is claimed to be installable yet,
            # so there is nothing to contradict.
            continue
        present = _modules_at(tag)
        missing = sorted(set(_REFERENCE.findall(body)) - present)
        if missing:
            offences[version] = missing
    return offences


def test_a_published_section_claims_only_modules_its_tag_carries() -> None:
    sections = _sections()
    tags = _tags()
    published = [v for v in sections if f"{TAG_PREFIX}{v}" in tags]
    assert published, (
        "no changelog section corresponds to a release tag; this gate would "
        "prove nothing"
    )
    offences = claims_absent_from_tag(sections, tags)
    assert not offences, (
        "these changelog sections name modules their published tag does not "
        f"contain: {offences}. Move the entry to the version that will ship it "
        "— a released heading is a statement about an artifact, not about main"
    )


def test_the_guard_still_bites_on_the_defect_it_was_written_for() -> None:
    """Plant the exact a101 defect and a near miss beside it.

    Without this, the test above would be a check over a clean tree, which
    proves nothing about the check. The planted defect is the real one:
    `request_evidence` under the a101 heading. The near miss is a module a101
    genuinely carries, filed under the same heading — a guard that flagged that
    too would be matching "mentions a module" rather than "claims a module the
    artifact lacks", and would fail every accurate section in the file.
    """
    tags = _tags()
    a101 = f"{TAG_PREFIX}0.1.0a101"
    assert a101 in tags, "the a101 tag is the fixture; it must be visible"

    defect = {"0.1.0a101": "## 0.1.0a101 — x\n\n- `dotmac_kernel.request_evidence`\n"}
    assert claims_absent_from_tag(defect, tags) == {"0.1.0a101": ["request_evidence"]}

    near_miss = {"0.1.0a101": "## 0.1.0a101 — x\n\n- `dotmac_kernel.app_factory`\n"}
    assert claims_absent_from_tag(near_miss, tags) == {}


def test_request_evidence_is_claimed_by_the_version_that_will_ship_it() -> None:
    """The positive half, and it must survive the release it is about.

    Deleting the entry outright would satisfy the guard above and lose the
    capability's record entirely, so this names where the claim must live
    rather than asserting the section is untagged — which would start failing
    the moment a102 is tagged, turning a correct release into a red build.
    """
    sections = _sections()
    claiming = sorted(v for v, body in sections.items() if "request_evidence" in body)
    assert claiming == ["0.1.0a102"], (
        f"request evidence is claimed by {claiming}; the a101 tag tree does "
        "not contain the module, and a102 is the first version whose source "
        "does"
    )
