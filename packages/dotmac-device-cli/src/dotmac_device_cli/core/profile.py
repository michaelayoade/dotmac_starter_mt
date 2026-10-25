"""Per-model capability profile: schema, strict loader and selection.

A profile is DATA. One TOML file describes one (vendor, model family, firmware
train). The loader rejects unknown keys, missing keys, bad regexes, unknown
template placeholders and an unproven ``lab_verified`` claim, so a profile that
loads is a profile that means what it says. Every field either has a value or
the literal ``"unsupported"``; nothing is silently defaulted.

Profiles carry no site names, addresses, usernames, credentials or fleet counts.
The repository is public; a consuming product keeps its own fleet-to-profile map.
"""

from __future__ import annotations

import hashlib
import json
import re
import string
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from dotmac_device_cli.core.results import Reason

UNSUPPORTED = "unsupported"
VERIFICATION_STATUSES = frozenset({"unverified", "prod_observed", "lab_verified"})
FAMILIES = frozenset({"olt", "cpe"})
PAGER_ANSWERS = frozenset({"answer_space"})
STRIP_KINDS = frozenset({"pager_marker", "ansi"})
LOGOUT_STATES = frozenset({"user", "privileged"})

# Vendor-neutral typed operations a profile may claim. The dialect composes the
# command and parses the reply; the engine never names a vendor.
OPERATIONS = frozenset({"version", "list_onts", "ont_detail", "service_ports"})

# Classifier ids a profile may define: every Reason, plus the one that is a
# VALUE rather than a failure (the device affirmatively said the thing is absent).
MARKER_IDS = frozenset({r.value for r in Reason} | {"ont_absent"})

# Parameters a template may reference. Each has a validator in ``render``.
KNOWN_PARAMS = frozenset({"frame", "slot", "port", "ont_id", "serial_hex16", "index"})

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDER = string.Formatter()


class ProfileError(ValueError):
    """The profile data does not satisfy the schema."""


@lru_cache(maxsize=512)
def compile_regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


@dataclass(frozen=True, slots=True)
class Verification:
    status: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class SshPolicy:
    """Algorithm preferences the HOST applies. The package never connects."""

    kex: tuple[str, ...]
    host_key_types: tuple[str, ...]
    ciphers: tuple[str, ...]
    macs: tuple[str, ...]
    password_only: bool


@dataclass(frozen=True, slots=True)
class Prompts:
    user: str
    privileged: str
    config: str
    interface: Mapping[str, str]
    derive_from_prompt_line: bool
    pager: str
    continuation: str
    confirm: str


@dataclass(frozen=True, slots=True)
class Privilege:
    enter: str
    expect: str
    elevate: str  # ``unsupported`` or a template needing a host-supplied secret


@dataclass(frozen=True, slots=True)
class Paging:
    off_command: str  # ``unsupported`` or a command
    on_pager: str
    strip: tuple[str, ...]

    @property
    def off_supported(self) -> bool:
        return self.off_command != UNSUPPORTED


@dataclass(frozen=True, slots=True)
class SendPolicy:
    atomic_line: bool
    settle_seconds: float
    drain_before_command: bool
    inter_command_delay: float
    max_line_length: int
    verify_echo: bool


@dataclass(frozen=True, slots=True)
class ContinuationPolicy:
    answer_cr_only_if_offered: bool
    max_cr_answers: int


@dataclass(frozen=True, slots=True)
class LogoutPolicy:
    command: str
    max_attempts: int
    confirm_regex: str
    confirm_answer: str
    only_from_states: tuple[str, ...]
    max_seconds: float


@dataclass(frozen=True, slots=True)
class Confirmation:
    """The ONLY confirmation a write template may answer: exact regex, exact answer."""

    regex: str
    answer: str


@dataclass(frozen=True, slots=True)
class Limits:
    session_slots: int
    reserved_priority_slots: int
    release_grace_seconds: float
    relogin_grace_seconds: float
    max_reopens_per_scope: int
    write_max_logins: int


@dataclass(frozen=True, slots=True)
class Template:
    command: str
    max_seconds: float
    enter: str | None = None


@dataclass(frozen=True, slots=True)
class Quirk:
    text: str
    transcripts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Detect:
    """Regexes (one capture group each) that read model and firmware from the
    device's own version text. Used only to REPORT a mismatch, never to choose."""

    model: str
    firmware: str


