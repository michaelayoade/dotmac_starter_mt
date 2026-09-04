"""The deployment-foundation release lane is built, and it actually closes the
circularity Michael found in review.

Three defects motivated this file, and each gets its own section below:

1. **The release path was circular.** `dotmac-deployment-foundation` is
   `EXTRACTION.toml`'s `universal-facility` classification, and no existing
   release lane accepted it — `release-modules.json` wants a
   `db_schema`/`manifest_attr`/`kernel_floor` it does not have, and
   `release-adapters.json` proves an IMPORT surface for a package that is
   never imported by a product process. `.github/release-facilities.json` +
   `.github/workflows/release-facility.yml` is the fix: a package can now
   actually be published.
2. **`deployment-conformance.yml` installed from public PyPI with no
   credential.** The distribution does not exist there — at best a hard
   failure, at worst a name-squat installing arbitrary code under the real
   package's name. It now installs ONLY from the private Forgejo index,
   authenticated as the READ-only `ci-reader` identity, and never references
   the PUBLISH credential or a public index.
3. **Conformance validated the parse, not the descriptor.** It now calls
   `dotmac_deployment_foundation.conformance.check_all` and fails on any
   finding.

## Why this file walks PARSED YAML/JSON rather than grepping

A grep for `FORGEJO_PUBLISH_TOKEN` or `pypi.org` over the raw file text would
also match the file's own PROSE explaining why those things must be absent —
`deployment-conformance.yml`'s header discusses both at length precisely to
justify the rule it enforces. `test_the_lane_shares_conventions_with_its_
siblings` in `test_adapter_release_lane.py` already established the fix for
workflow-level checks (strip `#` comment lines before scanning); this file
goes one step further for the two negative-space rules, because a `#`-comment
strip is fragile against a token that only ever appears in RUN-SCRIPT
comments, YAML block-scalar prose, or a multi-line description string —
none of which start with a bare `#`. Walking the values PyYAML/json actually
produced is the version of that fix which cannot be fooled by comment shape:
a YAML `#` comment never survives parsing into the data structure at all, so
a predicate over the parsed tree is blind to it by construction, not by a
heuristic that has to remember to strip it.
"""

from __future__ import annotations

import ast
import copy
import json
import tomllib
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FACILITY_ALLOWLIST = PROJECT_ROOT / ".github" / "release-facilities.json"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-facility.yml"
CONFORMANCE_WORKFLOW = (
    PROJECT_ROOT / ".github" / "workflows" / "deployment-conformance.yml"
)
ADOPTER_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "deployment-adopter.yml"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _string_values(node: Any) -> list[str]:
    """Every string VALUE (and key) reachable in a parsed YAML/JSON tree.

    Deliberately structural, not textual: a YAML `#` comment is discarded by
    the parser before this function ever sees the tree, so a needle that only
    ever appears in a comment cannot show up here — which is the whole point
    of preferring this over `needle in raw_text`.
    """
    found: list[str] = []
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_string_values(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_string_values(item))
    return found


def _load_release_facility():
    """Import `scripts/release_facility.py` so its refusals can be EXERCISED.

    The AST checks above read shape; a refusal is behaviour, and a guard that
    only reads shape cannot tell a real refusal from a well-named one.
    """
    import importlib.util
    import sys

    scripts = PROJECT_ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(
        "release_facility", scripts / "release_facility.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _execution_plan_probe_keywords(source: str) -> set[str]:
    """Constructor fields exercised by the installed-wheel round-trip probe."""
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "FoundationExecutionPlanV1"
    ]
    assert len(calls) == 1
    return {keyword.arg for keyword in calls[0].keywords if keyword.arg is not None}


def _references(path: Path, needle: str) -> bool:
    """Does the PARSED document contain `needle` in any string value?"""
    tree = _load_yaml(path)
    return any(needle in value for value in _string_values(tree))


