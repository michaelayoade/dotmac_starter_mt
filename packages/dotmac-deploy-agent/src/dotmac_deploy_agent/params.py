"""params.env: canonical render, refusals, write, post-run check (DESIGN 2.5).

Control computes `canonical_render(params)` at plan time and binds its digest in
the plan. The agent re-renders from the same typed params and refuses unless its
digest equals the plan's. The file holds NON-secret material only.

Render rule (must match Control, pinned by the vectors in the tests): one line
`MATERIAL=value` + LF per param that has a `material`, plus `APP_IMAGE=<planned
image reference>`, ALL sorted bytewise by name.
"""

from __future__ import annotations

import math
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from .canon import digest
from .files import read_regular, remove_file, require_private_dir, write_private
from .refusal import Refusal

APP_IMAGE: Final = "APP_IMAGE"
PARAMS_FILE: Final = "params.env"
_MATERIAL: Final = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
# Control's `SECRET_NAMED_MATERIAL` (vector `secret_name_vectors`): a material whose
# NAME says secret may only point at a FILE (type path), never carry a value.
_SECRETISH_NAME: Final = re.compile(
    r"PASS|PWD|SECRET|TOKEN|CREDENTIAL|DSN|SALT|COOKIE|PRIVATE_KEY|API_KEY"
    r"|(?:^|_)KEY(?:$|_)"
)
# Control's `looks_secret_shaped`, ported rule for rule (contract/target_api_v1.json
# `value_vectors`): the agent refuses nothing Control accepts and accepts nothing
# Control refuses. Any change is a vector change in Control first.
_SECRET_SHAPES: Final = (
    re.compile(r"-----BEGIN [A-Z ]*-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."),
    re.compile(r"\bhv[sbr]\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bs\.[A-Za-z0-9]{24}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"://[^/\s:@]*:[^/\s@]+@"),
)
_HIGH_ENTROPY_RUN: Final = re.compile(r"[A-Za-z0-9+_=-]{32,}")
#: Types Control returns before its secret-shape check (never string-like).
_NOT_STRINGLIKE: Final = frozenset({"bool", "int", "duration_seconds", "image_digest"})
OVERRIDE_NAMES: Final = (
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
    "compose.override.yml",
    "compose.override.yaml",
)


@dataclass(frozen=True)
class Param:
    name: str
    material: str | None
    type: str
    value: str


def _entropy(text: str) -> float:
    counts: dict[str, int] = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    return -sum((n / len(text)) * math.log2(n / len(text)) for n in counts.values())


def secret_shaped(value: str) -> bool:
    if any(shape.search(value) for shape in _SECRET_SHAPES):
        return True
    for run in _HIGH_ENTROPY_RUN.findall(value):
        if (
            any(c.isdigit() for c in run)
            and any(c.isalpha() for c in run)
            and _entropy(run) >= 4.0
        ):
            return True
    return False


def check_alterable(value: str) -> None:
    """Refuse what Foundation's env parser would alter, and an empty value."""
    if (
        not value
        or "\n" in value
        or "\r" in value
        or "\x00" in value
        or value != value.strip()
        or (len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"")
    ):
        raise Refusal("params_value_invalid")


_INT: Final = re.compile(r"-?[0-9]{1,18}")
_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")


def check_value(value: str, kind: str = "string") -> None:
    check_alterable(value)
    # Control returns before its secret-shape check for these types, because it
    # first requires their exact shape: so must the agent, or a token could ride
    # in an `int` param.
    if kind in ("int", "duration_seconds") and not _INT.fullmatch(value):
        raise Refusal("params_value_invalid")
    if kind == "bool" and value not in ("true", "false"):
        raise Refusal("params_value_invalid")
    if kind == "image_digest" and not _DIGEST.fullmatch(value):
        raise Refusal("params_value_invalid")
    if kind not in _NOT_STRINGLIKE and secret_shaped(value):
        raise Refusal("params_secret_shaped")


def canonical_render(params: Sequence[Param], app_image: str) -> bytes:
    lines = _material_lines(params)
    check_alterable(app_image)  # the planned image is Control's; never secret-checked
    lines[APP_IMAGE] = app_image
    return "".join(f"{name}={lines[name]}\n" for name in sorted(lines)).encode("utf-8")


def job_env_render(params: Sequence[Param]) -> bytes:
    """One job's scoped params: `MATERIAL=value` lines, sorted, no `APP_IMAGE`."""
    lines = _material_lines(params)
    return "".join(f"{name}={lines[name]}\n" for name in sorted(lines)).encode("utf-8")


def _material_lines(params: Sequence[Param]) -> dict[str, str]:
    lines: dict[str, str] = {}
    for param in params:
        if param.type == "secret":
            raise Refusal("params_secret_shaped")
        if param.material is None:
            continue
        if not _MATERIAL.fullmatch(param.material) or param.material == APP_IMAGE:
            raise Refusal("params_value_invalid")
        if param.material in lines:
            raise Refusal("params_value_invalid")
        # A secret-named material may only point at a FILE, never carry a value.
        if _SECRETISH_NAME.search(param.material) and param.type != "path":
            raise Refusal("params_secret_shaped")
        check_value(param.value, param.type)
        lines[param.material] = param.value
    return lines


def render_digest(render: bytes) -> str:
    return digest(render)


def with_app_image(render: bytes, app_image: str) -> bytes:
    """The same render carrying another `APP_IMAGE` pin (Foundation's restore)."""
    out: list[bytes] = []
    for line in render.splitlines(keepends=True):
        out.append(
            f"{APP_IMAGE}={app_image}\n".encode()
            if line.startswith(b"APP_IMAGE=")
            else line
        )
    return b"".join(out)


def refuse_preexisting_overrides(deploy_dir: Path) -> None:
    """`<deploy_dir>/.env` or a Compose override would shadow the generated file."""
    if os.path.lexists(deploy_dir / ".env"):
        raise Refusal("dotenv_present")
    if any(os.path.lexists(deploy_dir / name) for name in OVERRIDE_NAMES):
        raise Refusal("compose_override_present")


def params_path(deploy_run_dir: Path) -> Path:
    return deploy_run_dir / PARAMS_FILE


def write_params_env(deploy_run_dir: Path, render: bytes) -> Path:
    require_private_dir(deploy_run_dir)
    path = params_path(deploy_run_dir)
    write_private(path, render, 0o400)
    return path


class PostRun(Enum):
    PLANNED = "planned"
    PLANNED_PREVIOUS_PIN = "planned_previous_pin"
    INTEGRITY_VIOLATION = "integrity_violation"


def classify_post_run(
    path: Path, *, planned_render: bytes, previous_app_image: str | None
) -> PostRun:
    """After the run the file must equal the planned render, or that render with
    the previous pin (Foundation's restore on a failed switch). Anything else,
    including a missing file, a symlink, another owner or a wide mode, is an
    integrity violation. Foundation changes the mode to 0600; 0400 is untouched.
    """
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
        ):
            return PostRun.INTEGRITY_VIOLATION
        content = read_regular(path, limit=1024 * 1024)
    except (OSError, Refusal):
        return PostRun.INTEGRITY_VIOLATION
    if content == planned_render:
        return PostRun.PLANNED
    if previous_app_image is not None and content == with_app_image(
        planned_render, previous_app_image
    ):
        return PostRun.PLANNED_PREVIOUS_PIN
    return PostRun.INTEGRITY_VIOLATION


def remove_runtime_files(deploy_run_dir: Path, names: Sequence[str]) -> None:
    for name in names:
        remove_file(deploy_run_dir / name)
