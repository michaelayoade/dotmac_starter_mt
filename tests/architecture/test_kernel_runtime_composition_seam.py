"""Kernel-owned paths run through a product-supplied `DatabaseRuntime` with
the reference runtime (`dotmac_kernel.db`) genuinely unavailable.

This is the closing half of the direct-import retirement: a product whose
direct `dotmac_kernel.db` import count is zero can still have the KERNEL
itself reach the eager reference runtime transitively, at boot or on every
authenticated request, through `dotmac_kernel.deps`, `middleware.tenant`, and
the `app_factory` startup checks. "No direct import" cannot detect that — the
only proof that means anything blocks the reference runtime from ever being
importable and then drives real work through the seam
(`ProductAssemblySpec.database_runtime` -> `session_runtime
.bind_database_runtime` -> `session_runtime.resolve_database_runtime`).

`scripts/check_kernel_runtime_composition_seam.py` is that probe: it sets
``sys.modules["dotmac_kernel.db"] = None`` before importing anything else (so
any later import of it anywhere in the process raises `ImportError`), then
drives boot, an authenticated request (`platform_auth` + `deps`), a
CLI-shaped entry point, and a worker path (`messaging.worker.run_once`)
through a `DatabaseRuntime` it constructs and binds itself.

A bespoke, feature-free `ProductAssemblySpec` cannot see the sharpest hazard:
the real `app.assembly` composes `FEATURE_MODULES` via `load_manifests` at
IMPORT TIME, before `create_app` ever runs, and several feature services
import `dotmac_kernel.db` at module scope to reach `conflict_savepoint` — an
eager chain no zero-feature probe exercises.
`test_real_assembly_strict_composition_leaves_the_reference_unloaded` and
`test_one_eager_feature_import_defeats_a_strict_binding` below acceptance-test
that directly, against `app.assembly` (or a copy of it with one import
planted back).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KERNEL_SOURCE = ROOT / "packages" / "dotmac-kernel" / "src"
PROBE = ROOT / "scripts" / "check_kernel_runtime_composition_seam.py"

_EXPECTED_STAGES = (
    "PASS boot: create_app + startup validation ran on the product runtime",
    "PASS authenticated request: platform logout resolved "
    "deps.get_platform_db through the product runtime",
    "PASS CLI entry point: upsert ran through resolve_database_runtime()",
    "PASS worker path: run_once reached the product runtime's session",
    "PASS dotmac_kernel.db stayed unimported for the whole probe",
)


def _run_probe(
    tmp_path: Path,
    probe: Path = PROBE,
    *,
    source_root: Path = KERNEL_SOURCE,
    app_root: Path | None = None,
    argv: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "PLATFORM_DATABASE_URL", "PYTHONPATH"}
    }
    path_entries = [str(source_root)]
    if app_root is not None:
        path_entries.insert(0, str(app_root))
    program = (
        "import runpy, sys; "
        f"sys.path[0:0] = {path_entries!r}; "
        f"sys.argv = [{str(probe)!r}, *{list(argv)!r}]; "
        f"runpy.run_path({str(probe)!r}, run_name='__main__')"
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", program],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_every_named_path_runs_on_the_product_runtime_while_db_is_unimportable(
    tmp_path: Path,
) -> None:
    result = _run_probe(tmp_path)
    assert result.returncode == 0, result.stderr
    for stage in _EXPECTED_STAGES:
        assert stage in result.stdout, (
            f"expected stage missing from probe output: {stage!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def test_authenticated_request_stage_fails_when_the_fallback_is_restored(
    tmp_path: Path,
) -> None:
    """Sensitivity control for the authenticated-request stage.

    That stage sidesteps `login()` to avoid hashing a password (hard rule 42),
    driving `POST /platform/auth/logout` from a pre-seeded session instead of
    a minted credential. That is only a proof of the runtime seam if it
    genuinely depends on which runtime is installed — otherwise it is a stub
    that would pass whether or not the seam works, and would keep passing if
    the seam were deleted.

    `main_fallback_sensitivity()` (`argv[1] == "fallback"`) seeds the
    IDENTICAL session into the product's own runtime but never installs it,
    with `dotmac_kernel.db` genuinely importable this time (the fallback is
    deliberately live). The reference runtime it falls back to is a
    different, empty database, so the same logout call must be REFUSED.
    """
    result = _run_probe(tmp_path, argv=("fallback",))
    assert result.returncode == 0, result.stderr
    assert "PASS fallback sensitivity:" in result.stdout, (
        f"the fallback sensitivity control did not report success\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_strict_mode_refuses_a_missed_binding_without_importing_the_reference(
    tmp_path: Path,
) -> None:
    """The seam existing is not the same as the seam being USED.

    `resolve_database_runtime()`'s fallback to `dotmac_kernel.db.runtime` is
    correct for the reference assembly, but it means a product that simply
    forgets to set `ProductAssemblySpec.database_runtime` gets the eager
    reference runtime with no complaint — the same defect shape one level up.
    `require_database_runtime=True` is the declared opt-out: `create_app`
    must refuse to build, and the refusal itself must not import
    `dotmac_kernel.db` (a refusal that reaches the thing it refuses to fall
    back to would prove nothing).
    """
    result = _run_probe(tmp_path, argv=("strict",))
    assert result.returncode == 0, result.stderr
    assert (
        "PASS strict mode: create_app refused a missed database_runtime "
        "binding without ever importing dotmac_kernel.db"
    ) in result.stdout


def test_strict_mode_is_a_no_op_when_a_runtime_is_supplied(tmp_path: Path) -> None:
    """Near-miss half: declaring `require_database_runtime=True` alongside a
    REAL `database_runtime` must build normally — strictness only refuses the
    missing-binding case, it does not forbid the field's normal, satisfied
    use. This reuses the full probe (`main()`), which never sets
    `require_database_runtime`; the point here is that setting it does not
    change behaviour once a runtime is actually installed, checked by editing
    the probe's own spec construction to add the flag."""
    import shutil

    planted_root = tmp_path / "strict-satisfied-src"
    shutil.copytree(KERNEL_SOURCE, planted_root)

    probe_source = PROBE.read_text()
    anchor = "        database_runtime=runtime,\n"
    assert probe_source.count(anchor) == 1, "probe spec construction has changed shape"
    planted_probe_source = probe_source.replace(
        anchor,
        anchor + "        require_database_runtime=True,\n",
        1,
    )
    planted_probe = tmp_path / "strict_satisfied_probe.py"
    planted_probe.write_text(planted_probe_source)

    result = _run_probe(tmp_path, probe=planted_probe, source_root=planted_root)
    assert result.returncode == 0, result.stderr
    for stage in _EXPECTED_STAGES:
        assert stage in result.stdout


