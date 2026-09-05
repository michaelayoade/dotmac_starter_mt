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
enrolment it records EVERY published tag already visible in the repository,
then enrols that lineage in every migration-history map. That distinction is
load-bearing: a module can be published before this guard monitors it, and
recording only the newest tag would immediately fail the bidirectional tag
oracle. The history is derived only from peeled tags, package identity and the
migration prefix carried by the reviewed release inputs. It refuses when the
requested tag does not exist or the declared version and tag disagree. A
A missing ledger row is a no-op so a partial repair can converge.

It also REFUSES rather than skipping when it cannot tell where a release's
migrations are. ``--package-dir`` and ``--no-lineage`` are mutually
exclusive and one is required, and a supplied path is checked for shape,
for naming this distribution, for being readable at the tag, and for
carrying this distribution's lineage -- four separate refusals, because
``git ls-tree`` exits 0 and prints nothing for a path that does not exist,
so a typo and 'no migrations' were previously the same empty dict.

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
import importlib.util
import json
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER = REPO_ROOT / "docs" / "inventories" / "declared-publication-baseline.json"
RELEASED_TAGS_MODULE = (
    REPO_ROOT / "tests" / "architecture" / "test_released_migrations.py"
)
SOURCE_DRIFT_BASELINE = (
    REPO_ROOT / "tests" / "architecture" / "published_source_drift_baseline.json"
)
KERNEL_AUTHORIZATION = REPO_ROOT / ".github" / "kernel-release-authorization.json"
KERNEL_VERIFICATIONS = (
    REPO_ROOT / "docs" / "inventories" / "kernel-release-verifications"
)

#: Where a distribution's migrations live inside its package, if it has any.
_MIGRATIONS = "src/{import_name}/migrations/versions"


class ReleaseRecordError(RuntimeError):
    """The record cannot be written, and guessing is not an option."""


def _local_script(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_release_record_{name}", path)
    if spec is None or spec.loader is None:
        raise ReleaseRecordError(f"cannot load the local {name} script")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves a class's annotations through its registered module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
    """The exact peeled commit a tag points at."""
    return _git("rev-parse", f"{tag}^{{commit}}").strip()


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


def published_release_history(
    distribution: str, package_dir: str, import_name: str
) -> dict[str, tuple[str, dict[str, str]]]:
    """Every published tag for ``distribution``, newest first.

    A first enrolment may be delayed until the second or later release. The
    local tag set is the same fail-closed oracle consumed by
    ``test_released_migrations`` (CI checks out full history), so discovering
    the complete prefix here is stronger than pretending the tag passed to the
    current release is the distribution's first.
    """
    tags = _git(
        "tag", "--list", "--sort=-version:refname", f"{distribution}-v*"
    ).splitlines()
    if not tags:
        raise ReleaseRecordError(
            f"{distribution} has no published tags available for first enrolment"
        )
    return {
        historical_tag: (
            tag_commit(historical_tag),
            migration_digests(historical_tag, package_dir, import_name),
        )
        for historical_tag in tags
    }


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
        rf'^    "{name}": frozenset\((.*?)\),$',
        mapping,
        flags=re.MULTILINE | re.DOTALL,
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
    historical_releases: dict[str, tuple[str, dict[str, str]]] | None = None,
) -> dict[str, str]:
    """Render deterministic rows for a distribution's first enrolment."""
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
    releases = historical_releases or {tag: (commit, digests)}
    if releases.get(tag) != (commit, digests):
        raise ReleaseRecordError(
            f"first enrolment history does not contain requested tag {tag} "
            "with its resolved coordinates and digests"
        )
    expected_tag_prefix = f"{distribution}-v"
    unexpected = sorted(
        historical_tag
        for historical_tag in releases
        if not historical_tag.startswith(expected_tag_prefix)
    )
    if unexpected:
        raise ReleaseRecordError(
            f"first enrolment history for {distribution} contains foreign tags: "
            f"{unexpected}"
        )
    all_digests = {
        filename: digest
        for _, release_digests in releases.values()
        for filename, digest in release_digests.items()
    }
    prefix = _migration_prefix(all_digests)
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
            + "".join(
                _rendered_entry(
                    historical_tag,
                    distribution,
                    historical_commit,
                    historical_digests,
                )
                for historical_tag, (
                    historical_commit,
                    historical_digests,
                ) in releases.items()
            )
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
    historical_releases: dict[str, tuple[str, dict[str, str]]] | None = None,
) -> tuple[str, bool]:
    """Reconcile one owner's published history, enrolling it when needed."""
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
            historical_releases=historical_releases,
        )
        for mapping, entry in entries.items():
            if f'"{distribution}":' in _mapping_text(text, mapping):
                raise ReleaseRecordError(
                    f"{distribution} already appears in {mapping} but has no "
                    "RELEASED_TAGS anchor; the migration guard is inconsistent"
                )
            text = _insert_mapping_entry(text, mapping, entry)
        return text, True

    if historical_releases is not None:
        recorded_owner_tags = {
            recorded_tag
            for recorded_tag, (owner, _, _) in releases.items()
            if owner == distribution
        }
        absent_from_oracle = sorted(recorded_owner_tags - set(historical_releases))
        if absent_from_oracle:
            raise ReleaseRecordError(
                f"{distribution} records tags absent from the published oracle: "
                f"{absent_from_oracle}"
            )
        for historical_tag, (
            historical_commit,
            historical_digests,
        ) in historical_releases.items():
            recorded = releases.get(historical_tag)
            expected = (distribution, historical_commit, historical_digests)
            if recorded is not None and recorded != expected:
                raise ReleaseRecordError(
                    f"{historical_tag} is already recorded with different "
                    "coordinates or digests"
                )

        missing = [
            (historical_tag, historical_commit, historical_digests)
            for historical_tag, (
                historical_commit,
                historical_digests,
            ) in historical_releases.items()
            if historical_tag not in releases
        ]
        previously_released = {
            filename
            for owner, _, files in releases.values()
            if owner == distribution
            for filename in files
        }
        text, retired_current = _retire_unreleased(
            text,
            distribution,
            set(digests) - previously_released,
            require_all=existing is None,
        )
        repair_files = {
            filename
            for _, _, historical_digests in missing
            for filename in historical_digests
        }
        text, retired_history = _retire_unreleased(
            text,
            distribution,
            repair_files,
            require_all=False,
        )
        if not missing:
            return text, retired_current or retired_history

        anchor = re.search(rf'\n    "{name}-v[^"]+": \(', text)
        if anchor is None:  # pragma: no cover - the mapping edit cannot remove it
            raise ReleaseRecordError(
                f"lost the RELEASED_TAGS anchor for {distribution}"
            )
        at = anchor.start() + 1
        rendered_entries = "".join(
            _rendered_entry(
                historical_tag, distribution, historical_commit, historical_digests
            )
            for historical_tag, historical_commit, historical_digests in missing
        )
        return text[:at] + rendered_entries + text[at:], True

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


