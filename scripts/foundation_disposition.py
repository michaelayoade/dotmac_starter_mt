#!/usr/bin/env python3
"""``CandidateDisposition.v1`` — what became of a candidate, appended not edited.

`scripts/foundation_candidate.py` owns ``CandidateArtifact.v1``: the six facts
that make one candidate's bytes re-fetchable. It records a candidate coming into
existence and says nothing about what happened next, because at record time
nothing has.

This file owns the second half. A candidate is eventually **consumed** — it is
published, or it is superseded, or (the case this file exists for) a post-freeze
change makes the version name a tree the artifact was never built from, and the
candidate becomes unusable while its bytes remain historically real.

## Why a SEPARATE record rather than a field on the receipt

The obvious shape is `published: true` flipped on the existing receipt. It is
the wrong shape, and the reason is the whole design:

* Downstream receipts — a restore proof, an issuer stand-up, a Lane 3
  rehearsal — bind to the candidate receipt. A receipt that can be edited after
  those bindings exist is a receipt whose meaning changes underneath them.
* `CandidateArtifact.v1` describes bytes that were built once. That description
  cannot become false; only its USABILITY can. Mixing an immutable measurement
  with a mutable judgement in one document means a reader can no longer tell
  which half they are trusting.

So ``CandidateArtifact.v1`` stays byte-for-byte as written, and every judgement
about it is APPENDED here, bound to the receipt by digest.

## Append-only, made mechanical rather than asserted

"Append-only" is a property, not a promise, so it is checked three ways and the
three are independent:

1. **A hash chain.** Each entry carries ``previous_digest``. For entry *n>1*
   that is the canonical digest of entry *n-1*. Editing any historical entry
   therefore invalidates every entry after it — a silent one-line edit is not
   available, only a full rewrite that shows up as one.

2. **An anchor that is not ours to move.** Entry 1 chains to the digest of the
   ``CandidateArtifact.v1`` receipt it dispositions, not to a zero genesis. So
   the same check that proves the log has not been rewritten also proves the
   receipt has not been touched — which is exactly the pair of claims a reader
   of this file needs, and a zero genesis would have proved neither.

3. **A two-directional count ratchet** (ADR-0018). :data:`EXPECTED_ENTRIES`
   fails when the log GROWS as well as when it shrinks. Appending is legitimate
   and cheap; it just cannot be silent, because the constant moves in the same
   diff.

Each entry additionally re-derives the receipt's own coordinates and compares
them field by field, so a receipt edited into a *different* candidate is caught
by identity as well as by digest.

## What a disposition does NOT do

It never authorizes anything. ``publishable: false`` records that a candidate
must not be published; it does not by itself stop a lane from publishing one.
The refusal belongs to the version-binding guard, which reads this log. A record
that both states a fact and enforces it is one edit away from being neither.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess  # nosec B404 -- argv list, shell=False; git only
import sys
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
LOG_PATH: Final = (
    REPO_ROOT / "docs" / "inventories" / "foundation-candidate-dispositions.json"
)

ENTRY_SCHEMA: Final = "CandidateDisposition.v1"
LOG_SCHEMA: Final = "CandidateDispositionLog.v1"
RECEIPT_SCHEMA: Final = "CandidateArtifact.v1"

#: The closed set of things that can become of a candidate. An open string would
#: let a writer invent a disposition nobody wrote a rule for, which is how
#: `publishable` would end up being the only field anything reads.
DISPOSITIONS: Final[tuple[str, ...]] = ("invalidated", "superseded", "published")

#: Dispositions that make a candidate permanently unpublishable. `superseded` is
#: deliberately NOT here: a superseded candidate was never wrong, it was merely
#: overtaken, and conflating the two would make "we moved on" read like "these
#: bytes are unsafe".
TERMINAL_UNPUBLISHABLE: Final[frozenset[str]] = frozenset({"invalidated"})

#: ADR-0018's two-directional ratchet. This fails on growth as well as on
#: shrinkage — an append is fine and must be VISIBLE, and a deletion cannot hide
#: behind a coincidental later append.
EXPECTED_ENTRIES: Final = 4

#: The six coordinates `CandidateArtifact.v1` exists to preserve, plus the
#: repository they are addressable in. Compared field by field rather than only
#: by digest, so a receipt swapped for a different candidate is refused by
#: identity even if somebody also updated the digests.
ARTIFACT_COORDINATES: Final[tuple[str, ...]] = (
    "artifact_id",
    "filename",
    "repository",
    "run_id",
    "sha256",
    "size_bytes",
    "source_sha",
)

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

_REQUIRED_ENTRY_FIELDS: Final[tuple[str, ...]] = (
    "artifact",
    "disposition",
    "facility",
    "previous_digest",
    "publishable",
    "reason",
    "receipt_bytes_sha256",
    "receipt_digest",
    "receipt_path",
    "recorded_at",
    "schema",
    "sequence",
    "version",
)


class DispositionError(ValueError):
    """The log does not describe an append-only history."""


def canonical_bytes(document: Any) -> bytes:
    """The exact bytes a digest covers.

    Sorted keys and tight separators, so the same facts always produce the same
    message — the rule `evidence.py` and `document.py` already apply, for the
    same reason: two byte strings with identical meaning must not have different
    standings.
    """
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest_of(document: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(document)).hexdigest()


def receipt_digests(path: Path) -> tuple[str, str]:
    """(canonical-content digest, raw-bytes digest) of a candidate receipt.

    Both, deliberately. The canonical digest is what a signature or a chain
    should cover — an innocent re-serialization must not read as tampering. The
    raw digest is what proves the checked-in FILE is untouched, down to
    whitespace, which is the stronger claim this log needs to make about
    `CandidateArtifact.v1`.
    """
    raw = path.read_bytes()
    return digest_of(json.loads(raw)), "sha256:" + hashlib.sha256(raw).hexdigest()


def load_log(path: Path = LOG_PATH) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise DispositionError("the disposition log must be a JSON object")
    return document


def entries(log: dict[str, Any]) -> list[dict[str, Any]]:
    found = log.get("entries")
    if not isinstance(found, list):
        raise DispositionError("the disposition log carries no `entries` array")
    return [entry for entry in found if isinstance(entry, dict)]


def check(
    log: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
    expected_entries: int = EXPECTED_ENTRIES,
) -> list[str]:
    """Every way this log fails to be an append-only history. Empty means sound.

    Returns problems rather than raising on the first one: a rewritten log
    breaks the chain at one point and every field check after it, and reporting
    those one run at a time turns one repair into several.
    """
    problems: list[str] = []
    if log.get("schema") != LOG_SCHEMA:
        problems.append(f"log schema is {log.get('schema')!r}, expected {LOG_SCHEMA!r}")

    try:
        found = entries(log)
    except DispositionError as exc:
        return [*problems, str(exc)]

    if len(found) != expected_entries:
        problems.append(
            f"the log holds {len(found)} entry/entries and EXPECTED_ENTRIES is "
            f"{expected_entries}. This ratchet fails in BOTH directions: an "
            "append is legitimate and must move the constant in the same diff, "
            "and a deletion cannot hide behind a later append"
        )

    previous: dict[str, Any] | None = None
    for index, entry in enumerate(found):
        where = f"entry {index + 1}"
        missing = sorted(set(_REQUIRED_ENTRY_FIELDS) - set(entry))
        if missing:
            problems.append(f"{where} is missing required field(s) {missing}")
            previous = entry
            continue
        if entry["schema"] != ENTRY_SCHEMA:
            problems.append(
                f"{where} schema is {entry['schema']!r}, expected {ENTRY_SCHEMA!r}"
            )
        if entry["sequence"] != index + 1:
            problems.append(
                f"{where} declares sequence {entry['sequence']!r}; sequences are "
                f"contiguous from 1, so this position must be {index + 1}"
            )
        if entry["disposition"] not in DISPOSITIONS:
            problems.append(
                f"{where} disposition {entry['disposition']!r} is not one of "
                f"{list(DISPOSITIONS)}"
            )
        if entry["disposition"] in TERMINAL_UNPUBLISHABLE and entry["publishable"]:
            problems.append(
                f"{where} is {entry['disposition']!r} and claims publishable=true. "
                "An invalidated candidate is one whose version now names a tree "
                "its bytes were never built from; publishing it would put two "
                "contracts behind one version name"
            )
        if not str(entry.get("reason", "")).strip():
            problems.append(
                f"{where} carries no reason. A disposition with no reason is "
                "indistinguishable from drift somebody forgot to explain"
            )
        if entry["disposition"] == "invalidated":
            commit = str(entry.get("invalidating_commit", ""))
            if not _FULL_SHA.match(commit):
                problems.append(
                    f"{where} is invalidated and names invalidating_commit "
                    f"{commit!r}, which is not a full 40-hex commit. An "
                    "abbreviation is a search, not a coordinate"
                )

        problems.extend(_check_receipt(entry, where=where, repo_root=repo_root))
        problems.extend(_check_link(entry, previous, where=where, repo_root=repo_root))
        previous = entry

    return problems


def _check_receipt(entry: dict[str, Any], *, where: str, repo_root: Path) -> list[str]:
    """The entry's receipt is on disk, is a candidate receipt, and is UNCHANGED."""
    problems: list[str] = []
    path = repo_root / str(entry["receipt_path"])
    if not path.is_file():
        return [f"{where} names receipt {entry['receipt_path']!r}, which is absent"]
    try:
        canonical, raw = receipt_digests(path)
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{where}: receipt {entry['receipt_path']!r} is unreadable: {exc}"]

    if receipt.get("schema") != RECEIPT_SCHEMA:
        problems.append(
            f"{where} dispositions {entry['receipt_path']!r}, whose schema is "
            f"{receipt.get('schema')!r} rather than {RECEIPT_SCHEMA!r}"
        )
    if entry["receipt_digest"] != canonical:
        problems.append(
            f"{where} records receipt_digest {entry['receipt_digest']} and the "
            f"receipt canonicalizes to {canonical}. Either the receipt was "
            "edited after this disposition was written — which is the one thing "
            "CandidateArtifact.v1 must never be — or this entry was rewritten"
        )
    if entry["receipt_bytes_sha256"] != raw:
        problems.append(
            f"{where} records receipt_bytes_sha256 {entry['receipt_bytes_sha256']} "
            f"and the file hashes to {raw}. The content digest above can survive "
            "a reformat; this one cannot, and both are recorded so a reformat is "
            "reported rather than absorbed"
        )
    recorded = entry.get("artifact")
    if not isinstance(recorded, dict):
        return [*problems, f"{where} carries no artifact coordinate object"]
    for key in ARTIFACT_COORDINATES:
        if key not in recorded:
            problems.append(f"{where} artifact coordinates omit {key!r}")
        elif recorded[key] != receipt.get(key):
            problems.append(
                f"{where} records artifact {key}={recorded[key]!r} and the "
                f"receipt says {receipt.get(key)!r}. A disposition bound to a "
                "different candidate than the one it names is worse than none"
            )
    if entry["version"] != receipt.get("version"):
        problems.append(
            f"{where} dispositions version {entry['version']!r} and the receipt "
            f"records {receipt.get('version')!r}"
        )
    if entry["facility"] != receipt.get("facility"):
        problems.append(
            f"{where} names facility {entry['facility']!r} and the receipt "
            f"records {receipt.get('facility')!r}"
        )
    return problems


