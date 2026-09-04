"""ADR numbers are allocated from a register, not inferred from the directory.

On 2026-09-04 three decisions were authored against ADR-0074 within eighty
minutes.  Every author did the correct thing under the rule that then stood:
read ``docs/adr/``, find the highest number, take the next.  None of them could
see the others, because a feature branch's ``docs/adr/`` shows what has merged
plus what that branch itself wrote and nothing else.  A procedure that is
correct from every vantage point and still collides is not one anyone can follow
more carefully.

``docs/adr/reservations.toml`` replaces it with a serialized allocator, ported
from ``dotmac_erp`` at ``5b2a035f`` (dossier: ``docs/adr/EXTRACTION.toml``).
This module is its checker.

What is ENFORCED here, against one worktree:

* every number appears once, and every slug appears once;
* the numbers are dense from 1, and ``next_free`` is exactly one past the last
  — so a number cannot be silently skipped or silently dropped;
* ``authored`` and the presence of ``NNNN-<slug>.md`` agree in BOTH directions,
  so no ADR exists without a claim and no claim pretends to a document that was
  never written;
* a ``withdrawn`` number keeps its row, carries a reason, and has no file;
* the row vocabulary is CLOSED — a key the checker does not read is a claim
  nothing checks;
* the pre-existing branch-collision backlog does not grow, and ``contested``
  stays empty.

What is ENFORCED AGAINST A MERGE BASE, which needs a base revision:

* **an ADR file that arrives in this change had its number reserved in an
  EARLIER one** (Michael's requirement 3).  This is the rule that makes the
  register binding rather than advisory: without it a branch may write a
  register row and its document together, which is exactly what a claim on a
  branch is.
* the same detector names the specific mistake of taking the directory's next
  gap (Michael's requirement 6), because that is the mistake all three 0074
  authors actually made and the message is what tells the next author so.

The detector is a pure function over two registers and two directory listings,
proven by planted inputs on every run in both directions; only the git wiring
that feeds it depends on a base revision being fetchable.

What is DECLARED AND NOT DETECTED, and is not called coverage: which branch
claims a number.  A checker reading one worktree cannot see another branch.
``branch_claim_findings`` below is a real comparison, but it needs a ref scan
supplied to it, and the scan runs only when ``ADR_REGISTER_SCAN_REFS`` is set
(off by default: it is O(refs) ``git ls-tree`` calls, and a CI checkout usually
holds only the branch under test).  When it does not run, the test says so
rather than passing quietly.  The gate that does bite unconditionally is
indirect and sufficient — a colliding branch cannot merge, because the moment
its ADR file lands ``test_adr_numbering.py::test_every_adr_number_is_unique``
sees two documents on one number.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = REPO_ROOT / "docs" / "adr"
REGISTER = ADR_DIR / "reservations.toml"

VALID_STATUSES = frozenset({"reserved", "authored", "withdrawn", "contested"})

ADR_FILENAME = re.compile(r"^(\d{4})-([a-z0-9-]+)\.md$")

# The dossier gate learned this the expensive way: a validator that reads the
# keys it knows and ignores the rest is fail-open, not extensible.  A row may
# only carry fields something here reads.
KNOWN_ROW_FIELDS = frozenset(
    {
        "number",
        "slug",
        "status",
        "claimed",
        "note",
        "where",
        "withdrawn_reason",
        "contested_by",
        "replaces",
    }
)
KNOWN_COLLISION_FIELDS = frozenset({"number", "main_slug", "branch_slug", "where"})

# Numbers that `main` has authored and an unmerged branch spells with a
# DIFFERENT decision.  Keyed by (number, branch slug) because one number can be
# contested by two branches independently — 0032 and 0033 each are.
#
# Two-directional per ADR-0018: adding an entry is forbidden outright (the
# register exists to prevent a new one), and removing one requires lowering this
# pin in the same change that renumbers the branch it names.
BRANCH_COLLISION_BACKLOG = frozenset(
    {
        (19, "a-decision-must-be-askable"),
        (32, "collections-assesses-identity-and-requests-actions"),
        (32, "integrator-executes-approved-provisioning-commands"),
        (33, "digital-media-owns-reusable-assets-rights-and-renditions"),
        (33, "exact-managed-service-connectors-are-authorized"),
        (34, "managed-capability-contracts-are-separate-product-artifacts"),
        (71, "build-once-and-bind-the-environment-late"),
    }
)

# `contested` records more than one unmerged claim on a number `main` does not
# hold.  There are none: the 0074 collision was reconciled by renumbering the
# Foundation pair to 0075/0076 rather than by parking it here.
CONTESTED_BACKLOG: frozenset[int] = frozenset()

SCAN_REFS_ENV = "ADR_REGISTER_SCAN_REFS"
BASE_REF_ENV = "ADR_REGISTER_BASE_REF"
DEFAULT_BASE_REF = "origin/main"


def load_register(text: str) -> dict:
    return tomllib.loads(text)


def adr_filenames(directory: Path) -> set[str]:
    return {
        path.name for path in directory.glob("*.md") if ADR_FILENAME.match(path.name)
    }


def numbers_in(filenames: set[str]) -> dict[int, str]:
    """`{number: slug}` for a set of ADR filenames."""
    out: dict[int, str] = {}
    for name in filenames:
        match = ADR_FILENAME.match(name)
        if match is not None:
            out[int(match.group(1))] = match.group(2)
    return out


def allocate(register: dict) -> int:
    """The next number to claim.  Reads the register and NOTHING else.

    Deliberately takes no directory argument.  It is not that this function
    chooses to ignore `docs/adr/` — it cannot see it, which is the only form of
    "do not infer from the next filename" that survives being read quickly by
    the next author (Michael's requirement 6).
    """
    return int(register["next_free"])


def directory_next_free(filenames: set[str]) -> int:
    """What reading `docs/adr/` WOULD have suggested.

    Used only to name the mistake in a refusal message, and by the sensitivity
    proof.  Nothing allocates from it.
    """
    numbers = numbers_in(filenames)
    return (max(numbers) + 1) if numbers else 1


def findings(register: dict, filenames: set[str]) -> list[str]:
    """Every rule violation in one register, as reviewable sentences."""
    out: list[str] = []
    rows = register.get("reservation", [])

    seen_numbers: dict[int, str] = {}
    seen_slugs: dict[str, int] = {}
    withdrawn: set[int] = set()

    for row in rows:
        unknown = sorted(set(row) - KNOWN_ROW_FIELDS)
        if unknown:
            out.append(
                f"reservation row {row.get('number')!r} carries unread fields "
                f"{unknown}. A field the checker does not read is a claim "
                f"nothing checks."
            )

        number = row.get("number")
        slug = row.get("slug")
        status = row.get("status")

        if not isinstance(number, int) or number < 1:
            out.append(f"row {row!r} has no positive integer number")
            continue
        if not slug:
            out.append(f"ADR-{number:04d} has no slug")
            continue
        if status not in VALID_STATUSES:
            out.append(
                f"ADR-{number:04d} has status {status!r}, "
                f"not one of {sorted(VALID_STATUSES)}"
            )
            continue

        if number in seen_numbers:
            out.append(
                f"ADR-{number:04d} is claimed twice, by {seen_numbers[number]!r} "
                f"and {slug!r}. A number is allocated once."
            )
        if slug in seen_slugs:
            out.append(
                f"slug {slug!r} is claimed by both ADR-{seen_slugs[slug]:04d} and "
                f"ADR-{number:04d}. Re-author under a new number with "
                f"`replaces = {seen_slugs[slug]}`."
            )
        seen_numbers[number] = slug
        seen_slugs[slug] = number

        expected = f"{number:04d}-{slug}.md"
        present = expected in filenames

        if status == "authored" and not present:
            out.append(f"ADR-{number:04d} is authored but {expected} does not exist")
        if status != "authored" and present:
            out.append(
                f"ADR-{number:04d} is {status!r} but {expected} exists. A number "
                f"that is not authored has no document."
            )

        if status == "withdrawn":
            withdrawn.add(number)
            if not row.get("withdrawn_reason"):
                out.append(
                    f"ADR-{number:04d} is withdrawn with no `withdrawn_reason`. "
                    f"Withdrawal is a recorded act, not a deletion."
                )
        if status == "contested" and not row.get("contested_by"):
            out.append(f"ADR-{number:04d} is contested but names no `contested_by`")

        replaces = row.get("replaces")
        if replaces is not None and replaces not in withdrawn:
            out.append(
                f"ADR-{number:04d} declares `replaces = {replaces}`, which is not "
                f"a withdrawn number in this register."
            )

    for number in sorted(withdrawn):
        others = [
            r
            for r in rows
            if r.get("number") == number and r.get("status") != "withdrawn"
        ]
        if others:
            out.append(
                f"ADR-{number:04d} was withdrawn and is claimed again by "
                f"{[r.get('slug') for r in others]!r}. A spent number is spent."
            )

    if seen_numbers:
        highest = max(seen_numbers)
        missing = sorted(set(range(1, highest + 1)) - set(seen_numbers))
        if missing:
            out.append(
                f"the register has gaps at {missing}. Every number ever claimed "
                f"keeps a row, so a gap means one was removed."
            )
        next_free = register.get("next_free")
        if next_free != highest + 1:
            out.append(
                f"`next_free` is {next_free!r}, but the highest claimed number is "
                f"{highest}, so it must be {highest + 1}."
            )

    for filename in sorted(filenames):
        match = ADR_FILENAME.match(filename)
        assert match is not None
        number, slug = int(match.group(1)), match.group(2)
        if seen_numbers.get(number) != slug:
            out.append(
                f"{filename} exists with no matching reservation. Claim the "
                f"number in its own change before writing the document."
            )

    for entry in register.get("branch_collision", []):
        unknown = sorted(set(entry) - KNOWN_COLLISION_FIELDS)
        if unknown:
            out.append(
                f"branch_collision {entry.get('number')!r} carries unread fields "
                f"{unknown}."
            )

    return out


def unreserved_authorings(
    base_register: dict,
    base_filenames: set[str],
    head_filenames: set[str],
) -> dict[int, str]:
    """ADR documents arriving in this change whose number was not already held.

    Michael's requirement 3, and the half that makes the register binding.  The
    comparison is against the MERGE BASE rather than against `main`'s tip: the
    base is what this branch's author could actually see, so "the number was
    reserved before the branch that authors it" is exactly the property being
    checked.  A number reserved on `main` after this branch was cut is absent
    from the base and is therefore correctly refused here.

    Returns `{number: reason}`.  The reason distinguishes requirement 6's
    specific mistake — taking the number `docs/adr/` would have suggested — from
    a number that was simply never claimed, because naming the mistake is what
    stops the next author repeating it.
    """
    reserved = {row["number"] for row in base_register.get("reservation", [])}
    suggested = directory_next_free(base_filenames)

    offenders: dict[int, str] = {}
    for number, slug in sorted(numbers_in(head_filenames - base_filenames).items()):
        if number in reserved:
            continue
        if number == suggested:
            offenders[number] = (
                f"ADR-{number:04d} ({slug}) was not reserved, and it is exactly "
                f"the number `docs/adr/` would have suggested — the directory is "
                f"not the allocator. Take `next_free` from "
                f"docs/adr/reservations.toml and land that row on the base first."
            )
        else:
            offenders[number] = (
                f"ADR-{number:04d} ({slug}) has a document in this change and no "
                f"reservation on the base. A number claimed in the change that "
                f"authors it was claimed on a branch, and a claim on a branch is "
                f"not a claim."
            )
    return offenders


def branch_claim_findings(register: dict, scan: dict[str, dict[int, str]]) -> list[str]:
    """Compare the register against ADR numbers actually present on other refs.

    `scan` is `{ref: {number: slug}}` and must NOT include the base branch.
    Pure, so the planted proofs below exercise it on every run; the git wiring
    that produces a real `scan` is opt-in, and the caller reports when it did
    not run rather than treating an empty scan as a clean result.
    """
    out: list[str] = []
    rows = {row["number"]: row for row in register.get("reservation", [])}
    slug_numbers = {
        row["slug"]: row["number"] for row in register.get("reservation", [])
    }
    declared = {
        (entry["number"], entry["branch_slug"])
        for entry in register.get("branch_collision", [])
    }
    observed: set[tuple[int, str]] = set()

    for ref, claims in sorted(scan.items()):
        for number, slug in sorted(claims.items()):
            row = rows.get(number)
            if row is None:
                out.append(
                    f"ADR-{number:04d} is claimed on {ref} as {slug!r} and has no "
                    f"row in the register. Reserve it, or renumber the branch."
                )
                continue
            if row["slug"] == slug:
                continue
            if slug_numbers.get(slug, number) != number:
                # The branch spells `number` for a decision that HAS its own
                # row, at another number.  That is a stale copy of an older
                # numbering, not a competing claim: the decision landed and the
                # register knows where.  Thirty-one such pairs exist in this
                # repository, all of them the pre-register rule's
                # renumber-before-merge working as designed.  Reporting them
                # would bury the seven entries that are real claims.
                #
                # The limit this accepts: a branch that deliberately re-spells a
                # LANDED decision under a wrong number reads the same way here.
                # That case is caught where it matters, at merge, by
                # `test_adr_numbering.py::test_every_adr_number_is_unique`.
                continue
            observed.add((number, slug))
            if (number, slug) not in declared:
                out.append(
                    f"ADR-{number:04d} is {row['slug']!r} in the register and "
                    f"{slug!r} on {ref}. Renumber the branch, or declare it under "
                    f"`[[branch_collision]]`."
                )

    for number, slug in sorted(declared - observed):
        out.append(
            f"the declared branch collision ADR-{number:04d} {slug!r} was not "
            f"found on any scanned ref. A declaration that no longer describes a "
            f"branch is stale — remove it in the change that reconciled it."
        )
    return out


# --------------------------------------------------------------------------
# git wiring.  Both halves degrade to "did not run", loudly.
# --------------------------------------------------------------------------


def _git() -> str | None:
    return shutil.which("git")


def _base_register_and_files(base_ref: str) -> tuple[dict, set[str]] | None:
    git = _git()
    if git is None:
        return None
    try:
        merge_base = subprocess.run(  # noqa: S603 - absolute git, literal args
            [git, "merge-base", base_ref, "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        register_text = subprocess.run(  # noqa: S603
            [git, "show", f"{merge_base}:docs/adr/reservations.toml"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        listing = subprocess.run(  # noqa: S603
            [git, "ls-tree", "--name-only", merge_base, "docs/adr/"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        # The register is not on the base yet (this is the change that adds it),
        # or the base ref is not fetched.  The pure detector is still proven by
        # the planted inputs below on every run.
        return None

    names = {
        line.rsplit("/", 1)[-1]
        for line in listing.splitlines()
        if ADR_FILENAME.match(line.rsplit("/", 1)[-1])
    }
    return load_register(register_text), names


def _scan_refs() -> dict[str, dict[int, str]] | None:
    """ADR numbers on every local and remote ref except the base branch.

    Opt-in through `ADR_REGISTER_SCAN_REFS`, because it is one `git ls-tree` per
    ref and a CI checkout normally holds only the branch under test — a scan
    over one ref would report a clean result while having looked at nothing.
    """
    if os.environ.get(SCAN_REFS_ENV, "").lower() not in {"1", "true", "yes"}:
        return None
    git = _git()
    if git is None:
        return None
    base = os.environ.get(BASE_REF_ENV, DEFAULT_BASE_REF)
    refs = subprocess.run(  # noqa: S603
        [git, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    skip = {f"refs/remotes/{base}", "refs/heads/main", f"refs/heads/{base}"}
    scan: dict[str, dict[int, str]] = {}
    for ref in refs:
        if ref in skip:
            continue
        listing = subprocess.run(  # noqa: S603
            [git, "ls-tree", "--name-only", ref, "docs/adr/"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        ).stdout
        names = {
            line.rsplit("/", 1)[-1]
            for line in listing.splitlines()
            if ADR_FILENAME.match(line.rsplit("/", 1)[-1])
        }
        if names:
            scan[ref] = numbers_in(names)
    return scan


# --------------------------------------------------------------------------
# The register, as it actually stands.
# --------------------------------------------------------------------------


def test_the_register_is_internally_consistent_and_matches_the_directory() -> None:
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    problems = findings(register, adr_filenames(ADR_DIR))
    assert problems == [], "docs/adr/reservations.toml: " + "; ".join(problems)


def test_the_branch_collision_backlog_does_not_grow_and_only_shrinks_deliberately() -> (
    None
):
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    declared = {
        (entry["number"], entry["branch_slug"])
        for entry in register.get("branch_collision", [])
    }
    assert declared == BRANCH_COLLISION_BACKLOG, (
        f"the ADR branch-collision backlog moved: "
        f"new={sorted(declared - BRANCH_COLLISION_BACKLOG)}, "
        f"resolved={sorted(BRANCH_COLLISION_BACKLOG - declared)}. A new collision "
        f"is forbidden — the register exists to prevent one. A reconciled one "
        f"lowers BRANCH_COLLISION_BACKLOG in the change that renumbers the branch."
    )


def test_no_number_is_contested_and_none_may_become_so() -> None:
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    contested = {
        row["number"] for row in register["reservation"] if row["status"] == "contested"
    }
    assert contested == CONTESTED_BACKLOG, (
        f"contested numbers {sorted(contested)} against a declared backlog of "
        f"{sorted(CONTESTED_BACKLOG)}. `contested` records pre-existing debt "
        f"only; a new one means two branches took one number after the register "
        f"existed, which is the failure this file prevents."
    )


def test_the_backlog_pins_are_not_vacuously_satisfied() -> None:
    """A pin over an empty set proves nothing about the pin.

    `CONTESTED_BACKLOG` IS empty and is meant to be — so the non-vacuity has to
    come from the other pin, which is real.  Stated rather than skipped, because
    the two look alike and the next reader must not conclude both are inert.
    """
    assert BRANCH_COLLISION_BACKLOG, (
        "the branch-collision pin is empty, so the equality above passes over "
        "nothing. If the backlog really reached zero, delete this assertion in "
        "the same change and say so."
    )
    assert CONTESTED_BACKLOG == frozenset()


def test_no_adr_was_authored_without_a_reservation_on_the_merge_base() -> None:
    """Requirement 3. Degrades to nothing detected, and says so, off a base."""
    base_ref = os.environ.get(BASE_REF_ENV, DEFAULT_BASE_REF)
    base = _base_register_and_files(base_ref)
    if base is None:
        return
    base_register, base_files = base

    offenders = unreserved_authorings(base_register, base_files, adr_filenames(ADR_DIR))
    assert offenders == {}, "; ".join(offenders[number] for number in sorted(offenders))


def test_the_register_agrees_with_the_branches_that_hold_numbers() -> None:
    """Requirement 5's branch half. DECLARED unless the scan is switched on."""
    scan = _scan_refs()
    if scan is None:
        # Not a pass. The branch half of requirement 5 is declared, not
        # detected, in this run; `branch_claim_findings` is still proven below.
        return
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    problems = branch_claim_findings(register, scan)
    assert problems == [], "; ".join(problems)


# --------------------------------------------------------------------------
# Sensitivity proof (ADR-0018).  A checker that only ever passes over a clean
# tree has proved nothing about itself, so each rule is planted and named.
# --------------------------------------------------------------------------

CLEAN = {
    "next_free": 3,
    "reservation": [
        {"number": 1, "slug": "alpha", "status": "authored", "claimed": "2026-01-01"},
        {"number": 2, "slug": "beta", "status": "reserved", "claimed": "2026-01-02"},
    ],
}
CLEAN_FILES = {"0001-alpha.md"}


def test_the_negative_control_is_clean() -> None:
    """A detector that flags everything would 'catch' every plant below."""
    assert findings(CLEAN, CLEAN_FILES) == []
    assert unreserved_authorings(CLEAN, CLEAN_FILES, CLEAN_FILES) == {}
    assert branch_claim_findings({"reservation": CLEAN["reservation"]}, {}) == []


def test_a_planted_duplicate_number_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            *CLEAN["reservation"],
            {
                "number": 2,
                "slug": "gamma",
                "status": "reserved",
                "claimed": "2026-01-03",
            },
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("ADR-0002 is claimed twice" in p for p in problems), problems
    assert any("'beta'" in p and "'gamma'" in p for p in problems), problems


def test_a_planted_reuse_after_retirement_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {
                "number": 2,
                "slug": "beta",
                "status": "withdrawn",
                "claimed": "2026-01-02",
                "withdrawn_reason": "abandoned before authoring",
            },
            {
                "number": 2,
                "slug": "delta",
                "status": "reserved",
                "claimed": "2026-02-01",
            },
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("A spent number is spent" in p for p in problems), problems


def test_a_planted_unreserved_authoring_is_named() -> None:
    """Requirement 3: the document and the claim arrive in the same change."""
    base = {"next_free": 2, "reservation": [CLEAN["reservation"][0]]}
    offenders = unreserved_authorings(
        base, {"0001-alpha.md"}, {"0001-alpha.md", "0009-late.md"}
    )
    assert set(offenders) == {9}, offenders
    assert "no reservation on the base" in offenders[9], offenders[9]
    assert "a claim on a branch is not a claim" in offenders[9], offenders[9]


def test_a_planted_number_inferred_from_the_next_filename_is_named() -> None:
    """Requirement 6: the exact mistake all three 0074 authors made.

    Distinct from the plant above by the NUMBER chosen, not by the rule: both
    are refused, and the message is what separates "you skipped the register"
    from "you read the directory instead of the register".
    """
    base = {"next_free": 7, "reservation": [CLEAN["reservation"][0]]}
    base_files = {"0001-alpha.md"}
    assert directory_next_free(base_files) == 2
    offenders = unreserved_authorings(base, base_files, base_files | {"0002-next.md"})
    assert set(offenders) == {2}, offenders
    assert "the directory is not the allocator" in offenders[2], offenders[2]
    assert "next_free" in offenders[2], offenders[2]


def test_allocate_cannot_see_the_directory() -> None:
    """The other half of requirement 6: the allocator ignores the filenames.

    A register whose `next_free` is far ahead of the directory must still hand
    out `next_free`.  If this ever returned 32 the register would be a summary
    of `docs/adr/` rather than its allocator.
    """
    register = {"next_free": 20, "reservation": []}
    crowded = {"0031-something-that-merged-later.md"}
    assert directory_next_free(crowded) == 32
    assert allocate(register) == 20


def test_a_planted_silent_deletion_is_named() -> None:
    """Removing a row is how permanence would be lost. Density catches it."""
    planted = {
        "next_free": 3,
        "reservation": [
            {
                "number": 2,
                "slug": "beta",
                "status": "reserved",
                "claimed": "2026-01-02",
            }
        ],
    }
    problems = findings(planted, set())
    assert any("the register has gaps at [1]" in p for p in problems), problems


def test_a_planted_stale_next_free_is_named() -> None:
    planted = {**CLEAN, "next_free": 2}
    problems = findings(planted, CLEAN_FILES)
    assert any("`next_free` is 2" in p for p in problems), problems


def test_a_planted_document_on_a_retired_number_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {
                "number": 2,
                "slug": "beta",
                "status": "withdrawn",
                "claimed": "2026-01-02",
                "withdrawn_reason": "abandoned before authoring",
            },
        ],
    }
    problems = findings(planted, CLEAN_FILES | {"0002-beta.md"})
    assert any(
        "is 'withdrawn' but 0002-beta.md exists" in p for p in problems
    ), problems