def require_kernel_evidence(
    *, version: str, tag: str, commit: str
) -> dict[str, object]:
    path = KERNEL_VERIFICATIONS / f"{version}.json"
    if not path.is_file():
        raise ReleaseRecordError(
            f"kernel {version} has no durable independent-verification record"
        )
    payload = path.read_bytes()
    record = json.loads(payload)
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "schema",
            "version",
            "tag",
            "tag_object",
            "tag_disposition",
            "source_sha",
            "authorization",
            "publisher",
            "verifier",
            "verification_receipt_sha256",
            "verification_receipt_artifact",
            "tag_decision_receipt_sha256",
            "tag_decision_receipt_artifact",
            "registry",
            "files",
        }
        or record.get("schema") != "KernelReleaseEvidence.v1"
        or record.get("version") != version
        or record.get("tag") != tag
        or record.get("source_sha") != commit
        or (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        != payload
    ):
        raise ReleaseRecordError("durable kernel verification record differs")
    evidence = _local_script("write_kernel_release_verification_record")
    try:
        evidence.validate_persisted_record(record, version=version)
    except evidence.RecordRefused as failure:
        raise ReleaseRecordError(
            f"durable kernel verification record is invalid: {failure}"
        ) from failure
    tag_type = _git("cat-file", "-t", tag).strip()
    tag_object = _git("rev-parse", tag).strip()
    if tag_type != "tag" or record.get("tag_object") != tag_object:
        raise ReleaseRecordError(
            "durable kernel record does not bind the annotated tag"
        )
    return record


_PACKAGE_DIR_SHAPE = re.compile(r"packages/(?P<name>[a-z0-9][a-z0-9.-]*)\Z")


def anchored_distributions(text: str) -> set[str]:
    """Every distribution with a lineage, read from the map that already says so.

    ``RELEASED_TAGS`` anchors one entry per distribution whose migrations are
    immutable, so "does this distribution carry a lineage?" is already answered
    in a file this writer parses. Asking it here means clause 1 needs no second
    declaration to drift out of step with the first.
    """
    return {owner for owner, _, _ in _released_tags(text).values()}