@dataclass(frozen=True, slots=True)
class Profile:
    id: str
    vendor: str
    family: str
    dialect: str
    model_patterns: tuple[str, ...]
    firmware_patterns: tuple[str, ...]
    verification: Verification
    ssh: SshPolicy
    prompts: Prompts
    privilege: Privilege
    paging: Paging
    send: SendPolicy
    continuation: ContinuationPolicy
    logout: LogoutPolicy
    confirmations: Mapping[str, Confirmation]
    limits: Limits
    read_templates: Mapping[str, Template]
    write_templates: Mapping[str, Template]
    error_markers: Mapping[str, str]
    capabilities: Mapping[str, bool]
    quirks: Mapping[str, Quirk]
    detect: Detect
    operations: Mapping[str, bool]
    digest: str
    layer_path: tuple[str, ...] = ()  # where its layers live; also its corpus chain

    def claims(self, operation: str) -> bool:
        """True only when the profile EXPLICITLY declares the operation.

        Undeclared means unsupported. There is no fallback to another vendor's
        or family's syntax.
        """
        return self.operations.get(operation) is True

    def matches(self, vendor: str, model: str, firmware: str) -> bool:
        if vendor.strip().lower() != self.vendor:
            return False
        model_ok = any(re.search(p, model, re.IGNORECASE) for p in self.model_patterns)
        firmware_ok = any(re.search(p, firmware) for p in self.firmware_patterns)
        return model_ok and firmware_ok


@dataclass(frozen=True, slots=True)
class NoProfile:
    """No profile covers this device. The host must not guess one."""

    vendor: str
    model: str
    firmware: str


# ── strict loading ──────────────────────────────────────────────────────────


def _table(data: Mapping[str, Any], key: str, path: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ProfileError(f"{path}{key}: table required")
    return value


def _exact_keys(
    data: Mapping[str, Any], path: str, keys: set[str], *, prefix_ok: str = ""
) -> None:
    present = set(data)
    extra = {k for k in present - keys if not (prefix_ok and k.startswith(prefix_ok))}
    missing = keys - present
    if extra:
        raise ProfileError(f"{path}: unknown key(s) {sorted(extra)}")
    if missing:
        raise ProfileError(f"{path}: missing key(s) {sorted(missing)}")


def _str(data: Mapping[str, Any], key: str, path: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ProfileError(f"{path}.{key}: non-empty string required")
    return value


def _bool(data: Mapping[str, Any], key: str, path: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise ProfileError(f"{path}.{key}: boolean required")
    return value


def _int(data: Mapping[str, Any], key: str, path: str, *, minimum: int = 0) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProfileError(f"{path}.{key}: integer >= {minimum} required")
    return value


def _num(data: Mapping[str, Any], key: str, path: str) -> float:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ProfileError(f"{path}.{key}: non-negative number required")
    return float(value)


def _strs(data: Mapping[str, Any], key: str, path: str) -> tuple[str, ...]:
    value = data[key]
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) and v for v in value)
    ):
        raise ProfileError(f"{path}.{key}: non-empty list of strings required")
    return tuple(value)


def _regex(data: Mapping[str, Any], key: str, path: str) -> str:
    pattern = _str(data, key, path)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ProfileError(f"{path}.{key}: invalid regex ({exc})") from exc
    return pattern


def placeholders(command: str) -> set[str]:
    names: set[str] = set()
    for _, field, _, _ in _PLACEHOLDER.parse(command):
        if field is not None:
            names.add(field)
    return names


def _templates(data: Mapping[str, Any], path: str) -> dict[str, Template]:
    out: dict[str, Template] = {}
    for tid, raw in data.items():
        where = f"{path}.{tid}"
        if isinstance(raw, str):
            cmd, max_seconds, enter = raw, 60.0, None
        elif isinstance(raw, dict):
            unknown = set(raw) - {"cmd", "max_seconds", "enter"}
            if unknown or "cmd" not in raw:
                raise ProfileError(f"{where}: keys must be cmd[, max_seconds, enter]")
            cmd = raw["cmd"]
            max_seconds = float(raw.get("max_seconds", 60))
            enter = raw.get("enter")
        else:
            raise ProfileError(f"{where}: string or table required")
        if not isinstance(cmd, str) or not cmd.strip():
            raise ProfileError(f"{where}: command must be a non-empty string")
        for text in (cmd, enter or ""):
            unknown_params = placeholders(text) - KNOWN_PARAMS
            if unknown_params:
                raise ProfileError(
                    f"{where}: unknown placeholder(s) {sorted(unknown_params)}"
                )
        out[tid] = Template(command=cmd, max_seconds=max_seconds, enter=enter)
    return out


