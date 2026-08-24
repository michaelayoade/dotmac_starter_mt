"""The module release path is gated by a closed, checked-in allowlist.

What this protects: **a release run cannot build or publish a package nobody
reviewed.** The dispatch input is a string; the allowlist is a diff. Everything
downstream — what gets built, what the wheel must contain, which kernel it may
claim, what tag is written — comes from the allowlist entry, so the gate is also
the source of every fact the release asserts.

Three layers, and all three are tested here because each fails differently:

1. `scripts/release_module.py resolve` refuses an unlisted distribution. This is
   the enforced layer, and the sensitivity proof below is what makes the claim
   more than a comment.
2. The workflow's `choice` input constrains the same names at the UI layer.
   Convenience only — a `workflow_dispatch` API call is not bound by it.
3. This file asserts (1) and (2) agree, so the convenience list cannot drift
   away from the enforced one and quietly offer something the gate refuses.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = PROJECT_ROOT / ".github" / "release-modules.json"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-module.yml"
RECOVERY_WORKFLOW = (
    PROJECT_ROOT / ".github" / "workflows" / "recover-module-release.yml"
)
KERNEL_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-kernel.yml"
SCRIPT = PROJECT_ROOT / "scripts" / "release_module.py"


def _release_module_script():
    spec = importlib.util.spec_from_file_location("release_module", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executable(path: Path) -> str:
    """Workflow YAML with comment lines removed.

    A substring scan over the raw file matches the workflow's own prose — the
    composition workflow's header says "no `contents: write`", which is exactly
    the string a naive check looks for. Strip comments and the assertion is
    about what the workflow DOES, not what it says about itself.
    """
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _allowlist() -> dict[str, dict]:
    return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))["modules"]


def _resolve(distribution: str, version: str = "") -> subprocess.CompletedProcess:
    argv = [sys.executable, str(SCRIPT), "resolve", distribution]
    if version:
        argv += ["--version", version]
    # The "untrusted input" S603 warns about is this file's own literals — the
    # impostor names below, which exist precisely to prove the gate refuses
    # them. The interpreter and script path are both resolved constants.
    return subprocess.run(argv, capture_output=True, text=True)  # noqa: S603


# ── The gate ─────────────────────────────────────────────────────────────────


def test_an_unallowlisted_package_cannot_be_resolved() -> None:
    """SENSITIVITY PROOF. Without this, "the allowlist gates the release" is an
    assertion about a file nobody proved is consulted.

    Every downstream step — build, inspect, smoke, publish — takes its target
    from `resolve`. A non-zero exit here is what stops the workflow before
    `poetry build` ever runs.
    """
    for impostor in (
        "dotmac-kernel",  # real, but released by its OWN workflow
        "dotmac-ui",  # same
        # Real packages in `packages/`, deliberately not listed and with no
        # release workflow of their own. `dotmac-ticketing` used to stand here
        # and was promoted into the allowlist by the ERP-first ticket adoption
        # programme, so the proof needs a still-unlisted stand-in or it degrades
        # into "the gate refuses names that do not exist".
        "dotmac-template-studio",
        "packages/dotmac-release-catalog",  # a path, not a distribution
        "../../etc/passwd",  # a traversal attempt
        "",  # empty
    ):
        result = _resolve(impostor)
        assert result.returncode != 0, f"{impostor!r} was resolved"
        assert "not an allowlisted module" in result.stderr, impostor


def test_an_allowlisted_module_resolves_and_emits_its_facts() -> None:
    """Specificity for the test above: it must refuse because the name is not
    listed, not because `resolve` refuses everything."""
    for distribution in _allowlist():
        result = _resolve(distribution)
        assert result.returncode == 0, result.stderr
        emitted = dict(
            line.split("=", 1) for line in result.stdout.strip().splitlines()
        )
        assert emitted["package_dir"].startswith("packages/")
        assert emitted["tag"].startswith(emitted["tag_prefix"])


def test_files_is_release_allowlisted_with_its_schema_allocation() -> None:
    result = _resolve("dotmac-files", version="0.1.0a3")
    assert result.returncode == 0, result.stderr
    emitted = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    # `mod_files` is allocated in a54; a56 added its prerequisite contract,
    # and a3 now consumes ADR-0028's explicit selection surface from a61.
    # The highest capability actually imported sets the floor.
    assert emitted["kernel_floor"] == "0.1.0a61"
    assert emitted["db_schema"] == "mod_files"
    assert emitted["tag"] == "dotmac-files-v0.1.0a3"


def test_imports_is_release_allowlisted_for_the_erp_first_adopter() -> None:
    result = _resolve("dotmac-imports", version="0.1.0a2")
    assert result.returncode == 0, result.stderr
    emitted = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert emitted["kernel_floor"] == "0.1.0a56"
    assert emitted["db_schema"] == "mod_imports"
    assert emitted["tag"] == "dotmac-imports-v0.1.0a2"


def test_inbox_operations_is_release_allowlisted_for_the_sub_cutover() -> None:
    """Publication is the first executable gate in Sub's named adoption.

    A dossier naming Sub is not enough: without this closed-list row the
    release workflow refuses the distribution and Sub cannot pin immutable
    artifacts for its backfill and shadow phases.
    """
    result = _resolve("dotmac-inbox-operations", version="0.1.0a5")
    assert result.returncode == 0, result.stderr
    emitted = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert emitted["kernel_floor"] == "0.1.0a91"
    assert emitted["db_schema"] == "mod_inbox_ops"
    assert emitted["tag"] == "dotmac-inbox-operations-v0.1.0a5"


def test_service_catalog_is_release_allowlisted_for_the_sub_shadow() -> None:
    """Sub needs one immutable package coordinate before it can prove parity.

    Publication opens that adoption lane without moving technical-catalogue
    authority: Sub remains the writer until its backfill, full-cohort shadow
    comparison and local-writer retirement gates pass.
    """
    result = _resolve("dotmac-service-catalog", version="0.1.0a1")
    assert result.returncode == 0, result.stderr
    emitted = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert emitted["kernel_floor"] == "0.1.0a91"
    assert emitted["db_schema"] == "mod_svc_cat"
    assert emitted["tag"] == "dotmac-service-catalog-v0.1.0a1"


def test_service_catalog_is_release_allowlisted_for_the_sub_shadow() -> None:
    """Sub needs one immutable package coordinate before it can prove parity.

    Publication opens that adoption lane without moving technical-catalogue
    authority: Sub remains the writer until its backfill, full-cohort shadow
    comparison and local-writer retirement gates pass.
    """
    result = _resolve("dotmac-service-catalog", version="0.1.0a1")
    assert result.returncode == 0, result.stderr
    emitted = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert emitted["kernel_floor"] == "0.1.0a91"
    assert emitted["db_schema"] == "mod_svc_cat"
    assert emitted["tag"] == "dotmac-service-catalog-v0.1.0a1"


def test_document_rendering_is_allowlisted_as_an_explicitly_stateless_module() -> None:
    """A stateless module is not a protocol adapter and owns no fake schema.

    The null schema is an asserted shape: the resolver has already compared it
    to the manifest's absent short code and migration prefix.  Omitting the
    output entirely keeps downstream steps from mistaking an empty string for
    an unknown schema.
    """
    result = _resolve("dotmac-document-rendering", version="0.1.0a1")
    assert result.returncode == 0, result.stderr
    emitted = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert emitted["kernel_floor"] == "0.1.0a88"
    assert emitted["release_shape"] == "stateless"
    assert "db_schema" not in emitted
    assert emitted["tag"] == "dotmac-document-rendering-v0.1.0a1"


def test_release_smoke_executes_against_a_real_stateless_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-time canary for the embedded script the release runs in a clean venv.

    The first real Document Rendering dispatch reached this script and failed
    because it read ``manifest.stateful`` while ModuleManifest exposes
    ``is_stateful``. Importing ``release_module.py`` cannot catch an attribute
    lookup inside a string, so execute that exact string against a real kernel
    manifest and a synthetic installed-package path.
    """
    from dotmac_kernel.modules import ModuleManifest

    release = _release_module_script()
    probe = ModuleType("release_smoke_probe")
    probe.__file__ = str(
        PROJECT_ROOT / "site-packages-fixture" / "release_smoke_probe" / "__init__.py"
    )
    probe.module = ModuleManifest(
        code="release_smoke_probe",
        version="1.0.0",
        core=False,
    )
    monkeypatch.setitem(sys.modules, probe.__name__, probe)

    script = release._REGISTER.format(names=[("release_smoke_probe", "module", None)])
    exec(script, {})  # noqa: S102  # nosec B102 - the checked-in release script