# ── 1. the facility allowlist parses and is exactly one entry ───────────────


def test_the_facility_allowlist_parses_and_lists_exactly_one_entry() -> None:
    data = _load_json(FACILITY_ALLOWLIST)
    facilities = data["facilities"]
    assert set(facilities) == {"dotmac-deployment-foundation"}, (
        "the facility allowlist changed. Adding a second universal facility "
        "or removing the one listed is a reviewed diff, not an incidental "
        "edit — see the file's own $comment for why it is populated at all."
    )


def test_the_listed_facilitys_package_directory_exists_and_matches() -> None:
    entry = _load_json(FACILITY_ALLOWLIST)["facilities"]["dotmac-deployment-foundation"]
    package_dir = PROJECT_ROOT / entry["package_dir"]
    assert package_dir.is_dir(), f"{package_dir} does not exist"

    pyproject_path = package_dir / "pyproject.toml"
    assert pyproject_path.is_file(), f"{pyproject_path} does not exist"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    assert (
        pyproject["tool"]["poetry"]["name"] == "dotmac-deployment-foundation"
    ), "the allowlisted package_dir's pyproject.toml declares a different name"

    dossier_path = package_dir / "EXTRACTION.toml"
    assert dossier_path.is_file(), f"{dossier_path} does not exist"
    dossier = tomllib.loads(dossier_path.read_text(encoding="utf-8"))
    assert dossier["classification"] == "universal-facility"


def test_the_facility_entry_carries_no_stateful_facts() -> None:
    """The same ratchet `test_adapter_release_lane.py` holds for the adapter
    lane: a schema, a manifest attribute or a kernel floor means the package
    is a module and belongs in `release-modules.json` instead."""
    entry = _load_json(FACILITY_ALLOWLIST)["facilities"]["dotmac-deployment-foundation"]
    for field in ("db_schema", "manifest_attr", "kernel_floor"):
        assert field not in entry, field


def test_the_facility_tag_prefix_matches_the_distribution_name() -> None:
    entry = _load_json(FACILITY_ALLOWLIST)["facilities"]["dotmac-deployment-foundation"]
    assert entry["tag_prefix"] == "dotmac-deployment-foundation-v"


# ── 2. release-facility.yml publishes with the PUBLISH credential ───────────


def test_release_facility_workflow_has_the_three_job_shape() -> None:
    workflow = _load_yaml(RELEASE_WORKFLOW)
    assert set(workflow["jobs"]) == {"build", "publish", "verify"}


def test_release_facility_publish_job_uses_the_publish_token_and_environment() -> None:
    workflow = _load_yaml(RELEASE_WORKFLOW)
    publish_job = workflow["jobs"]["publish"]
    assert publish_job["environment"] == "registry-release"
    assert "FORGEJO_PUBLISH_TOKEN" in "\n".join(_string_values(publish_job)), (
        "the publish job must reference secrets.FORGEJO_PUBLISH_TOKEN — that "
        "is the whole point of the protected registry-release environment"
    )


def test_release_facility_dispatch_choice_matches_the_allowlist() -> None:
    """The same ratchet `test_adapter_release_lane.py` holds for `adapter`:
    the workflow's convenience `choice` list must match the enforced
    allowlist exactly, so the UI cannot drift from the gate."""
    workflow = _load_yaml(RELEASE_WORKFLOW)
    offered = set(workflow[True]["workflow_dispatch"]["inputs"]["facility"]["options"])
    allowlisted = set(_load_json(FACILITY_ALLOWLIST)["facilities"])
    assert offered == allowlisted


def test_release_facility_reasserts_freshness_before_publish_and_before_verify() -> (
    None
):
    workflow = _load_yaml(RELEASE_WORKFLOW)
    for job_name in ("build", "publish"):
        steps = workflow["jobs"][job_name]["steps"]
        blob = "\n".join(str(step.get("run", "")) for step in steps)
        assert "assert_current_main.sh" in blob, job_name


