"""Bounded, independently re-derived product observations.

Products publish only coordinates and digests from their own trees. Starter
owns the fixed source paths and structural selectors, fetches an immutable
protected-main product revision, re-extracts the selected bytes, and only then
parses or classifies them. The envelope cannot select a path or selector and
never carries source text, credential-shaped or otherwise.

This is a breaking ``dimensional-composition.v3`` boundary. A v2 dimension
record is not adapted: observations and centrally derived decisions are
different contracts.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final, TypeAlias

OBSERVATION_SCHEMA_VERSION: Final = "dimensional-composition.v3"
CONTRACT_REPOSITORY: Final = "dotmac_starter_mt"
CANONICAL_RECORD_PATH: Final = "docs/kernel-runtime-composition.json"
_COMMIT: Final = re.compile(r"^[0-9a-f]{40}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER: Final = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SOURCE_COMPONENT: Final = re.compile(r"^[A-Za-z0-9._-]+$")
# Requiring the separator makes the two namespaces disjoint by construction.
_REPOSITORY: Final = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
_PRODUCT_ID: Final = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
_REGULAR_BLOB_MODES: Final = frozenset({"100644", "100755"})
_OBSERVATION_ROW_KEYS: Final = frozenset(
    {"id", "source_path", "source_blob_sha256", "selector", "extract_sha256"}
)
_CONTRACT_REVISION_KEYS: Final = frozenset({"repository", "commit"})
_ENVELOPE_KEYS: Final = frozenset(
    {
        "schema_version",
        "repository",
        "product_id",
        "subject",
        "contract_revision",
        "observations",
    }
)


class ObservationRefusal(ValueError):
    """Evidence was present but did not satisfy the fixed contract."""


class ObservationAcquisitionError(RuntimeError):
    """Git acquisition failed; never report this as absent evidence."""


@dataclass(frozen=True)
class WholeFileLocator:
    """Select the complete UTF-8 file."""

    @property
    def selector(self) -> str:
        return "whole-file.v1"


@dataclass(frozen=True)
class PythonAssignmentKeywordLocator:
    """Select one keyword value on a named module-level constructor assignment."""

    assignment_target: str
    constructor: str
    keyword: str

    def __post_init__(self) -> None:
        for label, value in (
            ("assignment target", self.assignment_target),
            ("constructor", self.constructor),
            ("keyword", self.keyword),
        ):
            if not value.isidentifier():
                raise ValueError(f"Python selector {label} is invalid: {value!r}")

    @property
    def selector(self) -> str:
        return (
            "python-assignment-keyword.v1/"
            f"{self.assignment_target}/{self.constructor}/{self.keyword}"
        )


@dataclass(frozen=True)
class PythonStringAssignmentLocator:
    """Select the literal value of one named module-level string assignment."""

    assignment_target: str

    def __post_init__(self) -> None:
        if not self.assignment_target.isidentifier():
            raise ValueError(
                "Python string assignment target is invalid: "
                f"{self.assignment_target!r}"
            )

    @property
    def selector(self) -> str:
        return f"python-string-assignment.v1/{self.assignment_target}"


@dataclass(frozen=True)
class PoetryInstallCommandLocator:
    """Select one complete logical Poetry install/sync instruction."""

    source_format: str

    def __post_init__(self) -> None:
        if self.source_format not in {"dockerfile", "shell"}:
            raise ValueError(
                "PoetryInstallCommandLocator.source_format must be "
                "'dockerfile' or 'shell'"
            )

    @property
    def selector(self) -> str:
        return f"poetry-install-command.v1/{self.source_format}"


ObservationLocator: TypeAlias = (  # noqa: UP040 - product floor includes Python 3.11
    WholeFileLocator
    | PythonAssignmentKeywordLocator
    | PythonStringAssignmentLocator
    | PoetryInstallCommandLocator
)


def _validate_relative_source_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ValueError(f"invalid repository-relative source path {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value:
        raise ValueError(f"source path is not canonical and relative: {value!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"source path contains a forbidden component: {value!r}")
    if any(not _SOURCE_COMPONENT.fullmatch(part) for part in path.parts):
        raise ValueError(f"source path contains an unsafe component: {value!r}")
    if path.parts[0] == ".git":
        raise ValueError("source path may not address Git metadata")
    return value


@dataclass(frozen=True)
class ObservationSpec:
    """One Starter-owned source coordinate and structural selector."""

    observation_id: str
    source_path: str
    locator: ObservationLocator
    max_source_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.observation_id):
            raise ValueError(f"invalid observation id {self.observation_id!r}")
        _validate_relative_source_path(self.source_path)
        if not isinstance(
            self.locator,
            WholeFileLocator
            | PythonAssignmentKeywordLocator
            | PythonStringAssignmentLocator
            | PoetryInstallCommandLocator,
        ):
            raise TypeError(
                "locator must be one of the closed observation locator types"
            )
        if not 1 <= self.max_source_bytes <= 1024 * 1024:
            raise ValueError("max_source_bytes must be between 1 byte and 1 MiB")

    @property
    def selector(self) -> str:
        return self.locator.selector


@dataclass(frozen=True)
class ProductObservationSpec:
    """Starter's complete fixed observation contract for one product.

    ``repository`` is an underscore-form source coordinate. ``product_id`` is
    a hyphen-form product-owned stable identity. The disjoint alphabets prevent
    either field from being normalized into the other.
    """

    repository: str
    product_id: str
    subject: str
    observations: tuple[ObservationSpec, ...]

    def __post_init__(self) -> None:
        if not _REPOSITORY.fullmatch(self.repository):
            raise ValueError(
                "repository must use the underscore coordinate alphabet and "
                f"contain an underscore: {self.repository!r}"
            )
        if not _PRODUCT_ID.fullmatch(self.product_id):
            raise ValueError(
                "product_id must use the hyphen identity alphabet and contain "
                f"a hyphen: {self.product_id!r}"
            )
        if not _IDENTIFIER.fullmatch(self.subject):
            raise ValueError(f"invalid subject {self.subject!r}")
        ids = [item.observation_id for item in self.observations]
        if not ids:
            raise ValueError("a product observation spec may not be empty")
        if len(ids) != len(set(ids)):
            raise ValueError("a product observation spec has duplicate observation ids")


@dataclass(frozen=True)
class ObservationClaim:
    observation_id: str
    source_path: str
    source_blob_sha256: str
    selector: str
    extract_sha256: str


@dataclass(frozen=True)
class VerifiedObservation:
    """A checked claim plus centrally extracted bytes, never shown by repr."""

    claim: ObservationClaim
    extracted: bytes = field(repr=False)


@dataclass(frozen=True)
class VerifiedObservationEnvelope:
    repository: str
    product_id: str
    subject: str
    contract_revision: str
    product_revision: str
    observations: tuple[VerifiedObservation, ...]


def build_product_checkout_document(
    *,
    spec: ProductObservationSpec,
    product_clone: Path,
    contract_revision: str,
) -> dict[str, object]:
    """Derive a deterministic digest-only v3 document from exact Git HEAD."""

    if not _COMMIT.fullmatch(contract_revision):
        raise ObservationRefusal("contract revision is not an immutable commit")
    product_revision = checkout_head_revision(product_clone)
    rows: list[dict[str, str]] = []
    for observation_spec in spec.observations:
        source = read_regular_git_blob(
            product_clone,
            product_revision,
            observation_spec,
        )
        extracted = extract_observation(observation_spec, source)
        rows.append(
            {
                "id": observation_spec.observation_id,
                "source_path": observation_spec.source_path,
                "source_blob_sha256": hashlib.sha256(source).hexdigest(),
                "selector": observation_spec.selector,
                "extract_sha256": hashlib.sha256(extracted).hexdigest(),
            }
        )
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "repository": spec.repository,
        "product_id": spec.product_id,
        "subject": spec.subject,
        "contract_revision": {
            "repository": CONTRACT_REPOSITORY,
            "commit": contract_revision,
        },
        "observations": rows,
    }


def _git(
    clone: Path,
    arguments: Sequence[str],
    *,
    accepted_returncodes: frozenset[int] = frozenset({0}),
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(  # noqa: S603 - fixed git executable, argv only
            ["git", "-C", str(clone), *arguments],  # noqa: S607
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise ObservationAcquisitionError(
            f"could not launch git for {arguments[0] if arguments else '<missing>'}: "
            f"{exc}"
        ) from exc
    if result.returncode not in accepted_returncodes:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise ObservationAcquisitionError(
            f"git {arguments[0] if arguments else '<missing>'} failed with exit "
            f"{result.returncode}: {diagnostic or '<no diagnostic>'}"
        )
    return result


def _require_protected_main_ancestor(
    clone: Path, revision: str, protected_main_ref: str
) -> None:
    if not _COMMIT.fullmatch(revision):
        raise ObservationRefusal(
            "product revision must be exactly 40 lowercase hexadecimal "
            f"characters: {revision!r}"
        )
    result = _git(
        clone,
        ("merge-base", "--is-ancestor", revision, protected_main_ref),
        accepted_returncodes=frozenset({0, 1}),
    )
    if result.returncode == 1:
        raise ObservationRefusal(
            f"product revision {revision} is not an ancestor of {protected_main_ref}"
        )


def read_regular_git_blob(clone: Path, revision: str, spec: ObservationSpec) -> bytes:
    """Read only the fixed path as one bounded regular Git blob.

    Tree metadata is checked before content is read, so symlinks, submodules,
    directories and oversized blobs refuse without being followed or
    materialized. The payload's repeated path is never passed to this function.
    """

    tree = _git(
        clone,
        ("ls-tree", "-z", "--full-tree", revision, "--", spec.source_path),
    ).stdout
    entries = [entry for entry in tree.split(b"\0") if entry]
    if len(entries) != 1:
        raise ObservationRefusal(
            f"observation {spec.observation_id!r} expected one Git object at "
            f"{spec.source_path!r}; found {len(entries)}"
        )
    try:
        metadata, returned_path = entries[0].split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split(" ")
        decoded_path = returned_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ObservationAcquisitionError(
            "git ls-tree returned malformed metadata"
        ) from exc
    if decoded_path != spec.source_path:
        raise ObservationRefusal(
            f"Git returned {decoded_path!r} for fixed path {spec.source_path!r}"
        )
    if mode not in _REGULAR_BLOB_MODES or object_type != "blob":
        raise ObservationRefusal(
            f"observation {spec.observation_id!r} source is not a regular file "
            f"(mode={mode!r}, type={object_type!r})"
        )
    size_text = _git(clone, ("cat-file", "-s", object_id)).stdout.strip()
    try:
        size = int(size_text)
    except ValueError as exc:
        raise ObservationAcquisitionError(
            "git cat-file returned a non-integer size"
        ) from exc
    if size > spec.max_source_bytes:
        raise ObservationRefusal(
            f"observation {spec.observation_id!r} source is {size} bytes; "
            f"limit is {spec.max_source_bytes}"
        )
    content = _git(clone, ("cat-file", "blob", object_id)).stdout
    if len(content) != size:
        raise ObservationAcquisitionError(
            f"git reported {size} bytes for {spec.source_path!r} but returned "
            f"{len(content)}"
        )
    return content


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _extract_python_keyword(
    source: str, locator: PythonAssignmentKeywordLocator
) -> bytes:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ObservationRefusal(
            "fixed Python observation source does not parse"
        ) from exc
    values: list[str] = []
    for statement in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign):
            target, value = statement.target, statement.value
        if not (
            isinstance(target, ast.Name)
            and target.id == locator.assignment_target
            and isinstance(value, ast.Call)
            and _call_name(value.func) == locator.constructor
        ):
            continue
        for keyword in value.keywords:
            if keyword.arg == locator.keyword:
                segment = ast.get_source_segment(source, keyword.value)
                if segment is None:
                    raise ObservationRefusal(
                        "Python observation has no reproducible source segment"
                    )
                values.append(segment)
    if len(values) != 1:
        raise ObservationRefusal(
            f"Python selector extracted {len(values)} value(s); expected exactly one"
        )
    return values[0].encode("utf-8")


def _extract_python_string_assignment(
    source: str, locator: PythonStringAssignmentLocator
) -> bytes:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ObservationRefusal(
            "fixed Python observation source does not parse"
        ) from exc
    values: list[str] = []
    for statement in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign):
            target, value = statement.target, statement.value
        if not (
            isinstance(target, ast.Name)
            and target.id == locator.assignment_target
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            continue
        values.append(value.value)
    if len(values) != 1:
        raise ObservationRefusal(
            f"Python string selector extracted {len(values)} value(s); "
            "expected exactly one"
        )
    return values[0].encode("utf-8")


def _logical_dockerfile_instructions(source: str) -> tuple[str, ...]:
    instructions: list[str] = []
    current: list[str] = []
    for line in source.splitlines(keepends=True):
        if not current:
            if not re.match(r"^\s*RUN(?:\s|$)", line, flags=re.IGNORECASE):
                continue
            current.append(line)
        else:
            current.append(line)
        if not line.rstrip("\r\n").rstrip().endswith("\\"):
            instructions.append("".join(current).rstrip("\r\n"))
            current = []
    if current:
        raise ObservationRefusal("Dockerfile ends inside a continued RUN instruction")
    return tuple(instructions)


def _logical_shell_commands(source: str) -> tuple[str, ...]:
    commands: list[str] = []
    current: list[str] = []
    for line in source.splitlines(keepends=True):
        stripped = line.lstrip()
        if not current and (not stripped.strip() or stripped.startswith("#")):
            continue
        current.append(line)
        if not line.rstrip("\r\n").rstrip().endswith("\\"):
            commands.append("".join(current).strip())
            current = []
    if current:
        raise ObservationRefusal("shell recipe ends inside a continued command")
    return tuple(commands)


def _shell_tokens(candidate: str) -> tuple[str, ...]:
    logical = re.sub(r"\\[ \t]*\n[ \t]*", " ", candidate)
    lexer = shlex.shlex(logical, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        return tuple(lexer)
    except ValueError as exc:
        if re.search(r"\bpoetry\s+(?:install|sync)\b", candidate):
            raise ObservationRefusal(
                "a Poetry-shaped instruction has malformed shell syntax"
            ) from exc
        return ()


def _poetry_prefix_is_direct(prefix: tuple[str, ...]) -> bool:
    remaining = prefix
    if remaining and remaining[0].upper() == "RUN":
        remaining = remaining[1:]
    return all(
        word.startswith("--mount=")
        or bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word))
        for word in remaining
    )


def _contains_direct_poetry_install(tokens: tuple[str, ...]) -> bool:
    actions = 0
    segment: list[str] = []
    for word in (*tokens, ";"):
        if word not in {";", "&&", "||", "|"}:
            segment.append(word)
            continue
        if "poetry" in segment:
            poetry_index = segment.index("poetry")
            if _poetry_prefix_is_direct(tuple(segment[:poetry_index])) and any(
                item in {"install", "sync"} for item in segment[poetry_index + 1 :]
            ):
                actions += 1
        segment = []
    if actions > 1:
        raise ObservationRefusal(
            "one logical source instruction contains multiple Poetry installs"
        )
    return actions == 1


def _contains_poetry_install_shape(tokens: tuple[str, ...]) -> bool:
    for index, word in enumerate(tokens):
        if word == "poetry" and any(
            item in {"install", "sync"} for item in tokens[index + 1 :]
        ):
            return True
        if re.search(r"\bpoetry\s+(?:install|sync)\b", word):
            return True
    return False


def _extract_poetry_command(source: str, locator: PoetryInstallCommandLocator) -> bytes:
    candidates = (
        _logical_dockerfile_instructions(source)
        if locator.source_format == "dockerfile"
        else _logical_shell_commands(source)
    )
    selected: list[str] = []
    for candidate in candidates:
        tokens = _shell_tokens(candidate)
        direct = _contains_direct_poetry_install(tokens)
        shaped = _contains_poetry_install_shape(tokens)
        if shaped and not direct:
            raise ObservationRefusal(
                "a Poetry install/sync appears behind an unmodelled shell shape"
            )
        if direct:
            selected.append(candidate)
    if len(selected) != 1:
        raise ObservationRefusal(
            f"Poetry selector extracted {len(selected)} instruction(s); "
            "expected exactly one"
        )
    return selected[0].encode("utf-8")


def extract_observation(spec: ObservationSpec, source_bytes: bytes) -> bytes:
    """Apply one trusted selector to a bounded regular source blob."""

    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ObservationRefusal(
            f"observation {spec.observation_id!r} source is not UTF-8 text"
        ) from exc
    if isinstance(spec.locator, WholeFileLocator):
        return source_bytes
    if isinstance(spec.locator, PythonAssignmentKeywordLocator):
        return _extract_python_keyword(source, spec.locator)
    if isinstance(spec.locator, PythonStringAssignmentLocator):
        return _extract_python_string_assignment(source, spec.locator)
    return _extract_poetry_command(source, spec.locator)


def _parse_claim(payload: object, spec: ObservationSpec) -> ObservationClaim:
    if not isinstance(payload, Mapping):
        raise ObservationRefusal(
            f"observation {spec.observation_id!r} must be an object"
        )
    keys = set(payload)
    if not all(isinstance(key, str) for key in keys):
        raise ObservationRefusal("observation field names must be strings")
    unknown = sorted(keys - _OBSERVATION_ROW_KEYS)
    missing = sorted(_OBSERVATION_ROW_KEYS - keys)
    if unknown or missing:
        raise ObservationRefusal(
            f"observation {spec.observation_id!r} has "
            f"missing={missing!r}, unknown={unknown!r}"
        )
    observation_id = payload["id"]
    source_path = payload["source_path"]
    source_digest = payload["source_blob_sha256"]
    selector = payload["selector"]
    extract_digest = payload["extract_sha256"]
    if observation_id != spec.observation_id:
        raise ObservationRefusal(
            f"expected observation id {spec.observation_id!r}, got {observation_id!r}"
        )
    if source_path != spec.source_path:
        raise ObservationRefusal(
            f"observation {spec.observation_id!r} declares path {source_path!r}; "
            f"Starter fixes it at {spec.source_path!r}"
        )
    if selector != spec.selector:
        raise ObservationRefusal(
            f"observation {spec.observation_id!r} declares selector {selector!r}; "
            f"Starter fixes it at {spec.selector!r}"
        )
    if not isinstance(source_digest, str) or not _SHA256.fullmatch(source_digest):
        raise ObservationRefusal(
            f"observation {spec.observation_id!r} has an invalid source blob digest"
        )
    if not isinstance(extract_digest, str) or not _SHA256.fullmatch(extract_digest):
        raise ObservationRefusal(
            f"observation {spec.observation_id!r} has an invalid extract digest"
        )
    return ObservationClaim(
        observation_id,
        source_path,
        source_digest,
        selector,
        extract_digest,
    )


def _parse_contract_revision(payload: object, trusted_revision: str) -> str:
    if not isinstance(payload, Mapping) or set(payload) != _CONTRACT_REVISION_KEYS:
        raise ObservationRefusal(
            "contract_revision must contain exactly 'repository' and 'commit'"
        )
    if payload["repository"] != CONTRACT_REPOSITORY:
        raise ObservationRefusal(
            f"contract_revision.repository must be {CONTRACT_REPOSITORY!r}"
        )
    commit = payload["commit"]
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise ObservationRefusal("contract_revision.commit is not an immutable commit")
    if commit != trusted_revision:
        raise ObservationRefusal(
            f"contract revision {commit!r} does not match loaded contract "
            f"{trusted_revision!r}"
        )
    return commit


def _parse_envelope_claims(
    document: Mapping[str, object],
    *,
    spec: ProductObservationSpec,
    trusted_contract_revision: str,
) -> tuple[str, tuple[ObservationClaim, ...]]:
    """Parse every caller-authored value before any Git operation."""

    document_keys = set(document)
    if not all(isinstance(key, str) for key in document_keys):
        raise ObservationRefusal("observation envelope field names must be strings")
    if document_keys != _ENVELOPE_KEYS:
        raise ObservationRefusal(
            f"observation envelope has "
            f"missing={sorted(_ENVELOPE_KEYS - document_keys)!r}, "
            f"unknown={sorted(document_keys - _ENVELOPE_KEYS)!r}"
        )
    if document["schema_version"] != OBSERVATION_SCHEMA_VERSION:
        raise ObservationRefusal(
            f"this reader accepts only {OBSERVATION_SCHEMA_VERSION!r}; "
            f"got {document['schema_version']!r}; no adapter exists"
        )
    expected_identity = {
        "repository": spec.repository,
        "product_id": spec.product_id,
        "subject": spec.subject,
    }
    for name, expected in expected_identity.items():
        if document[name] != expected:
            raise ObservationRefusal(
                f"envelope {name!r} is {document[name]!r}; expected {expected!r}"
            )
    if not _COMMIT.fullmatch(trusted_contract_revision):
        raise ObservationRefusal("trusted contract revision is not an immutable commit")
    contract_revision = _parse_contract_revision(
        document["contract_revision"], trusted_contract_revision
    )
    rows = document["observations"]
    if not isinstance(rows, list):
        raise ObservationRefusal("envelope 'observations' must be a list")
    if not all(
        isinstance(row, Mapping) and isinstance(row.get("id"), str) for row in rows
    ):
        raise ObservationRefusal("observation ids must be present strings")
    ids = [row["id"] for row in rows if isinstance(row, Mapping)]
    if len(ids) != len(set(ids)):
        raise ObservationRefusal("observation ids must be present and unique")
    by_id = {
        row["id"]: row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }
    expected_ids = {item.observation_id for item in spec.observations}
    if set(by_id) != expected_ids:
        raise ObservationRefusal(
            f"observation set differs: "
            f"missing={sorted(expected_ids - set(by_id))!r}, "
            f"unknown={sorted(set(by_id) - expected_ids)!r}"
        )
    return contract_revision, tuple(
        _parse_claim(by_id[item.observation_id], item) for item in spec.observations
    )


def _verify_claims_at_revision(
    *,
    spec: ProductObservationSpec,
    claims: tuple[ObservationClaim, ...],
    product_clone: Path,
    product_revision: str,
) -> tuple[VerifiedObservation, ...]:
    verified: list[VerifiedObservation] = []
    for observation_spec, claim in zip(spec.observations, claims, strict=True):
        source_bytes = read_regular_git_blob(
            product_clone, product_revision, observation_spec
        )
        source_digest = hashlib.sha256(source_bytes).hexdigest()
        if claim.source_blob_sha256 != source_digest:
            raise ObservationRefusal(
                f"observation {claim.observation_id!r} source digest disagrees with Git"
            )
        extracted = extract_observation(observation_spec, source_bytes)
        extract_digest = hashlib.sha256(extracted).hexdigest()
        if claim.extract_sha256 != extract_digest:
            raise ObservationRefusal(
                f"observation {claim.observation_id!r} extract digest "
                "disagrees with Git"
            )
        verified.append(VerifiedObservation(claim=claim, extracted=extracted))
    identity = next(
        (
            item.extracted
            for item in verified
            if item.claim.observation_id == "product-identity"
        ),
        None,
    )
    if identity is None:
        return tuple(verified)
    try:
        decoded_identity = identity.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ObservationRefusal("product identity is not UTF-8") from exc
    if decoded_identity != spec.product_id:
        raise ObservationRefusal(
            f"product identity source declares {decoded_identity!r}; "
            f"Starter fixes it at {spec.product_id!r}"
        )
    return tuple(verified)


def checkout_head_revision(product_clone: Path) -> str:
    try:
        revision = _git(product_clone, ("rev-parse", "--verify", "HEAD^{commit}"))
        decoded = revision.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ObservationAcquisitionError("git returned a non-ASCII HEAD") from exc
    if not _COMMIT.fullmatch(decoded):
        raise ObservationAcquisitionError(
            f"git returned a non-immutable HEAD coordinate: {decoded!r}"
        )
    return decoded


def read_checkout_json_document(
    product_clone: Path,
    *,
    record_path: str = CANONICAL_RECORD_PATH,
    product_revision: str | None = None,
) -> Mapping[str, object]:
    """Read one strict JSON object from a fixed regular Git blob.

    ``product_revision`` lets the action bind record and observations to the
    single immutable HEAD it resolved. Omitting it is convenient for callers
    that only need to inspect the record itself.
    """

    _validate_relative_source_path(record_path)
    record_spec = ObservationSpec(
        "composition-observation-envelope",
        record_path,
        WholeFileLocator(),
        max_source_bytes=1024 * 1024,
    )
    content = read_regular_git_blob(
        product_clone,
        product_revision or checkout_head_revision(product_clone),
        record_spec,
    )

    def refuse_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise ObservationRefusal(f"JSON field {key!r} is duplicated")
            parsed[key] = value
        return parsed

    try:
        document = json.loads(content, object_pairs_hook=refuse_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationRefusal(
            f"{record_path!r} is not a strict UTF-8 JSON document"
        ) from exc
    if not isinstance(document, Mapping):
        raise ObservationRefusal("observation envelope root must be an object")
    return document


def verify_product_checkout_envelope(
    document: Mapping[str, object],
    *,
    spec: ProductObservationSpec,
    product_clone: Path,
    trusted_contract_revision: str,
) -> VerifiedObservationEnvelope:
    """Verify product-authored claims against the checkout's exact Git HEAD.

    Product CI legitimately runs on a pull-request merge commit that is not a
    protected-main ancestor yet. It proves that the record and cited sources
    agree in that exact candidate tree; the central all-of gate separately
    enforces protected-main ancestry before accepting a product revision.
    """

    contract_revision, claims = _parse_envelope_claims(
        document,
        spec=spec,
        trusted_contract_revision=trusted_contract_revision,
    )
    product_revision = checkout_head_revision(product_clone)
    verified = _verify_claims_at_revision(
        spec=spec,
        claims=claims,
        product_clone=product_clone,
        product_revision=product_revision,
    )
    return VerifiedObservationEnvelope(
        repository=spec.repository,
        product_id=spec.product_id,
        subject=spec.subject,
        contract_revision=contract_revision,
        product_revision=product_revision,
        observations=verified,
    )


def load_and_verify_product_checkout_envelope(
    *,
    spec: ProductObservationSpec,
    product_clone: Path,
    trusted_contract_revision: str,
    record_path: str = CANONICAL_RECORD_PATH,
) -> VerifiedObservationEnvelope:
    """Resolve HEAD once, then verify its record and sources at that object."""

    product_revision = checkout_head_revision(product_clone)
    document = read_checkout_json_document(
        product_clone,
        record_path=record_path,
        product_revision=product_revision,
    )
    contract_revision, claims = _parse_envelope_claims(
        document,
        spec=spec,
        trusted_contract_revision=trusted_contract_revision,
    )
    verified = _verify_claims_at_revision(
        spec=spec,
        claims=claims,
        product_clone=product_clone,
        product_revision=product_revision,
    )
    return VerifiedObservationEnvelope(
        repository=spec.repository,
        product_id=spec.product_id,
        subject=spec.subject,
        contract_revision=contract_revision,
        product_revision=product_revision,
        observations=verified,
    )


def verify_observation_envelope(
    document: Mapping[str, object],
    *,
    spec: ProductObservationSpec,
    product_clone: Path,
    product_revision: str,
    trusted_contract_revision: str,
    protected_main_ref: str = "origin/main",
) -> VerifiedObservationEnvelope:
    """Verify one product envelope against immutable protected-main Git blobs.

    The caller must load ``spec`` from ``trusted_contract_revision``; the
    catalogue universe is therefore the one at the record's contract commit,
    not whatever happens to be checked out as Starter HEAD. All payload
    coordinates are rejected before the first Git operation. Fetch/checkout
    failure is an acquisition error, not an intentional unbound state.
    """

    contract_revision, claims = _parse_envelope_claims(
        document,
        spec=spec,
        trusted_contract_revision=trusted_contract_revision,
    )

    # Caller-authored coordinates are all rejected before the first Git call.
    _require_protected_main_ancestor(
        product_clone, product_revision, protected_main_ref
    )
    verified = _verify_claims_at_revision(
        spec=spec,
        claims=claims,
        product_clone=product_clone,
        product_revision=product_revision,
    )

    return VerifiedObservationEnvelope(
        repository=spec.repository,
        product_id=spec.product_id,
        subject=spec.subject,
        contract_revision=contract_revision,
        product_revision=product_revision,
        observations=verified,
    )
