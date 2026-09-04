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

What is ENFORCED about a declared collision, unconditionally, off one worktree:

* every ``[[branch_collision]]`` row is DISPOSED into one of six buckets and
  carries the ``evidence`` that put it there (Michael's ruling, 2026-09-04:
  classify every claimant rather than mechanically renumbering 1,273 refs), and
  a ``historical_equivalent`` row names an AUTHORED number that carries the same
  decision — not the colliding number, and not a row with the same slug, which
  would make it history rather than a claim;
* every NON-ACTIVE row records its SUCCESSOR OR ITS EXPLICIT RESUMPTION RULE,
  and a row that omits it is REFUSED (Michael's ruling, 2026-09-04: "There are
  no active collision repairs to perform. What remains is preventing abandoned
  history from accidentally becoming authority").  A branch classified as
  not-active with nothing saying where its decision went, or on what terms it
  could come back, is that abandoned history: the only thing a later reader can
  do is open the branch and believe it;
* a disposition may not contradict the evidence beside it — ``active`` over a
  pull request that was closed unmerged, or ``closed_unmerged`` with no closed
  pull request, is a row whose two halves disagree, and the bucket is the half
  a reader acts on;
* a row that records Michael's authorization to DELETE a remote branch carries
  the coordinates that survive the deletion — the full tip, a rescue ref and a
  rescue tag.  Nothing here deletes anything; the register records, the human
  deletes;
* the thirty-one EXCLUDED historical pairs stay excluded and stay checkable.
  ``docs/adr/historical-renumberings.toml`` holds them with their equivalence
  evidence, and ``historical_findings`` re-derives that evidence from the
  register alone: the slug the branch spells has its own authored row, at a
  different number.  The two files must stay disjoint — a pair that is both a
  live claim and history is hidden in the quiet file.

What is DECLARED AND NOT DETECTED, and is not called coverage: which branch
claims a number.  A checker reading one worktree cannot see another branch.
``branch_claim_findings`` below is a real comparison, but it needs a ref scan
supplied to it, and the scan runs only when ``ADR_REGISTER_SCAN_REFS`` is set
(off by default: it is O(refs) ``git ls-tree`` calls, and a CI checkout usually
holds only the branch under test).

THE AUTHORITY IS THE MERGE-BASE RESERVATION GATE, not that scan.  Michael's
ruling, 2026-09-04: "The global-ref scan is an audit tool, not a dependable CI
gate.  CI often sees only one branch or a shallow ref set.  The merge-base
reservation gate is therefore the authority."  ``unreserved_authorings`` runs on
every branch against its own merge base, and the merge gate
``test_adr_numbering.py::test_every_adr_number_is_unique`` fails the moment a
colliding document actually lands.  Both bite unconditionally.

The scan is a SUPPLEMENTARY AUDIT, and it reports its own coverage: how many
refs were visible, whether the checkout was shallow, whether remote refs were
fetched, and how many refs actually carried an ADR.  When that coverage is
inadequate it declares NON-EXECUTION and skips, so "zero collisions found" can
never be read as "only one branch was visible".  Both halves are planted and
proven — a one-ref scan, a shallow checkout, an unfetched remote and an
unrequested scan each report non-execution, and adequate coverage over a planted
collision finds it, so the hardened path cannot be one that only ever declines.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = REPO_ROOT / "docs" / "adr"
REGISTER = ADR_DIR / "reservations.toml"
HISTORICAL = ADR_DIR / "historical-renumberings.toml"

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
KNOWN_COLLISION_FIELDS = frozenset(
    {
        "number",
        "main_slug",
        "branch_slug",
        "where",
        "disposition",
        "evidence",
        "successor_or_resumption",
        "represented_by",
        "tip",
        "document",
        "pr",
        "pr_state",
        "pr_closed_at",
        "rescue_ref",
        "rescue_tag",
        "remote_deletion_authorized",
    }
)
KNOWN_HISTORICAL_FIELDS = frozenset({"number", "slug", "landed_at", "refs"})

# Michael's ruling, 2026-09-04 (branch disposition), REPLACING the first
# classification vocabulary of the same day:
#
#   "There are no active collision repairs to perform.  What remains is
#    preventing abandoned history from accidentally becoming authority."
#
# The vocabulary is CLOSED and has six values, and `evidence` is required beside
# it, because a disposition asserted with no reading of the ref is a guess about
# a branch NAME — and a branch name such as `archive/*` is evidence of STORAGE
# LOCATION, NOT INTENT.  That is why the previous `rescue-only` value is gone:
# it named where a commit lives rather than what is to be done with it, and the
# one archive ref below now carries two different dispositions on two rows.
DISPOSITIONS = frozenset(
    {
        # intended for promotion: reserve a NEW number on `main` first, rebase,
        # rename, update citations one at a time BY MEANING, re-run the semantic
        # citation verification, confirm unrelated same-number refs unchanged.
        "active",
        # a CANDIDATE, not a corpse: the problem may still be real and the lane
        # is simply not being worked.  The only non-active value with a future,
        # so its resumption rule states the terms on which it may be picked up.
        "dormant",
        # a later decision replaced it.  The successor is named; never merged.
        "superseded",
        # its pull request was closed without merging, and that closure IS the
        # disposition.  Requires `pr`, `pr_state` and `pr_closed_at`.
        "closed_unmerged",
        # ruled unmergeable whatever the pull-request state says — stronger than
        # `closed_unmerged`, because a closed PR can be reopened by anyone with
        # the button and a branch on `origin` can be merged with no PR at all.
        "never_merge",
        # the SAME decision is already on `main`; `represented_by` names the
        # number carrying it, confirmed by reading BOTH documents.  Retire the
        # duplicate branch — this is not a renumbering.
        "historical_equivalent",
    }
)

# The one value that needs no successor: work still headed for `main` has no
# "where did it go" to answer.  Every other row must answer it.
ACTIVE_DISPOSITION = "active"

# Pull-request state is EVIDENCE, not a bucket.  The retired `pushed-open`
# conflated the two: whether a branch is pushed and whether its PR is open are
# facts about the world, while the disposition states intent.  A pushed, open
# claimant is `active` if it is meant to land and `never_merge` if it is not.
PR_STATES = frozenset({"open", "closed_unmerged", "merged", "none"})

# A full commit id, so a coordinate recorded before a branch is deleted is one
# that can still be resolved afterwards.  An abbreviation is not a coordinate:
# it is only unique in a repository that still holds the object.
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

# The path an ADR takes on the branch.  Its number must be the number the row
# says is contested, or the row is describing a different document.
BRANCH_DOCUMENT = re.compile(r"^docs/adr/(\d{4})-[a-z0-9-]+\.md$")

# UTC, to the second.  A closure time that cannot be compared is not evidence.
PR_CLOSED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

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

# The excluded historical pairs: a branch spelling a number that `main` later
# gave to the SAME decision.  Preserved with their equivalence evidence in
# `docs/adr/historical-renumberings.toml`, deliberately OUTSIDE the live
# collision queue — thirty-eight entries, thirty-one of them already reconciled,
# would bury the seven a human still has to act on.
HISTORICAL_RENUMBERING_COUNT = 31

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

    authored_slugs = {
        row["number"]: row["slug"]
        for row in rows
        if row.get("status") == "authored" and isinstance(row.get("number"), int)
    }
    for entry in register.get("branch_collision", []):
        unknown = sorted(set(entry) - KNOWN_COLLISION_FIELDS)
        if unknown:
            out.append(
                f"branch_collision {entry.get('number')!r} carries unread fields "
                f"{unknown}."
            )
        out.extend(_collision_classification_findings(entry, authored_slugs))

    return out


def _collision_classification_findings(
    entry: dict, authored: dict[int, str]
) -> list[str]:
    """Every declared collision is DISPOSED, with evidence and a way forward.

    Michael's ruling, 2026-09-04: a claimant is put in exactly one bucket before
    anything is renumbered, and — the half this function exists for now — every
    NON-ACTIVE row records its successor or its explicit resumption rule.  "There
    are no active collision repairs to perform.  What remains is preventing
    abandoned history from accidentally becoming authority."

    `authored` is `{number: slug}` for the AUTHORED rows only — a
    `historical_equivalent` row has to name a decision that is actually on
    `main`, so a `reserved` or `withdrawn` number cannot answer for it.
    """
    out: list[str] = []
    number = entry.get("number")
    branch_slug = entry.get("branch_slug")
    disposition = entry.get("disposition")

    if disposition not in DISPOSITIONS:
        out.append(
            f"branch_collision ADR-{number!r} {branch_slug!r} has disposition "
            f"{disposition!r}, not one of {sorted(DISPOSITIONS)}. Classify the "
            f"claimant before renumbering anything."
        )
        return out

    if not str(entry.get("evidence", "")).strip():
        out.append(
            f"branch_collision ADR-{number!r} {branch_slug!r} is classified "
            f"{disposition!r} with no `evidence`. A bucket read off a branch "
            f"NAME is a guess; state what was read — ref tip and date, whether "
            f"it is on `origin`, pull-request state, what `main` carries."
        )

    out.extend(_successor_findings(entry, disposition, number, branch_slug))
    out.extend(_evidence_agreement_findings(entry, disposition, number, branch_slug))

    represented_by = entry.get("represented_by")
    if disposition == "historical_equivalent":
        if represented_by is None:
            out.append(
                f"branch_collision ADR-{number!r} {branch_slug!r} is classified "
                f"`historical_equivalent` — the decision is already on `main` — "
                f"but names no `represented_by`. Name the number that carries "
                f"it: without it the claim is unfalsifiable and the branch stays "
                f"the only readable copy of its own decision."
            )
        elif represented_by not in authored:
            out.append(
                f"branch_collision ADR-{number!r} {branch_slug!r} is represented "
                f"by ADR-{represented_by!r}, which is not an AUTHORED decision in "
                f"this register. A number that is merely reserved is not on "
                f"`main`, so it cannot already carry the branch's decision."
            )
        elif represented_by == number:
            out.append(
                f"branch_collision ADR-{number:04d} {branch_slug!r} cannot be "
                f"represented by ADR-{number:04d}: that number holds a DIFFERENT "
                f"decision on `main`, which is why this is a collision."
            )
        elif authored[represented_by] == branch_slug:
            out.append(
                f"branch_collision ADR-{number:04d} {branch_slug!r} names a row "
                f"with the SAME slug at ADR-{represented_by:04d}. That is a "
                f"branch carrying an older numbering of a landed decision, not a "
                f"competing claim — it belongs in "
                f"docs/adr/historical-renumberings.toml."
            )
    elif represented_by is not None:
        out.append(
            f"branch_collision ADR-{number!r} {branch_slug!r} is classified "
            f"{disposition!r} but names `represented_by`. Only "
            f"`historical_equivalent` may."
        )
    return out


def _successor_findings(
    entry: dict, disposition: str, number: object, branch_slug: object
) -> list[str]:
    """Michael's ruling: every non-active row names where the decision went.

    This is the defect the ruling exists to prevent, so it is refused rather
    than warned about.  A row that says only "not active" leaves a later reader
    one move: open the branch and believe it.  `active` is exempt because work
    still headed for `main` has no "where did it go" to answer.
    """
    if disposition == ACTIVE_DISPOSITION:
        return []
    if str(entry.get("successor_or_resumption", "")).strip():
        return []
    return [
        f"branch_collision ADR-{number!r} {branch_slug!r} is classified "
        f"{disposition!r} with no `successor_or_resumption`. Every non-active "
        f"row records its SUCCESSOR or its explicit RESUMPTION RULE. A "
        f"disposition alone says the work stopped; it does not say where the "
        f"decision went or on what terms it may come back, and abandoned "
        f"history with no forwarding address is what later gets read as "
        f"authority."
    ]


def _evidence_agreement_findings(
    entry: dict, disposition: str, number: object, branch_slug: object
) -> list[str]:
    """The bucket and the facts beside it must agree.

    A disposition is what a reader ACTS on; the evidence is what they would
    check if they doubted it.  A row where the two disagree is worse than an
    unclassified one, because it reads as settled.  These are structural
    comparisons against declared fields, not prose matching — a claim read out
    of a sentence is a guess about a sentence.
    """
    out: list[str] = []

    pr_state = entry.get("pr_state")
    if pr_state is not None and pr_state not in PR_STATES:
        out.append(
            f"branch_collision ADR-{number!r} {branch_slug!r} has pr_state "
            f"{pr_state!r}, not one of {sorted(PR_STATES)}."
        )
    if pr_state in {"open", "closed_unmerged", "merged"} and entry.get("pr") is None:
        out.append(
            f"branch_collision ADR-{number!r} {branch_slug!r} records pr_state "
            f"{pr_state!r} but no `pr` number, so the state cannot be checked."
        )
    if pr_state == "closed_unmerged":
        closed_at = str(entry.get("pr_closed_at", ""))
        if not PR_CLOSED_AT.fullmatch(closed_at):
            out.append(
                f"branch_collision ADR-{number!r} {branch_slug!r} was closed "
                f"unmerged but `pr_closed_at` is {closed_at!r}, not an "
                f"ISO-8601 UTC instant (YYYY-MM-DDTHH:MM:SSZ)."
            )
        if disposition == ACTIVE_DISPOSITION:
            out.append(
                f"branch_collision ADR-{number!r} {branch_slug!r} is classified "
                f"`active` — intended for promotion — over a pull request that "
                f"was CLOSED UNMERGED. The disposition contradicts its own "
                f"evidence. One of the two is wrong, and the disposition is the "
                f"half a reader acts on."
            )
    elif pr_state == "merged" and disposition == ACTIVE_DISPOSITION:
        out.append(
            f"branch_collision ADR-{number!r} {branch_slug!r} is classified "
            f"`active` over a MERGED pull request. A landed decision is not a "
            f"claimant; if the number really collides it is history — see "
            f"docs/adr/historical-renumberings.toml."
        )

    if disposition == "closed_unmerged" and pr_state != "closed_unmerged":
        out.append(
            f"branch_collision ADR-{number!r} {branch_slug!r} is classified "
            f"`closed_unmerged` but records pr_state {pr_state!r}. The closure "
            f"IS that disposition; without it the bucket asserts a pull-request "
            f"state nothing recorded."
        )

    tip = entry.get("tip")
    if tip is not None and not FULL_SHA.fullmatch(str(tip)):
        out.append(
            f"branch_collision ADR-{number!r} {branch_slug!r} has tip {tip!r}, "
            f"not a full forty-character commit id. An abbreviation resolves "
            f"only in a repository that still holds the object, which is the "
            f"one case these coordinates exist for."
        )

    document = entry.get("document")
    if document is not None:
        match = BRANCH_DOCUMENT.fullmatch(str(document))
        if match is None:
            out.append(
                f"branch_collision ADR-{number!r} {branch_slug!r} has document "
                f"{document!r}, which is not a docs/adr/NNNN-slug.md path."
            )
        elif int(match.group(1)) != number:
            out.append(
                f"branch_collision ADR-{number!r} {branch_slug!r} names document "
                f"{document!r}, whose number is not {number!r}. The row would be "
                f"describing a different document from the one that collides."
            )

    if entry.get("remote_deletion_authorized"):
        missing = [
            field
            for field in ("tip", "rescue_ref", "rescue_tag")
            if not str(entry.get(field, "")).strip()
        ]
        if missing:
            out.append(
                f"branch_collision ADR-{number!r} {branch_slug!r} records "
                f"authorization to DELETE the remote branch but is missing "
                f"{missing}. Deleting a ref whose coordinates were never written "
                f"down is how history stops being reachable; record the tip and "
                f"the rescue ref and tag FIRST."
            )
        if disposition == ACTIVE_DISPOSITION:
            out.append(
                f"branch_collision ADR-{number!r} {branch_slug!r} is classified "
                f"`active` and also authorizes deleting its remote branch."
            )
    elif bool(entry.get("rescue_ref")) != bool(entry.get("rescue_tag")):
        out.append(
            f"branch_collision ADR-{number!r} {branch_slug!r} names a rescue ref "
            f"without its tag or a tag without its ref. Both, or neither: a "
            f"branch-shaped ref can be deleted as easily as the branch was."
        )

    return out


def historical_findings(register: dict, historical: dict) -> list[str]:
    """The 31 excluded pairs, and the evidence that they really are excluded.

    Michael's ruling: the historical pairs stay OUT of the live collision queue,
    "with their equivalence evidence preserved".  Preserved has to mean
    checkable, or the file is a set of assertions nobody verifies.

    The claim each row makes is exactly one thing, and it is derivable from the
    register alone — no network, no ref scan: the slug the branch spells at
    `number` HAS ITS OWN authored row, at `landed_at`, and `landed_at` is a
    different number.  That is what makes the pair a stale copy of an older
    numbering rather than a competing claim.  The two files must also stay
    disjoint: a pair cannot be both history and a live claim.
    """
    out: list[str] = []
    rows = {row["number"]: row for row in register.get("reservation", [])}
    by_slug = {row["slug"]: row for row in register.get("reservation", [])}
    live = {
        (entry["number"], entry["branch_slug"])
        for entry in register.get("branch_collision", [])
    }

    entries = historical.get("historical", [])
    declared_count = historical.get("count")
    if declared_count != len(entries):
        out.append(
            f"`count` is {declared_count!r} but the file holds {len(entries)} "
            f"rows. The count is the ADR-0018 pin; move it in the same change "
            f"that moves a row."
        )

    seen: set[tuple[int, str]] = set()
    for entry in entries:
        unknown = sorted(set(entry) - KNOWN_HISTORICAL_FIELDS)
        if unknown:
            out.append(f"historical row {entry!r} carries unread fields {unknown}.")
        number = entry.get("number")
        slug = entry.get("slug")
        landed_at = entry.get("landed_at")
        if not isinstance(number, int) or not slug or not isinstance(landed_at, int):
            out.append(f"historical row {entry!r} is not a (number, slug, landed_at)")
            continue
        if (number, slug) in seen:
            out.append(f"historical pair ADR-{number:04d} {slug!r} is listed twice.")
        seen.add((number, slug))

        if (number, slug) in live:
            out.append(
                f"ADR-{number:04d} {slug!r} is BOTH a declared branch collision "
                f"and a historical renumbering. A pair is one or the other: a "
                f"live claim needs a disposition, history needs a landed number."
            )
        if number not in rows:
            out.append(
                f"historical ADR-{number:04d} {slug!r} spells a number with no "
                f"row in the register at all."
            )
        row = by_slug.get(slug)
        if row is None:
            out.append(
                f"historical ADR-{number:04d} {slug!r} claims the decision landed "
                f"under another number, but no row in the register carries that "
                f"slug. Then it is not history — it is an undeclared claim."
            )
            continue
        if row["number"] != landed_at:
            out.append(
                f"historical ADR-{number:04d} {slug!r} says it landed at "
                f"ADR-{landed_at:04d}; the register carries that slug at "
                f"ADR-{row['number']:04d}."
            )
        if row.get("status") != "authored":
            out.append(
                f"historical ADR-{number:04d} {slug!r} points at "
                f"ADR-{landed_at:04d}, which is {row.get('status')!r} rather than "
                f"authored. A decision that has not landed cannot be the reason "
                f"an older numbering is history."
            )
        if landed_at == number:
            out.append(
                f"historical ADR-{number:04d} {slug!r} landed at its own number, "
                f"so nothing was renumbered and the row says nothing."
            )
        refs = entry.get("refs")
        if not isinstance(refs, int) or refs < 1:
            out.append(
                f"historical ADR-{number:04d} {slug!r} records refs={refs!r}; the "
                f"observed ref count is context, but a pair seen on no ref was "
                f"never observed."
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


# --------------------------------------------------------------------------
# The all-ref audit scan, and its coverage.
#
# THE AUTHORITY IS THE MERGE-BASE RESERVATION GATE.  `unreserved_authorings`
# runs on every branch's own CI against that branch's merge base, and the merge
# gate `test_adr_numbering.py::test_every_adr_number_is_unique` fails the moment
# a colliding document actually lands.  Those two bite unconditionally.
#
# This scan is a SUPPLEMENTARY AUDIT and nothing more.  Michael's ruling,
# 2026-09-04: "The global-ref scan is an audit tool, not a dependable CI gate.
# CI often sees only one branch or a shallow ref set.  The merge-base
# reservation gate is therefore the authority."  So the scan reports its own
# coverage — how many refs were visible, whether the checkout was shallow,
# whether remote refs were fetched — and declares NON-EXECUTION when the
# coverage is inadequate.  That is the whole point: it must be impossible to
# read "zero collisions found" as a proof when what actually happened is "only
# one branch was visible".
# --------------------------------------------------------------------------

# One visible ref proves nothing: it is the branch under test, and it agrees
# with itself.  Two is the smallest number at which the scan has compared
# anything at all.
MIN_REFS_WITH_ADRS = 2


@dataclass(frozen=True)
class ScanCoverage:
    """What the audit scan could actually see, reported whether or not it ran."""

    requested: bool
    git_available: bool
    shallow: bool
    remotes_configured: bool
    refs_visible: int
    remote_refs_visible: int
    refs_with_adrs: int
    reason: str | None

    @property
    def adequate(self) -> bool:
        return self.reason is None

    def describe(self) -> str:
        state = "RAN" if self.adequate else "DID NOT RUN"
        return (
            f"ADR all-ref audit scan {state}. "
            f"requested={self.requested} git={self.git_available} "
            f"shallow={self.shallow} remotes_configured={self.remotes_configured} "
            f"refs_visible={self.refs_visible} "
            f"remote_refs_visible={self.remote_refs_visible} "
            f"refs_carrying_adrs={self.refs_with_adrs}."
            + ("" if self.adequate else f" NON-EXECUTION: {self.reason}")
            + " The authority is the merge-base reservation gate"
            " (test_no_adr_was_authored_without_a_reservation_on_the_merge_base)"
            " and the merge gate test_adr_numbering.py; this scan is a"
            " supplementary audit."
        )


def scan_coverage(
    *,
    requested: bool,
    git_available: bool,
    shallow: bool,
    remotes_configured: bool,
    refs: list[str],
    refs_with_adrs: int,
) -> ScanCoverage:
    """Decide whether a scan over these refs is worth believing.

    Pure, so the non-execution paths below are PLANTED and proven rather than
    asserted in a docstring (ADR-0018).  Each clause names a way a clean result
    could be clean for the wrong reason.
    """
    remote_refs = [ref for ref in refs if ref.startswith("refs/remotes/")]
    reason: str | None = None
    if not requested:
        reason = (
            f"the scan was not requested — set {SCAN_REFS_ENV}=1 to run it. It is "
            f"off by default because it is one `git ls-tree` per ref."
        )
    elif not git_available:
        reason = "git is not on PATH, so no ref could be read."
    elif shallow:
        reason = (
            "the checkout is SHALLOW (`git rev-parse --is-shallow-repository` is "
            "true). A shallow clone can hold refs whose trees are absent, so an "
            "absent ADR file is indistinguishable from an unfetched one."
        )
    elif remotes_configured and not remote_refs:
        reason = (
            "a remote is configured but NO remote-tracking refs are present: "
            "`git fetch` was not run, so every branch that lives only on `origin` "
            "was invisible to this scan."
        )
    elif refs_with_adrs < MIN_REFS_WITH_ADRS:
        reason = (
            f"only {refs_with_adrs} ref carried any docs/adr/*.md, below the "
            f"minimum of {MIN_REFS_WITH_ADRS}. A scan over one ref compares the "
            f"branch under test with itself and reports clean having looked at "
            f"nothing."
        )
    return ScanCoverage(
        requested=requested,
        git_available=git_available,
        shallow=shallow,
        remotes_configured=remotes_configured,
        refs_visible=len(refs),
        remote_refs_visible=len(remote_refs),
        refs_with_adrs=refs_with_adrs,
        reason=reason,
    )


def _scan_refs() -> tuple[dict[str, dict[int, str]], ScanCoverage]:
    """ADR numbers on every local and remote ref except the base branch.

    Returns the scan AND its coverage, always.  The coverage is what the caller
    reports; an empty scan is never a clean result.
    """
    requested = os.environ.get(SCAN_REFS_ENV, "").lower() in {"1", "true", "yes"}
    git = _git()
    if git is None or not requested:
        return {}, scan_coverage(
            requested=requested,
            git_available=git is not None,
            shallow=False,
            remotes_configured=False,
            refs=[],
            refs_with_adrs=0,
        )

    def run(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - absolute git, literal args
            [git, *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        ).stdout

    shallow = run("rev-parse", "--is-shallow-repository").strip() == "true"
    remotes_configured = bool(run("remote").split())
    base = os.environ.get(BASE_REF_ENV, DEFAULT_BASE_REF)
    refs = run(
        "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"
    ).split()

    skip = {f"refs/remotes/{base}", "refs/heads/main", f"refs/heads/{base}"}
    scan: dict[str, dict[int, str]] = {}
    for ref in refs:
        if ref in skip:
            continue
        listing = run("ls-tree", "--name-only", ref, "docs/adr/")
        names = {
            line.rsplit("/", 1)[-1]
            for line in listing.splitlines()
            if ADR_FILENAME.match(line.rsplit("/", 1)[-1])
        }
        if names:
            scan[ref] = numbers_in(names)

    coverage = scan_coverage(
        requested=True,
        git_available=True,
        shallow=shallow,
        remotes_configured=remotes_configured,
        refs=refs,
        refs_with_adrs=len(scan),
    )
    return scan, coverage


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


def test_every_declared_collision_is_classified_with_evidence() -> None:
    """Michael's ruling: dispose of every claimant before renumbering anything.

    AUTHORED rows only, matching `findings`: "already on `main`" has to name a
    decision that is actually on `main`, and a merely `reserved` number is a
    claim rather than a document.
    """
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    authored = {
        row["number"]: row["slug"]
        for row in register["reservation"]
        if row.get("status") == "authored"
    }
    problems: list[str] = []
    for entry in register.get("branch_collision", []):
        problems.extend(_collision_classification_findings(entry, authored))
    assert problems == [], "docs/adr/reservations.toml: " + "; ".join(problems)


def test_the_historical_renumberings_are_preserved_and_stay_out_of_the_queue() -> None:
    """The 31 excluded pairs, their equivalence evidence, and the separation.

    Michael's ruling: they "should remain excluded, with their equivalence
    evidence preserved. Mixing them into the live collision queue would make the
    register less useful."
    """
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    historical = load_register(HISTORICAL.read_text(encoding="utf-8"))
    problems = historical_findings(register, historical)
    assert problems == [], "docs/adr/historical-renumberings.toml: " + "; ".join(
        problems
    )


def test_the_historical_pin_is_not_vacuous() -> None:
    """A file that emptied itself would satisfy every check above."""
    historical = load_register(HISTORICAL.read_text(encoding="utf-8"))
    assert historical["count"] == HISTORICAL_RENUMBERING_COUNT, (
        f"the historical-renumbering pin moved: file says "
        f"{historical['count']}, this module pins {HISTORICAL_RENUMBERING_COUNT}. "
        f"Two-directional per ADR-0018 — it may not grow silently, and it may "
        f"only shrink in the change that retires the refs it names."
    )


def test_the_register_agrees_with_the_branches_that_hold_numbers() -> None:
    """Requirement 5's branch half — a SUPPLEMENTARY AUDIT, not the authority.

    The authority is the merge-base reservation gate above and the merge gate in
    `test_adr_numbering.py`.  This scan reports its coverage and declines to run
    rather than reporting a clean result over refs it could not see.
    """
    scan, coverage = _scan_refs()
    if not coverage.adequate:
        pytest.skip(coverage.describe())
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    problems = branch_claim_findings(register, scan)
    assert problems == [], f"{coverage.describe()} Findings: " + "; ".join(problems)


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


# --------------------------------------------------------------------------
# Disposition proofs (ADR-0018).  The six buckets are a closed vocabulary,
# `evidence` is mandatory beside them, and — Michael's ruling, 2026-09-04 —
# every NON-ACTIVE row names its successor or its resumption rule.  Each way of
# skipping a disposition, and each way of asserting one the evidence contradicts,
# is planted and named against the clean control below, which must stay clean.
# A detector that flagged everything would "catch" all of these.
# --------------------------------------------------------------------------

CLEAN_COLLISION = {
    "number": 1,
    "main_slug": "alpha",
    "branch_slug": "a-different-decision",
    "where": "feat/something",
    "disposition": "dormant",
    "evidence": "tip abc1234, 2026-09-01, local only, main carries nothing.",
    "successor_or_resumption": (
        "No successor; nothing on `main` decides this. Resumption: revalidate "
        "the problem, choose the owner, reserve a fresh number from next_free."
    ),
}
CLEAN_AUTHORED = {1: "alpha", 2: "beta"}


def test_the_classification_negative_control_is_clean() -> None:
    assert _collision_classification_findings(CLEAN_COLLISION, CLEAN_AUTHORED) == []


def test_a_planted_unclassified_collision_is_named() -> None:
    planted = {k: v for k, v in CLEAN_COLLISION.items() if k != "disposition"}
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("has disposition None" in p for p in problems), problems


def test_a_planted_invented_disposition_is_named() -> None:
    """The vocabulary is CLOSED at six; a seventh is not a bucket."""
    planted = {**CLEAN_COLLISION, "disposition": "probably-fine"}
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("'probably-fine'" in p for p in problems), problems
    assert any("'historical_equivalent'" in p for p in problems), problems


def test_a_planted_retired_disposition_is_named() -> None:
    """The vocabulary REPLACED an earlier one; the old values must not pass.

    `rescue-only` named where a commit was stored, which Michael's ruling makes
    exactly the wrong classifier: a branch name is evidence of storage location,
    not intent.
    """
    for retired in ("rescue-only", "pushed-open", "represented-on-main"):
        planted = {**CLEAN_COLLISION, "disposition": retired}
        problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
        assert any(repr(retired) in p for p in problems), (retired, problems)


def test_a_planted_classification_without_evidence_is_named() -> None:
    """A bucket guessed from a branch NAME is what the ruling forbade."""
    planted = {**CLEAN_COLLISION, "evidence": "   "}
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("no `evidence`" in p for p in problems), problems


def test_a_planted_non_active_row_without_a_successor_or_resumption_rule_is_named() -> (
    None
):
    """THE defect this ruling exists to prevent.

    "There are no active collision repairs to perform.  What remains is
    preventing abandoned history from accidentally becoming authority."  A row
    that says only "not active" gives a later reader exactly one move: open the
    branch and believe it.  Planted on every non-active value, because a check
    that bit on only one of them would leave four ways through.
    """
    for disposition in sorted(DISPOSITIONS - {ACTIVE_DISPOSITION}):
        planted = {
            k: v for k, v in CLEAN_COLLISION.items() if k != "successor_or_resumption"
        }
        planted["disposition"] = disposition
        if disposition == "historical_equivalent":
            planted["represented_by"] = 2
        if disposition == "closed_unmerged":
            planted |= {
                "pr": 7,
                "pr_state": "closed_unmerged",
                "pr_closed_at": "2026-09-01T10:00:00Z",
            }
        problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
        assert any("no `successor_or_resumption`" in p for p in problems), (
            disposition,
            problems,
        )


def test_an_active_row_needs_no_successor() -> None:
    """The one exemption, and it is not the absence of a check.

    Work still headed for `main` has no "where did it go" to answer.  Proven
    beside the plant above so the rule cannot be satisfied by refusing every row.
    """
    planted = {
        k: v for k, v in CLEAN_COLLISION.items() if k != "successor_or_resumption"
    }
    planted["disposition"] = "active"
    assert _collision_classification_findings(planted, CLEAN_AUTHORED) == []


def test_a_planted_disposition_that_contradicts_its_evidence_is_named() -> None:
    """`active` over a pull request that was CLOSED UNMERGED.

    The two halves of the row disagree, and the disposition is the half a reader
    acts on — this reads as "being promoted" while the evidence says the door
    was shut.
    """
    planted = {
        **CLEAN_COLLISION,
        "disposition": "active",
        "pr": 500,
        "pr_state": "closed_unmerged",
        "pr_closed_at": "2026-08-29T19:10:16Z",
    }
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("contradicts its own" in p for p in problems), problems


def test_a_planted_active_row_over_a_merged_pull_request_is_named() -> None:
    planted = {
        **CLEAN_COLLISION,
        "disposition": "active",
        "pr": 504,
        "pr_state": "merged",
    }
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("MERGED pull request" in p for p in problems), problems


def test_a_row_whose_disposition_agrees_with_a_closed_pull_request_is_clean() -> None:
    """The same evidence under the honest bucket must pass.

    This is ADR-0071's real shape: closed unmerged, and ruled never-merge
    because a closed pull request can be reopened by anyone with the button.
    """
    planted = {
        **CLEAN_COLLISION,
        "disposition": "never_merge",
        "pr": 500,
        "pr_state": "closed_unmerged",
        "pr_closed_at": "2026-08-29T19:10:16Z",
    }
    assert _collision_classification_findings(planted, CLEAN_AUTHORED) == []


def test_a_planted_closed_unmerged_bucket_with_no_closed_pull_request_is_named() -> (
    None
):
    planted = {**CLEAN_COLLISION, "disposition": "closed_unmerged"}
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("but records pr_state" in p for p in problems), problems


def test_a_planted_invented_pull_request_state_is_named() -> None:
    planted = {**CLEAN_COLLISION, "pr": 1, "pr_state": "kind-of-open"}
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("has pr_state 'kind-of-open'" in p for p in problems), problems


def test_a_planted_remote_deletion_without_rescue_coordinates_is_named() -> None:
    """Michael authorized deleting a remote branch ONLY once it is durable here.

    The authorization and the coordinates that survive it are one act, so the
    row cannot carry the first without the second.
    """
    planted = {**CLEAN_COLLISION, "remote_deletion_authorized": True}
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("stops being reachable" in p for p in problems), problems
    assert any("'tip'" in p and "'rescue_tag'" in p for p in problems), problems


def test_a_remote_deletion_with_full_coordinates_is_clean() -> None:
    planted = {
        **CLEAN_COLLISION,
        "disposition": "never_merge",
        "tip": "3b409faf6555615e7ab580146f0288d9abd35926",
        "rescue_ref": "refs/rescue/example",
        "rescue_tag": "rescue-example-20260904",
        "remote_deletion_authorized": True,
    }
    assert _collision_classification_findings(planted, CLEAN_AUTHORED) == []


def test_a_planted_abbreviated_tip_is_named() -> None:
    """An abbreviation resolves only where the object still exists."""
    planted = {**CLEAN_COLLISION, "tip": "3b409faf"}
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("not a full forty-character" in p for p in problems), problems


def test_a_planted_document_from_a_different_number_is_named() -> None:
    planted = {**CLEAN_COLLISION, "document": "docs/adr/0071-something-else.md"}
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("whose number is not" in p for p in problems), problems


def test_a_planted_historical_equivalent_without_a_number_is_named() -> None:
    """A `historical_equivalent` row with no `represented_by`.

    The whole claim is "the same decision is already on `main`"; unnamed, it is
    unfalsifiable, and the branch stays the only readable copy of its decision.
    """
    planted = {**CLEAN_COLLISION, "disposition": "historical_equivalent"}
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("names no `represented_by`" in p for p in problems), problems


def test_a_planted_representation_by_an_unregistered_number_is_named() -> None:
    planted = {
        **CLEAN_COLLISION,
        "disposition": "historical_equivalent",
        "represented_by": 9,
    }
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("not an AUTHORED decision" in p for p in problems), problems


def test_a_planted_representation_by_the_colliding_number_itself_is_named() -> None:
    """ADR-0032 cannot be "already represented" by ADR-0032 — that IS the clash."""
    planted = {
        **CLEAN_COLLISION,
        "disposition": "historical_equivalent",
        "represented_by": 1,
    }
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("holds a DIFFERENT decision on `main`" in p for p in problems), problems


def test_a_planted_same_slug_representation_is_sent_to_the_historical_file() -> None:
    planted = {
        **CLEAN_COLLISION,
        "branch_slug": "beta",
        "disposition": "historical_equivalent",
        "represented_by": 2,
    }
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("historical-renumberings.toml" in p for p in problems), problems


def test_a_planted_stray_represented_by_is_named() -> None:
    planted = {**CLEAN_COLLISION, "represented_by": 2}
    problems = _collision_classification_findings(planted, CLEAN_AUTHORED)
    assert any("Only `historical_equivalent` may" in p for p in problems), problems


# --------------------------------------------------------------------------
# The historical file's equivalence evidence, proven in both directions.
# --------------------------------------------------------------------------

HIST_REGISTER = {
    "next_free": 4,
    "reservation": [
        {"number": 1, "slug": "alpha", "status": "authored", "claimed": "2026-01-01"},
        {"number": 2, "slug": "beta", "status": "authored", "claimed": "2026-01-02"},
        {"number": 3, "slug": "gamma", "status": "reserved", "claimed": "2026-01-03"},
    ],
    "branch_collision": [
        {
            "number": 1,
            "main_slug": "alpha",
            "branch_slug": "a-live-claim",
            "where": "feat/live",
            "disposition": "active",
            "evidence": "read it",
        }
    ],
}
CLEAN_HISTORICAL = {
    "count": 1,
    "historical": [{"number": 1, "slug": "beta", "landed_at": 2, "refs": 7}],
}


def test_the_historical_negative_control_is_clean() -> None:
    assert historical_findings(HIST_REGISTER, CLEAN_HISTORICAL) == []


def test_a_planted_historical_count_drift_is_named() -> None:
    planted = {**CLEAN_HISTORICAL, "count": 2}
    problems = historical_findings(HIST_REGISTER, planted)
    assert any("`count` is 2" in p for p in problems), problems


def test_a_planted_historical_pair_with_no_landed_decision_is_named() -> None:
    """Without a row carrying the slug, it is not history — it is a live claim."""
    planted = {
        "count": 1,
        "historical": [
            {"number": 1, "slug": "never-landed", "landed_at": 2, "refs": 1}
        ],
    }
    problems = historical_findings(HIST_REGISTER, planted)
    assert any("no row in the register carries that slug" in p for p in problems)


def test_a_planted_historical_pair_pointing_at_the_wrong_number_is_named() -> None:
    planted = {
        "count": 1,
        "historical": [{"number": 1, "slug": "beta", "landed_at": 3, "refs": 1}],
    }
    problems = historical_findings(HIST_REGISTER, planted)
    assert any("the register carries that slug at ADR-0002" in p for p in problems)


def test_a_planted_historical_pair_on_an_unlanded_decision_is_named() -> None:
    planted = {
        "count": 1,
        "historical": [{"number": 1, "slug": "gamma", "landed_at": 3, "refs": 1}],
    }
    problems = historical_findings(HIST_REGISTER, planted)
    assert any("rather than authored" in p for p in problems), problems


def test_a_planted_pair_in_both_files_is_named() -> None:
    """A pair is history or a live claim. Being both hides it in the quiet file."""
    planted = {
        "count": 1,
        "historical": [
            {"number": 1, "slug": "a-live-claim", "landed_at": 2, "refs": 1}
        ],
    }
    problems = historical_findings(HIST_REGISTER, planted)
    assert any("BOTH a declared branch collision" in p for p in problems), problems


# --------------------------------------------------------------------------
# Scan-coverage proofs (ADR-0018).  Michael's requirement in one sentence:
# "zero collisions found" must never be readable as "only one branch was
# visible".  So every way the scan can be blind is planted, and the positive
# control proves the adequate path is not clean by construction.
# --------------------------------------------------------------------------

ADEQUATE = {
    "requested": True,
    "git_available": True,
    "shallow": False,
    "remotes_configured": True,
    "refs": [
        "refs/heads/main",
        "refs/heads/feat/one",
        "refs/remotes/origin/feat/two",
    ],
    "refs_with_adrs": 3,
}


def test_adequate_coverage_is_reported_as_having_run() -> None:
    """The control. A gate that never runs would 'pass' every plant below."""
    coverage = scan_coverage(**ADEQUATE)
    assert coverage.adequate, coverage.describe()
    assert "RAN" in coverage.describe()
    assert coverage.refs_visible == 3
    assert coverage.remote_refs_visible == 1
    assert "merge-base reservation gate" in coverage.describe()


def test_a_one_ref_scan_reports_non_execution_not_clean() -> None:
    """The exact CI shape: the base plus one branch, agreeing with itself.

    `refs/remotes/origin/main` is present, so the unfetched-remote clause below
    is satisfied and this test really does exercise the ref-count clause rather
    than passing for the neighbouring reason.
    """
    coverage = scan_coverage(
        **{
            **ADEQUATE,
            "refs": ["refs/heads/feat/one", "refs/remotes/origin/main"],
            "refs_with_adrs": 1,
        }
    )
    assert not coverage.adequate
    assert "only 1 ref carried" in coverage.reason
    assert "DID NOT RUN" in coverage.describe()
    assert "NON-EXECUTION" in coverage.describe()


def test_a_shallow_checkout_reports_non_execution_not_clean() -> None:
    coverage = scan_coverage(**{**ADEQUATE, "shallow": True})
    assert not coverage.adequate
    assert "SHALLOW" in coverage.reason
    assert "NON-EXECUTION" in coverage.describe()


def test_unfetched_remote_refs_report_non_execution_not_clean() -> None:
    """A remote exists and nothing was fetched: every origin-only branch is dark."""
    coverage = scan_coverage(
        **{
            **ADEQUATE,
            "refs": ["refs/heads/main", "refs/heads/feat/one"],
            "refs_with_adrs": 2,
        }
    )
    assert not coverage.adequate
    assert "`git fetch` was not run" in coverage.reason


def test_an_unrequested_scan_reports_non_execution_not_clean() -> None:
    coverage = scan_coverage(**{**ADEQUATE, "requested": False})
    assert not coverage.adequate
    assert SCAN_REFS_ENV in coverage.reason


def test_a_missing_git_reports_non_execution_not_clean() -> None:
    coverage = scan_coverage(**{**ADEQUATE, "git_available": False})
    assert not coverage.adequate
    assert "git is not on PATH" in coverage.reason


def test_adequate_coverage_finds_a_planted_collision() -> None:
    """The positive control: the adequate path is not clean by construction.

    A non-execution report and a clean report must be reachable from DIFFERENT
    inputs, or the hardening above would have turned the scan into a gate that
    can only ever decline.
    """
    coverage = scan_coverage(**ADEQUATE)
    assert coverage.adequate
    register = {"next_free": 3, "reservation": CLEAN["reservation"]}
    scan = {"refs/remotes/origin/feat/two": {1: "an-undeclared-second-decision"}}
    problems = branch_claim_findings(register, scan)
    assert any("an-undeclared-second-decision" in p for p in problems), problems


def test_the_scan_is_supplementary_and_says_so_where_a_reader_will_look() -> None:
    """The ruling is recorded in the code, not only in a commit message."""
    body = Path(__file__).read_text(encoding="utf-8")
    assert "THE AUTHORITY IS THE MERGE-BASE RESERVATION GATE" in body
    assert "SUPPLEMENTARY AUDIT" in body
    register_text = REGISTER.read_text(encoding="utf-8")
    assert "THE AUTHORITY IS THE MERGE-BASE RESERVATION GATE" in register_text
