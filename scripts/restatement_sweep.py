"""AST sweep for kernel-owned facts restated in a product.

Written after a grep-shaped sweep under-reported twice in one hour: it missed
specs built through a callable rather than a literal constructor, and missed an
`import X as Y` because the alias was searched for instead of the name.
"""

from __future__ import annotations

import ast
import collections
import pathlib
import sys

VALUE_TYPE_NAMES = {"string", "integer", "boolean", "json", "list", "money"}


def specs(tree, path):
    """Every setting-spec declaration, in either shape.

    Literal `SettingSpec(...)`, and `setting_spec(...)` — the callable a
    `build_*_specs(setting_spec)` module is handed, which is the shape the grep
    could not see.
    """
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fn = getattr(n.func, "id", "") or getattr(n.func, "attr", "")
        if fn not in {"SettingSpec", "setting_spec", "SettingSpecDef"}:
            continue
        kw = {k.arg: k.value for k in n.keywords}
        if "key" not in kw:
            out.append((path, n.lineno, "<non-literal key>", "?"))
            continue
        try:
            key = ast.literal_eval(kw["key"])
        except (ValueError, SyntaxError):
            key = "<dynamic>"
        dom = ast.unparse(kw["domain"]).split(".")[-1] if "domain" in kw else "?"
        out.append((path, n.lineno, key, dom))
    return out


def type_branches(tree, path):
    """Comparisons that decide something by naming a value type."""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Compare):
            continue
        ops = [n.left, *n.comparators]
        srcs = [ast.unparse(o) for o in ops]
        names = [s for s in srcs if s.split(".")[-1].strip("\"'") in VALUE_TYPE_NAMES]
        if not names:
            continue
        if any("value_type" in s or "ValueType" in s for s in srcs):
            out.append((path, n.lineno, ast.unparse(n)))
    return out


#: Modules that OWN the settings table and may read it directly: the model,
#: the settings service, the resolver, the seed, and settings-admin surfaces.
#: Everything else reading it is a parallel reader.
OWNS_THE_TABLE = (
    "models/domain_settings.py",
    "services/domain_settings.py",
    "services/settings_spec.py",
    "services/settings_seed.py",
    "services/settings_api",
    "services/settings_secret_cleanup.py",
    "api/settings.py",
    "settings_specs/",
)


def direct_setting_reads(tree, path):
    """Distinct STATEMENTS in non-owning modules that query the settings table.

    Counted per statement, not per call: `db.query(X).filter(...).filter(...)`
    is one read, and counting its three calls would inflate a query chain into
    a finding. Over-reporting is the same failure as under-reporting, one sign
    away.
    """
    if any(o in path for o in OWNS_THE_TABLE):
        return []
    out, seen = [], set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.stmt):
            continue
        try:
            src = ast.unparse(n)
        except Exception:  # noqa: S112 - unparse is best-effort
            continue
        if "DomainSetting" not in src:
            continue
        if not any(c in src for c in (".query(", "select(", ".scalars(", ".execute(")):
            continue
        if n.lineno in seen:
            continue
        seen.add(n.lineno)
        out.append((path, n.lineno, src.replace(chr(10), " ")[:90]))
    return out


def cache_keys(tree, path):
    """A settings cache key CONSTRUCTED locally.

    Only f-strings: a bare `"settings:manage"` is a permission code, and
    counting those gave 139 "cache keys" in Sub and 56 in ERP, nearly all of
    them permissions. A key is interpolated; a permission is a literal.
    """
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.JoinedStr):
            continue
        try:
            s = ast.unparse(n)
        except Exception:  # noqa: S112 - unparse is best-effort
            continue
        if "settings:" in s and "{" in s:
            out.append((path, n.lineno, s[:90]))
    return out


def sweep(root: pathlib.Path, label: str):
    all_specs, branches, reads, keys, vocab = [], [], [], [], []
    for p in sorted(root.rglob("*.py")):
        s = p.as_posix()
        if any(
            x in s
            for x in (
                "/.venv/",
                "/worktrees/",
                "/node_modules/",
                "/.mypy_cache/",
                "/versions_archive/",
                "/__pycache__/",
            )
        ):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            # A file this interpreter cannot parse is not a finding about
            # restatement; it is a finding about the file, and belongs to
            # whatever lints it.
            continue
        rel = p.relative_to(root.parent).as_posix()
        all_specs += specs(tree, rel)
        branches += type_branches(tree, rel)
        reads += direct_setting_reads(tree, rel)
        keys += cache_keys(tree, rel)
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef) and n.name in {
                "SettingValueType",
                "SettingDomain",
            }:
                bases = ",".join(ast.unparse(b) for b in n.bases)
                vocab.append((rel, n.lineno, n.name, bases))
    print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")
    print(
        f"spec declarations: {len(all_specs)}  "
        f"(files: {len({s[0] for s in all_specs})})"
    )
    by_file = collections.Counter(s[0] for s in all_specs)
    for f, c in by_file.most_common(6):
        print(f"    {c:>4}  {f}")
    dup = [
        k
        for k, c in collections.Counter((s[3], s[2]) for s in all_specs).items()
        if c > 1
    ]
    print(f"  duplicated (domain,key): {len(dup)} {dup[:5]}")
    print("\nvocabulary classes:")
    for v in vocab:
        print(f"    {v[0]}:{v[1]}  class {v[2]}({v[3]})")
    print(
        f"\nbranch-on-value-type: {len(branches)} sites in "
        f"{len({b[0] for b in branches})} modules"
    )
    for f, c in collections.Counter(b[0] for b in branches).most_common(10):
        print(f"    {c:>4}  {f}")
    print(
        f"\nparallel readers (query the settings table directly): "
        f"{len(reads)} calls in {len({r[0] for r in reads})} modules"
    )
    for f, c in collections.Counter(r[0] for r in reads).most_common(10):
        print(f"    {c:>4}  {f}")
    print(
        f"\nlocal settings cache keys: {len(keys)} in "
        f"{len({k[0] for k in keys})} modules"
    )
    for f, c in collections.Counter(k[0] for k in keys).most_common(6):
        print(f"    {c:>4}  {f}")


sweep(pathlib.Path(sys.argv[1]), sys.argv[2])
