"""Reusable conformance suite: every profile must pass it, in CI.

A profile is not trusted because it loads. It must PROVE, with synthetic
transcripts and hand-written goldens, that its prompts and modes are detected,
the pager and continuation prompts are answered correctly, logout is clean and
refuses foreign questions, an unreadable reply is never mistaken for an empty
one, native addresses round-trip, and EVERY capability it claims has a replayed
transcript with a golden. A claimed capability with no transcript fails.

Corpus layout (one directory chain per profile; shared cases live at the
broadest layer that holds for every descendant)::

    <root>/<vendor>/conformance.toml
    <root>/<vendor>/<operation-dir>/<case>.txt|.expected.json|.meta.toml
    <root>/<vendor>/<family>/<model>/<firmware>/<operation-dir>/<case>...

A case's ``.txt`` is the device's full reply to one command, byte for byte
(``{command}`` stands for the echoed command line so a shared case holds for every
model's spelling). ``.expected.json`` is written BY HAND from the device's own
words and is never regenerated from the parser under test.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotmac_device_cli.core.classifier import OntInventory
from dotmac_device_cli.core.dialect import RefError, dialect_for
from dotmac_device_cli.core.ops import list_onts, ont_detail, service_ports, version
from dotmac_device_cli.core.profile import CAPABILITIES, Profile
from dotmac_device_cli.core.results import Ok, Unreadable, Unsupported
from dotmac_device_cli.core.session import (
    CliSession,
    LogoutResult,
    State,
    state_for_prompt_line,
)
from dotmac_device_cli.testing import FakeClock, ReplayChannel

REQUIRED_COVERS = frozenset(
    {"pager", "continuation", "logout_clean", "logout_foreign_confirm",
     "empty_explicit", "empty_unreadable"}
)  # fmt: skip

_OPS = {
    "version": version,
    "list_onts": list_onts,
    "ont_detail": ont_detail,
    "service_ports": service_ports,
}


@dataclass(frozen=True, slots=True)
class Case:
    id: str  # "<operation-dir>/<case>"
    layer_dir: Path
    operation: str  # an operation name or "logout"
    command: str  # exact command line or "*"
    ref: str | None
    covers: frozenset[str]
    capabilities: frozenset[str]
    eof_after_answer: bool
    text: str
    expected: Mapping[str, Any]


def chain_dirs(root: Path, layer_path: tuple[str, ...]) -> list[Path]:
    return [root.joinpath(*layer_path[: i + 1]) for i in range(len(layer_path))]


def load_cases(dirs: list[Path]) -> dict[str, Case]:
    """Cases visible to a profile; a later (narrower) layer overrides an earlier id."""
    cases: dict[str, Case] = {}
    for directory in dirs:
        if not directory.is_dir():
            continue
        for meta_path in sorted(directory.glob("*/*.meta.toml")):
            name = meta_path.name.removesuffix(".meta.toml")
            cid = f"{meta_path.parent.name}/{name}"
            meta = tomllib.loads(meta_path.read_text(encoding="utf-8"))
            txt = meta_path.with_name(f"{name}.txt").read_bytes().decode("utf-8")
            expected = json.loads(
                meta_path.with_name(f"{name}.expected.json").read_text(encoding="utf-8")
            )
            cases[cid] = Case(
                id=cid,
                layer_dir=directory,
                operation=meta["operation"],
                command=meta.get("command", "*"),
                ref=meta.get("ref"),
                covers=frozenset(meta.get("covers", [])),
                capabilities=frozenset(meta.get("capabilities", [])),
                eof_after_answer=bool(meta.get("eof_after_answer", False)),
                text=txt,
                expected=expected,
            )
    return cases


def _describe(result: object) -> dict[str, Any]:
    if isinstance(result, Ok):
        value = result.value
        if isinstance(value, OntInventory):
            return {
                "status": "ok",
                "explicitly_empty": value.explicitly_empty,
                "entries": [
                    {
                        "ref": e.ref.native,
                        "serial": e.serial,
                        "admin_state": e.admin_state,
                        "run_state": e.run_state,
                        "config_state": e.config_state,
                        "match_state": e.match_state,
                        "description": e.description,
                    }
                    for e in value.entries
                ],
            }
        return {"status": "ok", "text": value}
    if isinstance(result, Unreadable):
        return {
            "status": "unreadable",
            "reason": result.reason.value,
            "detail": result.detail,
        }
    if isinstance(result, Unsupported):
        return {"status": "unsupported", "operation": result.operation}
    raise TypeError(type(result))


def replay_case(profile: Profile, case: Case) -> dict[str, Any]:
    """Run one case through a fresh session over a replay channel."""
    clock = FakeClock()
    channel = ReplayChannel(
        clock,
        pager_regex=r"---- More(?:\s*\([^)]*\)\s*)?----",
        replies={f"{case.command}\n": case.text} if case.command != "*" else {},
        default_reply=case.text if case.command == "*" else None,
        eof_after_answer=case.eof_after_answer,
    )
    session = CliSession.attach(channel, profile, clock=clock)
    prepared = session.prepare()
    if isinstance(prepared, Unreadable):
        return {"status": "prepare_failed", "reason": prepared.reason.value}
    channel.sent.clear()
    if case.operation == "logout":
        result: dict[str, Any] = {"status": "logout", "result": session.logout().value}
    else:
        dialect = dialect_for(profile.dialect)
        ref = dialect.parse_ref(case.ref) if (dialect and case.ref) else None
        op = _OPS[case.operation]
        outcome = op(session, ref) if ref is not None else op(session)  # type: ignore[operator]
        result = _describe(outcome)
    sent = list(channel.sent)
    if case.command == "*" and sent:
        first = sent[0]  # a shared case holds for every model's spelling
        sent = ["{command}\n" if item == first else item for item in sent]
    return {"result": result, "sent": sent, "final_state": session.state.value}


def prompt_violations(profile: Profile, spec: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    want = {"user": State.USER, "privileged": State.PRIVILEGED, "config": State.CONFIG}
    for key, state in want.items():
        line = spec.get("prompts", {}).get(key)
        if line is None:
            out.append(f"conformance.toml has no sample prompt for {key}")
        elif state_for_prompt_line(profile, line) is not state:
            out.append(f"prompt {line!r} not detected as {state.value}")
    for line in spec.get("prompts", {}).get("interface", []):
        if state_for_prompt_line(profile, line) is not State.INTERFACE:
            out.append(f"prompt {line!r} not detected as interface")
    for line in spec.get("prompts", {}).get("not_a_prompt", []):
        if state_for_prompt_line(profile, line) is not None:
            out.append(f"{line!r} must not be detected as a prompt")
    return out


def ref_violations(profile: Profile, spec: Mapping[str, Any]) -> list[str]:
    dialect = dialect_for(profile.dialect)
    if dialect is None:
        return [f"no dialect {profile.dialect!r} is registered"]
    out: list[str] = []
    for sample in spec.get("refs", {}).get("samples", []):
        try:
            ref = dialect.parse_ref(sample)
        except RefError as exc:
            out.append(f"ref {sample!r} did not parse: {exc}")
            continue
        again = dialect.parse_ref(dialect.format_ref(ref))
        if again != ref or ref.native != dialect.format_ref(ref):
            out.append(f"ref {sample!r} does not round-trip")
    for bad in spec.get("refs", {}).get("invalid", []):
        try:
            dialect.parse_ref(bad)
            out.append(f"invalid ref {bad!r} was accepted")
        except RefError:
            pass
    if not spec.get("refs", {}).get("samples"):
        out.append("conformance.toml declares no ref samples")
    return out


def conformance_violations(profile: Profile, corpus_root: Path) -> list[str]:
    """Every way ``profile`` fails the suite. Empty means it passes."""
    problems: list[str] = []
    dirs = chain_dirs(corpus_root, profile.layer_path)
    cases = load_cases(dirs)

    spec_path = next(
        (
            d / "conformance.toml"
            for d in reversed(dirs)
            if (d / "conformance.toml").is_file()
        ),
        None,
    )
    if spec_path is None:
        return [f"{profile.id}: no conformance.toml in its corpus chain"]
    spec = tomllib.loads(spec_path.read_text(encoding="utf-8"))
    problems += [f"{profile.id}: {m}" for m in prompt_violations(profile, spec)]
    problems += [f"{profile.id}: {m}" for m in ref_violations(profile, spec)]

    for cid, case in sorted(cases.items()):
        try:
            actual = replay_case(profile, case)
        except Exception as exc:
            problems.append(f"{profile.id}: {cid} raised {type(exc).__name__}: {exc}")
            continue
        if actual != case.expected:
            problems.append(f"{profile.id}: {cid} differs from its golden")

    claimed = [op for op, on in profile.operations.items() if on]
    for op in claimed:
        if not any(c.operation == op for c in cases.values()):
            problems.append(f"{profile.id}: claims {op!r} but has no transcript for it")
    proven = (
        frozenset().union(*(c.capabilities for c in cases.values()))
        if cases
        else frozenset()
    )
    for tag in sorted(proven - CAPABILITIES):
        problems.append(f"{profile.id}: a transcript tags unknown capability {tag!r}")
    for cap, on in sorted(profile.capabilities.items()):
        if on and cap not in proven:
            problems.append(
                f"{profile.id}: declares capability {cap!r} true but no "
                "transcript proves it"
            )
    for op, on in profile.operations.items():
        if (
            on
            and (dialect := dialect_for(profile.dialect))
            and op not in dialect.operations()
        ):
            problems.append(f"{profile.id}: claims {op!r} but its dialect cannot do it")

    covered = (
        frozenset().union(*(c.covers for c in cases.values())) if cases else frozenset()
    )
    for missing in sorted(REQUIRED_COVERS - covered):
        problems.append(f"{profile.id}: no transcript covers {missing!r}")
    for c in cases.values():
        res = c.expected.get("result", {})
        if "empty_explicit" in c.covers and not (
            res.get("status") == "ok" and res.get("explicitly_empty") is True
        ):
            problems.append(
                f"{profile.id}: {c.id} claims empty_explicit but golden is not"
            )
        if "empty_unreadable" in c.covers and res.get("status") != "unreadable":
            problems.append(
                f"{profile.id}: {c.id} claims empty_unreadable but golden is not"
            )
        if "logout_clean" in c.covers and res.get("result") != LogoutResult.CLEAN.value:
            problems.append(
                f"{profile.id}: {c.id} claims logout_clean but golden is not"
            )

    for qid, quirk in profile.quirks.items():
        for ref in quirk.transcripts:
            if ref not in cases:
                problems.append(
                    f"{profile.id}: quirk {qid} cites missing transcript {ref}"
                )
    return problems


# ── publication guard: FAILS, never cleans ───────────────────────────────────

_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
_DOC_NETS = ((192, 0, 2), (198, 51, 100), (203, 0, 113))
_HEX16 = re.compile(r"\b[0-9A-Fa-f]{16}\b")
_HWTC = re.compile(r"\b[A-Z]{4}[0-9A-F]{8}\b")
_SYN_SERIAL = re.compile(r"^(?:48575443)0000\d{4}$|^HWTC0000\d{4}$")
_MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
_SYN_MAC = re.compile(r"^00[:-]00[:-]5[Ee][:-]00[:-]53[:-][0-9A-Fa-f]{2}$")
_DESC = re.compile(r"[A-Za-z0-9_]*(?:CUSTOMER_|_authd_)[A-Za-z0-9_]*")
_SYN_DESC = re.compile(r"^CUSTOMER_\d{2}_zone_Zone_1_authd_20240101$")
_SECRET = re.compile(r"(?i)\b(?:password|passwd|community|key)\b\s*[:=]?\s*\S+")
_PROMPT = re.compile(r"([A-Za-z0-9._-]+)(?:\([^)\r\n]*\))?[>#]\s*$", re.MULTILINE)


def publication_violations(text: str) -> list[str]:
    """Anything in a transcript that must not be committed to a public repository."""
    out: list[str] = []
    for m in _IPV4.finditer(text):
        octets = tuple(int(g) for g in m.groups())
        if max(octets) > 255 or octets[:3] not in _DOC_NETS:
            out.append(f"IPv4 outside RFC 5737 ranges: {m.group(0)}")
    for m in _HEX16.finditer(text):
        if not _SYN_SERIAL.match(m.group(0)):
            out.append(f"serial outside the synthetic range: {m.group(0)}")
    for m in _HWTC.finditer(text):
        if not _SYN_SERIAL.match(m.group(0)):
            out.append(f"serial outside the synthetic range: {m.group(0)}")
    for m in _MAC.finditer(text):
        if not _SYN_MAC.match(m.group(0)):
            out.append(f"MAC outside the documentation range: {m.group(0)}")
    for m in _DESC.finditer(text):
        if not _SYN_DESC.match(m.group(0)):
            out.append(f"description outside the synthetic pattern: {m.group(0)}")
    for m in _SECRET.finditer(text):
        out.append(f"possible secret value: {m.group(0)[:20]}")
    for m in _PROMPT.finditer(text):
        if m.group(1) != "lab-olt":
            out.append(f"hostname other than lab-olt: {m.group(1)}")
    return out


__all__ = [
    "REQUIRED_COVERS",
    "Case",
    "chain_dirs",
    "conformance_violations",
    "load_cases",
    "prompt_violations",
    "publication_violations",
    "ref_violations",
    "replay_case",
]