def _lineage_glob(text: str, distribution: str) -> str | None:
    tree = ast.parse(_mapping_text(text, "LINEAGE_GLOBS"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "LINEAGE_GLOBS"
    )
    if assignment.value is None:  # pragma: no cover - annotated-assignment invariant
        raise ReleaseRecordError("LINEAGE_GLOBS has no literal value")
    parsed = ast.literal_eval(assignment.value)
    if not isinstance(parsed, dict):  # pragma: no cover - literal-map invariant
        raise ReleaseRecordError("LINEAGE_GLOBS is not a literal dictionary")
    value = parsed.get(distribution)
    return value if isinstance(value, str) else None


def resolve_lineage_inputs(
    *,
    distribution: str,
    tag: str,
    package_dir: str | None,
    import_name: str | None,
    no_lineage: bool,
    module_text: str,
) -> tuple[str | None, str | None, dict[str, str]]:
    """Answer "where is this release's lineage?" or refuse, saying which input is wrong.

    ONE REFUSAL PER WAY OF BEING WRONG, deliberately. `git ls-tree` exits 0 and
    prints nothing for a path that does not exist, so a typo'd `--package-dir`
    and a distribution with no migrations produce the SAME value — an empty dict
    from a successful call. That is why `if digests:` reported success at a101
    and again at a102, and it is why one generic failure would not be enough: a
    single message cannot tell the operator whether to fix the flag, fix the
    import name, or stop passing the flag at all.
    """
    anchored = anchored_distributions(module_text)
    carries_lineage = distribution in anchored

    if package_dir is not None and no_lineage:
        raise ReleaseRecordError(
            f"contradictory lineage declaration for {distribution}: "
            f"--package-dir {package_dir!r} and --no-lineage were both given; "
            "exactly one is true"
        )
    if package_dir is None:
        if no_lineage:
            if carries_lineage:
                raise ReleaseRecordError(
                    f"MISMATCHED lineage declaration: {distribution} was declared "
                    "--no-lineage, but it is anchored in RELEASED_TAGS and its "
                    "released migrations are immutable. Pass --package-dir "
                    f"packages/{distribution} instead"
                )
            return None, None, {}
        raise ReleaseRecordError(
            f"MISSING lineage declaration for {distribution}: pass --package-dir "
            "for a distribution with migrations, or --no-lineage for one without. "
            "Omission used to mean 'not applicable' and silently skipped the tag "
            "oracle, which left main red after a101 and again after a102"
        )

    shape = _PACKAGE_DIR_SHAPE.fullmatch(package_dir)
    if shape is None:
        raise ReleaseRecordError(
            f"INVALID --package-dir {package_dir!r} for {distribution}: expected "
            f"the form packages/<distribution>, e.g. packages/{distribution}"
        )
    if shape.group("name") != distribution:
        raise ReleaseRecordError(
            f"MISMATCHED --package-dir {package_dir!r} for {distribution}: it "
            f"names {shape.group('name')!r}. A well-formed path to the wrong "
            "package reads no migrations and would have skipped in silence"
        )

    # The caller may override the import name; if it does, the readability
    # check below runs against the path the override actually resolves to.
    # Deriving here and letting a later override recompute the digests would
    # reinstate the silent skip through a second door.
    resolved_import = import_name or distribution.replace("-", "_")
    digests = migration_digests(tag, package_dir, resolved_import)
    if not digests:
        if carries_lineage:
            raise ReleaseRecordError(
                f"UNREADABLE lineage for {distribution}: no migration is readable "
                f"under {package_dir}/"
                f"{_MIGRATIONS.format(import_name=resolved_import)} "
                f"at {tag}, yet {distribution} is anchored in RELEASED_TAGS. git "
                "ls-tree exits 0 on a path that does not exist, so this is a "
                "refusal rather than an empty result"
            )
        raise ReleaseRecordError(
            f"UNREADABLE lineage for {distribution}: --package-dir {package_dir!r} "
            f"carries no migration at {tag}. If this distribution has no lineage, "
            "say so with --no-lineage rather than passing a path that reads empty"
        )

    glob = _lineage_glob(module_text, distribution)
    if glob is not None and not any(fnmatch(name, glob) for name in digests):
        raise ReleaseRecordError(
            f"MISMATCHED lineage for {distribution}: {sorted(digests)[:3]} under "
            f"{package_dir} match none of its recorded lineage glob {glob!r}; the "
            "path is readable but the migrations are not this distribution's"
        )
    return package_dir, resolved_import, digests


def write_record(
    *,
    distribution: str,
    version: str,
    tag: str,
    package_dir: str | None,
    import_name: str | None,
    no_lineage: bool = False,
) -> list[str]:
    """Apply both halves of the record. Returns what changed, for the caller."""
    expected_tag = f"{distribution}-v{version}"
    if tag != expected_tag:
        raise ReleaseRecordError(
            f"tag/version identity mismatch: expected {expected_tag!r}, got {tag!r}"
        )
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
    baseline_text = SOURCE_DRIFT_BASELINE.read_text(encoding="utf-8")
    new_baseline = baseline_text
    authorization_text = KERNEL_AUTHORIZATION.read_text(encoding="utf-8")
    new_authorization = authorization_text
    # Validate the caller's lineage inputs BEFORE reading any state. A wrong
    # --package-dir is the caller's defect; masking it behind a ledger or
    # authorization refusal sends the operator to repair the wrong thing.
    resolved_dir, resolved_import, digests = resolve_lineage_inputs(
        distribution=distribution,
        tag=tag,
        package_dir=package_dir,
        import_name=import_name,
        no_lineage=no_lineage,
        module_text=module_text,
    )
    package_dir, import_name = resolved_dir, resolved_import
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
        if digests:
            historical_releases = published_release_history(
                distribution, package_dir, import_name
            )
            if historical_releases.get(tag) != (commit, digests):
                raise ReleaseRecordError(
                    f"published history for {distribution} does not contain "
                    f"requested tag {tag} with its resolved coordinates and "
                    "digests"
                )
            new_module, added = add_released_tag(
                module_text,
                tag,
                distribution,
                commit,
                digests,
                package_dir=package_dir,
                import_name=import_name,
                historical_releases=historical_releases,
            )
            if added:
                changed.append(
                    f"reconciled {distribution} in RELEASED_TAGS with "
                    f"{len(historical_releases)} published tag(s) read from "
                    "the tag oracle"
                )

    if distribution == "dotmac-kernel":
        require_kernel_evidence(version=version, tag=tag, commit=commit)
        authorization = _local_script("kernel_release_authorization")
        source_drift = _local_script("published_source_drift")
        active = authorization.load_authorization()
        if active is None:
            if row is not None:
                raise ReleaseRecordError(
                    "kernel authorization is consumed but publication ledger remains"
                )
            rendered_baseline = source_drift.render_baseline()
            if rendered_baseline != baseline_text:
                raise ReleaseRecordError(
                    "kernel authorization is consumed but source census differs"
                )
        else:
            try:
                new_authorization = authorization.consume_for_release(
                    version=version, tag=tag, commit=commit
                )
            except authorization.KernelReleaseAuthorizationError as failure:
                raise ReleaseRecordError(
                    f"kernel release authorization cannot be consumed: {failure}"
                ) from failure
            json.loads(new_authorization)
            new_baseline = source_drift.render_baseline()
            rendered = json.loads(new_baseline)
            if (
                rendered["released_total"]
                <= json.loads(baseline_text)["released_total"]
            ):
                raise ReleaseRecordError(
                    "kernel tag did not increase the released-source census"
                )
            changed.append("consumed the kernel release authorization")
            changed.append(
                "recomputed the published-source census from the tagged tree "
                f"({rendered['released_total']} released, "
                f"{rendered['drifted_total']} drifted)"
            )

    # Validate every premise before any file changes. A refused first
    # enrolment must not leave a half-repair that hides the stale ledger row.
    if new_ledger != ledger_text:
        LEDGER.write_text(new_ledger, encoding="utf-8")
    if new_module != module_text:
        RELEASED_TAGS_MODULE.write_text(new_module, encoding="utf-8")
    if new_baseline != baseline_text:
        SOURCE_DRIFT_BASELINE.write_text(new_baseline, encoding="utf-8")
    if new_authorization != authorization_text:
        KERNEL_AUTHORIZATION.write_text(new_authorization, encoding="utf-8")

    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    # EXACTLY ONE, and neither has a default. Omission used to mean "this
    # distribution has no migration lineage" -- and also "the caller forgot",
    # because nothing could tell them apart. argparse refuses the empty case
    # here so no release lane can express it again.
    lineage = parser.add_mutually_exclusive_group(required=True)
    lineage.add_argument(
        "--package-dir",
        help="package directory of a distribution WITH migrations, e.g. "
        "packages/dotmac-integration",
    )
    lineage.add_argument(
        "--no-lineage",
        action="store_true",
        help="declare that this distribution has NO migration lineage. An "
        "explicit claim, checked against RELEASED_TAGS -- not an omission",
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
            no_lineage=args.no_lineage,
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
