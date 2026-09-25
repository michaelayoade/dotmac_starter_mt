"""``ReleaseAuthority.v1``: the complete executable surface of a module release.

A module release is not just the two dispatched workflow files. It is every
local script, shell fragment and composite action they can actually execute:
``release-module.yml`` and ``recover-module-release.yml`` themselves, the
local scripts their ``run:`` steps invoke, the local composite actions their
``uses: ./...`` steps mount (recursively, through those actions' own ``run:``
steps), and every local Python module those scripts import or name by path —
recursively again. ``.github/release-modules.json`` is the policy input every
release resolves against, so it is always part of the authority regardless of
whether any run body happens to name it literally.

This module answers exactly one question, twice: "what is the surface" and
"what is its digest". Both answers are DERIVED, never hand-maintained — the
closure in :func:`derive_surface` walks the real files, and a change to any
one of them changes the digest computed by :func:`authority_digest`.

## Closure rules

Starting from the two release workflow files:

1. From every ``run:`` step body (in a workflow job step, or in a local
   composite action's own ``runs.steps[].run``), collect every
   ``scripts/<path>`` token and every EXISTING ``.github/<path>`` token found
   by regex. A token that does not resolve to a real file is silently
   ignored here — a ``run:`` body may reference a shell variable or an
   illustrative path (``.github/bootstrap/poetry-requirements-py${py}.txt``),
   and this pass is a discovery heuristic, not the authority on what must
   exist. (Existence is enforced downstream, in step 4, for references that
   really do claim to name a surface file.)
2. For every ``uses: ./<dir>`` step, add ``<dir>/action.yml`` (or
   ``action.yaml`` if that is what exists) and recurse into its own
   ``run:`` bodies per rule 1.
3. For every ``.sh`` file already in the surface, collect
   ``scripts/<path>`` tokens from its full text (same existence-filtered
   regex as rule 1) and add any that resolve to real files.
4. For every ``.py`` file already in the surface, parse it with ``ast`` and
   walk every ``Import``/``ImportFrom`` node at any depth:
   - ``import X`` / ``from X import ...`` where ``scripts/X.py`` exists is a
     local reference: add ``scripts/X.py`` and recurse.
   - any other TOP-LEVEL module name (the first dotted component) that is
     not in ``sys.stdlib_module_names`` is an EXTERNAL import, recorded by
     that top-level name (e.g. ``dotmac_kernel`` from
     ``from dotmac_kernel.modules import ModuleRegistry``).
   Additionally, walk every string constant in the file for
   ``scripts/<path>`` substrings that resolve to real files, and add+recurse
   into those too (this is what would catch a script that names a sibling
   script only inside an f-string or a ``subprocess.run([..., "scripts/
   foo.py", ...])`` call — nothing in this closure currently needs it, but a
   literal path reference is exactly as load-bearing as an import).
5. ``.github/release-modules.json`` and ``scripts/release_authority.py``
   itself are always members of the surface, regardless of whether anything
   references them (the JSON is the policy input every release resolves
   against; this file is the authority's own logic). The authority's own
   ledger file, ``docs/inventories/release-authority.json``, is deliberately
   EXCLUDED — it holds the digest this module computes, and including it
   would make the digest depend on itself.
6. A reference collected by rule 1, 2, 3 or the import half of rule 4 that
   does NOT resolve to a real file raises :class:`ReleaseAuthorityError` — a
   dangling reference is a defect in the surface, not something to skip.
   (Rule 1's own regex pass is existence-filtered before it ever becomes a
   "reference" in this sense; once a token has passed that filter and been
   queued, at read time it must still be there, or the read itself raises.)

**Implementation note on rule 1/2's scope.** Without a YAML parser (this
module is stdlib-only and does not import ``yaml``), the regex pass over a
workflow or composite-action file runs over its FULL text rather than being
restricted to parsed ``run:``/``uses:`` fields. In this repository's actual
two workflows and two local actions, every existing-file match happens to sit
inside a `run:` step body or a genuine `uses: ./...` line — a handful of
prose comments also mention `scripts/release_module.py` and
`.github/release-modules.json`, but both are already surface members for
other reasons, so the simplification changes nothing here. A future workflow
that merely mentions an unrelated real file in a comment would incorrectly
pull it into the surface; this is a known, documented gap, not a silent one.

Stdlib only, deliberately — this must run before anything else in the
release toolchain and without any dependency of its own.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCHEMA = "ReleaseAuthority.v1"
LEDGER_SCHEMA = "ReleaseAuthorityLedger.v1"

LEDGER_PATH = "docs/inventories/release-authority.json"
AUTHORITY_MODULE_PATH = "scripts/release_authority.py"
POLICY_PATH = ".github/release-modules.json"

ROOT_WORKFLOWS: tuple[str, ...] = (
    ".github/workflows/release-module.yml",
    ".github/workflows/recover-module-release.yml",
)

_SCRIPTS_TOKEN = re.compile(r"scripts/[A-Za-z0-9_./-]+\.(?:py|sh)")
_GITHUB_TOKEN = re.compile(r"\.github/[A-Za-z0-9_./-]+\.(?:yml|yaml|json)")
_USES_LOCAL = re.compile(r"uses:\s*(\./[A-Za-z0-9_./-]+)")
_DIGEST_PREFIX = "sha256:"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ReleaseAuthorityError(Exception):
    """A declared or discovered surface reference could not be resolved."""


Reader = Callable[[str], "str | None"]
BytesReader = Callable[[str], "bytes | None"]


def worktree_reader(root: Path) -> Reader:
    """Read a repo-relative path's text from the given working tree."""

    def read(path: str) -> str | None:
        candidate = root / path
        if not candidate.is_file():
            return None
        return candidate.read_text(encoding="utf-8")

    return read


