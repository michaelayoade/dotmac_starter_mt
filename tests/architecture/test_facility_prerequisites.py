"""A module that CALLS a kernel storage facility must DECLARE it.

The vocabulary is `kernel_facilities.py`; this is the gate over it.

Four modules shipped with an undeclared runtime dependency before anything
checked — `dotmac-numbering`, `dotmac-integration` and
`dotmac-entitlement-allocation` on the at-most-once ledger, `dotmac-approvals`
on the outbox relay. The first three were found by grepping the ledger. The
fourth was found only when somebody enumerated the KERNEL instead of the
modules, which is the difference this file institutionalises: it fails on a
facility nobody has classified, not merely on a call nobody remembered.

## What replaces what

`test_numbering_module.py` carried a hand-written version of this for one
module and one facility. It is retired here rather than kept alongside: two
guards for one invariant drift, and the survivor is the one that fails when the
kernel grows. Its two properties are preserved and generalised — the assertion
is driven from the CALL (so it cannot pass with the declaration deleted), and
the plane placement is checked.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from tests.architecture.kernel_facilities import (
    FROZEN,
    MAPPED,
    OUT_OF_SCOPE,
    PACKAGES,
    REPO_ROOT,
    Facility,
    derive_persistence_backed,
)

# ── Finding the calls ───────────────────────────────────────────────────────


def _kernel_facility_calls(path: Path) -> set[str]:
    """Every kernel facility this file CALLS, as facility keys.

    Resolves through imports, which is the whole difficulty:

        from dotmac_kernel.idempotency import execute_once      -> direct
        from dotmac_kernel.idempotency import execute_once as go -> alias
        from dotmac_kernel import idempotency                    -> attribute
        import dotmac_kernel.messaging.outbox as outbox          -> attribute

    A grep for `execute_once(` finds the first and misses the rest, and finds
    the word in a docstring or a comment that mentions it — which is exactly
    how a guard earns a reputation for noise. Only `ast.Call` nodes count, so
    prose about a facility is not a call to it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # local name -> facility key, for `from ... import name [as alias]`
    direct: dict[str, str] = {}
    # local name -> kernel module path, for `import`/`from ... import module`
    modules: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if not node.module.startswith("dotmac_kernel"):
                continue
            tail = node.module[len("dotmac_kernel") :].lstrip(".").replace(".", "/")
            for alias in node.names:
                local = alias.asname or alias.name
                if tail:
                    direct[local] = f"{tail}:{alias.name}"
                    modules[local] = f"{tail}/{alias.name}"
                else:
                    modules[local] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("dotmac_kernel"):
                    continue
                tail = alias.name[len("dotmac_kernel") :].lstrip(".").replace(".", "/")
                modules[alias.asname or alias.name.split(".")[-1]] = tail

    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id in direct:
            called.add(direct[function.id])
        elif isinstance(function, ast.Attribute) and isinstance(
            function.value, ast.Name
        ):
            module = modules.get(function.value.id)
            if module:
                called.add(f"{module}:{function.attr}")
    return called


def _module_packages() -> list[Path]:
    """Installable module packages — the assembly is a different case.

    `app/` runs the kernel lineage, so it always has the tables; requiring it to
    declare a prerequisite would be asking it to depend on itself. A published
    module cannot assume any of that, which is the entire point.
    """
    return sorted(
        path
        for path in PACKAGES.iterdir()
        if (path / "pyproject.toml").is_file() and path.name != "dotmac-kernel"
    )


def _source_files(package: Path) -> list[Path]:
    return sorted(
        path
        for path in (package / "src").rglob("*.py")
        if "migrations/versions" not in path.as_posix()
    )