def test_ticketing_is_release_allowlisted_with_its_schema_allocation() -> None:
    """The gate the ERP-first ticket adoption programme had to pass first.

    A module absent from the allowlist is not merely unreleased — it is
    unreleasable, so `first_cutover` names a cutover no product can begin. The
    kernel floor is `0.1.0a61`, and deliberately NOT the `0.1.0a39` that added
    `TICKETING_MIGRATION_OWNER` to `MIGRATION_OWNER_LEDGER`. This module's floor
    has been raised by a kernel CAPABILITY rather than by its own namespace row,
    three times now: `platform_tables` (ADR-0023, dual-plane), the logical
    prerequisite contract (ADR-0006 D1 amendment, a56), and explicit plane
    selection (ADR-0028, a61) — its manifest declares `supported_plane_sets` and
    its lineage reads the assembly's selection, neither of which an earlier
    kernel can even import. The floor is always the highest capability the
    module actually consumes.
    """
    result = _resolve("dotmac-ticketing", version="0.1.0a4")
    assert result.returncode == 0, result.stderr
    emitted = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert emitted["kernel_floor"] == "0.1.0a61"
    assert emitted["db_schema"] == "mod_tkt"
    assert emitted["tag"] == "dotmac-ticketing-v0.1.0a4"


#: The only distributions whose `allowed_requires` may carry `alembic`, each
#: because it ships per-plane link helpers that emit DDL into a CONSUMING
#: product's own migration lineage — which makes `from alembic import op` a real
#: runtime import rather than a build-time one.
#:
#: `dotmac-billing` joined `dotmac-ticketing` on 2026-08-23. It is a second
#: instance of the same mechanism, not the copy-paste spread the original
#: single-module form warned about: `dotmac_billing.linking` says in its own
#: docstring that "the helpers emit a link table into the adopter's own
#: migration lineage", and carries the identical tenant-composite-FK-with-forced
#: RLS versus platform-revoke split.
#:
#: Membership is by MECHANISM, never by convenience. A module that merely wants
#: the dependency does not qualify; one that emits DDL into someone else's
#: lineage does, and must be added here deliberately.
LINK_HELPER_DISTRIBUTIONS: frozenset[str] = frozenset(
    {"dotmac-ticketing", "dotmac-billing"}
)