def test_the_probe_genuinely_blocks_the_reference_runtime(tmp_path: Path) -> None:
    """Sensitivity (near-miss half): if the block itself were a no-op, a bare
    `import dotmac_kernel.db` would succeed silently and this probe would be
    proving nothing. Confirm the block bites on its own, isolated from the
    seam under test."""
    program = (
        "import sys; sys.modules['dotmac_kernel.db'] = None; "
        f"sys.path.insert(0, {str(KERNEL_SOURCE)!r}); "
        "import dotmac_kernel.db"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "PLATFORM_DATABASE_URL", "PYTHONPATH"}
    }
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", program],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    # `sys.modules[name] = None` raises `ModuleNotFoundError` (a subclass of
    # `ImportError`) — the exception CLASS printed in the traceback, not the
    # base-class name. Assert the stable substring the actual message
    # carries, not a name that only sometimes appears literally.
    assert "halted; None in sys.modules" in result.stderr


def test_the_seam_bites_a_reintroduced_transitive_reach(tmp_path: Path) -> None:
    """Plant half: reintroduce the exact regression this seam retired
    (`dotmac_kernel.deps` importing `dotmac_kernel.db` at MODULE SCOPE again)
    in a copied source tree, and observe the probe fail against it — proving
    the guard is the LIVE BEHAVIOUR, not the absence of a grep hit.

    An earlier version of this plant inserted the import as the first line
    INSIDE `get_db`'s function body, keyed off the `runtime =
    resolve_database_runtime()` line found there — which only executes when
    `get_db` itself is CALLED. This probe's routes never call plain `get_db`
    (only `get_platform_db`, via `/platform/auth/logout`), so that plant
    never fired and the probe passed for the wrong reason — exactly the
    "test named for a property but not exercising it" failure mode this
    file's whole design exists to avoid (CI caught it: `test_the_seam_bites
    _a_reintroduced_transitive_reach` reported the probe passing after the
    plant). The fix is a genuine MODULE-SCOPE import — inserted right after
    `deps.py`'s own real imports, so it fires the instant `deps` is
    imported, regardless of which function anything later calls.
    """
    import shutil

    planted_root = tmp_path / "planted-src"
    shutil.copytree(KERNEL_SOURCE, planted_root)
    deps_path = planted_root / "dotmac_kernel" / "deps.py"
    source = deps_path.read_text()
    anchor = "from dotmac_kernel.session_runtime import resolve_database_runtime\n"
    assert source.count(anchor) == 1, "deps.py's own import block has changed shape"
    deps_path.write_text(
        source.replace(
            anchor,
            anchor + "from dotmac_kernel.db import get_db as _ref  # noqa: F401\n",
            1,
        )
    )

    probe_source = PROBE.read_text()
    planted_probe = tmp_path / "planted_probe.py"
    planted_probe.write_text(probe_source)

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "PLATFORM_DATABASE_URL", "PYTHONPATH"}
    }
    program = (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(planted_root)!r}); "
        f"runpy.run_path({str(planted_probe)!r}, run_name='__main__')"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", program],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, (
        "the probe passed after deps.get_db was made to import dotmac_kernel.db "
        "again — the guard did not bite a real transitive reach"
    )
    # Same reasoning as `test_the_probe_genuinely_blocks_the_reference_runtime`:
    # `sys.modules[name] = None` raises `ModuleNotFoundError`, whose printed
    # message carries this exact stable substring, not the literal string
    # "ImportError".
    assert "halted; None in sys.modules" in result.stderr


