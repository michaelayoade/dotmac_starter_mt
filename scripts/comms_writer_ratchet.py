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
      ``queue_<...>*`` (a *definition*, not a call).
  O3  a string literal (not a docstring) that is a SQL write (``INSERT INTO`` /
      ``UPDATE`` / ``DELETE FROM``) against such a table.
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
  Building a message (``email.message``/``email.mime``) is not sending.

``inline_copy`` -- message copy is written in product code.
  C1  a call to a send/enqueue *sink* (callee name ``enqueue_*``, ``queue_*``,
      ``send_*`` or ``dispatch_*`` followed by ``email|mail|message|sms|
      notification|notice`` ..., or ``sendmail``/``send_mail``/``send_email``)
      with an argument that is inline copy.  Copy-bearing keywords: ``subject,
      body, html, html_body, text, text_body, message, content, plain, title,
      preheader``.  Positional arguments count too, and so does a ``**mapping``
      whose dict (``dict(...)`` / ``{...}`` / later ``d["subject"] = ...``)
      carries a copy keyword.
  An expression is *copy* when it is:
    * an f-string whose literal parts hold an HTML tag or two or more words;
    * a string literal with an HTML tag, or two or more words and a space;
    * ``tmpl.format(...)`` / ``tmpl % x`` / ``a + b`` / ``"".join([...])`` /
      ``.replace/.strip/...`` where the *template, receiver or an operand* is
      itself copy -- an incidental literal (``", ".join(to)``,
      ``.replace("\\n", " ")``) is not;
    * a name assigned from copy in the same function, module or an enclosing
      scope (tuple unpacking included);
    * a call to a *renderer*: a function (same module, or imported from inside
      the scanned root, at module level or inside a function) whose RETURN
      value is copy.  Resolution is transitive across the scanned root.
  Literals that never reach a sink argument or a renderer's return value are
  never copy: docstrings, log messages, ``raise`` messages and SQL fragments do
  not count.  ``kind="score_pass"``, ``subject=template.subject`` and a call
  that passes a template slug and variables to the kernel are clean.

Known blind spots (does NOT detect; reviewed 2026-11-18)
-------------------------------------------------------
  * mail-provider HTTP APIs called through a generic HTTP client (no SDK import,
    no SMTP call, no socket);
  * queue tables not named like a mail/sms/message outbox, and any function
    renamed away from ``enqueue_<channel>``/``queue_<channel>``;
  * notify wrappers: a send routed through a helper whose name does not match the
    sink pattern (``notify(...)``, ``publish(...)``, ``emit(...)``) with copy
    inside;
  * copy loaded from a file, a database row, an environment value or a template
    engine the product owns;
  * dynamic imports and renderers reached through a registry or ``getattr``;
  * message building through ``email.message`` headers (``msg["Subject"] = ...``)
    when no sink or SMTP call follows in scanned code.
These are stated, not enforced: the region is unmonitored, not exempt (ADR-0018).
Review the list when a detector changes or an adopter reports a miss.

Finding identity
----------------
``<path>::<kind>::<symbol>#<n>`` -- path relative to the product root, the kind,
the qualified symbol (``Class.method``, ``<module>`` at import scope, or
``import:smtplib`` / ``call:SMTP_SSL`` detail for ``direct_smtp``), and an
ordinal within that (path, kind, symbol).  No line numbers.

Coverage and exemptions
-----------------------
The whole ``root`` is scanned, not a list of directories (ADR-0018).
``families`` names entry-point families (web routes, CLI/commands, workers/jobs,
services, scripts, ...) with their roots; every declared family root must exist
and files outside every family are still scanned as ``other``.

Two lists remove findings, and both share one enforceable shape,
``{"glob", "kinds", "premise"}`` (``optional: true`` waives the must-match for
trees that exist only in some checkouts, such as ``.venv``):
  ``allowed``  -- the named delivery owners (kernel delivery, each
                  ``dotmac-delivery-*`` adapter, listed one package at a time)
                  and ONLY the kinds each really needs;
  ``exclude``  -- non-runtime trees (tests, migrations, vendored code) and the
                  kinds not scanned there.
