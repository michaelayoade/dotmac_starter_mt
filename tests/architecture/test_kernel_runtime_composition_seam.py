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


def test_second_binding_conflict_is_refused(tmp_path: Path) -> None:
    """One process supports one runtime binding — a second, DIFFERENT one
    cannot clobber the first, and downgrading strictness has no public path.

    `main_second_binding_conflict()` proves BOTH directions of idempotence in
    one process: reinstalling the IDENTICAL binding succeeds; a DIFFERENT
    runtime refuses and leaves the original authoritative; weakening an
    already-required binding also refuses.
    """
    result = _run_probe(tmp_path, argv=("second-binding",))
    assert result.returncode == 0, result.stderr
    for marker in (
        "PASS idempotence: reinstalling the identical binding succeeded",
        "PASS second-binding conflict: a different runtime was refused and "
        "the original binding is still authoritative",
        "PASS strictness is monotonic: weakening an already-required "
        "binding refused",
    ):
        assert marker in result.stdout, (
            f"expected marker missing: {marker!r}\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


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
    calls the real `create_app`, and asserts `dotmac_kernel.db` never entered
    `sys.modules` at any point.

    KNOWN TO CURRENTLY FAIL on `main` / this branch's base: several feature
    services (`app/features/{tenants,parties,rbac,auth,custom_fields}
    /service.py`) still import `conflict_savepoint` from `dotmac_kernel.db`
    at module scope, so `import app.assembly` alone already reaches
    `dotmac_kernel.db`. The predecessor `refactor/conflict-savepoint-engine-
    free-owner` (#678) moves those onto the already-public, engine-free
    `dotmac_kernel.transactions.conflict_savepoint`; this test is written to
    the POST-rebase world and will pass once this branch rebases onto that
    fix, not before. It is deliberately NOT weakened, skipped or marked
    xfail to make that pass silently — a failure here, right now, is exactly
    the ordering dependency, stated rather than hidden.
    """
    result = _run_probe(tmp_path, app_root=ROOT, argv=("real-assembly",))
    assert result.returncode == 0, (
        "real-assembly strict composition failed -- expected until this "
        "branch rebases onto the conflict_savepoint eager-import predecessor "
        f"(#678)\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert (
        "PASS real assembly, strict: composing app.assembly's real feature "
        "manifests under a strict binding left dotmac_kernel.db unloaded"
    ) in result.stdout


def test_one_eager_feature_import_defeats_a_strict_binding(tmp_path: Path) -> None:
    """Plant: reintroduce ONE eager `dotmac_kernel.db` import into a copy of
    the real `app.assembly` and observe strict composition REFUSE.

    This is the acceptance criterion for the whole lane, independent of
    whether the predecessor migration has landed here yet: it manufactures
    the exact broken state (one feature service importing
    `dotmac_kernel.db` at module scope) on top of whatever this tree
    currently has, and proves `bind_database_runtime`'s atomic claim catches
    it — `import app.assembly` reaches `dotmac_kernel.db` before
    `create_app` ever calls `bind_database_runtime(..., required=True)`, so
    the bind loses the race and refuses with `RuntimeBindingError`.
    """
    import shutil

    planted_app_root = tmp_path / "planted-app-root"
    shutil.copytree(ROOT / "app", planted_app_root / "app")
    service_path = planted_app_root / "app" / "features" / "parties" / "service.py"
    source = service_path.read_text()
    anchor = "from dotmac_kernel.crud import CRUDManager\n"
    assert anchor in source, "parties/service.py's import block has changed shape"
    if "from dotmac_kernel.db import conflict_savepoint" not in source:
        source = source.replace(
            anchor,
            anchor + "from dotmac_kernel.db import conflict_savepoint  # noqa: F401\n",
            1,
        )
    service_path.write_text(source)

    probe_source = PROBE.read_text()
    planted_probe = tmp_path / "planted_real_assembly_probe.py"
    planted_probe.write_text(probe_source)

    result = _run_probe(
        tmp_path,
        probe=planted_probe,
        app_root=planted_app_root,
        argv=("real-assembly",),
    )
    assert result.returncode != 0, (
        "strict composition of the real assembly succeeded even with an "
        "eager dotmac_kernel.db import planted back into a feature service "
        "— the guard did not bite a real transitive reach"
    )
    assert (
        "RuntimeBindingError" in result.stderr
        or "already reached dotmac_kernel.db" in result.stderr
    ), (
        f"refused, but not for the expected reason\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
