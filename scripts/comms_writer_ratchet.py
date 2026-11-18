#!/usr/bin/env python3
"""Product-writer ratchet for tenant-facing messages (ADR-0006 5f, ADR-0018).

ADR-0006 5f says a product composed from Starter sends every tenant-facing
message through the kernel ``delivery`` owner and ``dotmac-template-studio``.
Two shapes are defects: a per-product outbox, and hard-coded message copy.
Direct SMTP is the same defect one layer down.  This checker finds all three in
a product source tree and compares them with a two-directional backlog.

Stdlib only; reads source with :mod:`ast`; imports nothing it scans.

Detector rules (every finding is one of three kinds)
----------------------------------------------------

``outbox_writer`` -- the product owns a mail queue.
  O1  a class named ``*email/mail/sms/message/notification ... outbox*`` (either
      order, any case, underscores optional), or a class whose ``__tablename__``
      (or a ``Table(...)`` / ``create_table(...)`` first argument) matches.
  O2  a function named ``enqueue_<email|mail|message|sms|notification>*`` or
      ``queue_<email|mail|...>*`` (a *definition*, not a call).
  O3  a string literal that is a SQL write (``INSERT INTO`` / ``UPDATE`` /
      ``DELETE FROM``) against such a table.
  The bare word ``outbox`` is NOT enough: the kernel's event outbox (ADR-0014)
  is used by many modules for domain events, which is allowed.

``direct_smtp`` -- the product talks to a mail transport itself.
  S1  ``import``/``from ... import`` of ``smtplib``, ``aiosmtplib``, ``smtpd``,
      ``yagmail``, ``sendgrid``, ``mailgun``, ``mailjet_rest``, ``postmarker``,
      ``postmark``, ``sib_api_v3_sdk``, ``mailchimp_transactional``,
      ``flask_mail``, ``fastapi_mail``, ``django.core.mail``, ``resend``.
  S2  a call to ``SMTP``/``SMTP_SSL``/``LMTP``/``sendmail``/``starttls`` on any
      receiver, or ``boto3.client("ses"|"sesv2")``.
  S3  ``socket.create_connection``/``connect`` with a literal port 25, 465 or
      587 in the address tuple.
  Building a message (``email.message``/``email.mime``) is not sending and is
  not flagged.

``inline_copy`` -- message copy is written in product code.
  C1  a call to a send/enqueue *sink* (callee name ``enqueue_*``, ``queue_*``,
      ``send_*`` or ``dispatch_*`` followed by ``email|mail|message|sms|
      notification|notice`` ..., or ``sendmail``/``send_mail``/``send_email``)
      with an argument that is inline copy.  Copy-bearing keyword names:
      ``subject, body, html, html_body, text, text_body, message, content,
      plain, title, preheader``.  Positional string arguments count too.
  An argument is *inline copy* when it is: an f-string; a ``str.format`` /
  ``%`` / ``+`` / ``"".join`` expression containing a string literal; a string
  literal that contains an HTML tag or three or more words; a name assigned in
  the same function from any of those; or a name assigned from a call to a
  same-module function whose body holds such a literal (one hop; this is how
  product renderers like ``_wrap_html`` are caught).
  Arguments such as ``kind="score_pass"`` (one word, not a copy keyword) or
  ``subject=template.subject`` are not copy.  A call that passes a template
  slug and variables to the kernel is therefore clean.

Finding identity
----------------
``<path>::<kind>::<symbol>#<n>`` -- path relative to the product root, the kind,
the qualified symbol (``Class.method``, ``<module>`` at import scope, or
``import:smtplib`` / ``call:SMTP_SSL`` detail for ``direct_smtp``), and an
ordinal within that (path, kind, symbol).  No line numbers, so unrelated edits
do not churn the backlog; reordering identical findings inside one function
does.

Coverage
--------
The whole ``root`` is scanned, not a list of directories (ADR-0018: a guard
that covers one directory is the failure this exists to prevent).  ``families``
names the entry-point families (web routes, CLI/commands, workers/jobs,
services, scripts, ...) with their roots.  Every declared family root must
exist (a renamed directory cannot silently drop out of the scan) and files
outside every family are still scanned and reported as family ``other``.
``allowed`` is a list of ``{"glob", "premise"}`` owners (kernel delivery, the
``dotmac-delivery-*`` adapters); each glob must match at least one file.
Tests, migrations and alembic trees are not product runtime and are skipped.

Backlog
-------
Two-directional (ADR-0018): fails when a finding appears that is not listed AND
when a listed finding is gone.  Lowering is ``--write-backlog`` in the same
change.  Exit 0 clean, 1 on any ratchet/coverage failure, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

KINDS: Final = ("outbox_writer", "direct_smtp", "inline_copy")
SKIP_DIRS: Final = frozenset(
    {
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "tests",
        "test",
        "migrations",
        "alembic",
        ".git",
    }
)

_CHANNEL: Final = r"(?:e?mail|sms|message|notification)s?"
_OUTBOX_NAME: Final = re.compile(
    rf"(?:{_CHANNEL}[_ ]?outbox|outbox[_ ]?{_CHANNEL})", re.IGNORECASE
)
_ENQUEUE_DEF: Final = re.compile(
    rf"^(?:enqueue|queue)_{_CHANNEL}(?:_|$)|^(?:enqueue|queue)_{_CHANNEL}",
    re.IGNORECASE,
)
_SQL_WRITE: Final = re.compile(
    rf"\b(?:insert\s+into|update|delete\s+from)\s+[\w\".]*{_CHANNEL}_?outbox",
    re.IGNORECASE,
)
_SMTP_MODULES: Final = frozenset(
    {
        "smtplib",
        "aiosmtplib",
        "smtpd",
        "aiosmtpd",
        "yagmail",
        "sendgrid",
        "mailgun",
        "mailjet_rest",
        "postmarker",
        "postmark",
        "sib_api_v3_sdk",
        "mailchimp_transactional",
        "flask_mail",
        "fastapi_mail",
        "resend",
        "django.core.mail",
    }
)
_SMTP_CALLS: Final = frozenset(
    {"SMTP", "SMTP_SSL", "LMTP", "sendmail", "starttls", "SMTPClient"}
)
_SMTP_PORTS: Final = frozenset({25, 465, 587})
_SINK: Final = re.compile(
    rf"^(?:(?:enqueue|queue|send|dispatch)_{_CHANNEL}\w*|sendmail|send_mail|send_email)$",
    re.IGNORECASE,
)
_COPY_KW: Final = frozenset(
    {
        "subject",
        "body",
        "html",
        "html_body",
        "text",
        "text_body",
        "message",
        "content",
        "plain",
        "title",
        "preheader",
    }
)
_HTML_TAG: Final = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>")
_WORDS: Final = re.compile(r"[A-Za-z]{2,}")


@dataclass(frozen=True)
class Finding:
    path: str
    kind: str
    symbol: str

    @property
    def base(self) -> str:
        return f"{self.path}::{self.kind}::{self.symbol}"


def _is_copy_literal(value: str) -> bool:
    if _HTML_TAG.search(value):
        return True
    return len(_WORDS.findall(value)) >= 3 and " " in value.strip()


def _has_copy_literal(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.JoinedStr):
            return True
        if (
            isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and _is_copy_literal(sub.value)
        ):
            return True
    return False


def _is_expr_copy(node: ast.AST) -> bool:
    """True for an expression that is itself inline copy."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and _is_copy_literal(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _has_str_literal(node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in {"format", "join", "format_map", "strip", "replace"}:
            return _has_str_literal(node.func.value) or any(
                _has_str_literal(a) for a in node.args
            )
    if isinstance(node, ast.IfExp):
        return _is_expr_copy(node.body) or _is_expr_copy(node.orelse)
    return False