def test_only_link_helper_modules_may_require_alembic_at_runtime() -> None:
    """Specificity for the allowlist entries' widened `allowed_requires`."""
    for distribution, entry in _allowlist().items():
        permits_alembic = "alembic" in entry["wheel_contents"]["allowed_requires"]
        expected = distribution in LINK_HELPER_DISTRIBUTIONS
        assert permits_alembic == expected, distribution


def test_every_alembic_exception_actually_imports_alembic() -> None:
    """The exemption states an ENFORCEABLE premise (ADR-0018).

    Listing a distribution above claims it imports Alembic at runtime. If that
    stopped being true the entry would silently become a permission nobody
    needs, so the claim is checked against the source rather than trusted — the
    same reason the allowlist verifies a schema against the manifest instead of
    believing the JSON.
    """
    for distribution in sorted(LINK_HELPER_DISTRIBUTIONS):
        package = distribution.replace("-", "_")
        linking = (
            PROJECT_ROOT / "packages" / distribution / "src" / package / "linking.py"
        )
        assert linking.is_file(), f"{distribution} has no linking module"
        assert "from alembic import op" in linking.read_text(encoding="utf-8"), (
            f"{distribution} is exempted to require alembic but its linking "
            "module does not import it — remove the exemption or the dependency"
        )


def test_a_mismatched_version_is_refused_rather_than_inferred() -> None:
    """The dispatched version must equal the package's. Inferring it would let a
    typo publish a version whose number nobody chose."""
    distribution = next(iter(_allowlist()))
    result = _resolve(distribution, version="9.9.9")
    assert result.returncode != 0
    assert "!= package version" in result.stderr


# ── The allowlist tells the truth about the repository ───────────────────────


@pytest.mark.parametrize("distribution", sorted(_allowlist()))
def test_each_entry_matches_the_package_it_names(distribution: str) -> None:
    entry = _allowlist()[distribution]
    package = PROJECT_ROOT / entry["package_dir"]
    manifest = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]

    assert manifest["name"] == distribution
    assert manifest["dependencies"]["dotmac-kernel"] == f">={entry['kernel_floor']}"
    # The import name and manifest attribute must actually exist, or every
    # verification step would fail only after a wheel had been built.
    assert (package / "src" / entry["import_name"] / "manifest.py").is_file()
    assert entry["tag_prefix"] == f"{distribution}-v"