def _check_link(
    entry: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    where: str,
    repo_root: Path,
) -> list[str]:
    """The chain link, and the anchor that makes the first one mean something."""
    stated = str(entry["previous_digest"])
    if not _DIGEST.match(stated):
        return [f"{where} previous_digest {stated!r} is not a sha256:<64 hex> digest"]
    if previous is None:
        path = repo_root / str(entry["receipt_path"])
        if not path.is_file():
            return []
        anchor, _ = receipt_digests(path)
        if stated != anchor:
            return [
                f"{where} is the first entry, so it must chain to the digest of "
                f"the receipt it dispositions ({anchor}), and it chains to "
                f"{stated}. The anchor is the receipt rather than a zero genesis "
                "on purpose: it makes one check prove both that the log was not "
                "rewritten and that CandidateArtifact.v1 was not touched"
            ]
        return []
    expected = digest_of(previous)
    if stated != expected:
        return [
            f"{where} chains to {stated} and the preceding entry canonicalizes "
            f"to {expected}. An edit to any earlier entry breaks every link "
            "after it, which is what makes a quiet single-line rewrite "
            "unavailable"
        ]
    return []


LOG_RELATIVE: Final = LOG_PATH.relative_to(REPO_ROOT)


def _git(repo_root: Path, *args: str) -> str:
    """Read-only git, or a refusal — never a silent empty answer.

    The chain and the anchor are in-file properties, and an in-file property can
    always be rewritten by rewriting the whole file. Git history is the only
    oracle in reach that a single commit cannot edit, so it is what actually
    carries "append-only" once the log has more than one commit behind it.

    An unavailable oracle is not a pass. `test_declared_publication.py` learned
    that the expensive way — its sweep used to `pytest.skip` on a refusal, so on
    CI every check in the module skipped silently and the gate was green while
    measuring nothing.
    """
    result = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip()}. The append-only property is proved "
            "against git history; without history this check cannot answer and "
            "must not report a pass it has not earned"
        )
    return result.stdout