def test_a_docstring_mention_of_the_reference_module_is_not_flagged(
    tmp_path: Path,
) -> None:
    """Near-miss half: a comment/docstring naming `dotmac_kernel.db` (there are
    dozens, by design — see `session_runtime.py`'s module docstring) must not
    trip the same probe that catches a real import."""
    import shutil

    planted_root = tmp_path / "near-miss-src"
    shutil.copytree(KERNEL_SOURCE, planted_root)
    deps_path = planted_root / "dotmac_kernel" / "deps.py"
    source = deps_path.read_text()
    anchor = "    runtime = resolve_database_runtime()\n"
    assert source.count(anchor) == 1
    deps_path.write_text(
        source.replace(
            anchor,
            "    # See dotmac_kernel.db for the reference assembly's own instance.\n"
            + anchor,
            1,
        )
    )

    probe_source = PROBE.read_text()
    planted_probe = tmp_path / "near_miss_probe.py"
    planted_probe.write_text(probe_source)

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "PLATFORM_DATABASE_URL", "PYTHONPATH"}
    }
    program = (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(planted_root)!r}); "
        f"runpy.run_path({str(planted_probe)!r}, run_name='__main__')"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", program],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for stage in _EXPECTED_STAGES:
        assert stage in result.stdout


# `bind_database_runtime` has exactly THREE named transitions (see its own
# docstring for why they are named separately rather than filed under one
# "idempotent rebinding" label): IDEMPOTENCE, MONOTONIC PROMOTION, and
# REFUSAL — and REFUSAL has two distinct causes. Four tests, four probe
# entry points, one transition (or refusal cause) each — never one test
# covering more than one, which is exactly how promotion could get quietly
# filed under idempotence and the seal loosen without anyone deciding to
# loosen it.