``kinds`` is required (no implicit "everything"), the premise must be stated, a
glob must match at least one file, and globs are path-segment safe: ``*`` never
crosses ``/``; ``**`` spans whole segments.  A finding of a kind the entry does
not list is NOT suppressed.

Backlog
-------
``--check --base-ref REF`` additionally refuses any listed id that the backlog
committed at REF does not already hold (a hand-added id would otherwise hide a
new finding); use it for adopters whose backlog is non-empty.
Two-directional (ADR-0018): fails when a finding appears that is not listed AND
when a listed finding is gone.  ``--write-backlog`` is REMOVE-ONLY: it refuses to
add a finding unless ``--allow-additions`` is passed (which is the very thing
the ratchet exists to prevent).  Exit 0 clean, 1 on any failure, 2 on usage.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Final

KINDS: Final = ("outbox_writer", "direct_smtp", "inline_copy")
#: An exclusion hiding every kind must name one of these path segments.
NON_RUNTIME_SEGMENTS: Final = frozenset(
    {"tests", "migrations", "alembic", ".venv", "node_modules"}
)

_CHANNEL: Final = r"(?:e?mail|sms|message|notification)s?"
_OUTBOX_NAME: Final = re.compile(
    rf"(?:{_CHANNEL}[_ ]?outbox|outbox[_ ]?{_CHANNEL})", re.IGNORECASE
)
_ENQUEUE_DEF: Final = re.compile(rf"^(?:enqueue|queue)_{_CHANNEL}", re.IGNORECASE)
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
_STR_PASSTHROUGH: Final = frozenset(
    {"replace", "strip", "lstrip", "rstrip", "lower", "upper", "title", "encode"}
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


# -- path-segment-safe globbing --


def glob_match(rel: str, glob: str) -> bool:
    """``*``/``?``/``[]`` match within one segment; ``**`` spans whole segments."""

    def go(p: list[str], g: list[str]) -> bool:
        if not g:
            return not p
        if g[0] == "**":
            return any(go(p[i:], g[1:]) for i in range(len(p) + 1))
        return bool(p) and fnmatchcase(p[0], g[0]) and go(p[1:], g[1:])

    return go(rel.split("/"), glob.split("/"))


# -- expression classification --


def _text_is_copy(value: str, *, need_space: bool) -> bool:
    if _HTML_TAG.search(value):
        return True
    if need_space and not any(ch.isspace() for ch in value.strip()):
        return False
    return len(_WORDS.findall(value)) >= 2


def _fstring_is_copy(node: ast.JoinedStr) -> bool:
    parts = [
        v.value
        for v in node.values
        if isinstance(v, ast.Constant) and isinstance(v.value, str)
    ]
    return _text_is_copy(" ".join(parts), need_space=False)


@dataclass
class Ctx:
    """Names known to hold copy (and dicts known to carry a copy keyword)."""

    names: dict[str, bool] = field(default_factory=dict)
    dicts: dict[str, bool] = field(default_factory=dict)
    parent: Ctx | None = None

    def name_copy(self, name: str) -> bool:
        ctx: Ctx | None = self
        while ctx is not None:
            if name in ctx.names:
                return ctx.names[name]
            ctx = ctx.parent
        return False

    def dict_copy(self, name: str) -> bool:
        ctx: Ctx | None = self
        while ctx is not None:
            if ctx.dicts.get(name):
                return True
            ctx = ctx.parent
        return False


class Resolver:
    """Decides whether a call target is a renderer. Set by the project pass."""

    def __init__(self) -> None:
        self.copy_funcs: set[tuple[str, str]] = set()
        self.imports: dict[str, dict[str, tuple[str, str | None]]] = {}
        self.local_funcs: dict[str, set[str]] = {}

    def is_renderer(self, rel: str, func: ast.expr) -> bool:
        if isinstance(func, ast.Name):
            name = func.id
            if (rel, name) in self.copy_funcs:
                return True
            target = self.imports.get(rel, {}).get(name)
            return bool(
                target and target[1] and (target[0], target[1]) in self.copy_funcs
            )
        if isinstance(func, ast.Attribute):
            base = func.value
            if isinstance(base, ast.Name):
                target = self.imports.get(rel, {}).get(base.id)
                if target and target[1] is None:  # module alias
                    return (target[0], func.attr) in self.copy_funcs
            # self.helper(...) / obj.helper(...): same-file function by name
            return (rel, func.attr) in self.copy_funcs
        return False


def expr_copy(node: ast.AST, ctx: Ctx, rel: str, res: Resolver) -> bool:
    """True for an expression that carries inline message copy."""
    if isinstance(node, ast.JoinedStr):
        return _fstring_is_copy(node)
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and _text_is_copy(
            node.value, need_space=True
        )
    if isinstance(node, ast.Name):
        return ctx.name_copy(node.id)
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Add):
            return expr_copy(node.left, ctx, rel, res) or expr_copy(
                node.right, ctx, rel, res
            )
        if isinstance(node.op, ast.Mod):
            return expr_copy(node.left, ctx, rel, res)
        return False
    if isinstance(node, ast.IfExp):
        return expr_copy(node.body, ctx, rel, res) or expr_copy(
            node.orelse, ctx, rel, res
        )
    if isinstance(node, ast.BoolOp):
        return any(expr_copy(v, ctx, rel, res) for v in node.values)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            recv = node.func.value
            if attr in {"format", "format_map"} or attr in _STR_PASSTHROUGH:
                return expr_copy(recv, ctx, rel, res)
            if attr == "join":
                if expr_copy(recv, ctx, rel, res):
                    return True
                for arg in node.args[:1]:
                    if isinstance(arg, ast.List | ast.Tuple | ast.Set):
                        if any(expr_copy(e, ctx, rel, res) for e in arg.elts):
                            return True
                    elif isinstance(arg, ast.GeneratorExp | ast.ListComp | ast.SetComp):
                        if expr_copy(arg.elt, ctx, rel, res):
                            return True
                return False
        return res.is_renderer(rel, node.func)
    return False