def history_violations(
    *, repo_root: Path = REPO_ROOT, path: Path = LOG_RELATIVE
) -> list[str]:
    """Every past revision of the log whose entries are not a PREFIX of today's.

    This is the real append-only proof, and it is the one an in-file chain
    cannot give: with a single entry the chain has nothing to link, so entry 1
    is protected only by its anchor to the receipt — and a writer editing entry
    1 alone leaves the anchor correct. History closes that: once the entry is
    committed, changing it means changing a commit.

    A file with no commits behind it yet (the change that introduces it) has
    nothing to violate, and reports nothing. The teeth arrive with the second
    commit, which is the first moment there is a past to contradict.
    """
    revisions = _git(repo_root, "log", "--format=%H", "--", str(path)).split()
    if not revisions:
        return []
    current = [digest_of(entry) for entry in entries(load_log(repo_root / path))]
    problems: list[str] = []
    for revision in revisions:
        blob = _git(repo_root, "show", f"{revision}:{path}")
        try:
            past = [digest_of(entry) for entry in entries(json.loads(blob))]
        except (ValueError, DispositionError) as exc:
            problems.append(f"{revision[:12]}: unreadable disposition log: {exc}")
            continue
        if past != current[: len(past)]:
            problems.append(
                f"{revision[:12]} recorded {len(past)} entry/entries that are "
                f"not a prefix of today's {len(current)}. An append-only log may "
                "only ever GROW at the end; a past revision disagreeing with "
                "today's prefix means an entry was edited or removed after it "
                "was committed"
            )
    return problems