def test_rebinding_the_identical_runtime_and_policy_is_idempotent(
    tmp_path: Path,
) -> None:
    """IDEMPOTENCE: the IDENTICAL runtime, at the IDENTICAL policy, changes
    nothing. Calling `bind_database_runtime` twice with the same arguments
    is exactly as safe as calling it once (a second `create_app` call
    composing the identical spec, for instance)."""
    result = _run_probe(tmp_path, argv=("rebind-idempotent",))
    assert result.returncode == 0, result.stderr
    assert (
        "PASS idempotence: reinstalling the identical (runtime, policy) " "succeeded"
    ) in result.stdout


def test_the_same_runtime_may_be_promoted_from_optional_to_required(
    tmp_path: Path,
) -> None:
    """MONOTONIC PROMOTION: the SAME runtime, `required` going False -> True.
    The binding DOES change here — that is what makes it a promotion and not
    idempotence — and it is permitted only because strictness tightens,
    never loosens."""
    result = _run_probe(tmp_path, argv=("rebind-promotion",))
    assert result.returncode == 0, result.stderr
    assert (
        "PASS monotonic promotion: the same runtime's policy tightened from "
        "optional to required"
    ) in result.stdout


def test_binding_a_different_runtime_is_refused(tmp_path: Path) -> None:
    """REFUSAL, cause 1 of 2: a DIFFERENT `DatabaseRuntime` object, at any
    policy, is never accepted — one process supports one binding, and a
    second application supplying a different instance is a configuration
    conflict, never a silent clobber of the first."""
    result = _run_probe(tmp_path, argv=("rebind-refuses-different-runtime",))
    assert result.returncode == 0, result.stderr
    assert (
        "PASS refusal (different runtime): a second, different runtime was "
        "refused and the original binding is still authoritative"
    ) in result.stdout


def test_downgrading_an_already_required_binding_is_refused(tmp_path: Path) -> None:
    """REFUSAL, cause 2 of 2: the SAME runtime, `required` going True ->
    False (a downgrade). There is no public way to loosen strictness once
    sealed — a distinct cause from a different runtime, even though both
    land on the same REFUSAL outcome, which is why this gets its own test
    rather than being folded into the different-runtime one."""
    result = _run_probe(tmp_path, argv=("rebind-refuses-downgrade",))
    assert result.returncode == 0, result.stderr
    assert (
        "PASS refusal (downgrade): weakening an already-required binding "
        "to optional was refused"
    ) in result.stdout


def test_reference_import_after_a_strict_bind_is_refused(tmp_path: Path) -> None:
    """Paired plant, ORDERING 2 of 2: a reference-runtime IMPORT arriving
    AFTER a strict bind must refuse — before constructing any engine.

    Ordering 1 of 2 is `test_one_eager_feature_import_defeats_a_strict_binding`
    below (an eager reference import arriving BEFORE a strict bind, so the
    BIND refuses against the recorded claim). The atomic claim
    (`session_runtime._claim_lock`) exists precisely to make the outcome
    independent of which side runs first; proving only one ordering would
    leave the other genuinely untested — a claim advertised as mutual but
    only checked in one direction.
    """
    result = _run_probe(tmp_path, argv=("after-strict-bind",))
    assert result.returncode == 0, result.stderr
    assert (
        "PASS paired plant (reference-after-bind): dotmac_kernel.db refused "
        "to import after a strict binding was already sealed, before "
        "constructing any engine"
    ) in result.stdout