def _has_str_literal(node: ast.AST) -> bool:
    return any(
        isinstance(s, ast.JoinedStr)
        or (isinstance(s, ast.Constant) and isinstance(s.value, str) and s.value)
        for s in ast.walk(node)
    )


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[str] = []
        for elt in target.elts:
            out.extend(_target_names(elt))
        return out
    return []


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: str, tree: ast.Module) -> None:
        self.path = path
        self.found: list[Finding] = []
        self.stack: list[str] = []
        # One-hop renderers: same-module functions whose body holds copy.
        self.copy_functions: set[str] = {
            n.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not _ENQUEUE_DEF.match(n.name)
            and _has_copy_literal(n)
        }
        # Scope-level assignments: symbol -> {name: is_copy}
        self.assigned: list[dict[str, bool]] = [{}]
        self.visit(tree)

    # -- helpers -----------------------------------------------------------
    @property
    def symbol(self) -> str:
        return ".".join(self.stack) if self.stack else "<module>"

    def emit(self, kind: str, symbol: str | None = None) -> None:
        self.found.append(Finding(self.path, kind, symbol or self.symbol))

    def _name_is_copy(self, name: str) -> bool:
        return any(scope.get(name, False) for scope in reversed(self.assigned))

    def _arg_is_copy(self, node: ast.expr) -> bool:
        if _is_expr_copy(node):
            return True
        if isinstance(node, ast.Name):
            return self._name_is_copy(node.id)
        if isinstance(node, ast.Call):
            name = _callee_name(node.func)
            return name in self.copy_functions if name else False
        return False

    # -- scopes ------------------------------------------------------------
    def _enter(self, name: str, node: ast.AST) -> None:
        self.stack.append(name)
        self.assigned.append({})
        self.generic_visit(node)
        self.assigned.pop()
        self.stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        hit = bool(_OUTBOX_NAME.search(node.name))
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "__tablename__"
                    for t in stmt.targets
                )
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
                and _OUTBOX_NAME.search(stmt.value.value)
            ):
                hit = True
        if hit:
            self.emit("outbox_writer", self.symbol)
        self.assigned.append({})
        self.generic_visit(node)
        self.assigned.pop()
        self.stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        if _ENQUEUE_DEF.match(node.name):
            self.emit("outbox_writer", self.symbol)
        self.stack.pop()
        self._enter(node.name, node)

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    # -- assignments feed the one-function copy tracking -------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        scope = self.assigned[-1]
        is_copy = self._arg_is_copy(node.value)
        for target in node.targets:
            for name in _target_names(target):
                scope[name] = scope.get(name, False) or is_copy
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            for name in _target_names(node.target):
                self.assigned[-1][name] = self._arg_is_copy(node.value)
        self.generic_visit(node)

    # -- imports ----------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if self._is_smtp_module(alias.name):
                self.emit("direct_smtp", f"import:{alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level == 0 and self._is_smtp_module(module):
            self.emit("direct_smtp", f"import:{module}")
        elif node.level == 0:
            for alias in node.names:
                if self._is_smtp_module(f"{module}.{alias.name}"):
                    self.emit("direct_smtp", f"import:{module}.{alias.name}")
        self.generic_visit(node)

    @staticmethod
    def _is_smtp_module(name: str) -> bool:
        return any(name == m or name.startswith(m + ".") for m in _SMTP_MODULES)

    # -- strings ----------------------------------------------------------
    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _SQL_WRITE.search(node.value):
            self.emit("outbox_writer", f"sql:{self.symbol}")
        self.generic_visit(node)

    # -- calls ------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        name = _callee_name(node.func)
        if name in _SMTP_CALLS:
            self.emit("direct_smtp", f"call:{name}")
        elif name == "client" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value in {"ses", "sesv2"}:
                self.emit("direct_smtp", "call:boto3.ses")
        elif name in {"create_connection", "connect"} and self._socket_port(node):
            self.emit("direct_smtp", "call:socket")
        if name in {"Table", "create_table"} and node.args:
            first = node.args[0]
            if (
                isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _OUTBOX_NAME.search(first.value)
            ):
                self.emit("outbox_writer", f"table:{first.value}")
        in_writer = bool(self.stack) and bool(_ENQUEUE_DEF.match(self.stack[-1]))
        if name and _SINK.match(name) and not in_writer:
            candidates: list[ast.expr] = list(node.args)
            candidates += [
                kw.value
                for kw in node.keywords
                if kw.arg is not None and kw.arg.lower() in _COPY_KW
            ]
            if any(self._arg_is_copy(c) for c in candidates):
                self.emit("inline_copy", self.symbol)
        self.generic_visit(node)

    @staticmethod
    def _socket_port(node: ast.Call) -> bool:
        for arg in node.args[:1]:
            if isinstance(arg, ast.Tuple) and len(arg.elts) == 2:
                port = arg.elts[1]
                if isinstance(port, ast.Constant) and port.value in _SMTP_PORTS:
                    return True
        return False


def scan_source(path: str, source: str) -> list[Finding]:
    return _Scanner(path, ast.parse(source, filename=path)).found


def number(findings: list[Finding]) -> list[str]:
    """Stable ids: base plus a 1-based ordinal within the same base."""
    seen: Counter[str] = Counter()
    ids: list[str] = []
    for f in findings:
        seen[f.base] += 1
        ids.append(f"{f.base}#{seen[f.base]}")
    return ids


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(root.rglob("*.py")):
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts[:-1]):
            continue
        files.append(p)
    return files


