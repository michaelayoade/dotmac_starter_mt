#!/usr/bin/env python3
"""Write the post-release record a release workflow leaves behind.

## Why this exists

A release workflow writes the TAG. It does not write the RECORD, and the
repository has two ledgers that a publication invalidates the moment the tag
lands:

1. ``docs/inventories/declared-publication-baseline.json`` — the ``unpublished``
   map excusing a distribution that declares a version nobody can install.
   Publishing means REMOVING that row.
2. ``RELEASED_TAGS`` in ``tests/architecture/test_released_migrations.py`` —
   ``tag -> (distribution, commit, {migration: sha256})``, which holds released
   migration bytes immutable. Publishing a module with a lineage means ADDING
   that entry.

Miss either and five gates fail, so ``main`` is red from the instant of the tag
until a human remembers. Between 2026-08-21 and 2026-08-22 that happened FOUR
times — twice for the same distribution, one version apart. The fourth left
four open pull requests red at once, and each presented as *that branch* being
broken rather than as ``main`` being broken, which is what made it expensive.

The tests state the rule verbatim. Nothing enforced it. This script is the
enforcement: the workflow calls it right after tagging, so the record is
mechanical rather than remembered.

## What it does NOT do

It does not decide anything. It removes a row that a tag has already made
false, and records digests read from that tag. It refuses when the tag does not
exist, when the ledger row is missing (already recorded — that is a no-op, not
an error), or when the declared version and the tag disagree.

It is deliberately runnable BY HAND for repair: the same command that the
workflow runs closes an older gap, which is how the a11 and a12 records were
reconstructed.

## Text, not a round-trip

The JSON ledger is edited as TEXT. Re-serialising it with ``json.dumps``
rewrites every non-ASCII character in the ``$comment`` prose to a ``\\uXXXX``
escape, which turns a five-line removal into a fifteen-line diff touching
paragraphs the change has no business touching.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER = REPO_ROOT / "docs" / "inventories" / "declared-publication-baseline.json"
RELEASED_TAGS_MODULE = (
    REPO_ROOT / "tests" / "architecture" / "test_released_migrations.py"
)

#: Where a distribution's migrations live inside its package, if it has any.
_MIGRATIONS = "src/{import_name}/migrations/versions"


class ReleaseRecordError(RuntimeError):
    """The record cannot be written, and guessing is not an option."""


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise ReleaseRecordError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or 'no stderr'}"
        )
    return result.stdout


def tag_commit(tag: str) -> str:
    """The commit a tag points at, abbreviated the way the map records it."""
    return _git("rev-parse", "--short=8", f"{tag}^{{commit}}").strip()


def migration_digests(tag: str, package_dir: str, import_name: str) -> dict[str, str]:
    """sha256 of every migration AS PUBLISHED, read from the tag itself.

    Read from the tag rather than the working tree on purpose: the whole point
    of the map is to catch a released migration edited afterwards, and a digest
    taken from the tree would agree with the edit.
    """
    prefix = f"{package_dir}/{_MIGRATIONS.format(import_name=import_name)}"
    listing = _git("ls-tree", "-r", "--name-only", tag, f"{prefix}/").split()
    digests: dict[str, str] = {}
    for path in sorted(listing):
        name = path.rsplit("/", 1)[-1]
        if not name.endswith(".py") or name == "__init__.py":
            continue
        blob = subprocess.run(
            ["git", "show", f"{tag}:{path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        digests[name] = hashlib.sha256(blob).hexdigest()
    return digests


def remove_ledger_row(text: str, distribution: str) -> tuple[str, bool]:
    """Drop one distribution's row, preserving every other byte of the file.

    Returns the new text and whether anything was removed. A missing row is a
    NO-OP rather than a failure: re-running the record after a partial repair
    must converge, not refuse.
    """
    name = re.escape(distribution)
    pattern = re.compile(rf'\n    "{name}": \{{.*?\n    \}},', re.DOTALL)
    new_text, count = pattern.subn("", text, count=1)
    if count:
        return new_text, True

    # The row may be the LAST entry, which carries no trailing comma; removing
    # it has to take the PRECEDING comma instead or the JSON is left invalid.
    last = re.compile(
        rf',\n    "{name}": \{{(?:(?!\n    \}}).)*\n    \}}',
        re.DOTALL,
    )
    new_text, count = last.subn("", text, count=1)
    return new_text, bool(count)


def _rendered_entry(tag: str, distribution: str, commit: str, digests: dict) -> str:
    lines = [f'    "{tag}": (', f'        "{distribution}",', f'        "{commit}",']
    if digests:
        lines.append("        {")
        for name, digest in digests.items():
            lines.append(f'            "{name}": (')
            lines.append(f'                "{digest}"')
            lines.append("            ),")
        lines.append("        },")
    else:
        lines.append("        {},")
    lines.append("    ),")
    return "\n".join(lines) + "\n"


def add_released_tag(
    text: str, tag: str, distribution: str, commit: str, digests: dict[str, str]
) -> tuple[str, bool]:
    """Insert one `RELEASED_TAGS` entry, or report it is already present."""
    if f'"{tag}":' in text:
        return text, False
    name = re.escape(distribution)
    anchor = re.search(rf'\n    "{name}-v[^"]+": \(', text)
    if anchor is None:
        raise ReleaseRecordError(
            f"no existing {distribution} entry to anchor to in RELEASED_TAGS; "
            "add the first one by hand so the file's ordering stays deliberate"
        )
    at = anchor.start() + 1
    entry = _rendered_entry(tag, distribution, commit, digests)
    return text[:at] + entry + text[at:], True


def write_record(
    *,
    distribution: str,
    version: str,
    tag: str,
    package_dir: str | None,
    import_name: str | None,
) -> list[str]:
    """Apply both halves of the record. Returns what changed, for the caller."""
    try:
        commit = tag_commit(tag)
    except ReleaseRecordError as failure:
        raise ReleaseRecordError(
            f"{tag} does not resolve to a commit — the record must never be "
            f"written before the tag it describes ({failure})"
        ) from failure

    changed: list[str] = []

    ledger_text = LEDGER.read_text(encoding="utf-8")
    row = json.loads(ledger_text)["unpublished"].get(distribution)
    if row is not None:
        declared = row.get("declared")
        if declared != version:
            raise ReleaseRecordError(
                f"the ledger excuses {distribution} at {declared!r} but "
                f"{version!r} was published; the row describes a different "
                "version and removing it would erase a live exemption"
            )
        new_text, removed = remove_ledger_row(ledger_text, distribution)
        if removed:
            json.loads(new_text)  # never leave the ledger unparseable
            LEDGER.write_text(new_text, encoding="utf-8")
            changed.append(f"removed the {distribution} publication-ledger row")

    if package_dir and import_name:
        digests = migration_digests(tag, package_dir, import_name)
        if digests:
            module_text = RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
            new_module, added = add_released_tag(
                module_text, tag, distribution, commit, digests
            )
            if added:
                RELEASED_TAGS_MODULE.write_text(new_module, encoding="utf-8")
                changed.append(
                    f"recorded {tag} in RELEASED_TAGS "
                    f"({len(digests)} migration digest(s) read from the tag)"
                )

    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--package-dir",
        help="package directory, e.g. packages/dotmac-integration; omit for a "
        "distribution with no migration lineage",
    )
    parser.add_argument(
        "--import-name",
        help="e.g. dotmac_integration; derived from --package-dir when omitted",
    )
    args = parser.parse_args(argv)

    # The two always agree in this repository, and threading a second value
    # through three workflows is a second thing to get wrong.
    import_name = args.import_name
    if args.package_dir and not import_name:
        import_name = args.package_dir.rstrip("/").rsplit("/", 1)[-1].replace("-", "_")

    try:
        changed = write_record(
            distribution=args.distribution,
            version=args.version,
            tag=args.tag,
            package_dir=args.package_dir,
            import_name=import_name,
        )
    except ReleaseRecordError as failure:
        print(f"release record REFUSED: {failure}", file=sys.stderr)
        return 1

    if not changed:
        print(f"release record for {args.tag} already complete — nothing to do")
        return 0
    for line in changed:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