def dict_carries_copy(node: ast.AST, ctx: Ctx, rel: str, res: Resolver) -> bool:
    if isinstance(node, ast.Name):
        return ctx.dict_copy(node.id)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "dict":
            return any(
                kw.arg in _COPY_KW and expr_copy(kw.value, ctx, rel, res)
                for kw in node.keywords
                if kw.arg is not None
            )
    if isinstance(node, ast.Dict):
        return any(
            isinstance(k, ast.Constant)
            and k.value in _COPY_KW
            and expr_copy(v, ctx, rel, res)
            for k, v in zip(node.keys, node.values, strict=True)
        )
    return False


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple | ast.List):
        out: list[str] = []
        for elt in target.elts:
            out.extend(_target_names(elt))
        return out
    return []


_OWN_CACHE: dict[int, tuple[ast.AST, list[ast.AST]]] = {}


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Assignment/return nodes in ``scope`` excluding nested defs and lambdas.

    Memoised per scope (the scan walks every scope several times); the cached
    entry holds the scope itself so its id cannot be reused while cached.
    """
    cached = _OWN_CACHE.get(id(scope))
    if cached is not None and cached[0] is scope:
        return cached[1]
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign | ast.Return):
            out.append(node)
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
        ):
            continue
        stack.extend(ast.iter_child_nodes(node))
    out.sort(key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))
    _OWN_CACHE[id(scope)] = (scope, out)
    return out


def build_ctx(scope: ast.AST, parent: Ctx | None, rel: str, res: Resolver) -> Ctx:
    ctx = Ctx(parent=parent)
    nodes = _own_nodes(scope)
    for _ in range(2):  # a second pass lets later assignments feed earlier uses
        for node in nodes:
            if isinstance(node, ast.Assign):
                value, targets = node.value, node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value, targets = node.value, [node.target]
            elif isinstance(node, ast.AugAssign):
                if isinstance(node.target, ast.Name) and expr_copy(
                    node.value, ctx, rel, res
                ):
                    ctx.names[node.target.id] = True
                continue
            else:
                continue
            is_copy = expr_copy(value, ctx, rel, res)
            carries = dict_carries_copy(value, ctx, rel, res)
            for target in targets:
                if isinstance(target, ast.Subscript) and isinstance(
                    target.value, ast.Name
                ):
                    key = target.slice
                    if (
                        isinstance(key, ast.Constant)
                        and key.value in _COPY_KW
                        and is_copy
                    ):
                        ctx.dicts[target.value.id] = True
                    continue
                for name in _target_names(target):
                    if isinstance(target, ast.Tuple | ast.List) and isinstance(
                        value, ast.Tuple | ast.List
                    ):
                        continue
                    ctx.names[name] = ctx.names.get(name, False) or is_copy
                    if carries:
                        ctx.dicts[name] = True
                if (
                    isinstance(target, ast.Tuple | ast.List)
                    and isinstance(value, ast.Tuple | ast.List)
                    and len(target.elts) == len(value.elts)
                ):
                    for t, v in zip(target.elts, value.elts, strict=True):
                        for name in _target_names(t):
                            ctx.names[name] = ctx.names.get(name, False) or expr_copy(
                                v, ctx, rel, res
                            )
    return ctx


def returns_copy(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    parent: Ctx | None,
    rel: str,
    res: Resolver,
) -> bool:
    ctx = build_ctx(fn, parent, rel, res)
    for node in _own_nodes(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            elts = (
                node.value.elts
                if isinstance(node.value, ast.Tuple | ast.List)
                else [node.value]
            )
            if any(expr_copy(e, ctx, rel, res) for e in elts):
                return True
    return False


# -- project-wide pass --


def _dotted(rel: str) -> str:
    stem = rel[:-3] if rel.endswith(".py") else rel
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def _module_names(rel: str) -> list[str]:
    """Every dotted name a file can be imported as.

    The path-derived name, plus the name under a ``src/`` layout
    (``packages/dotmac-x/src/dotmac_x/render.py`` is ``dotmac_x.render``), so an
    absolute import from a sibling package or the assembly resolves.
    """
    names = [_dotted(rel)]
    if "/src/" in f"/{rel}":
        tail = f"/{rel}".split("/src/", 1)[1]
        names.append(_dotted(tail))
    return names


def _resolve_module(dotted: str, by_dotted: dict[str, str]) -> str | None:
    best: tuple[int, str] | None = None
    for d, rel in by_dotted.items():
        if dotted == d or dotted.endswith("." + d):
            if best is None or len(d) > best[0]:
                best = (len(d), rel)
    return best[1] if best else None


def _import_map(
    rel: str, imports: list[ast.Import | ast.ImportFrom], by_dotted: dict[str, str]
) -> dict[str, tuple[str, str | None]]:
    """name -> (target file, original name | None when the name is a module)."""
    out: dict[str, tuple[str, str | None]] = {}
    pkg = (
        _dotted(rel).split(".")[:-1]
        if not rel.endswith("__init__.py")
        else _dotted(rel).split(".")
    )
    for node in imports:
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _resolve_module(alias.name, by_dotted)
                if target and alias.asname:
                    out[alias.asname] = (target, None)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            for alias in node.names:
                name = alias.asname or alias.name
                as_module = _resolve_module(f"{module}.{alias.name}", by_dotted)
                host = _resolve_module(module, by_dotted) if module else None
                if as_module and as_module != host:
                    out[name] = (as_module, None)
                elif host:
                    out[name] = (host, alias.name)
    return out


@dataclass
class FileIndex:
    """One walk per file: functions, imports and docstring node ids."""

    functions: list[ast.FunctionDef | ast.AsyncFunctionDef]
    imports: list[ast.Import | ast.ImportFrom]
    docs: set[int]


def _index(tree: ast.Module) -> FileIndex:
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    imports: list[ast.Import | ast.ImportFrom] = []
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.append(node)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            imports.append(node)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docs.add(id(first.value))
    return FileIndex(functions, imports, docs)


@dataclass
class Project:
    trees: dict[str, ast.Module]
    index: dict[str, FileIndex]
    resolver: Resolver
    problems: list[str]


def analyse(sources: dict[str, str]) -> Project:
    _OWN_CACHE.clear()
    trees: dict[str, ast.Module] = {}
    problems: list[str] = []
    for rel, src in sorted(sources.items()):
        try:
            trees[rel] = ast.parse(src, filename=rel)
        except SyntaxError as exc:
            problems.append(f"{rel}: cannot parse ({exc.__class__.__name__})")
    by_dotted = {name: r for r in trees for name in _module_names(r)}
    res = Resolver()
    index = {rel: _index(tree) for rel, tree in trees.items()}
    for rel in trees:
        res.imports[rel] = _import_map(rel, index[rel].imports, by_dotted)
    candidates = {
        rel: [
            fn
            for fn in index[rel].functions
            if not _ENQUEUE_DEF.match(fn.name)
            and any(isinstance(n, ast.Return) for n in _own_nodes(fn))
        ]
        for rel in trees
    }
    for _ in range(6):  # transitive renderers; converges fast, bounded anyway
        changed = False
        for rel, fns in candidates.items():
            if not fns:
                continue
            module_ctx = build_ctx(trees[rel], None, rel, res)
            for fn in fns:
                key = (rel, fn.name)
                if key not in res.copy_funcs and returns_copy(fn, module_ctx, rel, res):
                    res.copy_funcs.add(key)
                    changed = True
        if not changed:
            break
    return Project(trees, index, res, problems)


# -- finding scan --


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(
        self, rel: str, tree: ast.Module, res: Resolver, docs: set[int]
    ) -> None:
        self.rel = rel
        self.res = res
        self.found: list[Finding] = []
        self.stack: list[str] = []
        self.docs = docs
        self.ctxs: list[Ctx] = [build_ctx(tree, None, rel, res)]
        self.visit(tree)

    @property
    def symbol(self) -> str:
        return ".".join(self.stack) if self.stack else "<module>"

    def emit(self, kind: str, symbol: str | None = None) -> None:
        self.found.append(Finding(self.rel, kind, symbol or self.symbol))

    def _copy(self, node: ast.expr) -> bool:
        return expr_copy(node, self.ctxs[-1], self.rel, self.res)

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
            self.emit("outbox_writer")
        self.ctxs.append(build_ctx(node, self.ctxs[-1], self.rel, self.res))
        self.generic_visit(node)
        self.ctxs.pop()
        self.stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        if _ENQUEUE_DEF.match(node.name):
            self.emit("outbox_writer")
        self.ctxs.append(build_ctx(node, self.ctxs[-1], self.rel, self.res))
        self.generic_visit(node)
        self.ctxs.pop()
        self.stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    @staticmethod
    def _is_smtp_module(name: str) -> bool:
        return any(name == m or name.startswith(m + ".") for m in _SMTP_MODULES)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if self._is_smtp_module(alias.name):
                self.emit("direct_smtp", f"import:{alias.name}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level == 0 and self._is_smtp_module(module):
            self.emit("direct_smtp", f"import:{module}")
        elif node.level == 0:
            for alias in node.names:
                if self._is_smtp_module(f"{module}.{alias.name}"):
                    self.emit("direct_smtp", f"import:{module}.{alias.name}")

    def visit_Constant(self, node: ast.Constant) -> None:
        if (
            isinstance(node.value, str)
            and id(node) not in self.docs
            and _SQL_WRITE.search(node.value)
        ):
            self.emit("outbox_writer", f"sql:{self.symbol}")

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
        if name and _SINK.match(name) and not in_writer and self._sink_has_copy(node):
            self.emit("inline_copy")
        self.generic_visit(node)

    def _sink_has_copy(self, node: ast.Call) -> bool:
        ctx = self.ctxs[-1]
        for arg in node.args:
            if self._copy(arg):
                return True
        for kw in node.keywords:
            if kw.arg is None:  # **mapping
                if dict_carries_copy(kw.value, ctx, self.rel, self.res):
                    return True
            elif kw.arg.lower() in _COPY_KW and self._copy(kw.value):
                return True
        return False

    @staticmethod
    def _socket_port(node: ast.Call) -> bool:
        for arg in node.args[:1]:
            if isinstance(arg, ast.Tuple) and len(arg.elts) == 2:
                port = arg.elts[1]
                if isinstance(port, ast.Constant) and port.value in _SMTP_PORTS:
                    return True
        return False


def number(findings: list[Finding]) -> list[str]:
    """Stable ids: base plus a 1-based ordinal within the same base."""
    seen: Counter[str] = Counter()
    ids: list[str] = []
    for f in findings:
        seen[f.base] += 1
        ids.append(f"{f.base}#{seen[f.base]}")
    return ids


def scan_sources(sources: dict[str, str]) -> tuple[list[Finding], list[str]]:
    project = analyse(sources)
    findings: list[Finding] = []
    for rel, tree in project.trees.items():
        findings.extend(
            _Scanner(rel, tree, project.resolver, project.index[rel].docs).found
        )
    return findings, project.problems


def scan_source(path: str, source: str) -> list[Finding]:
    """One-file convenience (renderers are resolved within the file only)."""
    return scan_sources({path: source})[0]


# -- product scan --


def _walk(root: Path, prune: list[str]) -> tuple[list[str], dict[str, int]]:
    """Python files under root, plus how many pruned trees each glob swallowed."""
    rels: list[str] = []
    pruned: Counter[str] = Counter()
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath).relative_to(root).as_posix()
        here = "" if here == "." else here
        kept = []
        for d in sorted(dirnames):
            rel_dir = f"{here}/{d}" if here else d
            if d == "__pycache__":
                continue
            hit = next((g for g in prune if glob_match(f"{rel_dir}/x", g)), None)
            if hit is not None:
                if next((root / rel_dir).rglob("*.py"), None) is not None:
                    pruned[hit] += 1
                continue
            kept.append(d)
        dirnames[:] = kept
        for f in sorted(filenames):
            if f.endswith(".py"):
                rels.append(f"{here}/{f}" if here else f)
    return sorted(rels), dict(pruned)


def _validate_entries(entries: list[dict[str, Any]], label: str) -> list[str]:
    problems: list[str] = []
    for e in entries:
        glob = e.get("glob")
        if not glob:
            problems.append(f"{label} entry without a glob: {e!r}")
            continue
        if not e.get("premise"):
            problems.append(f"{label} {glob!r} states no premise")
        kinds = e.get("kinds")
        if not isinstance(kinds, list) or not kinds or not set(kinds) <= set(KINDS):
            problems.append(
                f"{label} {glob!r} must list its `kinds` explicitly "
                f"(subset of {list(KINDS)}); there is no implicit 'everything'"
            )
        elif (
            label == "exclusion"
            and set(kinds) == set(KINDS)
            and not NON_RUNTIME_SEGMENTS & set(glob.split("/"))
        ):
            problems.append(
                f"{label} {glob!r} hides every kind but names no non-runtime "
                f"segment ({sorted(NON_RUNTIME_SEGMENTS)}); an all-kinds "
                "exclusion is only for non-runtime trees. Use `allowed` with "
                "explicit kinds for a delivery owner"
            )
    return problems


@dataclass
class Result:
    ids: list[str]
    families: dict[str, int]
    problems: list[str]


def scan_product(config: dict[str, Any], base_dir: Path) -> Result:
    """Scan one product config. Paths in ``config`` are relative to base_dir."""
    root = (base_dir / config["root"]).resolve()
    if not root.is_dir():
        return Result([], {}, [f"root {config['root']!r} is not a directory"])
    problems: list[str] = []
    families: dict[str, list[str]] = config.get("families", {})
    for fam, roots in sorted(families.items()):
        for r in roots:
            if not (root / r).exists():
                problems.append(
                    f"family {fam!r} root {r!r} does not exist: an entry-point "
                    "family cannot silently drop out of the scan"
                )
    allowed: list[dict[str, Any]] = config.get("allowed", [])
    excluded: list[dict[str, Any]] = config.get("exclude", [])
    problems += _validate_entries(allowed, "allowed owner")
    problems += _validate_entries(excluded, "exclusion")

    full_prune = [
        e["glob"]
        for e in excluded
        if e.get("glob", "").endswith("/**") and set(e.get("kinds") or []) == set(KINDS)
    ]
    rels, pruned = _walk(root, full_prune)

    for entry in [*allowed, *excluded]:
        glob = entry.get("glob", "")
        n = sum(1 for r in rels if glob_match(r, glob)) + pruned.get(glob, 0)
        if n == 0 and not entry.get("optional"):
            problems.append(
                f"{'allowed owner' if entry in allowed else 'exclusion'} "
                f"{glob!r} matches no file: its premise is unverifiable, "
                "remove or fix it"
            )

    sources: dict[str, str] = {}
    fam_counts: Counter[str] = Counter()
    for rel in rels:
        fam, best = "other", -1
        for name, froots in sorted(families.items()):
            for r in froots:
                r = r.rstrip("/")
                if (rel == r or rel.startswith(r + "/")) and len(r) > best:
                    fam, best = name, len(r)
        fam_counts[fam] += 1
        try:
            sources[rel] = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            problems.append(f"{rel}: cannot read ({exc.__class__.__name__})")

    findings, parse_problems = scan_sources(sources)
    problems += parse_problems

    def exempt(f: Finding) -> bool:
        for entry in [*allowed, *excluded]:
            if f.kind in (entry.get("kinds") or []) and glob_match(
                f.path, entry.get("glob", "")
            ):
                return True
        return False

    kept = [f for f in findings if not exempt(f)]
    return Result(number(kept), dict(fam_counts), problems)


# -- backlog --


def load_backlog(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = data.get("findings")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValueError("backlog 'findings' must be a list of id strings")
    return list(ids)


def compare(ids: list[str], backlog: list[str]) -> list[str]:
    problems: list[str] = []
    for d in sorted(i for i, n in Counter(backlog).items() if n > 1):
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
            "then run --write-backlog (remove-only). Do not hand-edit."
        ),
        "product": product,
        "total": len(ids),
        "findings": sorted(ids),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def base_backlog(backlog_path: Path, ref: str) -> list[str]:
    """The backlog's ids as committed at ``ref`` (fail-closed on any doubt)."""
    if not ref or ref.startswith("-"):
        raise ValueError(f"unusable base ref {ref!r}")
    cwd = backlog_path.resolve().parent
    top = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top.returncode != 0:
        raise ValueError("base-ref check needs a git checkout")
    rel = backlog_path.resolve().relative_to(Path(top.stdout.strip()).resolve())
    shown = subprocess.run(
        ["git", "-C", str(cwd), "show", f"{ref}:{rel.as_posix()}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        raise ValueError(f"no backlog at {ref}:{rel.as_posix()}")
    ids = json.loads(shown.stdout).get("findings")
    if not isinstance(ids, list):
        raise ValueError("base backlog 'findings' must be a list")
    return [str(i) for i in ids]


def run(
    config_path: Path,
    backlog_path: Path,
    *,
    write: bool,
    allow_additions: bool = False,
    base_ref: str | None = None,
) -> int:
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
        problems = list(result.problems)
        previous: list[str] | None = None
        try:
            previous = load_backlog(backlog_path)
        except (OSError, ValueError):
            previous = None
        added = sorted(set(result.ids) - set(previous or []))
        if added and not allow_additions:
            problems += [
                f"refusing to ADD finding {i}: --write-backlog is remove-only "
                "(pass --allow-additions only to seed a new product's backlog)"
                for i in added
            ]
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        if problems:
            return 1
        write_backlog(backlog_path, config.get("product", ""), result.ids)
        print(f"wrote {backlog_path} ({len(result.ids)} finding(s))")
        return 0
    problems = list(result.problems)
    try:
        listed = load_backlog(backlog_path)
        problems += compare(result.ids, listed)
        if base_ref is not None:
            base = set(base_backlog(backlog_path, base_ref))
            problems += [
                f"backlog id {i} is not in the backlog at {base_ref}: a backlog "
                "may only shrink; a hand-added id hides a new finding"
                for i in sorted(set(listed) - base)
            ]
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
    parser.add_argument(
        "--allow-additions",
        action="store_true",
        help="let --write-backlog ADD findings (seeding only; defeats the ratchet)",
    )
    parser.add_argument(
        "--base-ref",
        help="git ref whose committed backlog bounds this one: --check refuses "
        "any listed id that ref's backlog does not already hold",
    )
    args = parser.parse_args(argv)
    if args.base_ref and args.write_backlog:
        parser.error("--base-ref only applies to --check")
    if args.allow_additions and not args.write_backlog:
        parser.error("--allow-additions only applies to --write-backlog")
    try:
        return run(
            args.config,
            args.backlog,
            write=args.write_backlog,
            allow_additions=args.allow_additions,
            base_ref=args.base_ref,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