def test_real_assembly_strict_composition_leaves_the_reference_unloaded(
    tmp_path: Path,
) -> None:
    """Acceptance against the REAL Starter assembly, in a fresh interpreter
    — not the zero-feature surrogate every other test in this file uses.

    A bespoke `ProductAssemblySpec` with no feature manifests cannot see the
    sharpest hazard this seam exists to close: `app.assembly` composes
    `FEATURE_MODULES` via `load_manifests` at IMPORT TIME (`app/assembly.py`'s
    module-level `ProductAssemblySpec(...)` call), before `create_app` ever
    runs. `main_real_assembly_strict()` imports the real `app.assembly`,
    rebinds it to a product runtime under `require_database_runtime=True`,
    calls the real `create_app`, THEN enters the ASGI lifespan (driving the
    real startup seed hook, `app/features/settings/seed.py
    ::seed_platform_defaults`, which resolves `resolve_database_runtime()`
    rather than a deferred `dotmac_kernel.db` import) and verifies the
    seeded row landed in the PRODUCT runtime's own database — proving strict
    composition resolves through the bound runtime along more than the one
    "nothing imported" path: manifest composition AND a real runtime
    consumer both land on the product engine.

    MEASURED, not inferred from `sys.modules` membership alone:
    `main_real_assembly_strict()` installs a `sys.addaudithook` that records
    the stack of the first genuine REQUEST to import `dotmac_kernel.db` (not
    merely its eventual presence), because a bare "is the key present" check
    cannot distinguish "app.assembly imported it" from some unrelated entry
    — see that function's own docstring. A failure here would report the
    OBSERVED import chain, not a guess about which PR landed.

    PREVIOUSLY FAILED, correctly, on this branch's pre-rebase base: several
    feature services (`app/features/{tenants,parties,rbac,auth,custom_fields}
    /service.py`) imported `conflict_savepoint` from `dotmac_kernel.db` at
    module scope, so `import app.assembly` alone already requested it. The
    predecessor `refactor/conflict-savepoint-engine-free-owner` (#678,
    merged as `478540bb`) moved those onto the already-public, engine-free
    `dotmac_kernel.transactions.conflict_savepoint`; this branch has since
    rebased onto that fix (`app/features/settings/seed.py` was left on its
    pre-fix form by #678 deliberately, for this branch's own
    `resolve_database_runtime()` fix to land on — see that file). Confirmed
    by static reading: `grep -rn "dotmac_kernel\\.db" app/` now returns only
    comment/docstring mentions, zero imports.
    """
    result = _run_probe(tmp_path, app_root=ROOT, argv=("real-assembly",))
    assert result.returncode == 0, (
        f"real-assembly strict composition failed\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    for marker in (
        "PASS real assembly: import app.assembly requested no import of "
        "dotmac_kernel.db",
        "PASS real assembly, strict: composing app.assembly's real feature "
        "manifests under a strict binding requested no import of "
        "dotmac_kernel.db",
        # Isolates a genuine resolve_database_runtime()/seed defect (this
        # marker missing, with a "CATEGORY 1" assertion instead) from a
        # fixture-only problem specific to the lifespan's asyncio.to_thread
        # dispatch (this marker present, but the marker below missing) — see
        # main_real_assembly_strict()'s own comment for the full reasoning.
        "PASS real assembly, strict, direct seed call: "
        "seed_platform_defaults() called directly",
        "PASS real assembly, strict, through seeding: the settings "
        "feature's startup seed wrote into the PRODUCT runtime, and "
        "dotmac_kernel.db was never requested through the whole lifespan",
    ):
        assert marker in result.stdout, (
            f"expected marker missing: {marker!r}\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def test_one_eager_feature_import_defeats_a_strict_binding(tmp_path: Path) -> None:
    """Plant: reintroduce ONE eager `dotmac_kernel.db` import into a copy of
    the real `app.assembly` and observe strict composition REFUSE — with
    admitted control, exactly one mutation, and the exact refusal checked.

    1. ADMIT CONTROL FIRST: the UNMUTATED copy must succeed (the same
       `main_real_assembly_strict()` acceptance probe used elsewhere in this
       file) — otherwise a "refusal" on the mutated copy could just as
       easily be a broken fixture as a caught regression.
    2. Apply EXACTLY ONE mutation: one `from dotmac_kernel.db import
       conflict_savepoint` line, in one file.
    3. Assert the EXACT observed cause on the SAME copy, now mutated — not
       "refused for *some* reason". `main_real_assembly_strict()`'s own
       audit hook (see that function) catches the planted import the moment
       `import app.assembly` requests it, which is EARLIER than
       `bind_database_runtime`'s atomic claim ever gets a chance to run —
       so the exact, expected failure is THAT hook's own `AssertionError`,
       carrying the OBSERVED import chain (stack), naming this exact
       planted file — not a hypothesis about which class raised.
    """
    import shutil

    planted_app_root = tmp_path / "planted-app-root"
    shutil.copytree(ROOT / "app", planted_app_root / "app")

    probe_source = PROBE.read_text()
    planted_probe = tmp_path / "planted_real_assembly_probe.py"
    planted_probe.write_text(probe_source)

    # 1. ADMIT CONTROL: the unmutated copy succeeds.
    control = _run_probe(
        tmp_path,
        probe=planted_probe,
        app_root=planted_app_root,
        argv=("real-assembly",),
    )
    assert control.returncode == 0, (
        "control run (unmutated copy) failed before any mutation was "
        "applied -- this is a fixture defect, not evidence about the "
        "plant\n"
        f"stdout:\n{control.stdout}\nstderr:\n{control.stderr}"
    )
    assert (
        "PASS real assembly: import app.assembly requested no import of "
        "dotmac_kernel.db"
    ) in control.stdout

    # 2. EXACTLY ONE mutation.
    service_path = planted_app_root / "app" / "features" / "parties" / "service.py"
    source = service_path.read_text()
    anchor = "from dotmac_kernel.crud import CRUDManager\n"
    assert anchor in source, "parties/service.py's import block has changed shape"
    assert "from dotmac_kernel.db import conflict_savepoint" not in source, (
        "the eager import is already present before the plant -- the "
        "eager-import predecessor has not actually landed on this tree, "
        "so this would not be testing the plant at all"
    )
    service_path.write_text(
        source.replace(
            anchor,
            anchor + "from dotmac_kernel.db import conflict_savepoint  # noqa: F401\n",
            1,
        )
    )

    # 3. Assert the EXACT observed cause: main_real_assembly_strict()'s own
    # import-tracking AssertionError, naming the planted file's import in
    # the recorded stack.
    mutated = _run_probe(
        tmp_path,
        probe=planted_probe,
        app_root=planted_app_root,
        argv=("real-assembly",),
    )
    assert mutated.returncode != 0, (
        "strict composition of the real assembly succeeded even with an "
        "eager dotmac_kernel.db import planted back into a feature service "
        "— the guard did not bite a real transitive reach\n"
        f"stdout:\n{mutated.stdout}\nstderr:\n{mutated.stderr}"
    )
    assert "AssertionError" in mutated.stderr, (
        f"refused, but not with the expected exception class\n"
        f"stdout:\n{mutated.stdout}\nstderr:\n{mutated.stderr}"
    )
    assert (
        "dotmac_kernel.db was requested during `import app.assembly` "
        "— OBSERVED import chain"
    ) in mutated.stderr, (
        f"refused, but not for the expected reason\nstdout:\n{mutated.stdout}\n"
        f"stderr:\n{mutated.stderr}"
    )
    assert "features/parties/service.py" in mutated.stderr, (
        "the observed import chain did not name the planted file -- "
        f"cannot confirm this is the plant's own mutation\nstderr:\n{mutated.stderr}"
    )


def test_the_retired_get_database_runtime_name_is_fully_gone() -> None:
    """Positive check that the rename to `resolve_database_runtime` is
    complete — a check that nothing still references the retired name,
    rather than relying on nothing happening to reference it.

    Checked at both ends of the seam: `session_runtime`, where the name was
    defined before the process-wide sealed binding replaced the free
    mutators, and `deps`, a consumer that binds names at import time (the
    exact shape where a stale monkeypatch target would silently miss a real
    reference — see the sibling unit tests, which patch `deps
    .resolve_database_runtime`, the consumer's own bound name, not
    `session_runtime`'s)."""
    import dotmac_kernel.deps as deps
    import dotmac_kernel.session_runtime as session_runtime

    assert not hasattr(session_runtime, "get_database_runtime")
    assert not hasattr(deps, "get_database_runtime")
    assert hasattr(session_runtime, "resolve_database_runtime")
    assert hasattr(deps, "resolve_database_runtime")