def load_profile(text: str) -> Profile:
    """Parse and validate one COMPLETE profile document. Raises ``ProfileError``."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"not valid TOML: {exc}") from exc
    return load_profile_document(data)


def load_profile_document(data: dict[str, Any]) -> Profile:
    """Validate a complete (already merged) document."""
    _exact_keys(
        data,
        "profile document",
        {
            "profile", "ssh", "prompts", "privilege", "paging", "send",
            "continuation", "logout", "confirmations", "limits", "templates",
            "error_markers", "capabilities", "quirks", "detect", "operations",
        },
    )  # fmt: skip

    meta = _table(data, "profile", "")
    _exact_keys(
        meta,
        "profile",
        {
            "id",
            "vendor",
            "family",
            "dialect",
            "model_patterns",
            "firmware_patterns",
            "verification",
        },  # fmt: skip
    )
    family = _str(meta, "family", "profile")
    if family not in FAMILIES:
        raise ProfileError(f"profile.family: one of {sorted(FAMILIES)}")
    ver = meta["verification"]
    if not isinstance(ver, dict):
        raise ProfileError("profile.verification: table required")
    _exact_keys(ver, "profile.verification", {"status", "receipt_sha256"})
    status = _str(ver, "status", "profile.verification")
    if status not in VERIFICATION_STATUSES:
        raise ProfileError(
            f"profile.verification.status: one of {sorted(VERIFICATION_STATUSES)}"
        )
    receipt = ver["receipt_sha256"]
    if not isinstance(receipt, str):
        raise ProfileError("profile.verification.receipt_sha256: string required")
    if status == "lab_verified" and not _SHA256_HEX.fullmatch(receipt):
        raise ProfileError(
            "lab_verified requires a lowercase 64-hex receipt_sha256; "
            "a profile cannot claim a lab it has no receipt for"
        )
    model_patterns = _strs(meta, "model_patterns", "profile")
    firmware_patterns = _strs(meta, "firmware_patterns", "profile")
    for pattern in (*model_patterns, *firmware_patterns):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ProfileError(f"profile pattern {pattern!r}: {exc}") from exc

    ssh = _table(data, "ssh", "")
    _exact_keys(
        ssh, "ssh", {"kex", "host_key_types", "ciphers", "macs", "password_only"}
    )

    prm = _table(data, "prompts", "")
    _exact_keys(
        prm,
        "prompts",
        {
            "user",
            "privileged",
            "config",
            "derive_from_prompt_line",
            "pager",
            "continuation",
            "confirm",
        },  # fmt: skip
        prefix_ok="interface_",
    )
    interface = {
        k.removeprefix("interface_"): _regex(prm, k, "prompts")
        for k in prm
        if k.startswith("interface_")
    }

    priv = _table(data, "privilege", "")
    _exact_keys(priv, "privilege", {"enter", "expect", "elevate"})
    if _str(priv, "expect", "privilege") not in ("user", "privileged"):
        raise ProfileError("privilege.expect: user or privileged")

    pag = _table(data, "paging", "")
    _exact_keys(pag, "paging", {"off_command", "on_pager", "strip"})
    if _str(pag, "on_pager", "paging") not in PAGER_ANSWERS:
        raise ProfileError(f"paging.on_pager: one of {sorted(PAGER_ANSWERS)}")
    strip = _strs(pag, "strip", "paging")
    if not set(strip) <= STRIP_KINDS:
        raise ProfileError(f"paging.strip: subset of {sorted(STRIP_KINDS)}")

    snd = _table(data, "send", "")
    _exact_keys(
        snd,
        "send",
        {"atomic_line", "settle_seconds", "drain_before_command",
         "inter_command_delay", "max_line_length", "verify_echo"},
    )  # fmt: skip

    cont = _table(data, "continuation", "")
    _exact_keys(cont, "continuation", {"answer_cr_only_if_offered", "max_cr_answers"})

    lg = _table(data, "logout", "")
    _exact_keys(
        lg,
        "logout",
        {"command", "max_attempts", "confirm_regex", "confirm_answer",
         "only_from_states", "max_seconds"},
    )  # fmt: skip
    states = _strs(lg, "only_from_states", "logout")
    if not set(states) <= LOGOUT_STATES:
        raise ProfileError(
            f"logout.only_from_states: subset of {sorted(LOGOUT_STATES)}"
        )

    lim = _table(data, "limits", "")
    _exact_keys(
        lim,
        "limits",
        {"session_slots", "reserved_priority_slots", "release_grace_seconds",
         "relogin_grace_seconds", "max_reopens_per_scope", "write_max_logins"},
    )  # fmt: skip

    tpl = _table(data, "templates", "")
    _exact_keys(tpl, "templates", {"read", "write"})
    read_templates = _templates(_table(tpl, "read", "templates."), "templates.read")
    write_templates = _templates(_table(tpl, "write", "templates."), "templates.write")

    conf_raw = _table(data, "confirmations", "")
    confirmations: dict[str, Confirmation] = {}
    for tid, raw in conf_raw.items():
        if tid not in write_templates:
            raise ProfileError(f"confirmations.{tid}: not a write template")
        if not isinstance(raw, dict):
            raise ProfileError(f"confirmations.{tid}: table required")
        _exact_keys(raw, f"confirmations.{tid}", {"regex", "answer"})
        confirmations[tid] = Confirmation(
            regex=_regex(raw, "regex", f"confirmations.{tid}"),
            answer=_str(raw, "answer", f"confirmations.{tid}"),
        )

    markers_raw = _table(data, "error_markers", "")
    markers: dict[str, str] = {}
    for mid in markers_raw:
        if mid not in MARKER_IDS:
            raise ProfileError(f"error_markers.{mid}: not a known classifier id")
        markers[mid] = _regex(markers_raw, mid, "error_markers")

    caps_raw = _table(data, "capabilities", "")
    caps: dict[str, bool] = {}
    for cid in caps_raw:
        caps[cid] = _bool(caps_raw, cid, "capabilities")

    det_raw = _table(data, "detect", "")
    _exact_keys(det_raw, "detect", {"model", "firmware"})
    detect = Detect(
        model=_regex(det_raw, "model", "detect"),
        firmware=_regex(det_raw, "firmware", "detect"),
    )
    ops_raw = _table(data, "operations", "")
    operations: dict[str, bool] = {}
    for oid in ops_raw:
        if oid not in OPERATIONS:
            raise ProfileError(f"operations.{oid}: not a known operation")
        operations[oid] = _bool(ops_raw, oid, "operations")

    quirks_raw = _table(data, "quirks", "")
    quirks: dict[str, Quirk] = {}
    for qid, raw in quirks_raw.items():
        if not isinstance(raw, dict):
            raise ProfileError(f"quirks.{qid}: table required")
        _exact_keys(raw, f"quirks.{qid}", {"text", "transcripts"})
        quirks[qid] = Quirk(
            text=_str(raw, "text", f"quirks.{qid}"),
            transcripts=_strs(raw, "transcripts", f"quirks.{qid}"),
        )

    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    slots = _int(lim, "session_slots", "limits", minimum=1)
    return Profile(
        id=_str(meta, "id", "profile"),
        vendor=_str(meta, "vendor", "profile"),
        family=family,
        dialect=_str(meta, "dialect", "profile"),
        model_patterns=model_patterns,
        firmware_patterns=firmware_patterns,
        verification=Verification(status=status, receipt_sha256=receipt),
        ssh=SshPolicy(
            kex=_strs(ssh, "kex", "ssh"),
            host_key_types=_strs(ssh, "host_key_types", "ssh"),
            ciphers=_strs(ssh, "ciphers", "ssh"),
            macs=_strs(ssh, "macs", "ssh"),
            password_only=_bool(ssh, "password_only", "ssh"),
        ),
        prompts=Prompts(
            user=_regex(prm, "user", "prompts"),
            privileged=_regex(prm, "privileged", "prompts"),
            config=_regex(prm, "config", "prompts"),
            interface=interface,
            derive_from_prompt_line=_bool(prm, "derive_from_prompt_line", "prompts"),
            pager=_regex(prm, "pager", "prompts"),
            continuation=_regex(prm, "continuation", "prompts"),
            confirm=_regex(prm, "confirm", "prompts"),
        ),
        privilege=Privilege(
            enter=_str(priv, "enter", "privilege"),
            expect=_str(priv, "expect", "privilege"),
            elevate=_str(priv, "elevate", "privilege"),
        ),
        paging=Paging(
            off_command=_str(pag, "off_command", "paging"),
            on_pager=_str(pag, "on_pager", "paging"),
            strip=strip,
        ),
        send=SendPolicy(
            atomic_line=_bool(snd, "atomic_line", "send"),
            settle_seconds=_num(snd, "settle_seconds", "send"),
            drain_before_command=_bool(snd, "drain_before_command", "send"),
            inter_command_delay=_num(snd, "inter_command_delay", "send"),
            max_line_length=_int(snd, "max_line_length", "send", minimum=1),
            verify_echo=_bool(snd, "verify_echo", "send"),
        ),
        continuation=ContinuationPolicy(
            answer_cr_only_if_offered=_bool(
                cont, "answer_cr_only_if_offered", "continuation"
            ),
            max_cr_answers=_int(cont, "max_cr_answers", "continuation"),
        ),
        logout=LogoutPolicy(
            command=_str(lg, "command", "logout"),
            max_attempts=_int(lg, "max_attempts", "logout", minimum=1),
            confirm_regex=_regex(lg, "confirm_regex", "logout"),
            confirm_answer=_str(lg, "confirm_answer", "logout"),
            only_from_states=states,
            max_seconds=_num(lg, "max_seconds", "logout"),
        ),
        confirmations=confirmations,
        limits=Limits(
            session_slots=slots,
            reserved_priority_slots=_int(lim, "reserved_priority_slots", "limits"),
            release_grace_seconds=_num(lim, "release_grace_seconds", "limits"),
            relogin_grace_seconds=_num(lim, "relogin_grace_seconds", "limits"),
            max_reopens_per_scope=_int(lim, "max_reopens_per_scope", "limits"),
            write_max_logins=_int(lim, "write_max_logins", "limits", minimum=1),
        ),
        read_templates=read_templates,
        write_templates=write_templates,
        error_markers=markers,
        capabilities=caps,
        quirks=quirks,
        detect=detect,
        operations=operations,
        digest=digest,
    )


# ── command rendering ───────────────────────────────────────────────────────

_DIGITS = re.compile(r"^\d{1,3}$")
_HEX16 = re.compile(r"^[0-9A-F]{16}$")


class CommandRejected(ValueError):
    """A parameter or rendered line failed validation. Rejected, never stripped."""


def _check_param(name: str, value: object) -> str:
    text = str(value)
    if name in {"frame", "slot", "port"}:
        if not _DIGITS.fullmatch(text):
            raise CommandRejected(f"{name}: 1-3 digits required")
    elif name in {"ont_id", "index"}:
        if isinstance(value, bool) or not _DIGITS.fullmatch(text):
            raise CommandRejected(f"{name}: integer 0-999 required")
    elif name == "serial_hex16":
        if not _HEX16.fullmatch(text):
            raise CommandRejected("serial_hex16: 16 upper-case hex digits required")
    else:  # pragma: no cover - guarded by KNOWN_PARAMS at load time
        raise CommandRejected(f"{name}: unknown parameter")
    return text


def check_line(line: str, *, max_length: int) -> None:
    """Final check on a rendered line: single, printable ASCII, no ``;``."""
    if not line or len(line) > max_length:
        raise CommandRejected(f"line must be 1..{max_length} characters")
    if any(not (0x20 <= ord(ch) <= 0x7E) for ch in line):
        raise CommandRejected("line must be printable ASCII on one physical line")
    if ";" in line:
        raise CommandRejected("command separators are not allowed")


def render(template: Template, profile: Profile, **params: object) -> str:
    """Render a template with typed parameters, then validate the final line."""
    needed = placeholders(template.command)
    if set(params) != needed:
        raise CommandRejected(
            f"parameters must be exactly {sorted(needed)}, got {sorted(params)}"
        )
    line = template.command.format(**{k: _check_param(k, v) for k, v in params.items()})
    check_line(line, max_length=profile.send.max_line_length)
    return line