def test_the_installed_wheel_probe_rebuilds_every_execution_plan_field() -> None:
    """A required plan field must fail CI before it fails a candidate build."""
    from dotmac_deployment_foundation.execution_plan import (
        FoundationExecutionPlanV1,
    )

    facility = _load_release_facility()
    expected = {field.name for field in fields(FoundationExecutionPlanV1)}
    observed = _execution_plan_probe_keywords(facility._EXECUTION_PLAN_PROBE)
    assert observed == expected


def test_the_execution_plan_probe_field_guard_detects_an_omission() -> None:
    """Sensitivity proof for the omission that stopped candidate run 33917635417."""
    from dotmac_deployment_foundation.execution_plan import (
        FoundationExecutionPlanV1,
    )

    facility = _load_release_facility()
    planted = facility._EXECUTION_PLAN_PROBE.replace(
        '    application_profile_digest=document["application_profile_digest"],\n',
        "",
    )
    expected = {field.name for field in fields(FoundationExecutionPlanV1)}
    assert _execution_plan_probe_keywords(planted) != expected


# ── the lane consumes a candidate and cannot build one ──────────────────────


def _job_strings(job_name: str) -> str:
    workflow = _load_yaml(RELEASE_WORKFLOW)
    return "\n".join(_string_values(workflow["jobs"][job_name]))


def _mentions_poetry(tree: Any) -> bool:
    """Structural, so the explanatory COMMENTS naming `poetry build` — which
    exist precisely to stop someone reinstating it — cannot trip this."""
    return any("poetry" in value.lower() for value in _string_values(tree))


def test_the_release_lane_cannot_build_what_it_publishes() -> None:
    """`poetry build` GONE from the publish path, not merely unused.

    This lane used to build the wheel it published. It now fetches the one
    `foundation-candidate.yml` already built, digest-checked against the
    committed receipt. Leaving the build toolchain installed "in case" would
    leave `poetry build` one edit away from being reachable again on the path
    to the publish credential — so the toolchain is absent, and this is what
    keeps it absent.
    """
    assert not _mentions_poetry(_load_yaml(RELEASE_WORKFLOW)), (
        "release-facility.yml references poetry. This lane publishes the "
        "candidate's exact bytes; a build step here means the published "
        "artifact is not the one the downstream receipts name."
    )


def test_the_poetry_detector_would_catch_a_reinstated_build() -> None:
    """The check above passes over an absence, which passes for the wrong
    reason if the predicate is broken. Plant the thing and watch it fire."""
    workflow = _load_yaml(RELEASE_WORKFLOW)
    assert not _mentions_poetry(workflow)
    planted = copy.deepcopy(workflow)
    planted["jobs"]["build"]["steps"].append(
        {"name": "Build wheel + sdist", "run": "poetry build"}
    )
    assert _mentions_poetry(planted)


def test_the_digest_gate_runs_where_the_publish_credential_does_not_exist() -> None:
    """Structural, not incidental — the point the review asked to nail down.

    A mismatched artifact must fail in a job the publish token cannot reach.
    `build` declares no `environment:`, so `FORGEJO_PUBLISH_TOKEN` is
    unreachable from it; the digest gate lives there. Moving `verify-candidate`
    into `publish` — or giving `build` the environment — would silently
    reproduce `dotmac-deployment-control` 0.1.0a3: published first, verified
    after, permanently unprovable.
    """
    workflow = _load_yaml(RELEASE_WORKFLOW)
    build_job = workflow["jobs"]["build"]
    assert "environment" not in build_job, (
        "the build job acquired an environment. The digest gate must run "
        "where the publish credential does not exist."
    )
    assert "FORGEJO_PUBLISH_TOKEN" not in _job_strings("build")
    assert "verify-candidate" in _job_strings(
        "build"
    ), "the digest gate left the uncredentialed job"