def _matches(rel: str, glob: str) -> bool:
    return fnmatch.fnmatchcase(rel, glob)


@dataclass
class Result:
    ids: list[str]
    families: dict[str, int]
    problems: list[str]


def scan_product(config: dict[str, Any], base_dir: Path) -> Result:
    """Scan one product config. Paths in ``config`` are relative to base_dir."""
    root = (base_dir / config["root"]).resolve()
    problems: list[str] = []
    if not root.is_dir():
        return Result([], {}, [f"root {config['root']!r} is not a directory"])

    families: dict[str, list[str]] = config.get("families", {})
    for fam, roots in sorted(families.items()):
        for r in roots:
            if not (root / r).exists():
                problems.append(
                    f"family {fam!r} root {r!r} does not exist: an entry-point "
                    "family cannot silently drop out of the scan"
                )

    allowed: list[dict[str, str]] = config.get("allowed", [])
    allowed_hit = [0] * len(allowed)
    for entry in allowed:
        if not entry.get("premise"):
            problems.append(f"allowed owner {entry.get('glob')!r} states no premise")

    files = _iter_files(root)
    rels = [p.relative_to(root).as_posix() for p in files]
    for i, entry in enumerate(allowed):
        allowed_hit[i] = sum(1 for r in rels if _matches(r, entry["glob"]))
        if allowed_hit[i] == 0:
            problems.append(
                f"allowed owner {entry['glob']!r} matches no file: the premise "
                "is unverifiable, remove or fix it"
            )

    fam_counts: Counter[str] = Counter()
    findings: list[Finding] = []
    for path, rel in zip(files, rels, strict=True):
        fam, best = "other", -1
        for name, froots in sorted(families.items()):
            for r in froots:
                r = r.rstrip("/")
                if (rel == r or rel.startswith(r + "/")) and len(r) > best:
                    fam, best = name, len(r)
        fam_counts[fam] += 1
        if any(_matches(rel, e["glob"]) for e in allowed):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            findings.extend(scan_source(rel, source))
        except (SyntaxError, UnicodeDecodeError) as exc:
            problems.append(f"{rel}: cannot parse ({exc.__class__.__name__})")
    return Result(number(findings), dict(fam_counts), problems)