@pytest.mark.parametrize("distribution", sorted(_allowlist()))
def test_runtime_dependency_policy_matches_each_package_before_release(
    distribution: str,
) -> None:
    """PR-time twin of the wheel metadata check.

    The release workflow reads dependencies from built METADATA. Waiting until
    then to discover that a reviewed runtime dependency is absent from the
    allowlist turns publication into the first integration test. Both surfaces
    come from checked-in files, so drift is a pull-request failure instead.
    """
    entry = _allowlist()[distribution]
    package = PROJECT_ROOT / entry["package_dir"]
    manifest = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]

    declared = set(manifest["dependencies"])
    permitted = set(entry["wheel_contents"]["allowed_requires"])
    assert declared == permitted, (
        f"{distribution} declares {sorted(declared)}, but its release policy "
        f"permits {sorted(permitted)}"
    )


def test_brand_profiles_local_smoke_dependency_is_derived_from_policy() -> None:
    """The release that exposed the gap, plus its sensitivity proof.

    ``dotmac-brand-profiles`` is the first module with a permitted first-party
    dependency beyond the separately handled kernel. The pre-publish smoke must
    therefore build ``dotmac-ui`` from this exact checkout. Removing that name
    from the reviewed wheel policy must also remove the local artifact; otherwise
    discovery has become an unreviewed repository-wide dependency collector.
    """
    module = _release_module_script()
    entry = module.resolve("dotmac-brand-profiles")

    dependencies = module.local_first_party_dependencies(entry)
    assert [path.relative_to(PROJECT_ROOT).as_posix() for path in dependencies] == [
        "packages/dotmac-ui"
    ]

    planted = {
        **entry,
        "wheel_contents": {
            **entry["wheel_contents"],
            "allowed_requires": [
                name
                for name in entry["wheel_contents"]["allowed_requires"]
                if name != "dotmac-ui"
            ],
        },
    }
    assert module.local_first_party_dependencies(planted) == []


def test_verify_wheel_adds_first_party_artifacts_to_find_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The built dependency directory must reach pip, not stop at the workflow."""
    module = _release_module_script()
    commands: list[list[str]] = []

    monkeypatch.setattr(
        module,
        "_venv",
        lambda _path: (tmp_path / "python", tmp_path / "pip"),
    )
    monkeypatch.setattr(module, "_verify_installed", lambda *_args: None)

    def fake_run(command: list[str], *, check: bool, **_kwargs: object) -> None:
        assert check is True
        commands.append(command)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.cmd_verify_wheel(
        argparse.Namespace(
            distribution="dotmac-brand-profiles",
            dist="module-dist",
            kernel_dist="kernel-dist",
            dependency_dist=["first-party-dist"],
        )
    )

    assert len(commands) == 1
    install = commands[0]
    dependency_link = install.index("first-party-dist")
    assert install[dependency_link - 1] == "--find-links"


def test_local_dependency_builder_builds_the_discovered_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery must drive a real Poetry build into the shared link directory."""
    module = _release_module_script()
    commands: list[tuple[list[str], Path]] = []

    def fake_run(command: list[str], *, cwd: Path, check: bool) -> None:
        assert check is True
        commands.append((command, cwd))

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    output = tmp_path / "dependency-dist"
    module.cmd_build_local_dependencies(
        argparse.Namespace(
            distribution="dotmac-brand-profiles",
            output=str(output),
        )
    )

    assert output.is_dir()
    assert commands == [
        (
            ["poetry", "build", "--output", str(output.resolve())],
            PROJECT_ROOT / "packages" / "dotmac-ui",
        )
    ]


def test_workflow_builds_first_party_artifacts_before_the_wheel_smoke() -> None:
    """Static wiring proof for the protected release path."""
    build = _executable(WORKFLOW).split("build:", 1)[1].split("publish:", 1)[0]
    assert "build-local-dependencies" in build
    assert "--dependency-dist" in build
    assert build.index("build-local-dependencies") < build.index("Release wheel smoke")


@pytest.mark.parametrize("distribution", sorted(_allowlist()))
def test_required_wheel_contents_exist_in_the_source_tree(
    distribution: str,
) -> None:
    """A required path that does not exist in source can never appear in a
    wheel, so the policy would fail every release rather than catch anything."""
    entry = _allowlist()[distribution]
    src = PROJECT_ROOT / entry["package_dir"] / "src"
    for required in entry["wheel_contents"]["required"]:
        assert (src / required).is_file(), required


