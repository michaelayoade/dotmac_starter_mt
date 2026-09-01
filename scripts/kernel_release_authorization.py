#!/usr/bin/env python3
"""Authorize one kernel release without weakening the development marker.

The kernel's ordinary state is a published public version plus ``+dev``.  A
bare, unpublished version is a claim, so it is accepted only in one tightly
bound transition:

1. a protected commit changes only ``kernel-release-authorization.json``;
2. its immediate child changes only the mechanical version surfaces;
3. the release workflow rechecks the same record before build, publish,
   registry verification and tag creation; and
4. the post-tag recorder consumes the record.

The authorization binds the previous annotated tag, the exact protected-main
base, the next numeric alpha and a digest of every tracked kernel release input.
Only the two version literals inside that input are normalized.  In particular,
``__init__.py`` is not excluded: changing any byte besides its one assignment
invalidates the authorization.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORIZATION_PATH = REPO_ROOT / ".github" / "kernel-release-authorization.json"
KERNEL_PYPROJECT = "packages/dotmac-kernel/pyproject.toml"
KERNEL_INIT = "packages/dotmac-kernel/src/dotmac_kernel/__init__.py"
KERNEL_CHANGELOG = "packages/dotmac-kernel/CHANGELOG.md"
PUBLICATION_LEDGER = "docs/inventories/declared-publication-baseline.json"
MODULE_CATALOG = "docs/MODULE_CATALOG.md"
POETRY_LOCK = "poetry.lock"
SCHEMA = "KernelReleaseAuthorization.v1"
TAG_PREFIX = "dotmac-kernel-v"

# Every allowed allocation path is a mechanical version surface.  The package
# changelog is already inside the bound release-input digest and is deliberately
# absent here: an allocation changes no release notes, even if the digest logic
# would also catch the mutation later.
ALLOCATION_PATHS = frozenset(
    {
        KERNEL_PYPROJECT,
        KERNEL_INIT,
        POETRY_LOCK,
        MODULE_CATALOG,
        PUBLICATION_LEDGER,
    }
)
REQUIRED_ALLOCATION_PATHS = frozenset(
    {
        KERNEL_PYPROJECT,
        KERNEL_INIT,
        POETRY_LOCK,
        MODULE_CATALOG,
        PUBLICATION_LEDGER,
    }
)

_PUBLIC_ALPHA = re.compile(r"0\.1\.0a([1-9][0-9]*)")
_VERSION = re.compile(r"(0\.1\.0a[1-9][0-9]*)(?:\+([A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*))?")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SHA = re.compile(r"[0-9a-f]{40}")
_KERNEL_CATALOG_PACKAGE_CELL = "[`dotmac-kernel`](../packages/dotmac-kernel/README.md)"


class KernelReleaseAuthorizationError(RuntimeError):
    """The release transition is not the one that was authorized."""


@dataclass(frozen=True)
class ReleaseAuthorization:
    latest_tag: str
    latest_tag_object: str
    latest_tag_commit: str
    base_sha: str
    target_version: str
    normalized_release_input_digest: str

    @classmethod
    def parse(cls, value: object) -> ReleaseAuthorization:
        if not isinstance(value, dict):
            raise KernelReleaseAuthorizationError(
                "active authorization must be an object"
            )
        required = {
            "latest_tag",
            "latest_tag_object",
            "latest_tag_commit",
            "base_sha",
            "target_version",
            "normalized_release_input_digest",
        }
        if set(value) != required:
            raise KernelReleaseAuthorizationError(
                "active authorization fields differ from the v1 contract: "
                f"expected {sorted(required)}, got {sorted(value)}"
            )
        strings = {key: item for key, item in value.items() if isinstance(item, str)}
        if set(strings) != required:
            raise KernelReleaseAuthorizationError(
                "every authorization field must be text"
            )
        record = cls(**strings)
        for label, sha in (
            ("latest_tag_object", record.latest_tag_object),
            ("latest_tag_commit", record.latest_tag_commit),
            ("base_sha", record.base_sha),
        ):
            if _SHA.fullmatch(sha) is None:
                raise KernelReleaseAuthorizationError(
                    f"{label} is not a full commit/object SHA"
                )
        if _DIGEST.fullmatch(record.normalized_release_input_digest) is None:
            raise KernelReleaseAuthorizationError(
                "normalized_release_input_digest is not a typed sha256 digest"
            )
        if _PUBLIC_ALPHA.fullmatch(record.target_version) is None:
            raise KernelReleaseAuthorizationError(
                "target_version is not a public kernel alpha"
            )
        return record

    def as_json(self) -> dict[str, str]:
        return {
            "latest_tag": self.latest_tag,
            "latest_tag_object": self.latest_tag_object,
            "latest_tag_commit": self.latest_tag_commit,
            "base_sha": self.base_sha,
            "target_version": self.target_version,
            "normalized_release_input_digest": self.normalized_release_input_digest,
        }


def _git(*args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=not binary
    )
    if result.returncode != 0:
        stderr = (
            result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        )
        raise KernelReleaseAuthorizationError(
            f"git {' '.join(args)} failed: {stderr.strip() or 'no stderr'}"
        )
    return result.stdout


def _text_at(ref: str, path: str) -> str:
    value = _git("show", f"{ref}:{path}")
    assert isinstance(value, str)
    return value


def _bytes_at(ref: str, path: str) -> bytes:
    value = _git("show", f"{ref}:{path}", binary=True)
    assert isinstance(value, bytes)
    return value


def _head() -> str:
    value = _git("rev-parse", "HEAD")
    assert isinstance(value, str)
    return value.strip()


def _parent(ref: str) -> str:
    value = _git("rev-parse", f"{ref}^")
    assert isinstance(value, str)
    return value.strip()


def _changed_paths(base: str, head: str) -> set[str]:
    value = _git("diff", "--name-only", base, head)
    assert isinstance(value, str)
    return {line for line in value.splitlines() if line}


def _tag_alpha(tag: str) -> int:
    if not tag.startswith(TAG_PREFIX):
        raise KernelReleaseAuthorizationError(f"foreign kernel tag {tag!r}")
    match = _PUBLIC_ALPHA.fullmatch(tag.removeprefix(TAG_PREFIX))
    if match is None:
        raise KernelReleaseAuthorizationError(f"malformed kernel release tag {tag!r}")
    return int(match.group(1))


def latest_release() -> tuple[str, str, str]:
    value = _git("tag", "--list", f"{TAG_PREFIX}*")
    assert isinstance(value, str)
    tags = [line for line in value.splitlines() if line]
    if not tags:
        raise KernelReleaseAuthorizationError("no kernel release tag exists")
    latest = max(tags, key=_tag_alpha)
    object_type = _git("cat-file", "-t", latest)
    assert isinstance(object_type, str)
    if object_type.strip() != "tag":
        raise KernelReleaseAuthorizationError(f"{latest} is not an annotated tag")
    tag_object = _git("rev-parse", latest)
    tag_commit = _git("rev-parse", f"{latest}^{{commit}}")
    assert isinstance(tag_object, str) and isinstance(tag_commit, str)
    return latest, tag_object.strip(), tag_commit.strip()


def _next_version(tag: str) -> str:
    return f"0.1.0a{_tag_alpha(tag) + 1}"


def _normalize_pyproject(data: bytes) -> bytes:
    text = data.decode("utf-8")
    parsed = tomllib.loads(text)
    version = parsed.get("tool", {}).get("poetry", {}).get("version")
    if not isinstance(version, str):
        raise KernelReleaseAuthorizationError("kernel pyproject has no Poetry version")
    lines = text.splitlines(keepends=True)
    in_poetry = False
    changed = 0
    for index, line in enumerate(lines):
        header = re.fullmatch(r"\s*\[([^]]+)]\s*(?:\n)?", line)
        if header:
            in_poetry = header.group(1) == "tool.poetry"
            continue
        assignment = re.fullmatch(
            r"(?P<prefix>\s*version\s*=\s*)"
            r"(?P<quote>['\"])(?P<value>[^'\"]+)(?P=quote)"
            r"(?P<suffix>\s*(?:#.*)?(?:\n)?)",
            line,
        )
        if in_poetry and assignment is not None:
            lines[index] = (
                assignment["prefix"]
                + assignment["quote"]
                + "<AUTHORIZED_KERNEL_VERSION>"
                + assignment["quote"]
                + assignment["suffix"]
            )
            changed += 1
    if changed != 1:
        raise KernelReleaseAuthorizationError(
            f"expected one [tool.poetry] version assignment, found {changed}"
        )
    return "".join(lines).encode("utf-8")


def _normalize_init(data: bytes) -> bytes:
    text = data.decode("utf-8")
    tree = ast.parse(text)
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__version__"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(assignments) != 1:
        raise KernelReleaseAuthorizationError(
            f"expected one literal __version__ assignment, found {len(assignments)}"
        )
    node = assignments[0]
    if node.lineno != node.end_lineno:
        raise KernelReleaseAuthorizationError("__version__ assignment spans lines")
    lines = text.splitlines(keepends=True)
    original = lines[node.lineno - 1]
    literal = original[node.value.col_offset : node.value.end_col_offset]
    literal_match = re.fullmatch(
        r"(?P<prefix>[rRuUbB]*)(?P<quote>'''|\"\"\"|'|\").*(?P=quote)",
        literal,
    )
    if literal_match is None:
        raise KernelReleaseAuthorizationError("__version__ is not one string literal")
    normalized_literal = (
        literal_match["prefix"]
        + literal_match["quote"]
        + "<AUTHORIZED_KERNEL_VERSION>"
        + literal_match["quote"]
    )
    lines[node.lineno - 1] = (
        original[: node.value.col_offset]
        + normalized_literal
        + original[node.value.end_col_offset :]
    )
    return "".join(lines).encode("utf-8")


def _normalize_lock(data: bytes) -> bytes:
    text = data.decode("utf-8")
    # Parse first so a textual match cannot bless a malformed lock file.
    parsed = tomllib.loads(text)
    matches = [
        item
        for item in parsed.get("package", [])
        if item.get("name") == "dotmac-kernel"
    ]
    if len(matches) != 1:
        raise KernelReleaseAuthorizationError(
            f"expected one dotmac-kernel lock entry, found {len(matches)}"
        )
    blocks = list(re.finditer(r"(?ms)^\[\[package]]\n.*?(?=^\[\[package]]|\Z)", text))
    selected = [block for block in blocks if 'name = "dotmac-kernel"' in block.group()]
    if len(selected) != 1:
        raise KernelReleaseAuthorizationError("cannot locate one kernel lock block")
    block = selected[0]
    normalized, count = re.subn(
        r'(?m)^(version\s*=\s*")[^"]+("\s*)$',
        r"\g<1><AUTHORIZED_KERNEL_VERSION>\g<2>",
        block.group(),
        count=1,
    )
    if count != 1:
        raise KernelReleaseAuthorizationError(
            "kernel lock block has no one version line"
        )
    return (text[: block.start()] + normalized + text[block.end() :]).encode()


def _normalize_ledger(data: bytes) -> bytes:
    text = data.decode("utf-8")
    row = json.loads(text).get("unpublished", {}).get("dotmac-kernel")
    if not isinstance(row, dict) or not isinstance(row.get("declared"), str):
        raise KernelReleaseAuthorizationError("publication ledger has no kernel row")
    pattern = re.compile(r'(?s)("dotmac-kernel"\s*:\s*\{.*?"declared"\s*:\s*")[^"]+(")')
    normalized, count = pattern.subn(
        r"\g<1><AUTHORIZED_KERNEL_VERSION>\g<2>", text, count=1
    )
    if count != 1:
        raise KernelReleaseAuthorizationError("cannot locate one kernel ledger value")
    return normalized.encode()


def _kernel_catalog_row(text: str) -> tuple[int, str]:
    rows: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines(keepends=True)):
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = tuple(cell.strip() for cell in stripped[1:-1].split("|"))
        if cells and cells[0] == _KERNEL_CATALOG_PACKAGE_CELL:
            rows.append((index, line))
    if len(rows) != 1:
        raise KernelReleaseAuthorizationError(
            f"expected one kernel catalogue row, found {len(rows)}"
        )
    return rows[0]


def _normalize_catalog(data: bytes) -> bytes:
    text = data.decode("utf-8")
    lines = text.splitlines(keepends=True)
    index, row = _kernel_catalog_row(text)
    normalized, count = re.subn(
        r"(?<=\| `)0\.1\.0a[1-9][0-9]*(?:\+[A-Za-z0-9.]+)?(?=` \|)",
        "<AUTHORIZED_KERNEL_VERSION>",
        row,
        count=1,
    )
    if count != 1:
        raise KernelReleaseAuthorizationError(
            "kernel catalogue row has no version field"
        )
    lines[index] = normalized
    return "".join(lines).encode()


def _normalized_allocation_surface(ref: str, path: str) -> bytes:
    data = _bytes_at(ref, path)
    if path == KERNEL_PYPROJECT:
        return _normalize_pyproject(data)
    if path == KERNEL_INIT:
        return _normalize_init(data)
    if path == POETRY_LOCK:
        return _normalize_lock(data)
    if path == PUBLICATION_LEDGER:
        return _normalize_ledger(data)
    if path == MODULE_CATALOG:
        return _normalize_catalog(data)
    return data


def normalized_release_input_digest(ref: str) -> str:
    listing = _git(
        "ls-tree",
        "-r",
        "--name-only",
        ref,
        "--",
        "packages/dotmac-kernel",
        "package.json",
        "package-lock.json",
    )
    assert isinstance(listing, str)
    paths = sorted(line for line in listing.splitlines() if line)
    if KERNEL_PYPROJECT not in paths or KERNEL_INIT not in paths:
        raise KernelReleaseAuthorizationError(
            "kernel release input listing is incomplete"
        )
    digest = hashlib.sha256(b"KernelReleaseInputs.v1\0")
    for path in paths:
        data = _bytes_at(ref, path)
        if path == KERNEL_PYPROJECT:
            data = _normalize_pyproject(data)
        elif path == KERNEL_INIT:
            data = _normalize_init(data)
        encoded = path.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"


def _document(active: ReleaseAuthorization | None) -> dict[str, Any]:
    existing = json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
    return {
        "$schema": SCHEMA,
        "$comment": existing["$comment"],
        "active": None if active is None else active.as_json(),
    }


def render_document(active: ReleaseAuthorization | None) -> str:
    return json.dumps(_document(active), indent=2) + "\n"


def parse_authorization_document(value: object) -> ReleaseAuthorization | None:
    if not isinstance(value, dict):
        raise KernelReleaseAuthorizationError(
            "authorization document must be an object"
        )
    if set(value) != {"$schema", "$comment", "active"}:
        raise KernelReleaseAuthorizationError(
            "authorization document has unknown fields"
        )
    if value["$schema"] != SCHEMA:
        raise KernelReleaseAuthorizationError("authorization document schema is not v1")
    if not isinstance(value["$comment"], list) or not all(
        isinstance(line, str) for line in value["$comment"]
    ):
        raise KernelReleaseAuthorizationError(
            "authorization comment must be text lines"
        )
    if value["active"] is None:
        return None
    return ReleaseAuthorization.parse(value["active"])


def load_authorization() -> ReleaseAuthorization | None:
    return parse_authorization_document(
        json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
    )


def authorization_at(ref: str) -> ReleaseAuthorization | None:
    return parse_authorization_document(
        json.loads(_text_at(ref, str(AUTHORIZATION_PATH.relative_to(REPO_ROOT))))
    )


def prepare(base_sha: str, target_version: str) -> ReleaseAuthorization:
    resolved = _git("rev-parse", base_sha)
    assert isinstance(resolved, str)
    resolved_base = resolved.strip()
    if resolved_base != base_sha or _SHA.fullmatch(base_sha) is None:
        raise KernelReleaseAuthorizationError(
            "base_sha must be one exact full commit SHA"
        )
    latest_tag, tag_object, tag_commit = latest_release()
    expected = _next_version(latest_tag)
    if target_version != expected:
        raise KernelReleaseAuthorizationError(
            f"target must be the next numeric alpha {expected}, got {target_version}"
        )
    collision = _git("tag", "--list", f"{TAG_PREFIX}{target_version}")
    assert isinstance(collision, str)
    if collision.strip():
        raise KernelReleaseAuthorizationError(f"{target_version} already has a tag")
    return ReleaseAuthorization(
        latest_tag=latest_tag,
        latest_tag_object=tag_object,
        latest_tag_commit=tag_commit,
        base_sha=base_sha,
        target_version=target_version,
        normalized_release_input_digest=normalized_release_input_digest(base_sha),
    )


def _validate_bound_tag(
    record: ReleaseAuthorization, *, allow_target_tag: bool
) -> None:
    tag_object = _git("rev-parse", record.latest_tag)
    tag_commit = _git("rev-parse", f"{record.latest_tag}^{{commit}}")
    tag_type = _git("cat-file", "-t", record.latest_tag)
    assert isinstance(tag_object, str) and isinstance(tag_commit, str)
    assert isinstance(tag_type, str)
    if tag_type.strip() != "tag":
        raise KernelReleaseAuthorizationError(
            "bound predecessor is not an annotated tag"
        )
    if tag_object.strip() != record.latest_tag_object:
        raise KernelReleaseAuthorizationError("bound predecessor tag object moved")
    if tag_commit.strip() != record.latest_tag_commit:
        raise KernelReleaseAuthorizationError("bound predecessor peeled commit moved")
    if record.target_version != _next_version(record.latest_tag):
        raise KernelReleaseAuthorizationError(
            "target is no longer the next numeric alpha"
        )
    actual_latest, _, _ = latest_release()
    target_tag = f"{TAG_PREFIX}{record.target_version}"
    accepted = (
        {record.latest_tag, target_tag} if allow_target_tag else {record.latest_tag}
    )
    if actual_latest not in accepted:
        raise KernelReleaseAuthorizationError(
            f"kernel tag set advanced to {actual_latest}; authorization binds "
            f"{record.latest_tag}"
        )


def _kernel_version(ref: str) -> str:
    parsed = tomllib.loads(_text_at(ref, KERNEL_PYPROJECT))
    value = parsed.get("tool", {}).get("poetry", {}).get("version")
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise KernelReleaseAuthorizationError(
            f"kernel version {value!r} has an invalid shape"
        )
    return value


def _assert_authorization_commit(record: ReleaseAuthorization, commit: str) -> None:
    parent = _parent(commit)
    if parent != record.base_sha:
        raise KernelReleaseAuthorizationError(
            f"authorization commit parent {parent} != bound base {record.base_sha}"
        )
    changed = _changed_paths(parent, commit)
    expected = {str(AUTHORIZATION_PATH.relative_to(REPO_ROOT))}
    if changed != expected:
        raise KernelReleaseAuthorizationError(
            f"authorization commit must change only {sorted(expected)}, got "
            f"{sorted(changed)}"
        )
    if (
        normalized_release_input_digest(record.base_sha)
        != record.normalized_release_input_digest
    ):
        raise KernelReleaseAuthorizationError(
            "bound base release-input digest does not match"
        )


def _assert_version_surfaces(ref: str, version: str) -> None:
    init_tree = ast.parse(_text_at(ref, KERNEL_INIT))
    runtime = [
        node.value.value
        for node in init_tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__version__"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if runtime != [version]:
        raise KernelReleaseAuthorizationError(
            f"kernel runtime version surface is {runtime}, expected {[version]}"
        )
    lock = tomllib.loads(_text_at(ref, POETRY_LOCK))
    lock_versions = [
        item.get("version")
        for item in lock.get("package", [])
        if item.get("name") == "dotmac-kernel"
    ]
    if lock_versions != [version]:
        raise KernelReleaseAuthorizationError(
            f"poetry.lock kernel version is {lock_versions}, expected {[version]}"
        )
    ledger = json.loads(_text_at(ref, PUBLICATION_LEDGER))["unpublished"]
    row = ledger.get("dotmac-kernel")
    if not isinstance(row, dict) or row.get("declared") != version:
        raise KernelReleaseAuthorizationError(
            "publication ledger does not bind dotmac-kernel to the target version"
        )
    _, catalog_row = _kernel_catalog_row(_text_at(ref, MODULE_CATALOG))
    if f"| `{version}` |" not in catalog_row:
        raise KernelReleaseAuthorizationError(
            "module catalogue does not bind dotmac-kernel to the target version"
        )
    if f"## {version} " not in _text_at(ref, KERNEL_CHANGELOG):
        raise KernelReleaseAuthorizationError(
            f"kernel changelog has no {version} release heading"
        )


def validate_allocation(
    record: ReleaseAuthorization, head: str, *, allow_target_tag: bool = False
) -> None:
    _validate_bound_tag(record, allow_target_tag=allow_target_tag)
    authorization_commit = _parent(head)
    _assert_authorization_commit(record, authorization_commit)
    changed = _changed_paths(authorization_commit, head)
    unexpected = changed - ALLOCATION_PATHS
    if unexpected:
        raise KernelReleaseAuthorizationError(
            f"allocation commit changes unauthorized paths: {sorted(unexpected)}"
        )
    if not REQUIRED_ALLOCATION_PATHS.issubset(changed):
        raise KernelReleaseAuthorizationError(
            "allocation commit did not change every required version surface: "
            f"{sorted(REQUIRED_ALLOCATION_PATHS - changed)}"
        )
    for path in ALLOCATION_PATHS:
        if _normalized_allocation_surface(record.base_sha, path) != (
            _normalized_allocation_surface(head, path)
        ):
            raise KernelReleaseAuthorizationError(
                f"allocation changed {path} beyond its one version value"
            )
    if _kernel_version(head) != record.target_version:
        raise KernelReleaseAuthorizationError(
            "allocation does not declare the target version"
        )
    _assert_version_surfaces(head, record.target_version)
    current_digest = normalized_release_input_digest(head)
    if current_digest != record.normalized_release_input_digest:
        raise KernelReleaseAuthorizationError(
            "allocation changed kernel release inputs beyond the two normalized "
            "version literals"
        )


def validate_release_source(*, source_sha: str, version: str) -> dict[str, object]:
    if _SHA.fullmatch(source_sha) is None:
        raise KernelReleaseAuthorizationError("release source is not a full SHA")
    resolved = _git("rev-parse", source_sha)
    assert isinstance(resolved, str)
    if resolved.strip() != source_sha:
        raise KernelReleaseAuthorizationError("release source does not resolve exactly")
    authorization_commit = _parent(source_sha)
    record = authorization_at(authorization_commit)
    if record is None:
        raise KernelReleaseAuthorizationError(
            "release source parent has no active authorization"
        )
    if record.target_version != version:
        raise KernelReleaseAuthorizationError(
            "release source authorization targets a different version"
        )
    validate_allocation(record, source_sha, allow_target_tag=True)
    return {
        "schema": "KernelReleaseSourceBinding.v1",
        "state": "allocated",
        "source_sha": source_sha,
        "authorization_commit": authorization_commit,
        "authorization": record.as_json(),
    }


def validate_current_state(
    *, expected_version: str | None = None, allow_target_tag: bool = False
) -> str:
    head = _head()
    version = _kernel_version(head)
    if expected_version is not None and version != expected_version:
        raise KernelReleaseAuthorizationError(
            f"workflow input {expected_version!r} != committed kernel version "
            f"{version!r}"
        )
    record = load_authorization()
    tag = f"{TAG_PREFIX}{version}"
    existing = _git("tag", "--list", tag)
    assert isinstance(existing, str)
    if record is None:
        if existing.strip():
            published = _git("rev-parse", f"{tag}:packages/dotmac-kernel/src")
            current = _git("rev-parse", "HEAD:packages/dotmac-kernel/src")
            assert isinstance(published, str) and isinstance(current, str)
            if published.strip() != current.strip():
                raise KernelReleaseAuthorizationError(
                    f"kernel declares released {version} but its source differs "
                    "from the tag"
                )
            return "released"
        if "+" not in version:
            raise KernelReleaseAuthorizationError(
                f"bare unpublished kernel version {version} has no active authorization"
            )
        return "development"

    if "+" in version:
        _validate_bound_tag(record, allow_target_tag=False)
        _assert_authorization_commit(record, head)
        if (
            normalized_release_input_digest(head)
            != record.normalized_release_input_digest
        ):
            raise KernelReleaseAuthorizationError(
                "authorization-only commit changed the bound release inputs"
            )
        return "authorized"

    validate_allocation(record, head, allow_target_tag=allow_target_tag)
    return "allocated"


def consume_for_release(*, version: str, tag: str, commit: str) -> str:
    record = load_authorization()
    if record is None:
        raise KernelReleaseAuthorizationError(
            "kernel release has no active authorization"
        )
    expected_tag = f"{TAG_PREFIX}{version}"
    if version != record.target_version or tag != expected_tag:
        raise KernelReleaseAuthorizationError(
            "release coordinates do not match the active kernel authorization"
        )
    if commit != _head():
        raise KernelReleaseAuthorizationError(
            f"tag peels to {commit}, but the protected release head is {_head()}"
        )
    validate_allocation(record, commit, allow_target_tag=True)
    return render_document(None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--base-sha", required=True)
    prepare_parser.add_argument("--target", required=True)
    prepare_parser.add_argument("--write", action="store_true")
    verify_parser = subparsers.add_parser("verify-release")
    verify_parser.add_argument("--version", required=True)
    verify_parser.add_argument(
        "--phase", required=True, choices=("build", "publish", "verify", "tag")
    )
    source_parser = subparsers.add_parser("verify-source")
    source_parser.add_argument("--source-sha", required=True)
    source_parser.add_argument("--version", required=True)
    source_parser.add_argument("--output", type=Path, required=True)
    subparsers.add_parser("check-tree")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            record = prepare(args.base_sha, args.target)
            rendered = render_document(record)
            if args.write:
                AUTHORIZATION_PATH.write_text(rendered, encoding="utf-8")
                print(f"wrote {AUTHORIZATION_PATH.relative_to(REPO_ROOT)}")
            else:
                print(rendered, end="")
        elif args.command == "verify-release":
            state = validate_current_state(expected_version=args.version)
            if state != "allocated":
                raise KernelReleaseAuthorizationError(
                    f"{args.phase}: kernel release requires allocated state, "
                    f"got {state}"
                )
            print(
                f"{args.phase}: kernel {args.version} matches its active authorization"
            )
        elif args.command == "verify-source":
            binding = validate_release_source(
                source_sha=args.source_sha, version=args.version
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(binding, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            print(
                f"verify: kernel {args.version} release source is its allocated state"
            )
        else:
            print(f"kernel release state: {validate_current_state()}")
    except KernelReleaseAuthorizationError as failure:
        print(f"kernel release REFUSED: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
