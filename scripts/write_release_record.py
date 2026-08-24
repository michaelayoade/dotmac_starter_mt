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
   that entry and removing each newly immutable filename from ``UNRELEASED`` in
   the same file. A migration cannot remain both released and editable.

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
false, and records digests read from that tag. On a distribution's first
release it also enrols that lineage in every migration-history map, using only
the package identity and migration prefix carried by the reviewed release
inputs. It refuses when the tag does not exist or when the declared version and
the tag disagree. A missing ledger row is a no-op so a partial repair can
converge.

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
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

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


def _mapping_end(text: str, name: str) -> int:
    """Index of one top-level mapping's closing brace.

    The release-history module deliberately keeps these registries as literal
    dictionaries so reviewers can inspect the complete claim. A column-zero
    closing brace is therefore a load-bearing, narrow text seam rather than a
    generic Python rewriter.
    """
    header = re.search(
        rf"^{re.escape(name)}: [^\n=]+ = \{{\n", text, flags=re.MULTILINE
    )
    if header is None:
        raise ReleaseRecordError(f"cannot find the {name} literal mapping")
    closing = re.search(r"^}", text[header.end() :], flags=re.MULTILINE)
    if closing is None:
        raise ReleaseRecordError(f"cannot find the end of the {name} literal mapping")
    return header.end() + closing.start()


def _mapping_text(text: str, name: str) -> str:
    header = re.search(
        rf"^{re.escape(name)}: [^\n=]+ = \{{\n", text, flags=re.MULTILINE
    )
    if header is None:
        raise ReleaseRecordError(f"cannot find the {name} literal mapping")
    return text[header.start() : _mapping_end(text, name) + 1]


def _insert_mapping_entry(text: str, name: str, entry: str) -> str:
    if not entry.startswith("    ") or not entry.endswith("\n"):
        raise ReleaseRecordError(f"invalid rendered entry for {name}")
    at = _mapping_end(text, name)
    return text[:at] + entry + text[at:]