@pytest.mark.parametrize("distribution", sorted(_allowlist()))
def test_the_declared_schema_matches_the_module_manifest(distribution: str) -> None:
    """`db_schema` is asserted at registry-verification time. If the allowlist
    and the manifest disagree, that assertion fails AFTER publication — too
    late to matter."""
    entry = _allowlist()[distribution]
    manifest_path = (
        PROJECT_ROOT
        / entry["package_dir"]
        / "src"
        / entry["import_name"]
        / "manifest.py"
    )
    tree = ast.parse(manifest_path.read_text(encoding="utf-8"))
    declaration = next(
        statement.value
        for statement in tree.body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == entry["manifest_attr"]
            for target in statement.targets
        )
        and isinstance(statement.value, ast.Call)
    )
    keywords = {
        keyword.arg: keyword.value
        for keyword in declaration.keywords
        if keyword.arg is not None
    }

    if entry["db_schema"] is None:
        for field in ("short_code", "migration_prefix"):
            value = keywords.get(field)
            assert value is None or (
                isinstance(value, ast.Constant) and value.value is None
            ), f"{distribution}: stateless release declares {field}"
    else:
        short_code = entry["db_schema"].removeprefix("mod_")
        value = keywords["short_code"]
        assert isinstance(value, ast.Constant)
        assert value.value == short_code


def test_a_stateless_manifest_cannot_be_given_a_schema_in_the_allowlist(
    tmp_path: Path,
) -> None:
    """Sensitivity proof for the null half of the release-shape contract."""
    module = _release_module_script()
    entry = {**_allowlist()["dotmac-document-rendering"], "db_schema": "mod_fake"}
    allowlist = tmp_path / "release-modules.json"
    allowlist.write_text(
        json.dumps({"modules": {"dotmac-document-rendering": entry}}),
        encoding="utf-8",
    )
    module.ALLOWLIST = allowlist
    with pytest.raises(SystemExit) as refused:
        module.resolve("dotmac-document-rendering")
    assert "manifest schema None" in str(refused.value)
    assert "mod_fake" in str(refused.value)


def test_the_kernel_is_not_releasable_as_a_module() -> None:
    """It has its own workflow with its own inspection rules — a kernel wheel
    MUST ship compiled CSS and a module wheel must NOT. One workflow covering
    both would weaken whichever check the other cannot satisfy."""
    assert "dotmac-kernel" not in _allowlist()
    assert "dotmac-ui" not in _allowlist()
    assert KERNEL_WORKFLOW.is_file()


# ── The UI layer agrees with the enforced layer ──────────────────────────────


def test_the_workflow_choice_options_match_the_allowlist() -> None:
    """Drift here would offer a maintainer a module the gate then refuses — or,
    worse, quietly stop offering one that is still publishable."""
    source = WORKFLOW.read_text(encoding="utf-8")
    options_block = source.split("options:", 1)[1].split("version:", 1)[0]
    offered = {
        line.strip().removeprefix("- ")
        for line in options_block.splitlines()
        if line.strip().startswith("- ")
    }
    assert offered == set(_allowlist())


def test_the_recovery_workflow_choice_options_match_the_allowlist() -> None:
    source = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    options_block = source.split("options:", 1)[1].split("version:", 1)[0]
    offered = {
        line.strip().removeprefix("- ")
        for line in options_block.splitlines()
        if line.strip().startswith("- ")
    }
    assert offered == set(_allowlist())


# ── The security sequence is preserved, step for step ────────────────────────


def test_the_release_sequence_matches_the_kernel_workflow() -> None:
    """The module path may not be a weaker version of the kernel path.

    Each of these exists because of a specific failure: publishing from a stale
    branch, publishing bytes other than the ones inspected, publishing after an
    approval whose SHA has since moved, and tagging a release nobody verified
    was installable from the index.
    """
    source = _executable(WORKFLOW)

    assert (
        source.count("assert_current_main.sh") == 2
    ), "main must be asserted at build AND re-asserted after the approval wait"
    assert "environment: registry-release" in source
    assert "download-artifact" in source, "publish must use the built bytes"
    assert "poetry build" in source
    assert source.index("poetry build") < source.index(
        "twine upload"
    ), "the build must precede the upload in the same file order"
    # Publication happens once, in the publish job.
    assert source.count("twine upload") == 1
    # The tag is written only in the verify job, after registry verification.
    verify = source.split("verify:", 1)[1]
    assert "git tag" in verify
    assert "verify-registry" in verify
    assert verify.index("verify-registry") < verify.index("git tag")