def _manifest_requires(package: Path) -> set[str]:
    """Every prerequisite the manifest declares, across all three lists."""
    manifest = next(package.rglob("manifest.py"), None)
    if manifest is None:
        return set()
    tree = ast.parse(manifest.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg not in {
            "requires",
            "tenant_requires",
            "platform_requires",
        }:
            continue
        for element in ast.walk(node.value):
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                names.add(element.value)
            # `IDEMPOTENCY_LEDGER_V1.name` — the spelling every module uses
            elif isinstance(element, ast.Attribute) and element.attr == "name":
                if isinstance(element.value, ast.Name):
                    names.add(element.value.id)
    return names


def _verified_in_migrations(package: Path) -> set[str]:
    """Prerequisite names any migration in this lineage actually verifies.

    Read from the `*_REQUIRES` tuples the revisions pass to
    `require_prerequisites`, so emptying a tuple fails this gate even though
    the manifest still declares the name.
    """
    names: set[str] = set()
    for path in package.rglob("migrations/versions/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name) or not target.id.endswith("REQUIRES"):
                continue
            for element in ast.walk(node.value):
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    names.add(element.value)
    return names


#: Spec constants as modules spell them, mapped to the prerequisite they name.
#: `IDEMPOTENCY_LEDGER_V1.name` reads as the constant, not the string.
_CONSTANTS = {
    "IDEMPOTENCY_LEDGER_V1": "idempotency_ledger.v1",
    "OUTBOX_RELAY_V1": "outbox_relay.v1",
    "TENANT_SCOPE_CATALOG_V1": "tenant_scope_catalog.v1",
    "MODULE_DATABASE_ROLES_V1": "module_database_roles.v1",
}


def _normalise(names: set[str]) -> set[str]:
    return {_CONSTANTS.get(name, name) for name in names}


def _callers_of(key: str) -> set[str]:
    """Module source files calling one facility, as `<package>/<relpath>`."""
    callers: set[str] = set()
    for package in _module_packages():
        for path in _source_files(package):
            if key in _kernel_facility_calls(path):
                relative = path.relative_to(package).as_posix()
                callers.add(f"{package.name}/{relative}")
    return callers


# ── Completeness: the half that makes this a guard ──────────────────────────


def test_every_storage_touching_kernel_entrypoint_is_classified() -> None:
    """A kernel facility nobody classified fails BY NAME.

    Without this the file is an allowlist: it would check the three facilities
    somebody thought of and say nothing about the fourth, which is precisely
    how `dotmac-approvals` shipped an undeclared relay dependency while two
    other modules were being fixed for the ledger.
    """
    derived = derive_persistence_backed()
    known = {facility.key for facility in MAPPED} | set(FROZEN)
    unclassified = sorted(
        key
        for key in derived
        if key not in known
        and key not in OUT_OF_SCOPE
        and key.split(":")[0] not in OUT_OF_SCOPE
    )
    assert not unclassified, (
        "kernel entrypoints take a database handle and are classified nowhere:\n  "
        + "\n  ".join(f"{key}  ({derived[key]})" for key in unclassified)
        + "\n\nClassify each in tests/architecture/kernel_facilities.py: MAPPED "
        "with its prerequisite, FROZEN with a caller ratchet, or OUT_OF_SCOPE "
        "with the premise that makes the exclusion enforceable."
    )


def test_no_classified_facility_has_disappeared() -> None:
    """The other direction: a classification whose facility is gone is stale.

    A `MAPPED` entry for a deleted function proves nothing and reads as
    coverage, so the registry has to shrink deliberately.
    """
    derived = set(derive_persistence_backed())
    for facility in MAPPED:
        assert facility.key in derived, (
            f"MAPPED names {facility.key}, which no longer takes a database "
            "handle in the kernel — remove the entry or fix the key"
        )
    for key in FROZEN:
        assert key in derived, (
            f"FROZEN names {key}, which no longer takes a database handle — "
            "remove the entry; a ratchet over nothing is not evidence"
        )


# ── The obligation, both halves ─────────────────────────────────────────────


@pytest.mark.parametrize("facility", MAPPED, ids=lambda f: f.key)
def test_every_caller_declares_the_prerequisite_in_its_manifest(
    facility: Facility,
) -> None:
    """Half one: composition can refuse an assembly that cannot supply it."""
    assert facility.prerequisite is not None
    for package in _module_packages():
        calls = set()
        for path in _source_files(package):
            calls |= _kernel_facility_calls(path)
        if facility.key not in calls:
            continue
        declared = _normalise(_manifest_requires(package))
        assert facility.prerequisite in declared, (
            f"{package.name} calls {facility.key} ({facility.note}) but its "
            f"manifest does not declare {facility.prerequisite!r}. An adopter "
            "running its own lineage would migrate cleanly and fail at the "
            "first call."
        )


@pytest.mark.parametrize("facility", MAPPED, ids=lambda f: f.key)
def test_every_caller_verifies_the_prerequisite_in_a_migration(
    facility: Facility,
) -> None:
    """Half two: DEPLOY refuses a database that does not have the effect.

    Separate from the manifest half on purpose. A declaration is a claim about
    an assembly; only `require_prerequisites` in a revision is a claim checked
    against a live catalogue, and a module can have either without the other.
    """
    assert facility.prerequisite is not None
    for package in _module_packages():
        calls = set()
        for path in _source_files(package):
            calls |= _kernel_facility_calls(path)
        if facility.key not in calls:
            continue
        if not list(package.rglob("migrations/versions/*.py")):
            continue  # stateless module: nothing to verify in, nothing to gate
        verified = _verified_in_migrations(package)
        assert facility.prerequisite in verified, (
            f"{package.name} calls {facility.key} and declares "
            f"{facility.prerequisite!r} on its manifest, but no revision "
            "verifies it. The declaration is then a promise nothing checks "
            "against a real database — add a revision whose upgrade() calls "
            "require_prerequisites."
        )


# ── Frozen facilities: a ratchet, not a prose exemption ─────────────────────


@pytest.mark.parametrize("key", sorted(FROZEN), ids=lambda k: k)
def test_frozen_facilities_gain_no_new_callers(key: str) -> None:
    """Two-directional (ADR-0018).

    Growing fails: a new caller of a facility we already know is undeclarable
    is new debt, taken silently.

    Shrinking ALSO fails: the frozen set is evidence, and evidence that quietly
    matches whatever the tree happens to contain is not evidence. Lowering it is
    a deliberate edit in the change that removes the caller.
    """
    reason, frozen = FROZEN[key]
    actual = _callers_of(key)
    added = sorted(actual - frozen)
    removed = sorted(frozen - actual)
    assert not added, (
        f"new caller(s) of the frozen facility {key}: {added}\n\n{reason}\n\n"
        "Do not add the file to the frozen set to make this pass: the set is "
        "what the eventual prerequisite has to convert."
    )
    assert not removed, (
        f"{key} is no longer called by {removed}, but the frozen set still "
        f"lists it. Lower the set in the change that removed the caller.\n\n"
        f"{reason}"
    )


def test_the_platform_audit_freeze_names_its_successor() -> None:
    """The exception is linked to the work that ends it, not left open.

    Michael named `platform_audit_log.v1` on 2026-08-16 and the remaining work
    is a spec, a verifier, a binding, typed actors, module declarations and
    PostgreSQL proofs. This asserts the entry says so, so nobody reads the
    freeze as a decision that platform audit needs no prerequisite.
    """
    reason, callers = FROZEN["audit:write_platform_audit_event"]
    assert "platform_audit_log.v1" in reason
    assert callers, "the freeze claims callers exist; it must name them"


# ── Sensitivity ─────────────────────────────────────────────────────────────


def test_the_call_detector_sees_through_an_alias() -> None:
    """`from ... import execute_once as run` is still a call to the ledger.

    A guard that only matched the canonical spelling would be defeated by an
    import alias — accidentally, most likely, which is worse than deliberately.
    """
    source = (
        "from dotmac_kernel.idempotency import execute_once as run\n"
        "def go(db):\n    return run(db, scope='x', key='y', operation=str)\n"
    )
    path = REPO_ROOT / "tests/architecture/.alias_probe.py"
    path.write_text(source, encoding="utf-8")
    try:
        assert "idempotency:execute_once" in _kernel_facility_calls(path)
    finally:
        path.unlink()


def test_the_call_detector_sees_an_attribute_call() -> None:
    """`from dotmac_kernel import messaging` then `messaging.outbox...`."""
    source = (
        "import dotmac_kernel.messaging.outbox as ob\n"
        "def go(db):\n    return ob.enqueue_event(db, tenant_id=None)\n"
    )
    path = REPO_ROOT / "tests/architecture/.attribute_probe.py"
    path.write_text(source, encoding="utf-8")
    try:
        assert "messaging/outbox:enqueue_event" in _kernel_facility_calls(path)
    finally:
        path.unlink()


def test_the_call_detector_ignores_prose() -> None:
    """Specificity. A docstring naming a facility is not a call to it.

    Half the grep hits across `packages/` are exactly this — manifests and
    migrations explaining which facility they declare — and a detector that
    counted them would demand prerequisites from files that call nothing.
    """
    source = (
        '"""This module explains execute_once and enqueue_event at length."""\n'
        "# write_platform_audit_event is mentioned here too\n"
        "VALUE = 'execute_once'\n"
    )
    path = REPO_ROOT / "tests/architecture/.prose_probe.py"
    path.write_text(source, encoding="utf-8")
    try:
        assert _kernel_facility_calls(path) == set()
    finally:
        path.unlink()


def test_the_completeness_gate_fails_on_an_unclassified_facility() -> None:
    """The guard's own failure mode, exercised.

    Written against the real derivation with one classification removed, so it
    proves the gate would name a NEW kernel facility rather than proving a
    hand-made example.
    """
    derived = derive_persistence_backed()
    assert derived, "the derivation found nothing; the detector is broken"
    victim = next(iter(sorted(f.key for f in MAPPED)))
    known = ({facility.key for facility in MAPPED} | set(FROZEN)) - {victim}
    unclassified = [
        key
        for key in derived
        if key not in known
        and key not in OUT_OF_SCOPE
        and key.split(":")[0] not in OUT_OF_SCOPE
    ]
    assert unclassified == [victim], (
        "removing one classification must leave exactly that facility "
        f"unclassified; got {unclassified}"
    )


def test_the_manifest_reader_resolves_the_constant_spelling() -> None:
    """`requires=(IDEMPOTENCY_LEDGER_V1.name,)` must read as the string.

    Every module spells it as the constant. A reader that only understood
    string literals would report every one of them as undeclared, and the
    obvious "fix" would be to weaken the gate.
    """
    package = PACKAGES / "dotmac-numbering"
    assert "idempotency_ledger.v1" in _normalise(_manifest_requires(package))


def test_at_least_one_module_is_actually_covered() -> None:
    """A gate that matches nothing passes for the wrong reason."""
    covered = {
        package.name
        for facility in MAPPED
        for package in _module_packages()
        if any(
            facility.key in _kernel_facility_calls(path)
            for path in _source_files(package)
        )
    }
    assert covered, "no module calls any mapped facility; this file proves nothing"
    assert {
        "dotmac-numbering",
        "dotmac-integration",
        "dotmac-approvals",
    } <= covered, f"expected the known callers to be detected, found {sorted(covered)}"


def test_the_release_allowlist_is_covered_by_the_scan() -> None:
    """Every releasable module is scanned, or the gate has a blind spot."""
    allowlist = set(
        tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        .get("tool", {})
        .get("poetry", {})
        .get("dependencies", {})
    )
    scanned = {package.name for package in _module_packages()}
    missing = sorted(
        name
        for name in allowlist
        if name.startswith("dotmac-")
        and name != "dotmac-kernel"
        and (PACKAGES / name).is_dir()
        and name not in scanned
    )
    assert not missing, f"path-dependency modules not scanned: {missing}"