def _validate_sha(sha: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReleaseAuthorityError(f"not a 40-hex commit SHA: {sha!r}")


def commit_reader(root: Path, sha: str) -> Reader:
    """Read a repo-relative path's text as it existed at ``sha``."""
    _validate_sha(sha)

    def read(path: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{sha}:{path}"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        # Strict decode, deliberately (unlike a lossy `errors="replace"`):
        # `authority_digest` re-encodes this text to hash it, and only a
        # strict UTF-8 round-trip is guaranteed to reproduce the original
        # bytes exactly. Every surface file is source text, so a decode
        # failure here would itself be a defect worth raising loudly.
        return result.stdout.decode("utf-8")

    return read


def _local_action_dirs(text: str) -> list[str]:
    return [match.group(1).removeprefix("./") for match in _USES_LOCAL.finditer(text)]


def _existing_tokens(pattern: re.Pattern[str], text: str, read: Reader) -> list[str]:
    found: list[str] = []
    for match in pattern.finditer(text):
        token = match.group(0)
        if read(token) is not None:
            found.append(token)
    return found


def _action_manifest_path(action_dir: str, read: Reader) -> str:
    for name in ("action.yml", "action.yaml"):
        candidate = f"{action_dir}/{name}"
        if read(candidate) is not None:
            return candidate
    raise ReleaseAuthorityError(
        f"local action {action_dir!r} has neither action.yml nor action.yaml"
    )


def _require(path: str, read: Reader) -> str:
    text = read(path)
    if text is None:
        raise ReleaseAuthorityError(f"referenced surface file does not exist: {path}")
    return text


def derive_surface(read: Reader) -> tuple[list[str], list[str]]:
    """Walk the release closure starting at the two release workflows.

    Returns ``(sorted repo-relative file paths, sorted external import names)``.
    """
    files: set[str] = {POLICY_PATH, AUTHORITY_MODULE_PATH}
    external: set[str] = set()
    queue: list[str] = list(ROOT_WORKFLOWS)
    queued: set[str] = set(queue) | files

    # The two roots must exist and are always in the surface.
    for root in ROOT_WORKFLOWS:
        _require(root, read)
        files.add(root)

    # Ensure the always-included files actually exist too.
    _require(POLICY_PATH, read)
    _require(AUTHORITY_MODULE_PATH, read)

    def enqueue(path: str) -> None:
        if path not in queued:
            queued.add(path)
            queue.append(path)
        files.add(path)

    while queue:
        current = queue.pop()
        files.add(current)
        text = _require(current, read)

        if current.endswith((".yml", ".yaml")):
            # Local composite actions this file's `run:`/`uses:` steps reach.
            for action_dir in _local_action_dirs(text):
                manifest = _action_manifest_path(action_dir, read)
                enqueue(manifest)
            for token in _existing_tokens(_SCRIPTS_TOKEN, text, read):
                enqueue(token)
            for token in _existing_tokens(_GITHUB_TOKEN, text, read):
                enqueue(token)

        elif current.endswith(".sh"):
            for token in _existing_tokens(_SCRIPTS_TOKEN, text, read):
                enqueue(token)

        elif current.endswith(".py"):
            tree = ast.parse(text, filename=current)
            top_level_names: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_level_names.append(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module is not None:
                        top_level_names.append(node.module.split(".")[0])
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for token in _existing_tokens(_SCRIPTS_TOKEN, node.value, read):
                        enqueue(token)

            for top in top_level_names:
                local_candidate = f"scripts/{top}.py"
                if read(local_candidate) is not None:
                    enqueue(local_candidate)
                elif top not in sys.stdlib_module_names:
                    external.add(top)

    return sorted(files), sorted(external)


def authority_digest(files: list[str], external: list[str], read: Reader) -> str:
    """The canonical ``sha256:...`` digest of the given declared surface.

    ``files`` and ``external`` are hashed exactly as given (already expected
    to be sorted by the caller for a stable digest, though this function
    sorts again defensively). Every file's BYTES are read fresh via ``read``
    — the digest is never computed from cached content.
    """
    bytes_read = _reader_as_bytes(read)
    entries = []
    for path in sorted(files):
        data = bytes_read(path)
        if data is None:
            raise ReleaseAuthorityError(f"cannot digest missing file: {path}")
        entries.append({"path": path, "sha256": hashlib.sha256(data).hexdigest()})
    payload = {
        "schema": SCHEMA,
        "files": entries,
        "external_imports": sorted(external),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _DIGEST_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reader_as_bytes(read: Reader) -> BytesReader:
    """Adapt a text ``Reader`` into a bytes reader for digesting.

    Text readers in this module always decode as UTF-8; re-encoding gives the
    original bytes for any surface file, which is exclusively source text.
    """

    def read_bytes(path: str) -> bytes | None:
        text = read(path)
        if text is None:
            return None
        return text.encode("utf-8")

    return read_bytes


def reconstruct_at(root: Path, sha: str, files: list[str], external: list[str]) -> str:
    """The digest of exactly ``files``/``external`` as they exist at ``sha``."""
    read = commit_reader(root, sha)
    return authority_digest(files, external, read)


def parse_authority_ledger(text: str) -> dict:
    """Strictly parse the ``ReleaseAuthorityLedger.v1`` document.

    Refuses: duplicate JSON keys, an unexpected key set, a malformed digest
    (anywhere in ``active.digest`` or ``history``), ``active.digest`` absent
    from ``history``, and a duplicate entry within ``history``.
    """
    seen_duplicate: list[str] = []

    def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                seen_duplicate.append(key)
            result[key] = value
        return result

    data = json.loads(text, object_pairs_hook=_no_duplicates)
    if seen_duplicate:
        raise ReleaseAuthorityError(
            "duplicate key(s) in release authority ledger: "
            f"{sorted(set(seen_duplicate))}"
        )

    allowed_top = {"$comment", "schema", "active", "history"}
    extra = set(data) - allowed_top
    if extra:
        raise ReleaseAuthorityError(f"unexpected top-level key(s): {sorted(extra)}")
    missing = {"schema", "active", "history"} - set(data)
    if missing:
        raise ReleaseAuthorityError(f"missing top-level key(s): {sorted(missing)}")

    if data["schema"] != LEDGER_SCHEMA:
        raise ReleaseAuthorityError(
            f"unexpected schema {data['schema']!r}, expected {LEDGER_SCHEMA!r}"
        )

    active = data["active"]
    if not isinstance(active, dict):
        raise ReleaseAuthorityError("'active' must be an object")
    allowed_active = {"digest", "files", "external_imports"}
    extra_active = set(active) - allowed_active
    if extra_active:
        raise ReleaseAuthorityError(f"unexpected active key(s): {sorted(extra_active)}")
    missing_active = allowed_active - set(active)
    if missing_active:
        raise ReleaseAuthorityError(f"missing active key(s): {sorted(missing_active)}")

    digest = active["digest"]
    if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
        raise ReleaseAuthorityError(f"malformed active.digest: {digest!r}")

    history = data["history"]
    if not isinstance(history, list) or not all(isinstance(h, str) for h in history):
        raise ReleaseAuthorityError("'history' must be a list of strings")
    for entry in history:
        if not _DIGEST_RE.match(entry):
            raise ReleaseAuthorityError(f"malformed history digest: {entry!r}")
    if len(set(history)) != len(history):
        raise ReleaseAuthorityError("'history' contains a duplicate digest")
    if digest not in history:
        raise ReleaseAuthorityError("active.digest is not present in history")

    if not isinstance(active["files"], list) or not all(
        isinstance(f, str) for f in active["files"]
    ):
        raise ReleaseAuthorityError("'active.files' must be a list of strings")
    if not isinstance(active["external_imports"], list) or not all(
        isinstance(e, str) for e in active["external_imports"]
    ):
        raise ReleaseAuthorityError(
            "'active.external_imports' must be a list of strings"
        )

    return data


def _write_ledger(
    path: Path, files: list[str], external: list[str], digest: str
) -> None:
    existing_history: list[str] = []
    if path.is_file():
        try:
            existing = parse_authority_ledger(path.read_text(encoding="utf-8"))
            existing_history = list(existing["history"])
        except (ReleaseAuthorityError, json.JSONDecodeError):
            existing_history = []

    history = list(existing_history)
    if digest not in history:
        history.append(digest)

    document = {
        "$comment": (
            "Generated by `python scripts/release_authority.py write`. Do not "
            "hand-edit `active` — regenerate it. `history` is append-only: a "
            "past digest is never removed, since historical release rows "
            "validate against it."
        ),
        "schema": LEDGER_SCHEMA,
        "active": {
            "digest": digest,
            "files": sorted(files),
            "external_imports": sorted(external),
        },
        "history": history,
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _diff_lists(expected: list[str], actual: list[str]) -> str:
    expected_set, actual_set = set(expected), set(actual)
    lines = []
    for missing in sorted(expected_set - actual_set):
        lines.append(f"  - missing: {missing}")
    for added in sorted(actual_set - expected_set):
        lines.append(f"  + unexpected: {added}")
    return "\n".join(lines)


def cmd_check(args: argparse.Namespace) -> int:
    ledger_path = REPO_ROOT / LEDGER_PATH
    if not ledger_path.is_file():
        print(f"::error::no release authority ledger at {LEDGER_PATH}", file=sys.stderr)
        return 1
    try:
        ledger = parse_authority_ledger(ledger_path.read_text(encoding="utf-8"))
    except ReleaseAuthorityError as failure:
        print(
            f"::error::release authority ledger is malformed: {failure}",
            file=sys.stderr,
        )
        return 1

    read = worktree_reader(REPO_ROOT)
    try:
        derived_files, derived_external = derive_surface(read)
    except ReleaseAuthorityError as failure:
        print(
            f"::error::could not derive the release surface: {failure}", file=sys.stderr
        )
        return 1

    active = ledger["active"]
    problems: list[str] = []

    if derived_files != active["files"]:
        problems.append("files:\n" + _diff_lists(active["files"], derived_files))
    if derived_external != active["external_imports"]:
        problems.append(
            "external_imports:\n"
            + _diff_lists(active["external_imports"], derived_external)
        )

    try:
        actual_digest = authority_digest(derived_files, derived_external, read)
    except ReleaseAuthorityError as failure:
        print(
            f"::error::could not digest the release surface: {failure}", file=sys.stderr
        )
        return 1

    if actual_digest != active["digest"]:
        problems.append(
            f"digest: ledger says {active['digest']}, worktree computes {actual_digest}"
        )

    if problems:
        print(
            "::error::release authority is out of date with the working tree:",
            file=sys.stderr,
        )
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            "::error::run `python scripts/release_authority.py write` and commit "
            f"the updated {LEDGER_PATH}",
            file=sys.stderr,
        )
        return 1

    print(f"release authority OK: {active['digest']}")
    return 0


def cmd_write(_args: argparse.Namespace) -> int:
    read = worktree_reader(REPO_ROOT)
    files, external = derive_surface(read)
    digest = authority_digest(files, external, read)
    _write_ledger(REPO_ROOT / LEDGER_PATH, files, external, digest)
    print(f"wrote {LEDGER_PATH}: {digest}")
    return 0


def cmd_digest_at(args: argparse.Namespace) -> int:
    ledger_path = REPO_ROOT / LEDGER_PATH
    ledger = parse_authority_ledger(ledger_path.read_text(encoding="utf-8"))
    active = ledger["active"]
    digest = reconstruct_at(
        REPO_ROOT, args.commit, active["files"], active["external_imports"]
    )
    print(digest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="verify the ledger matches the worktree (default)")
    sub.add_parser("write", help="recompute the surface and update the active digest")

    digest_at = sub.add_parser(
        "digest-at", help="reconstruct the active file list's digest at a commit"
    )
    digest_at.add_argument("--commit", required=True)

    args = parser.parse_args(argv)
    command = args.command or "check"

    try:
        if command == "check":
            return cmd_check(args)
        if command == "write":
            return cmd_write(args)
        if command == "digest-at":
            return cmd_digest_at(args)
    except ReleaseAuthorityError as failure:
        print(f"::error::{failure}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