def load_backlog(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = data.get("findings")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValueError("backlog 'findings' must be a list of id strings")
    return list(ids)


def compare(ids: list[str], backlog: list[str]) -> list[str]:
    problems: list[str] = []
    dupes = sorted(i for i, n in Counter(backlog).items() if n > 1)
    for d in dupes:
        problems.append(f"backlog lists {d} more than once")
    current, listed = set(ids), set(backlog)
    for i in sorted(current - listed):
        problems.append(
            f"NEW finding {i}: route the message through kernel delivery and "
            "template-studio (ADR-0006 5f); do not add it to the backlog"
        )
    for i in sorted(listed - current):
        problems.append(
            f"LISTED finding gone {i}: lower the backlog in the same change "
            "(--write-backlog)"
        )
    return problems


def write_backlog(path: Path, product: str, ids: list[str]) -> None:
    payload = {
        "_comment": (
            "Frozen product-writer debt (ADR-0006 5f, ADR-0018). Two-directional "
            "ratchet: fails if a finding appears OR a listed finding disappears "
            "without this file being regenerated in the same change. Retire "
            "entries by moving the send to kernel delivery + template-studio, "
            "then run --write-backlog. Do not hand-edit."
        ),
        "product": product,
        "total": len(ids),
        "findings": sorted(ids),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(config_path: Path, backlog_path: Path, *, write: bool) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    result = scan_product(config, config_path.parent)
    by_kind: Counter[str] = Counter(i.split("::")[1] for i in result.ids)
    print(
        f"comms-writer ratchet [{config.get('product', '?')}]: "
        f"{len(result.ids)} finding(s) "
        + ", ".join(f"{k}={by_kind.get(k, 0)}" for k in KINDS)
    )
    print(
        "families scanned (files): "
        + ", ".join(f"{k}={v}" for k, v in sorted(result.families.items()))
    )
    if write:
        if result.problems:
            for p in result.problems:
                print(f"ERROR: {p}", file=sys.stderr)
            return 1
        write_backlog(backlog_path, config.get("product", ""), result.ids)
        print(f"wrote {backlog_path} ({len(result.ids)} finding(s))")
        return 0
    problems = list(result.problems)
    try:
        problems += compare(result.ids, load_backlog(backlog_path))
    except (OSError, ValueError) as exc:
        problems.append(f"backlog unreadable: {exc}")
    for p in problems:
        print(f"ERROR: {p}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--backlog", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="default")
    mode.add_argument("--write-backlog", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(args.config, args.backlog, write=args.write_backlog)
    except (OSError, ValueError, KeyError) as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
