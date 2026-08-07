"""Every cache key carries its scope, and is built by `dotmac_kernel.cache`.

A cross-tenant cache leak is the failure mode a green test suite cannot see:
tenant B reads the entry tenant A populated, no exception is raised, and the
only evidence is in someone else's data. So the rules are static and structural
rather than behavioural.

Three checks:

1. No module builds a cache key by hand. A key built with an f-string is a key
   whose scope segment is optional.
2. No `lru_cache`/`cache` decorates a function that takes tenant-bearing input.
   This is the easily-missed one: a process-wide memo on a tenant-scoped getter
   looks like an optimisation and behaves like a data leak. It is also the shape
   someone would copy from `get_brand()`'s zero-argument `@lru_cache(maxsize=1)`
   onto a loader that does take a tenant.
3. `cache_key` cannot be called without a scope — proven by calling it.

SCOPE LIMITATION: this walks the kernel package, the assembly, and installed
module packages. A new package that caches must be added to `_SOURCE_ROOTS`, and
`test_the_scan_is_not_vacuous` fails loudly if a root stops resolving.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dotmac_kernel.cache import PlatformScope, TenantScope, cache_key

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_SOURCE_ROOTS: tuple[Path, ...] = (
    PROJECT_ROOT / "packages/dotmac-kernel/src/dotmac_kernel",
    PROJECT_ROOT / "packages/dotmac-template-studio/src/dotmac_template_studio",
    PROJECT_ROOT / "app",
)

# The one module allowed to build a key from raw strings — it IS the builder.
_KEY_BUILDER = PROJECT_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/cache.py"

# `rate_limit` predates this module and already implements the exact pattern
# (scope first, `platform` a named literal). Listed explicitly rather than
# silently skipped, so the exemption is a decision someone can revisit — and it
# shrinks to nothing the day that key is migrated onto `cache_key`.
_GRANDFATHERED: frozenset[Path] = frozenset(
    {PROJECT_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/middleware/rate_limit.py"}
)

# Substrings that mark a string as a cache key being assembled by hand.
_KEY_MARKERS = ("cache:", "cache_key", "_CACHE_KEY", "cachekey")

_MEMO_DECORATORS = {"lru_cache", "cache"}

# Parameter names that carry (or can reach) tenant identity. A memo over any of
# these is process-wide state keyed on one tenant's data.
_TENANT_PARAMS = {"tenant", "tenant_id", "tenant_slug", "scope", "party", "party_id"}


def _python_files() -> list[Path]:
    return sorted(
        path
        for root in _SOURCE_ROOTS
        for path in root.rglob("*.py")
        if path != _KEY_BUILDER and path not in _GRANDFATHERED
    )


def test_the_scan_is_not_vacuous() -> None:
    """Assert on the set walked — a moved package would empty it silently."""
    missing = [str(root) for root in _SOURCE_ROOTS if not root.is_dir()]
    assert not missing, f"_SOURCE_ROOTS names a missing directory: {missing}"
    assert len(_python_files()) > 50, "the source scan found suspiciously few files"


def test_no_module_builds_a_cache_key_by_hand() -> None:
    """A key assembled from an f-string is a key whose scope is optional."""
    violations: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            literal = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if any(marker in literal for marker in _KEY_MARKERS):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: {literal!r}"
                )
    assert not violations, (
        "cache key(s) built by string interpolation — use "
        "`dotmac_kernel.cache.cache_key(..., scope=...)`, which cannot omit the "
        "scope:\n" + "\n".join(violations)
    )


def _memo_decorated(tree: ast.AST) -> list[ast.FunctionDef]:
    found: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            name = (
                target.attr
                if isinstance(target, ast.Attribute)
                else target.id
                if isinstance(target, ast.Name)
                else None
            )
            if name in _MEMO_DECORATORS:
                found.append(node)
                break
    return found


def test_no_process_wide_memo_over_tenant_bearing_input() -> None:
    """`@lru_cache` may only memoise deployment-wide values.

    A memo is process-global state. Keyed on anything tenant-bearing, the first
    tenant through the door populates it and every later tenant reads that
    answer — the leak that looks like a cache hit.
    """
    violations: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for func in _memo_decorated(tree):
            params = {
                arg.arg
                for arg in (
                    *func.args.posonlyargs,
                    *func.args.args,
                    *func.args.kwonlyargs,
                )
            }
            offending = sorted(params & _TENANT_PARAMS)
            if offending:
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{func.lineno}: "
                    f"{func.name}({', '.join(offending)})"
                )
    assert not violations, (
        "memoised function(s) take tenant-bearing input — a process-wide memo "
        "over tenant data serves one tenant's value to every other:\n"
        + "\n".join(violations)
    )


# ── The builder's own guarantees ────────────────────────────────────────────


def test_scope_is_required() -> None:
    """Omitting the scope is a TypeError, not a plausible key."""
    with pytest.raises(TypeError):
        cache_key("flags", "x")  # type: ignore[call-arg]


def test_tenant_and_platform_segments_cannot_collide(tenant_id_pair) -> None:
    """The structural claim the whole design rests on."""
    a, b = tenant_id_pair
    assert cache_key("f", scope=TenantScope(a)) != cache_key("f", scope=TenantScope(b))
    assert cache_key("f", scope=TenantScope(a)) != cache_key("f", scope=PlatformScope())
    # A tenant segment is never the platform literal, whatever the uuid is.
    assert "platform" not in cache_key("f", scope=TenantScope(a)).split(":")[-1]


def test_a_part_cannot_forge_another_keys_shape() -> None:
    with pytest.raises(ValueError, match="separator"):
        cache_key("flags:evil", scope=PlatformScope())


def test_version_retires_a_generation() -> None:
    first = cache_key("flags", scope=PlatformScope(), version=1)
    second = cache_key("flags", scope=PlatformScope(), version=2)
    assert first != second
    assert first.startswith("flags:platform")


@pytest.fixture()
def tenant_id_pair():
    from uuid import uuid4

    return uuid4(), uuid4()
