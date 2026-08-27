"""The descriptor holds NAMES, never secret VALUES.

ADR-0009 says a secret is held and never dereferenced. This module restates
that for a file which is *checked in* and then rendered into configuration a
host reads: `deploy/product.toml` names the material
(``owner_material = "MIGRATION_DATABASE_URL"``) and the deployment host resolves
it. A DSN with an embedded password in that file is a credential in Git history
whether or not review caught it.

The scan runs at PARSE time, over every string in the document, before any
field is interpreted. Parse time matters: a guard that only inspects the fields
it understands cannot see a secret pasted into a field it does not, and an
unknown field is exactly where a mistake lands.

## What is refused, and why each rule exists

Every rule below is a shape that cannot be a legitimate descriptor value.
Nothing here guesses at entropy: an "is this string random enough?" heuristic
would refuse a legitimate image digest (64 hex characters), which is the single
most important value in the whole file. The rules are structural.

1. **A URL carrying credentials** — ``scheme://user:pass@host``. The userinfo
   segment is the entire point; a DSN *without* it is a legitimate value and is
   allowed.
2. **PEM armour** — ``-----BEGIN`` anything.
3. **A known vendor token prefix** — these are published, stable, and exist
   precisely so scanners can recognise them.
4. **An assignment to a secret-shaped name** — ``PASSWORD=…``, ``token: …``.
   Catches the paste-a-whole-env-line mistake.
5. **A JWT** — three base64url segments separated by dots, first decoding to a
   JSON header.

## What is deliberately ALLOWED

- ``bao://secret/dotmac/erp#migration_dsn`` — an approved OpenBao pointer. It is
  a name, and the kernel does not dereference it (`AGENTS.md` rule 20).
- ``sha256:…`` digests, image references, host names, credential-free DSNs,
  and every material NAME.

Allowing pointers is not a hole. A pointer is public by construction: knowing
where a secret lives grants nothing without the authority to read it, and the
alternative — refusing pointers — would force the location into an untracked
host file, which is the drift `seabone-staging-dotmac-sub-deploy-landmines`
records as having twice taken staging down.

The guard carries a sensitivity proof (``test_secrets_guard.py``): each rule is
shown RED against a planted value. A scanner over a clean tree passes for the
wrong reason otherwise.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from .errors import SecretValueError

# 1. A URL whose authority carries userinfo. The `[^\s/@]` classes keep this
#    from matching an ordinary path containing an `@`.
_URL_WITH_CREDENTIALS = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/@:]+:[^\s/@]+@")

# 2. PEM armour of any kind.
_PEM = re.compile(r"-----BEGIN [A-Z ]*(KEY|CERTIFICATE|PARAMETERS)")

# 3. Published, stable vendor token prefixes. Deliberately a closed list: an
#    open heuristic here would refuse legitimate values.
_TOKEN_PREFIXES: tuple[str, ...] = (
    "sk_live_",
    "sk_test_",
    "pk_live_",
    "rk_live_",
    "AKIA",
    "ASIA",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "glpat-",
    "xoxb-",
    "xoxp-",
    "xoxa-",
    "hvs.",
    "hvb.",
    "s.",  # legacy Vault/OpenBao service token, checked with a length floor
    "AIza",
    "SG.",
    "npm_",
    "dckr_pat_",
    "-----BEGIN",
)

# `s.` is short enough to appear in ordinary prose, so it needs a length floor.
_PREFIXES_NEEDING_LENGTH: Mapping[str, int] = {"s.": 24, "SG.": 24}

# 4. An assignment to a secret-shaped name, anywhere inside a string.
# `\b` is WRONG here and the sensitivity test is what said so: in
# `DATABASE_PASSWORD=…` the word `PASSWORD` is preceded by an underscore, which
# is a word character, so `\b` never matches and the most common paste of all —
# a whole environment line — walked straight through. The boundary that is
# actually wanted is "not a letter", which `_` satisfies.
_SECRET_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z])(pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|credential|auth)(?![A-Za-z])\s*[:=]\s*\S",
    re.IGNORECASE,
)

# 5. A JWT: header.payload.signature, all base64url.
_JWT = re.compile(r"\bey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")

# An approved pointer scheme. Matched BEFORE the assignment rule so that
# `bao://secret/erp#api_key` is a name rather than an assignment.
_POINTER_SCHEMES: tuple[str, ...] = (
    "bao://",
    "openbao://",
    "vault://",
    "env://",
    "file://",
)


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """One refused value, located precisely enough to fix."""

    path: str
    rule: str
    excerpt: str

    def __str__(self) -> str:
        return f"{self.path} matched {self.rule} ({self.excerpt})"


def _redact(value: str) -> str:
    """Never echo a secret back out.

    The excerpt exists so a human can find the line, not read the value. Ten
    characters of a 64-character token is enough to locate it and not enough to
    use it; a value shorter than that is described by shape alone.
    """
    if len(value) <= 12:
        return f"<{len(value)} chars>"
    return f"{value[:6]}…<{len(value) - 6} more chars>"


def _is_pointer(value: str) -> bool:
    return value.startswith(_POINTER_SCHEMES)


def _looks_like_jwt(value: str) -> bool:
    match = _JWT.search(value)
    if match is None:
        return False
    head = match.group(0).split(".", 1)[0]
    padded = head + "=" * (-len(head) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return False
    try:
        parsed = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    # A JWT header is an object with an algorithm. Anything else that happens to
    # start `ey` and decode to JSON is not one.
    return isinstance(parsed, dict) and "alg" in parsed


def scan_value(path: str, value: str) -> SecretFinding | None:
    """Return the first rule ``value`` violates, or ``None``.

    Order matters: pointer recognition runs first, so an approved pointer whose
    fragment happens to be named ``api_key`` is a name and not an assignment.
    """
    if _is_pointer(value):
        # A pointer may still not carry credentials in its authority.
        if _URL_WITH_CREDENTIALS.search(value):
            return SecretFinding(path, "url-with-credentials", _redact(value))
        return None
    if _URL_WITH_CREDENTIALS.search(value):
        return SecretFinding(path, "url-with-credentials", _redact(value))
    if _PEM.search(value):
        return SecretFinding(path, "pem-armour", "<PEM block>")
    for prefix in _TOKEN_PREFIXES:
        if not value.startswith(prefix):
            continue
        if len(value) < _PREFIXES_NEEDING_LENGTH.get(prefix, len(prefix) + 8):
            continue
        return SecretFinding(path, f"vendor-token-prefix:{prefix}", _redact(value))
    if _looks_like_jwt(value):
        return SecretFinding(path, "jwt", _redact(value))
    if _SECRET_ASSIGNMENT.search(value):
        return SecretFinding(path, "secret-assignment", _redact(value))
    return None


def _walk(node: object, path: str) -> Iterator[tuple[str, str]]:
    """Yield every ``(dotted-path, string)`` in a parsed TOML document.

    Walks the RAW parsed document — before any field is interpreted — so a
    secret pasted into a field the schema does not define is still seen.
    """
    if isinstance(node, str):
        yield path, node
        return
    if isinstance(node, Mapping):
        for key, child in node.items():
            yield from _walk(child, f"{path}.{key}" if path else str(key))
        return
    if isinstance(node, Sequence) and not isinstance(node, str | bytes):
        for index, child in enumerate(node):
            yield from _walk(child, f"{path}[{index}]")


def find_secrets(document: Mapping[str, object]) -> list[SecretFinding]:
    """Every refused value in ``document``, in document order."""
    findings: list[SecretFinding] = []
    for path, value in _walk(document, ""):
        finding = scan_value(path, value)
        if finding is not None:
            findings.append(finding)
    return findings


def require_no_secrets(document: Mapping[str, object], *, source: str = "") -> None:
    """Raise :class:`SecretValueError` if the document carries secret material.

    Reports EVERY finding rather than the first: an operator who has pasted one
    environment block has usually pasted several, and one-at-a-time refusal
    turns a single fix into five review cycles.
    """
    findings = find_secrets(document)
    if not findings:
        return
    detail = "; ".join(str(finding) for finding in findings)
    raise SecretValueError(
        f"{len(findings)} secret-shaped value(s) in the deployment descriptor: "
        f"{detail}. The descriptor holds material NAMES and approved pointers "
        f"(bao://…), never values (ADR-0009).",
        where=source,
    )
