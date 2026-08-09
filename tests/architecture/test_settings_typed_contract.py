"""The settings read API is typed — no public read returns `Any`.

`tests/unit/test_service_typing.py` bans `Any` payloads in feature services and
module packages, on the stated grounds that "a module is the less-reviewed code
of the two, so exempting it would put the weaker standard exactly where the
stronger one is needed". The kernel was exempt from its own rule, and its
most-called API — `resolve_value` — returned `Any`.

`Any` is not a weaker annotation, it is the ABSENCE of one, and it is
contagious: a value typed `Any` silences checking in every expression it flows
into, so one untyped read costs the type-safety of everything downstream. The
concrete cost was visible the moment it was removed — `rbac.service` was
passing an unchecked value straight into `timedelta(days=...)`.

So the reads are typed two ways, and this pins both:

* `resolve(db, spec)` returns the spec's declared type, because `SettingSpec`
  is generic and carries it.
* `resolve_value` / `resolve_many` are the DYNAMIC path, for keys chosen at
  runtime, and return `object` — a caller must narrow. That is honest where
  `Any` was not: the function genuinely cannot know the type.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dotmac_kernel import settings_resolver as sr

RESOLVER = Path(sr.__file__)

# Public reads and the return annotation each must carry. Spelled out rather
# than "not Any", so a change to any of them is a deliberate edit here.
EXPECTED_RETURNS = {
    "resolve": "T",
    "resolve_value": "object",
    "resolve_many": "dict[str, object]",
    "resolve_with_source": "tuple[object, SettingSource]",
}


def _returns(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.returns is not None:
            found[node.name] = ast.unparse(node.returns)
    return found


@pytest.mark.parametrize(("name", "expected"), sorted(EXPECTED_RETURNS.items()))
def test_public_read_returns_a_real_type(name: str, expected: str) -> None:
    returns = _returns(RESOLVER)
    assert name in returns, f"{name} is gone or lost its return annotation"
    assert returns[name] == expected, (
        f"{name} returns `{returns[name]}`, expected `{expected}`. The settings "
        "read API is typed: use `resolve(db, spec)` for a known setting and "
        "`object` for the dynamic path. `Any` is contagious — it silences "
        "checking in every expression the value reaches."
    )


def test_no_public_read_returns_any() -> None:
    """The rule the table above is one expression of, stated once so a NEW read
    function is covered without anyone remembering to add it."""
    offenders = [
        name
        for name, annotation in _returns(RESOLVER).items()
        if not name.startswith("_")
        and name.startswith("resolve")
        and annotation in {"Any", "Any | None"}
    ]
    assert not offenders, f"public settings reads returning `Any`: {offenders}"


def test_the_scan_is_not_vacuous() -> None:
    """A parse that finds nothing must fail loudly rather than pass silently."""
    returns = _returns(RESOLVER)
    assert returns, "the AST walk parsed no annotated functions at all"
    # Named rather than counted: a threshold encodes today's function count as
    # a rule and fails the day the module is legitimately refactored.
    for required in ("resolve", "resolve_value", "resolve_many"):
        assert required in returns, f"{required} was not parsed — check the walk"


def test_setting_spec_is_generic() -> None:
    """The typed path only works because the spec carries its own type."""
    assert getattr(sr.SettingSpec, "__class_getitem__", None) is not None, (
        "SettingSpec is no longer generic, so `resolve` cannot return the "
        "declared type and every caller is back to narrowing by hand"
    )
    spec = sr.SettingSpec[int](
        domain=sr.SettingDomain.audit,
        key="_probe",
        value_type=sr.SettingValueType.integer,
        default=1,
    )
    assert spec.default == 1