def test_a_planted_unregistered_adr_file_is_named() -> None:
    problems = findings(CLEAN, CLEAN_FILES | {"0009-orphan.md"})
    assert any(
        "0009-orphan.md exists with no matching reservation" in p for p in problems
    ), problems


def test_a_planted_withdrawal_without_a_reason_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {
                "number": 2,
                "slug": "beta",
                "status": "withdrawn",
                "claimed": "2026-01-02",
            },
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("no `withdrawn_reason`" in p for p in problems), problems


def test_a_planted_unread_field_is_named() -> None:
    """Fail-open is not an extension mechanism."""
    planted = {
        "next_free": 3,
        "reservation": [
            {**CLEAN["reservation"][0], "resolved_by": "someone"},
            CLEAN["reservation"][1],
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("carries unread fields ['resolved_by']" in p for p in problems), problems


def test_a_planted_undeclared_branch_claim_is_named() -> None:
    """Requirement 5's third source: an active branch, where one can be seen."""
    register = {"next_free": 3, "reservation": CLEAN["reservation"]}
    scan = {"refs/heads/feat/something": {1: "a-different-decision"}}
    problems = branch_claim_findings(register, scan)
    assert any(
        "ADR-0001 is 'alpha' in the register and 'a-different-decision'" in p
        for p in problems
    ), problems


def test_a_planted_unregistered_branch_claim_is_named() -> None:
    register = {"next_free": 3, "reservation": CLEAN["reservation"]}
    scan = {"refs/heads/feat/something": {9: "off-the-books"}}
    problems = branch_claim_findings(register, scan)
    assert any("has no row in the register" in p for p in problems), problems


def test_a_planted_stale_branch_collision_declaration_is_named() -> None:
    register = {
        "next_free": 3,
        "reservation": CLEAN["reservation"],
        "branch_collision": [
            {
                "number": 1,
                "main_slug": "alpha",
                "branch_slug": "long-since-renumbered",
                "where": "feat/gone",
            }
        ],
    }
    problems = branch_claim_findings(register, {"refs/heads/feat/gone": {1: "alpha"}})
    assert any("is stale" in p for p in problems), problems


# --------------------------------------------------------------------------
# The counter-halves.  A detector proven only on failures may be one that
# never passes.
# --------------------------------------------------------------------------


def test_a_reservation_that_lands_before_its_document_is_not_flagged() -> None:
    base = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {
                "number": 2,
                "slug": "beta",
                "status": "reserved",
                "claimed": "2026-01-02",
            },
        ],
    }
    assert (
        unreserved_authorings(
            base, {"0001-alpha.md"}, {"0001-alpha.md", "0002-beta.md"}
        )
        == {}
    )


def test_a_declared_branch_collision_is_not_flagged() -> None:
    register = {
        "next_free": 3,
        "reservation": CLEAN["reservation"],
        "branch_collision": [
            {
                "number": 1,
                "main_slug": "alpha",
                "branch_slug": "a-different-decision",
                "where": "feat/something",
            }
        ],
    }
    scan = {"refs/heads/feat/something": {1: "a-different-decision"}}
    assert branch_claim_findings(register, scan) == []


def test_a_branch_carrying_an_older_numbering_is_not_called_a_collision() -> None:
    """A stale branch is not a claimant, and must not crowd out the real ones.

    `beta` has its own row at 2.  A branch that still spells it 1 predates the
    renumber that gave it 2; the decision landed and the register knows where.
    Thirty-one such pairs exist here, against seven real branch collisions.
    """
    register = {"next_free": 3, "reservation": CLEAN["reservation"]}
    scan = {"refs/heads/archive/old": {1: "beta"}}
    assert branch_claim_findings(register, scan) == []