def _released_tags(text: str) -> dict[str, tuple[str, str, dict[str, str]]]:
    """Parse the literal release map whose values contain only immutable data."""
    tree = ast.parse(_mapping_text(text, "RELEASED_TAGS"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "RELEASED_TAGS"
    )
    if assignment.value is None:  # pragma: no cover - annotated assignment invariant
        raise ReleaseRecordError("RELEASED_TAGS has no literal value")
    parsed = ast.literal_eval(assignment.value)
    if not isinstance(parsed, dict):  # pragma: no cover - literal-map invariant
        raise ReleaseRecordError("RELEASED_TAGS is not a literal dictionary")
    return cast(dict[str, tuple[str, str, dict[str, str]]], parsed)


def _retire_unreleased(
    text: str,
    distribution: str,
    migrations: set[str],
    *,
    require_all: bool,
) -> tuple[str, bool]:
    """Move newly immutable filenames out of one lineage's editable set.

    ``require_all`` is true before a new tag row is added: every newly released
    migration must have been declared editable, or the release record is hiding
    a guard failure.  It is false when repairing a partial record that already
    contains the tag row; in that state an empty editable set is already the
    desired result, while any surviving intersection still needs removal.
    """
    if not migrations:
        return text, False

    name = re.escape(distribution)
    mapping = _mapping_text(text, "UNRELEASED")
    row = re.search(
        rf'^    "{name}": frozenset\(([^\n]*)\),$',
        mapping,
        flags=re.MULTILINE,
    )
    if row is None:
        raise ReleaseRecordError(
            f"{distribution} has released migrations but no UNRELEASED row"
        )

    payload = row.group(1).strip()
    current: set[str]
    if not payload:
        current = set()
    else:
        parsed = ast.literal_eval(payload)
        if not isinstance(parsed, set) or not all(
            isinstance(item, str) for item in parsed
        ):
            raise ReleaseRecordError(
                f"{distribution}: UNRELEASED must be a literal set of filenames"
            )
        current = parsed

    missing = migrations - current
    if missing and require_all:
        raise ReleaseRecordError(
            f"{distribution}: newly released migration(s) were not declared in "
            f"UNRELEASED: {sorted(missing)}"
        )

    remaining = current - migrations
    if remaining == current:
        return text, False
    if remaining:
        values = ", ".join(repr(item) for item in sorted(remaining))
        replacement = f'    "{distribution}": frozenset({{{values}}}),'
    else:
        replacement = f'    "{distribution}": frozenset(),'

    absolute_start = text.index(mapping) + row.start()
    absolute_end = text.index(mapping) + row.end()
    return text[:absolute_start] + replacement + text[absolute_end:], True


def _migration_prefix(digests: dict[str, str]) -> str:
    prefixes: set[str] = set()
    for filename, digest in digests.items():
        match = re.fullmatch(r"([a-z][a-z0-9]*)_\d{4}_[a-z0-9_]+\.py", filename)
        if match is None:
            raise ReleaseRecordError(
                f"cannot derive a migration prefix from released file {filename!r}"
            )
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ReleaseRecordError(
                f"released migration {filename!r} has a non-sha256 digest"
            )
        prefixes.add(match.group(1))
    if len(prefixes) != 1:
        raise ReleaseRecordError(
            "first-release enrolment requires exactly one migration prefix; "
            f"found {sorted(prefixes)}"
        )
    return next(iter(prefixes))


def _first_release_entries(
    *,
    distribution: str,
    tag: str,
    commit: str,
    digests: dict[str, str],
    package_dir: str | None,
    import_name: str | None,
) -> dict[str, str]:
    """Render deterministic enrolment rows for a distribution's first tag."""
    if package_dir is None or import_name is None:
        raise ReleaseRecordError(
            f"{distribution} has no RELEASED_TAGS anchor; package_dir and "
            "import_name are required for first-release enrolment"
        )
    expected_package_dir = f"packages/{distribution}"
    expected_import_name = distribution.replace("-", "_")
    if package_dir != expected_package_dir or import_name != expected_import_name:
        raise ReleaseRecordError(
            f"first-release identity mismatch for {distribution}: expected "
            f"{expected_package_dir} / {expected_import_name}, got "
            f"{package_dir} / {import_name}"
        )
    prefix = _migration_prefix(digests)
    versions_path = f"{package_dir}/src/{import_name}/migrations/versions"
    # Three tiers, because the generated file is format-checked in CI and a
    # record PR that fails `ruff format --check` blocks a release that has
    # ALREADY published its wheel. Ruff joins whatever fits, so emitting the
    # widest split unconditionally is not "safe" — it is wrong for any name
    # short enough to fit the middle form, which is what `dotmac-billing` hit.
    split_operand = (
        f'        REPO_ROOT / "{package_dir}" / "src/{import_name}/migrations/versions"'
    )
    compact_path = f'    "{distribution}": (REPO_ROOT / "{versions_path}"),\n'
    if len(compact_path.rstrip("\n")) <= 88:
        distribution_entry = compact_path
    elif len(split_operand) <= 88:
        distribution_entry = f'    "{distribution}": (\n{split_operand}\n    ),\n'
    else:
        distribution_entry = (
            f'    "{distribution}": (\n'
            "        REPO_ROOT\n"
            f'        / "{package_dir}"\n'
            f'        / "src/{import_name}/migrations/versions"\n'
            "    ),\n"
        )
    return {
        "DISTRIBUTIONS": distribution_entry,
        "LINEAGE_GLOBS": f'    "{distribution}": "{prefix}_*.py",\n',
        "TAG_PREFIXES": f'    "{distribution}": "{distribution}-v",\n',
        "RELEASED_TAGS": (
            f"    # ── {distribution} ──\n"
            + _rendered_entry(tag, distribution, commit, digests)
        ),
        "UNRELEASED": f'    "{distribution}": frozenset(),\n',
    }


def add_released_tag(
    text: str,
    tag: str,
    distribution: str,
    commit: str,
    digests: dict[str, str],
    *,
    package_dir: str | None = None,
    import_name: str | None = None,
) -> tuple[str, bool]:
    """Insert one release entry, enrolling a first distribution when needed."""
    releases = _released_tags(text)
    existing = releases.get(tag)
    if existing is not None and existing != (distribution, commit, digests):
        raise ReleaseRecordError(
            f"{tag} is already recorded with different coordinates or digests"
        )
    name = re.escape(distribution)
    anchor = re.search(rf'\n    "{name}-v[^"]+": \(', text)
    if anchor is None:
        entries = _first_release_entries(
            distribution=distribution,
            tag=tag,
            commit=commit,
            digests=digests,
            package_dir=package_dir,
            import_name=import_name,
        )
        for mapping, entry in entries.items():
            if f'"{distribution}":' in _mapping_text(text, mapping):
                raise ReleaseRecordError(
                    f"{distribution} already appears in {mapping} but has no "
                    "RELEASED_TAGS anchor; the migration guard is inconsistent"
                )
            text = _insert_mapping_entry(text, mapping, entry)
        return text, True

    previously_released = {
        filename
        for recorded_tag, (owner, _, files) in releases.items()
        if recorded_tag != tag and owner == distribution
        for filename in files
    }
    newly_released = set(digests) - previously_released
    text, retired = _retire_unreleased(
        text,
        distribution,
        newly_released,
        # Once the tag row exists this may be a repair after the editable set
        # was already cleared. Idempotence must accept that completed half.
        require_all=existing is None,
    )
    if existing is not None:
        return text, retired

    anchor = re.search(rf'\n    "{name}-v[^"]+": \(', text)
    if anchor is None:  # pragma: no cover - the mapping edit cannot remove it
        raise ReleaseRecordError(f"lost the RELEASED_TAGS anchor for {distribution}")
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

    ledger_text = LEDGER.read_text(encoding="utf-8")
    new_ledger = ledger_text
    module_text = RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    new_module = module_text
    changed: list[str] = []

    row = json.loads(ledger_text)["unpublished"].get(distribution)
    if row is not None:
        declared = row.get("declared")
        if declared != version:
            raise ReleaseRecordError(
                f"the ledger excuses {distribution} at {declared!r} but "
                f"{version!r} was published; the row describes a different "
                "version and removing it would erase a live exemption"
            )
        new_ledger, removed = remove_ledger_row(ledger_text, distribution)
        if removed:
            json.loads(new_ledger)  # never leave the ledger unparseable
            changed.append(f"removed the {distribution} publication-ledger row")

    if package_dir and import_name:
        digests = migration_digests(tag, package_dir, import_name)
        if digests:
            new_module, added = add_released_tag(
                module_text,
                tag,
                distribution,
                commit,
                digests,
                package_dir=package_dir,
                import_name=import_name,
            )
            if added:
                changed.append(
                    f"recorded {tag} in RELEASED_TAGS "
                    f"({len(digests)} migration digest(s) read from the tag)"
                )

    # Validate every premise before either file changes. A refused first
    # enrolment must not leave a half-repair that hides the stale ledger row.
    if new_ledger != ledger_text:
        LEDGER.write_text(new_ledger, encoding="utf-8")
    if new_module != module_text:
        RELEASED_TAGS_MODULE.write_text(new_module, encoding="utf-8")

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