def test_publish_re_asserts_the_allowlist_after_approval() -> None:
    """Defence in depth: a tampered `build` output must not smuggle a target
    past the gate while the run sits pending approval."""
    publish = _executable(WORKFLOW).split("publish:", 1)[1].split("verify:", 1)[0]
    assert "release_module.py resolve" in publish


def test_recovery_download_can_resolve_an_sdist_build_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Comparing an sdist still makes pip prepare its build metadata.

    The package itself must come from the private index, but its build backend
    comes from the public dependency index. Omitting the latter made recovery
    fail on ``poetry-core`` before it compared a single byte.
    """
    module = _release_module_script()
    built = tmp_path / "built"
    built.mkdir()
    wheel = built / "dotmac_release_catalog-0.1.0a1-py3-none-any.whl"
    sdist = built / "dotmac_release_catalog-0.1.0a1.tar.gz"
    wheel.write_bytes(b"wheel bytes")
    sdist.write_bytes(b"sdist bytes")

    private_index = "https://private.example/simple"
    public_index = "https://public.example/simple"
    monkeypatch.setenv("PUBLIC_INDEX_URL", public_index)

    commands: list[list[str]] = []

    def fake_download(command: list[str], *, check: bool) -> None:
        assert check is True
        assert command[command.index("--index-url") + 1] == private_index
        assert command[command.index("--extra-index-url") + 1] == public_index
        destination = Path(command[command.index("--dest") + 1])
        source = sdist if "--no-binary" in command else wheel
        (destination / source.name).write_bytes(source.read_bytes())
        commands.append(command)

    monkeypatch.setattr(module.subprocess, "run", fake_download)
    module.cmd_compare_published(
        argparse.Namespace(
            distribution="dotmac-release-catalog",
            version="0.1.0a1",
            index=private_index,
            dist=str(built),
        )
    )

    assert len(commands) == 2


def test_one_module_per_run() -> None:
    """Two releases in one dispatch would read as transactional when it is not:
    the second upload can fail with the first already public."""
    assert "type: choice" in _executable(WORKFLOW)
    # The rationale belongs in the file, and this asserts it is stated.
    assert "ONE MODULE PER RUN" in WORKFLOW.read_text(encoding="utf-8")


def test_composition_runs_before_the_tag_and_is_optional() -> None:
    """Level 2 in the release run itself, when a release completes a set.

    Ordering matters: the module is already published by then, so a failure
    cannot unpublish it — but the tag is this repository's assertion that the
    release is good, and writing it for a version whose SET does not compose
    would make the tag a lie. Hence composition, then tag.

    Optional matters too: the FIRST module of a set has nothing published to
    compose with, and a composition of one is just level 1 again.
    """
    verify = _executable(WORKFLOW).split("verify:", 1)[1]
    assert "Verify the published composition" in verify
    assert "if: inputs.compose_with != ''" in verify
    assert verify.index("Verify the published composition") < verify.index("git tag")


def test_composition_requires_an_exact_published_kernel() -> None:
    """Without a pinned kernel the check proves the set against whatever pip
    resolves — which may not be the kernel anyone published."""
    verify = _executable(WORKFLOW).split("verify:", 1)[1]
    assert "compose_kernel" in verify
    assert "--kernel" in verify
    # The guard that refuses the half-specified case.
    assert "compose_with is set but compose_kernel is empty" in WORKFLOW.read_text(
        encoding="utf-8"
    )


def test_the_tag_message_states_what_was_verified() -> None:
    """A tag reading "verified" without saying verified HOW invites the reader
    to assume the stronger claim."""
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "installed and registered alone" in source
    assert "composes with" in source


def test_the_composition_check_cannot_publish_or_tag() -> None:
    """It runs after releases exist and only reports. A composition failure is
    repaired by a NEW release, never by mutating a published one."""
    composition = _executable(
        PROJECT_ROOT / ".github" / "workflows" / "verify-module-composition.yml"
    )
    assert "twine upload" not in composition
    assert "git tag" not in composition
    assert "contents: write" not in composition