def next_previous_digest(
    log: dict[str, Any], *, receipt_path: Path, repo_root: Path = REPO_ROOT
) -> str:
    """What a newly appended entry must chain to."""
    found = entries(log)
    if found:
        return digest_of(found[-1])
    canonical, _ = receipt_digests(repo_root / receipt_path)
    return canonical


def _selected_log(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    repo_root = Path(args.repo_root).resolve()
    path = Path(args.log) if args.log else repo_root / LOG_RELATIVE
    return repo_root, load_log(path)


def cmd_check(args: argparse.Namespace) -> int:
    repo_root, log = _selected_log(args)
    problems = check(log, repo_root=repo_root)
    problems.extend(history_violations(repo_root=repo_root))
    if problems:
        print("candidate-disposition:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"disposition log sound: {len(entries(log))} entry/entries, "
        "chain intact, receipts unchanged"
    )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    _, log = _selected_log(args)
    for entry in entries(log):
        print(
            f"{entry['sequence']:>2}. {entry['facility']} {entry['version']} — "
            f"{entry['disposition']} (publishable={entry['publishable']})"
        )
        print(f"    receipt   {entry['receipt_path']} {entry['receipt_digest']}")
        if entry.get("invalidating_commit"):
            print(f"    by commit {entry['invalidating_commit']}")
        print(f"    reason    {entry['reason']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--log", default="")
    sub = parser.add_subparsers(dest="command", required=True)
    check_cmd = sub.add_parser("check", help="prove the log is append-only")
    check_cmd.set_defaults(handler=cmd_check)
    show_cmd = sub.add_parser("show", help="print every disposition")
    show_cmd.set_defaults(handler=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
