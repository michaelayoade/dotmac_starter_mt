#!/usr/bin/env python
"""Deterministic kernel-surface audit (kernel-boundary plan, Task 0).

Inventories every consumer of `dotmac_kernel.*` across the repository and classifies
the surface so the package split (Task 1+) can be planned against facts, not
assumptions. This script produces the DATA; the human-readable findings and the
classification decisions live in
`docs/superpowers/reviews/2026-07-18-kernel-surface-audit.md`.

Read-only. Emits nothing but its report. Deterministic: every collection is
sorted and no clock/random/host value enters the output, so re-running on an
unchanged tree yields byte-identical results (checked into CI intent later).

Consumers are separated by AREA so a test-only or tooling-only import can never
be mistaken for public kernel API:

- ``kernel``          — ``dotmac_kernel/**`` itself (the surface being audited)
- ``assembly``        — ``app/main.py`` + ``app/features/**`` (runtime that a
                        product assembly composes; imports here are the real
                        public-API pressure)
- ``alembic``         — ``alembic/**`` (migration runtime; imports here are
                        public but on the migration path, not the request path)
- ``tests``           — ``tests/**`` (MUST NOT define public API on their own)
- ``tooling``         — ``scripts/**`` (operator CLIs; public but out-of-band)

Usage:
    poetry run python scripts/audit_kernel_surface.py            # markdown
    poetry run python scripts/audit_kernel_surface.py --json     # machine data
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Area classification is by path prefix, most specific first.
_AREA_RULES: tuple[tuple[str, str], ...] = (
    ("dotmac_kernel/", "kernel"),
    ("app/main.py", "assembly"),
    ("app/features/", "assembly"),
    ("app/__init__.py", "assembly"),
    ("app/features/__init__.py", "assembly"),
    ("alembic/", "alembic"),
    ("tests/", "tests"),
    ("scripts/", "tooling"),
)

# Areas whose imports create genuine public-API obligations, in priority order
# (an import from a higher-priority area outranks a lower one when we summarize
# a symbol's strongest consumer).
_PUBLIC_AREAS = ("assembly", "alembic")
_NONPUBLIC_AREAS = ("tests", "tooling")
_AREA_ORDER = ("assembly", "alembic", "tooling", "tests", "kernel")


def _area_for(rel_path: str) -> str | None:
    for prefix, area in _AREA_RULES:
        if rel_path == prefix or rel_path.startswith(prefix):
            return area
    return None


def _core_module(name: str) -> str | None:
    """Return the dotted dotmac_kernel.* module a name refers to, or None."""
    if name == "dotmac_kernel" or name.startswith("dotmac_kernel."):
        return name
    return None


@dataclass
class ImportRef:
    area: str
    rel_path: str
    lineno: int
    module: str  # the dotmac_kernel.* module
    symbol: str  # imported name, or "*"/"<module>" for module-form imports


@dataclass
class ModuleFacts:
    module: str
    declared_all: list[str] = field(default_factory=list)
    defined: set[str] = field(default_factory=set)  # top-level def/class/assign


def _iter_py_files() -> list[Path]:
    out: list[Path] = []
    for base in ("app", "alembic", "tests", "scripts"):
        root = REPO_ROOT / base
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            out.append(p)
    return out


def _module_name(rel_path: str) -> str:
    dotted = rel_path[:-3].replace("/", ".")
    if dotted.endswith(".__init__"):
        dotted = dotted[: -len(".__init__")]
    return dotted


def _collect_imports(tree: ast.AST, area: str, rel_path: str) -> list[ImportRef]:
    refs: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            mod = _core_module(node.module)
            if mod is None:
                continue
            for alias in node.names:
                refs.append(ImportRef(area, rel_path, node.lineno, mod, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                mod = _core_module(alias.name)
                if mod is not None:
                    refs.append(ImportRef(area, rel_path, node.lineno, mod, "<module>"))
    return refs


def _collect_module_facts(tree: ast.AST, module: str) -> ModuleFacts:
    facts = ModuleFacts(module=module)
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            facts.defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    facts.defined.add(target.id)
                    if target.id == "__all__" and isinstance(
                        node.value, ast.List | ast.Tuple
                    ):
                        facts.declared_all = sorted(
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant)
                            and isinstance(elt.value, str)
                        )
    return facts


def build_audit() -> dict:
    imports: list[ImportRef] = []
    module_facts: dict[str, ModuleFacts] = {}
    parse_errors: list[str] = []

    for path in _iter_py_files():
        rel = str(path.relative_to(REPO_ROOT))
        area = _area_for(rel)
        if area is None:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:  # pragma: no cover - defensive
            parse_errors.append(f"{rel}: {exc}")
            continue
        if area != "kernel":
            imports.extend(_collect_imports(tree, area, rel))
        else:
            mod = _module_name(rel)
            module_facts[mod] = _collect_module_facts(tree, mod)

    # symbol -> {area -> [ (rel_path, lineno) ]}
    symbol_consumers: dict[tuple[str, str], dict[str, list[tuple[str, int]]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    module_consumers: dict[str, set[str]] = defaultdict(set)
    for ref in imports:
        symbol_consumers[(ref.module, ref.symbol)][ref.area].append(
            (ref.rel_path, ref.lineno)
        )
        module_consumers[ref.module].add(ref.area)

    # Classify each imported symbol.
    classified: list[dict] = []
    for (module, symbol), by_area in sorted(symbol_consumers.items()):
        areas = set(by_area)
        public_areas = sorted(areas & set(_PUBLIC_AREAS))
        nonpublic_only = not public_areas and bool(areas & set(_NONPUBLIC_AREAS))
        facts = module_facts.get(module)
        in_all = bool(facts and symbol in facts.declared_all)
        declared_all_exists = bool(facts and facts.declared_all)
        # Two independent leak signals, both meaning "runtime depends on
        # something the module does not present as public API":
        #   (a) the module declares an __all__ and this runtime-consumed
        #       symbol is not in it; and
        #   (b) the symbol is private-by-convention (leading underscore) yet a
        #       public area imports it across the module boundary.
        # Either flags a symbol that needs a public wrapper or a relocation
        # before the package split can pin the surface.
        private_named = symbol.startswith("_") and symbol != "<module>"
        leaked = bool(public_areas) and (
            (declared_all_exists and not in_all) or private_named
        )
        if public_areas:
            exposure = "public-runtime"
        elif nonpublic_only:
            exposure = "test-or-tooling-only"
        else:
            exposure = "unclassified"
        classified.append(
            {
                "module": module,
                "symbol": symbol,
                "exposure": exposure,
                "consumer_areas": sorted(areas),
                "in_module_all": in_all,
                "module_declares_all": declared_all_exists,
                "leaked_from_all": leaked,
                "consumer_count": sum(len(v) for v in by_area.values()),
                "sample_sites": sorted(
                    {f"{p}:{ln}" for v in by_area.values() for (p, ln) in v}
                )[:3],
            }
        )

    # Per-module rollup.
    modules_summary = []
    for mod in sorted(module_facts):
        facts = module_facts[mod]
        syms = [c for c in classified if c["module"] == mod]
        modules_summary.append(
            {
                "module": mod,
                "declares_all": bool(facts.declared_all),
                "declared_all": facts.declared_all,
                "imported_symbols": sorted({c["symbol"] for c in syms}),
                "public_runtime_symbols": sorted(
                    {c["symbol"] for c in syms if c["exposure"] == "public-runtime"}
                ),
                "test_or_tooling_only_symbols": sorted(
                    {
                        c["symbol"]
                        for c in syms
                        if c["exposure"] == "test-or-tooling-only"
                    }
                ),
                "leaked_symbols": sorted(
                    {c["symbol"] for c in syms if c["leaked_from_all"]}
                ),
                "consumer_areas": sorted(module_consumers.get(mod, set())),
                "unused_by_consumers": not syms,
            }
        )

    return {
        "modules": modules_summary,
        "symbols": classified,
        "parse_errors": sorted(parse_errors),
        "totals": {
            "kernel_modules": len(module_facts),
            "imported_symbols": len(classified),
            "public_runtime": sum(
                1 for c in classified if c["exposure"] == "public-runtime"
            ),
            "test_or_tooling_only": sum(
                1 for c in classified if c["exposure"] == "test-or-tooling-only"
            ),
            "leaked_from_all": sum(1 for c in classified if c["leaked_from_all"]),
        },
    }


def _md(audit: dict) -> str:
    lines: list[str] = []
    t = audit["totals"]
    lines.append("# Kernel surface audit (generated)")
    lines.append("")
    lines.append(
        "Generated by `scripts/audit_kernel_surface.py`. Deterministic; "
        "re-run to refresh. Narrative + decisions live in the sibling "
        "`2026-07-18-kernel-surface-audit.md`."
    )
    lines.append("")
    lines.append(
        f"- kernel modules: **{t['kernel_modules']}**  |  imported symbols: "
        f"**{t['imported_symbols']}**"
    )
    lines.append(
        f"- public-runtime symbols: **{t['public_runtime']}**  |  "
        f"test/tooling-only: **{t['test_or_tooling_only']}**  |  "
        f"leaked (runtime-used, not in `__all__`): **{t['leaked_from_all']}**"
    )
    if audit["parse_errors"]:
        lines.append(f"- parse errors: **{len(audit['parse_errors'])}**")
    lines.append("")

    lines.append("## Per-module rollup")
    lines.append("")
    lines.append(
        "| module | declares `__all__` | consumer areas | public-runtime | "
        "test/tooling-only | leaked |"
    )
    lines.append("|---|---|---|---|---|---|")
    for m in audit["modules"]:
        lines.append(
            "| `{module}` | {all} | {areas} | {pub} | {tt} | {leak} |".format(
                module=m["module"],
                all="yes" if m["declares_all"] else "—",
                areas=", ".join(m["consumer_areas"]) or "(none)",
                pub=len(m["public_runtime_symbols"]),
                tt=len(m["test_or_tooling_only_symbols"]),
                leak=", ".join(f"`{s}`" for s in m["leaked_symbols"]) or "—",
            )
        )
    lines.append("")

    lines.append("## Test/tooling-only symbols (MUST NOT become public API)")
    lines.append("")
    tt = [c for c in audit["symbols"] if c["exposure"] == "test-or-tooling-only"]
    if not tt:
        lines.append("_None — every imported kernel symbol has a runtime consumer._")
    else:
        lines.append("| module | symbol | consumer areas | sites |")
        lines.append("|---|---|---|---|")
        for c in tt:
            lines.append(
                "| `{m}` | `{s}` | {a} | {sites} |".format(
                    m=c["module"],
                    s=c["symbol"],
                    a=", ".join(c["consumer_areas"]),
                    sites="; ".join(c["sample_sites"]),
                )
            )
    lines.append("")

    lines.append("## Leaked internals (runtime-consumed, absent from `__all__`)")
    lines.append("")
    leaked = [c for c in audit["symbols"] if c["leaked_from_all"]]
    if not leaked:
        lines.append(
            "_None — every runtime-consumed symbol from an `__all__`-declaring "
            "module is advertised._"
        )
    else:
        lines.append("| module | symbol | consumer areas | sites |")
        lines.append("|---|---|---|---|")
        for c in leaked:
            lines.append(
                "| `{m}` | `{s}` | {a} | {sites} |".format(
                    m=c["module"],
                    s=c["symbol"],
                    a=", ".join(c["consumer_areas"]),
                    sites="; ".join(c["sample_sites"]),
                )
            )
    lines.append("")

    lines.append("## Public-runtime surface (candidate kernel API)")
    lines.append("")
    lines.append("| module | symbol | consumer areas | count |")
    lines.append("|---|---|---|---|")
    for c in audit["symbols"]:
        if c["exposure"] != "public-runtime":
            continue
        lines.append(
            "| `{m}` | `{s}` | {a} | {n} |".format(
                m=c["module"],
                s=c["symbol"],
                a=", ".join(c["consumer_areas"]),
                n=c["consumer_count"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    args = parser.parse_args()
    audit = build_audit()
    if args.json:
        print(json.dumps(audit, indent=2, sort_keys=True))
    else:
        print(_md(audit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