def test_the_candidate_is_resolved_from_the_receipt_not_a_hard_coded_repo() -> None:
    """The owning repository is DATA, because it stops being this one.

    After the Foundation's lanes move to their own repository the candidate is
    no longer here. A literal owner would be wrong that day; the receipt
    travels with the artifact and names its own home, so the migration edits a
    record rather than this lane. It also removes an error class now: a
    dispatch input could name a version whose receipt says something else.
    """
    build = _job_strings("build")
    assert "candidate_repository" in build, "the fetch must read the receipt"
    assert "resolve-candidate" in build
    assert "michaelayoade/dotmac_starter_mt" not in build, (
        "the candidate's owning repository is hard-coded. It comes from the "
        "receipt, which is what survives the split."
    )


def test_the_registry_readback_compares_against_the_receipt() -> None:
    """Never against what `publish` uploaded.

    Comparing the download with the upload compares an upload with itself and
    passes however wrong the upload was. The question is whether the INDEX
    ended up holding the candidate's bytes, and only the receipt answers it.
    """
    script = PROJECT_ROOT / "scripts" / "release_facility.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_verify_registry"
    )
    called = {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "require_candidate_bytes" in called, (
        "verify-registry must compare the bytes the index served against the "
        "committed receipt"
    )
    assert "candidate_receipt" in called


# ── 2b. the registry proof covers EVERY published artifact, fetched by name ──
#
# The defect this section exists for: `cmd_verify_registry` fetched with
# `pip download --no-deps --only-binary :all:` and compared the one wheel that
# came back. `publish` runs `twine upload dist/*`, so the index holds a wheel
# AND an sdist, and a resolver has no reason to retrieve the second one — the
# candidate receipt records the sdist's digest and nothing read it.
#
# `dotmac-deployment-control` 0.1.0a3 is the recorded precedent, in those exact
# terms: the sdist was on the index the whole time, nothing had ever compared
# its bytes, and the version was ruled unprovable.


def _facility_function(name: str) -> ast.FunctionDef:
    script = PROJECT_ROOT / "scripts" / "release_facility.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _resolver_fetch(function: ast.AST) -> bool:
    """Does this function ask a RESOLVER for the artifacts?

    Structural rather than textual, for the same reason `_string_values` is:
    the function's docstring argues at length about `pip download`, and a grep
    would match the argument against itself. Only the string constants that are
    actual subprocess arguments are read, and a docstring is an `ast.Expr`
    statement rather than a call argument.
    """
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        for argument in ast.walk(node):
            if isinstance(argument, ast.Constant) and argument.value in {
                "download",
                "--index-url",
                "--extra-index-url",
            }:
                return True
    return False


def test_the_registry_proof_does_not_ask_a_resolver_for_the_artifacts() -> None:
    assert not _resolver_fetch(_facility_function("cmd_verify_registry")), (
        "verify-registry resolves the pin instead of requesting each recorded "
        "filename. A resolver takes the wheel and leaves the sdist, which is "
        "correct pip behaviour and no proof at all about the sdist's bytes."
    )


def test_the_resolver_detector_would_catch_a_reinstated_pip_download() -> None:
    """The check above passes over an absence. Plant it and watch it fire."""
    planted = ast.parse(
        "def cmd_verify_registry(args):\n"
        "    subprocess.run([str(pip), 'download', '--index-url', args.index])\n"
    )
    assert _resolver_fetch(planted)


def test_the_registry_proof_requests_every_filename_the_receipt_binds() -> None:
    """Both distribution forms, enumerated from the receipt and fetched by name."""
    called = {
        node.func.id
        for node in ast.walk(_facility_function("cmd_verify_registry"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "candidate_filenames" in called, (
        "the fetch must enumerate the filenames the receipt binds, so the sdist "
        "is requested rather than left to a resolver's preference"
    )
    attributes = {
        node.func.attr
        for node in ast.walk(_facility_function("cmd_verify_registry"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "collect" in attributes, "the by-name registry read is not performed"


def test_the_receipt_binds_the_sdist_too_or_the_release_refuses() -> None:
    """A receipt that names only the wheel binds half of what `publish` uploads.

    Read as behaviour, not as a spelling: the function is executed against a
    receipt with no sdist block and must refuse.
    """
    facility = _load_release_facility()
    with pytest.raises(facility.ReleaseRefused, match="no sdist"):
        facility.candidate_artifacts(
            {"filename": "x-1-py3-none-any.whl", "sha256": "0" * 64}
        )


def test_the_candidate_producer_records_the_sdist_it_will_publish() -> None:
    """The receipt is the manifest, so the producer has to write both halves.

    `foundation_candidate.py record` wrote the wheel's filename, size and
    digest and nothing about the sdist — which is why `0.3.0a3`'s sdist block
    had to be added to its receipt by hand. A comparison can only be as
    complete as the record it compares against, so this asserts the RECORD.
    """
    source = (PROJECT_ROOT / "scripts" / "foundation_candidate.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    record = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_record"
    )
    keys = {
        node.value
        for node in ast.walk(record)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "sdist" in keys, (
        "the candidate receipt records only the wheel. `twine upload dist/*` "
        "publishes the sdist too, and an unrecorded artifact reaches the index "
        "bound to nothing."
    )


def test_every_releasable_candidate_receipt_binds_both_artifacts() -> None:
    """Two committed receipts predate the sdist binding, and they are EXEMPT
    on an enforceable premise rather than grandfathered (AGENTS.md rule 25).

    `foundation-candidate-0.3.0a1.json` and `-0.3.0a2.json` name a wheel and no
    sdist. Neither may be edited: `CandidateArtifact.v1` describes bytes that
    were built once and cannot become false, and `0.3.0a2`'s digest is the
    ANCHOR of the disposition log's hash chain — backfilling it would rewrite
    an append-only record to make a guard quieter.

    The premise that makes their exemption enforceable, and that this test
    ASSERTS rather than assumes: `cmd_resolve` refuses any version that is not
    the version this tree declares, so a receipt whose version is not the
    declared one cannot reach a release at all. Re-declare one of them and this
    fails here — which is the whole difference between an exemption and an
    unmonitored region.
    """
    facility = _load_release_facility()
    package_dir = PROJECT_ROOT / "packages" / "dotmac-deployment-foundation"
    pyproject = tomllib.loads(
        (package_dir / "pyproject.toml").read_text(encoding="utf-8")
    )
    declared = pyproject["tool"]["poetry"]["version"]

    receipts = sorted(
        (PROJECT_ROOT / "docs" / "inventories").glob("foundation-candidate-*.json")
    )
    assert receipts, "no candidate receipts found; this check would be vacuous"
    unbound: list[str] = []
    bound = 0
    for path in receipts:
        document = _load_json(path)
        if document.get("schema") != "CandidateArtifact.v1":
            continue
        try:
            names = facility.candidate_filenames(document)
        except SystemExit:
            unbound.append(str(document.get("version")))
            continue
        bound += 1
        assert len(names) == 2, f"{path.name} binds {sorted(names)}"
        assert any(name.endswith(".whl") for name in names), path.name
        assert any(name.endswith(".tar.gz") for name in names), path.name

    assert bound, "no receipt binds both artifacts; this check would be vacuous"
    assert declared not in unbound, (
        f"this tree declares {declared}, whose committed receipt binds no "
        "sdist. The exemption's premise was that such a receipt can never be "
        "released; declaring it removes the premise."
    )


def test_the_verify_job_reads_the_index_as_the_read_only_identity() -> None:
    """Whether the PUBLISHER can read its own upload is a different claim, and
    the wait step above already makes it."""
    verify = _job_strings("verify")
    assert (
        "FORGEJO_READ_TOKEN" in verify
    ), "the by-name fetch must authenticate as ci-reader, not as the publisher"
    assert "ci-reader" in verify


# ── 3 & 4. deployment-conformance.yml: no publish token, no public PyPI ─────


def test_deployment_conformance_never_references_the_publish_token() -> None:
    assert not _references(CONFORMANCE_WORKFLOW, "FORGEJO_PUBLISH_TOKEN")


def test_deployment_conformance_never_references_public_pypi() -> None:
    assert not _references(CONFORMANCE_WORKFLOW, "pypi.org")


def test_deployment_conformance_installs_from_the_private_index_as_ci_reader() -> None:
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    blob = "\n".join(_string_values(workflow))
    assert "registry.dotmac.io" in blob
    assert "ci-reader" in blob
    assert "FORGEJO_READ_TOKEN" in blob


def test_deployment_conformance_runs_check_all() -> None:
    """Problem 3: the gate must run the conformance CHECKS, not merely parse
    the descriptor. `check_all` is `conformance.py`'s aggregate — the
    function whose whole reason to return a list rather than raise is that a
    CI job can report every finding in one pass."""
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    blob = "\n".join(_string_values(workflow))
    assert "from dotmac_deployment_foundation.conformance import (" in blob
    assert "check_all," in blob
    assert "check_all(spec)" in blob


def test_deployment_conformance_declares_the_read_token_as_a_required_secret() -> None:
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    secrets = workflow[True]["workflow_call"]["secrets"]
    assert secrets["FORGEJO_READ_TOKEN"]["required"] is True


def test_deployment_conformance_has_a_require_real_digests_input_default_true() -> None:
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    inputs = workflow[True]["workflow_call"]["inputs"]
    assert inputs["require-real-digests"]["default"] is True
    assert inputs["require-real-digests"]["type"] == "boolean"


def _descriptor_conformance_script() -> str:
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    step = next(
        item
        for item in workflow["jobs"]["descriptor"]["steps"]
        if item.get("name") == "Run the descriptor conformance checks"
    )
    wrapper = step["run"]
    prefix = "python <<'PY'\n"
    assert wrapper.startswith(prefix)
    assert wrapper.endswith("\nPY\n")
    return wrapper.removeprefix(prefix).removesuffix("\nPY\n")


def _execute_descriptor_conformance_script() -> None:
    resolved_workflow = CONFORMANCE_WORKFLOW.resolve()
    assert resolved_workflow.is_relative_to(PROJECT_ROOT.resolve())
    # This sensitivity test intentionally executes the exact checked-in body;
    # the path assertion above is the enforceable premise for the exemption.
    exec(  # noqa: S102
        compile(_descriptor_conformance_script(), resolved_workflow, "exec")
    )


def test_non_strict_digest_mode_removes_only_placeholder_findings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Execute the workflow's actual script against its real placeholder.

    This is the control the original implementation lacked: setting the input
    false must change the verdict of the aggregate check, not merely skip a
    later duplicate after ``check_all`` has already failed.
    """

    monkeypatch.setenv("DESCRIPTOR", str(PROJECT_ROOT / "deploy" / "product.toml"))
    monkeypatch.setenv("REQUIRE_REAL_DIGESTS", "false")
    monkeypatch.syspath_prepend(
        str(PROJECT_ROOT / "packages" / "dotmac-deployment-foundation" / "src")
    )

    _execute_descriptor_conformance_script()

    assert "0 conformance findings" in capsys.readouterr().out


def test_strict_digest_mode_still_refuses_the_same_real_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The opposite verdict proves non-strict mode did not weaken the check."""

    monkeypatch.setenv("DESCRIPTOR", str(PROJECT_ROOT / "deploy" / "product.toml"))
    monkeypatch.setenv("REQUIRE_REAL_DIGESTS", "true")
    monkeypatch.syspath_prepend(
        str(PROJECT_ROOT / "packages" / "dotmac-deployment-foundation" / "src")
    )

    with pytest.raises(SystemExit, match="1"):
        _execute_descriptor_conformance_script()

    output = capsys.readouterr().out
    assert "image.reference is pinned to the placeholder" in output
    assert "assembly.manifest_digest is the placeholder" in output


def test_deployment_conformance_starters_own_descriptor_is_still_all_zero() -> None:
    """Sensitivity proof for `require-real-digests`, run against the REAL
    descriptor this repository ships today: `deploy/product.toml` still
    carries the placeholder, so the new step must have something real to
    fail on rather than being proven only in the abstract."""
    descriptor = (PROJECT_ROOT / "deploy" / "product.toml").read_text(encoding="utf-8")
    assert "0" * 64 in descriptor, (
        "deploy/product.toml no longer carries an all-zeros placeholder — "
        "if this is because real digests were wired in, this test (and the "
        "adopter workflow's dispatch-only header) should be updated together"
    )


def test_the_non_strict_image_audit_skip_is_unmistakable() -> None:
    """Michael's finding: a non-strict audit could 'return success for the
    impossible image'. The skip path must warn LOUDLY (not merely notice,
    which a quiet green run can bury) and must write to the job summary,
    so the run's summary page — not just the log a nobody reads — says
    SKIPPED rather than passed."""
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    image_steps = workflow["jobs"]["image"]["steps"]
    blob = "\n".join(str(step.get("run", "")) for step in image_steps)
    assert "::warning::" in blob
    assert "GITHUB_STEP_SUMMARY" in blob
    assert "SKIPPED" in blob


# ── the sensitivity proof: a planted token IS caught, a comment is NOT ──────


def test_a_planted_publish_token_is_caught_by_the_same_predicate() -> None:
    """SENSITIVITY PROOF. `test_deployment_conformance_never_references_the_
    publish_token` passing could mean the rule works, or it could mean the
    predicate is vacuous. Prove the difference: mutate a real copy of the
    parsed workflow so a string value actually carries the forbidden token,
    and show the exact same predicate now fails."""
    tree = _load_yaml(CONFORMANCE_WORKFLOW)
    mutated = copy.deepcopy(tree)
    mutated["jobs"]["descriptor"]["steps"].append(
        {
            "name": "planted violation",
            "env": {"TWINE_PASSWORD": "${{ secrets.FORGEJO_PUBLISH_TOKEN }}"},
            "run": "echo hi",
        }
    )
    assert any("FORGEJO_PUBLISH_TOKEN" in v for v in _string_values(mutated))


def test_a_comment_mentioning_the_publish_token_does_not_trip_the_predicate() -> None:
    """NEGATIVE control for the sensitivity proof above. A `#`-prefixed YAML
    comment mentioning the forbidden token — exactly the shape this
    workflow's own header comment uses to EXPLAIN the rule — must not trip
    the guard, because a guard that flags its own documentation gets
    disabled by the next person who hits it. `raw_text` finding the needle
    while the parsed-tree walk does not is the demonstration that this
    predicate is immune to the false positive a `needle in raw_text` check
    would produce.
    """
    synthetic = (
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "# This workflow must NEVER reference FORGEJO_PUBLISH_TOKEN or "
        "pypi.org.\n"
        "jobs:\n"
        "  x:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo hi\n"
    )
    assert "FORGEJO_PUBLISH_TOKEN" in synthetic  # the raw text DOES contain it
    assert "pypi.org" in synthetic

    tree = yaml.safe_load(synthetic)
    values = _string_values(tree)
    assert not any("FORGEJO_PUBLISH_TOKEN" in v for v in values), (
        "a YAML comment survived parsing into the data structure — it should "
        "have been discarded before this predicate ever ran"
    )
    assert not any("pypi.org" in v for v in values)


def test_real_conformance_workflow_is_the_negative_control() -> None:
    """The real file must pass both rules — proving the predicate above is
    not merely capable of failing, it correctly does not fire on the
    document this repository actually ships."""
    assert not _references(CONFORMANCE_WORKFLOW, "FORGEJO_PUBLISH_TOKEN")
    assert not _references(CONFORMANCE_WORKFLOW, "pypi.org")


# ── 5. deployment-adopter.yml is workflow_dispatch only ─────────────────────


def test_deployment_adopter_is_workflow_dispatch_only() -> None:
    workflow = _load_yaml(ADOPTER_WORKFLOW)
    triggers = workflow[True]
    assert set(triggers) == {"workflow_dispatch"}, (
        "deployment-adopter.yml must stay dispatch-only until "
        "dotmac-deployment-foundation is published AND deploy/product.toml's "
        "placeholder digests are replaced — see the file's own header for "
        "the exact re-enable condition. Running it on every push/PR before "
        "then teaches reviewers to ignore a check that cannot pass."
    )


def test_deployment_adopter_still_calls_the_real_reusable_workflow() -> None:
    """Specificity for the test above: dispatch-only must not have been
    achieved by disconnecting the job from the gate it exists to exercise."""
    workflow = _load_yaml(ADOPTER_WORKFLOW)
    deployment_job = workflow["jobs"]["deployment"]
    assert deployment_job["uses"] == "./.github/workflows/deployment-conformance.yml"
    assert "FORGEJO_READ_TOKEN" in deployment_job["secrets"]


def test_deployment_adopter_is_not_yet_wired_into_required_checks() -> None:
    """A guard that only checks `on:` could pass while the job is still
    reachable through some other trigger this file does not model (a
    `workflow_run`, say). Specificity: there must be exactly the one trigger
    key, holding no branch filters that would make it push/PR-shaped."""
    workflow = _load_yaml(ADOPTER_WORKFLOW)
    triggers = workflow[True]
    assert triggers == {"workflow_dispatch": {}} or triggers == {
        "workflow_dispatch": None
    }


# ── the required wheel-contents list names real modules ─────────────────────


def test_every_required_wheel_module_exists_in_the_package_source() -> None:
    """The list is checked against a BUILT WHEEL by `release_facility.py`, and
    only in the release lane. So a typo — or a module renamed without updating
    the list — surfaces as a FAILED RELEASE rather than as a failed test, which
    is the most expensive place in the sequence to learn it.

    This is the cheap source-side half: every path the list requires resolves to
    a file that exists. It cannot replace the wheel check (a file present in the
    tree can still be excluded from the built artifact, which is the property
    that list actually guards) and it is not trying to — it catches the class of
    error the wheel check catches too late.
    """
    facilities = _load_json(FACILITY_ALLOWLIST)["facilities"]
    checked = 0
    for name, entry in sorted(facilities.items()):
        package = PROJECT_ROOT / str(entry["package_dir"]) / "src"
        for required in entry["wheel_contents"]["required"]:
            checked += 1
            assert (package / required).is_file(), (
                f"{name} requires {required!r} in its wheel and "
                f"{package / required} does not exist. The release lane would "
                "refuse this, but only after building — fix the list or the "
                "module name"
            )
    assert checked, "no facility declared any required wheel contents"


def test_the_cli_entry_points_own_module_is_required() -> None:
    """Non-vacuity with a property rather than a count: whatever else the list
    holds, the module behind the console script must be in it. A list that
    omitted the entry point would be watching everything except the thing the
    release proof is about."""
    facilities = _load_json(FACILITY_ALLOWLIST)["facilities"]
    for name, entry in sorted(facilities.items()):
        required = set(entry["wheel_contents"]["required"])
        import_name = str(entry["import_name"])
        assert f"{import_name}/cli.py" in required, (
            f"{name} declares the console script {entry['entry_point']!r} and "
            "does not require its module"
        )
