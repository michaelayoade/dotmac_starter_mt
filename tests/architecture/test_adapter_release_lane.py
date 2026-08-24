"""The stateless-adapter release lane is built, closed, and currently SHUT.

Two claims, and they are separate:

* **The lane exists and is safe.** `release-adapter.yml` reproduces every safety
  step of the module lane — current-main SHA asserted before the build AND
  re-asserted after the approval wait, a wheel-content policy, the published
  bytes downloaded rather than rebuilt, install-and-verify from the index, and a
  tag written only at the end.
* **The door is shut.** `.github/release-adapters.json` lists nothing, so
  `scripts/release_adapter.py resolve` refuses every dispatch. `dotmac-auth-oidc`
  is merged and deliberately unlisted; absence is the safety mechanism until the
  `dotmac_workspace` pilot is green, exactly as ADR-0026 § 8 required of
  `dotmac-approvals` and ADR-0017 requires generally.

## The empty-set trap, and how this file avoids it

A parametrized check over an empty allowlist passes while enforcing nothing —
the failure mode this repository names repeatedly (`test_the_safe_filter_guard
_still_bites`). So the per-entry rules here are proved against a SYNTHETIC
allowlist built in a tmp directory and pointed at real packages, which shows the
gate accepting a well-formed adapter and refusing a stateful module, an entry
carrying stateful-only facts, and a package whose dossier disagrees. Without
those, "the gate is closed" would be indistinguishable from "the gate refuses
everything for the wrong reason".

## Why this is a separate lane at all

ADR-0006's 2026-08-14 amendment: a `stateless-protocol-adapter` has no
`ModuleManifest`, no lineage, no ledger allocation and no persistence import, so
the module lane's `db_schema`, `manifest_attr` and `kernel_floor` describe facts
it does not have. Making those OPTIONAL would not have been scoped to the
package that needs it — a stateful module whose `db_schema` was dropped in a bad
merge would stop being refused and start being treated as an adapter, its
namespace assertion silently skipped rather than failed. The last test in this
file is the ratchet against that: the module lane's three facts must stay
mandatory.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_ALLOWLIST = PROJECT_ROOT / ".github" / "release-adapters.json"
MODULE_ALLOWLIST = PROJECT_ROOT / ".github" / "release-modules.json"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-adapter.yml"
MODULE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-module.yml"
ADAPTER_SCRIPT = PROJECT_ROOT / "scripts" / "release_adapter.py"
MODULE_SCRIPT = PROJECT_ROOT / "scripts" / "release_module.py"

CLASSIFICATION = "stateless-protocol-adapter"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _adapters() -> dict[str, dict]:
    return json.loads(ADAPTER_ALLOWLIST.read_text(encoding="utf-8"))["adapters"]


def _modules() -> dict[str, dict]:
    return json.loads(MODULE_ALLOWLIST.read_text(encoding="utf-8"))["modules"]


def _executable(path: Path) -> str:
    """Workflow YAML with comment lines removed.

    A substring scan over the raw file matches the workflow's own prose — this
    one's header discusses `NamespaceRegistry` and `compose_with` at length
    precisely to explain why it has neither. Strip comments and the assertion is
    about what the workflow DOES, not what it says about itself.
    """
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _resolve(distribution: str, version: str = "") -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(ADAPTER_SCRIPT), "resolve", distribution]
    if version:
        argv += ["--version", version]
    # The "untrusted input" S603 warns about is this file's own literals — the
    # impostor names below exist precisely to prove the gate refuses them. The
    # interpreter and script path are both resolved constants.
    return subprocess.run(argv, capture_output=True, text=True)  # noqa: S603


def _synthetic(tmp_path: Path, adapters: dict[str, dict]) -> Any:
    """A `release_adapter` module whose allowlist is one this test wrote.

    Pointed at REAL packages, so the classification and pyproject checks run
    against real dossiers rather than fixtures — the point is to exercise the
    gate's reasoning, not to stub it out.
    """
    allowlist = tmp_path / "release-adapters.json"
    allowlist.write_text(json.dumps({"adapters": adapters}), encoding="utf-8")
    module = _load(ADAPTER_SCRIPT, f"release_adapter_{tmp_path.name}")
    module.ALLOWLIST = allowlist
    return module


def _auth_oidc_entry() -> dict:
    """What `dotmac-auth-oidc`'s entry will look like when it is finally listed."""
    return {
        "package_dir": "packages/dotmac-auth-oidc",
        "import_name": "dotmac_auth_oidc",
        "tag_prefix": "dotmac-auth-oidc-v",
        "wheel_contents": {
            "required": [
                "dotmac_auth_oidc/py.typed",
                "dotmac_auth_oidc/client.py",
            ],
            "forbidden_prefixes": ["app/", "tests/", "alembic/", "scripts/"],
            "allowed_requires": ["pyjwt", "httpx", "python"],
        },
    }


# ── The door is shut ─────────────────────────────────────────────────────────


def test_the_lane_is_open_for_exactly_the_adapter_the_pilot_earned() -> None:
    """This test used to assert the allowlist was EMPTY, and its docstring said
    to delete that assertion deliberately when an adapter was legitimately
    added. This is that deliberate replacement, 2026-08-15.

    What earned it: `dotmac_workspace` PR #2 ran the ceremony against a shared,
    atomic PostgreSQL store, bound the callback to the browser, and drove a
    change back into the package (starter PR #194) so a request-bound store
    could be supplied at all. A green CI run was never the bar; a real consumer
    was.

    The assertion inverts rather than disappears. An empty allowlist is now
    also a failure: it would mean the entry was reverted without the decision
    being re-argued, and this lane's whole doctrine is that listing and
    de-listing are both reviewed diffs.
    """
    assert set(_adapters()) == {"dotmac-auth-oidc"}, (
        "the adapter allowlist changed. Adding one requires a pilot consumer "
        "that actually ran; removing one requires saying why the published "
        "artifact should stop being reproducible from this repository."
    )


@pytest.mark.parametrize(
    "impostor",
    [
        # `dotmac-auth-oidc` is NOT here any more — it is the one listed adapter,
        # and `test_the_listed_adapter_resolves` covers it. Everything below is
        # still refused, which is what makes that resolution mean something.
        # Real, but released by their OWN workflows with their own rules.
        "dotmac-kernel",
        "dotmac-ui",
        # A real STATEFUL module. It must not be publishable through the lane
        # that skips namespace, lineage and dual-plane checks.
        "dotmac-ticketing",
        "dotmac-application-directory",
        # Never releasable by anything today.
        "dotmac-template-studio",
        "packages/dotmac-auth-oidc",  # a path, not a distribution
        "../../etc/passwd",  # a traversal attempt
        "",  # empty
    ],
)
def test_the_gate_refuses_every_dispatch_it_does_not_list(impostor: str) -> None:
    """SENSITIVITY PROOF for "the lane is closed".

    Every downstream step — build, inspect, smoke, publish — takes its target
    from `resolve`. A non-zero exit here is what stops the workflow before
    `poetry build` ever runs. Listing one adapter must not have opened the lane
    to anything else, and in particular must not have made a STATEFUL module
    publishable through the path that skips the namespace, lineage and
    dual-plane checks.
    """
    result = _resolve(impostor)
    assert result.returncode != 0, f"{impostor!r} was resolved"
    assert "not an allowlisted stateless protocol adapter" in result.stderr, impostor


def test_a_refusal_names_what_IS_publishable() -> None:
    """The refusal has to be actionable.

    While the lane was shut it said "(none — the lane is shut)", because an
    operator reading "publishable adapters are: " with an empty list would
    reasonably conclude the file was broken. Now that something is listed, the
    same sentence must name it — otherwise an operator who mistyped has no way
    to see what the right spelling was.
    """
    result = _resolve("dotmac-auth-oidk")  # a plausible typo
    assert result.returncode != 0
    assert "dotmac-auth-oidc" in result.stderr
    assert "release-adapters.json" in result.stderr


def test_the_listed_adapter_resolves() -> None:
    """SPECIFICITY for every refusal above, against the REAL allowlist.

    `test_a_well_formed_adapter_entry_resolves` proves the gate's reasoning
    against a synthetic file. This proves the file that ships actually works —
    a lane whose one entry did not resolve would fail at dispatch, which is the
    worst moment to discover it.
    """
    result = _resolve("dotmac-auth-oidc", version="0.1.0a1")
    assert result.returncode == 0, result.stderr
    assert "tag=dotmac-auth-oidc-v0.1.0a1" in result.stdout


# ── The gate is not merely refusing everything ───────────────────────────────


def test_a_well_formed_adapter_entry_resolves(tmp_path: Path) -> None:
    """SPECIFICITY for the refusals above: they must fail because the name is
    not listed, not because `resolve` refuses whatever it is handed.

    Uses the real `dotmac-auth-oidc` package, so the classification and the
    pyproject name/version checks all run for real.
    """
    module = _synthetic(tmp_path, {"dotmac-auth-oidc": _auth_oidc_entry()})
    entry = module.resolve("dotmac-auth-oidc")
    assert entry["import_name"] == "dotmac_auth_oidc"
    assert entry["package_path"].is_dir()


def test_a_stateful_module_cannot_be_smuggled_through_the_adapter_lane(
    tmp_path: Path,
) -> None:
    """THE property that justifies a second lane rather than optional fields.

    A module listed here would be published without a single namespace, lineage
    or dual-plane check. The tie is the GOVERNED classification in the package's
    own dossier, so this holds for the next module as much as this one.
    """
    module = _synthetic(
        tmp_path,
        {
            "dotmac-ticketing": {
                "package_dir": "packages/dotmac-ticketing",
                "import_name": "dotmac_ticketing",
                "tag_prefix": "dotmac-ticketing-v",
                "wheel_contents": {
                    "required": [],
                    "forbidden_prefixes": [],
                    "allowed_requires": [],
                },
            }
        },
    )
    with pytest.raises(SystemExit) as refused:
        module.resolve("dotmac-ticketing")
    assert "classification" in str(refused.value)
    assert CLASSIFICATION in str(refused.value)


@pytest.mark.parametrize("field", ["db_schema", "manifest_attr", "kernel_floor"])
def test_an_entry_carrying_a_stateful_fact_is_refused(
    tmp_path: Path, field: str
) -> None:
    """A schema, a manifest attribute or a kernel floor means the package is a
    module. Accepting it here — even with the classification check passing —
    would leave a fact declared that nothing in this lane ever verifies."""
    entry = {**_auth_oidc_entry(), field: "whatever"}
    module = _synthetic(tmp_path, {"dotmac-auth-oidc": entry})
    with pytest.raises(SystemExit) as refused:
        module.resolve("dotmac-auth-oidc")
    assert field in str(refused.value)
    assert "release-modules.json" in str(refused.value)


def test_a_mismatched_version_is_refused_rather_than_inferred(tmp_path: Path) -> None:
    """The dispatched version must equal the package's. Inferring it would let a
    typo publish a version whose number nobody chose."""
    import argparse

    module = _synthetic(tmp_path, {"dotmac-auth-oidc": _auth_oidc_entry()})
    with pytest.raises(SystemExit) as refused:
        module.cmd_resolve(
            argparse.Namespace(distribution="dotmac-auth-oidc", version="9.9.9")
        )
    assert "!= package version" in str(refused.value)


def test_resolve_emits_no_stateful_facts(tmp_path: Path, capsys) -> None:
    """An adapter has no schema, no manifest attribute and no kernel floor.
    Emitting them as empty strings would let a later step read "unknown" where
    the truth is "absent"."""
    import argparse

    module = _synthetic(tmp_path, {"dotmac-auth-oidc": _auth_oidc_entry()})
    module.cmd_resolve(
        argparse.Namespace(distribution="dotmac-auth-oidc", version="0.1.0a1")
    )
    emitted = dict(
        line.split("=", 1) for line in capsys.readouterr().out.strip().splitlines()
    )
    assert emitted["tag"] == "dotmac-auth-oidc-v0.1.0a1"
    assert emitted["package_dir"] == "packages/dotmac-auth-oidc"
    for absent in ("db_schema", "manifest_attr", "kernel_floor"):
        assert absent not in emitted


# ── The lane's own facts stay true ───────────────────────────────────────────


def test_the_runtime_persistence_roots_match_the_pr_time_checker() -> None:
    """The same ADR-0006 property is checked twice — statically over the source
    tree at PR time, and at runtime over the installed artifact at release time.
    Two lists that drift would hold the source and the wheel to different rules,
    and the release-time one is the copy nobody reads until it is too late."""
    from tests.architecture.test_product_first_extraction import PERSISTENCE_ROOTS

    adapter = _load(ADAPTER_SCRIPT, "release_adapter_roots")
    assert set(adapter.PERSISTENCE_ROOTS) == set(PERSISTENCE_ROOTS)


def test_the_secret_shape_predicate_is_shared_not_reimplemented() -> None:
    """Two copies of a name-shape list drift, and the drift is silent in the
    worst direction: the second copy relaxes, releases go green, and the first
    becomes the only real gate again. Identity, not similarity — the adapter
    lane must be calling the module lane's function object."""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import release_module

    adapter = _load(ADAPTER_SCRIPT, "release_adapter_secret")
    assert adapter.secret_shaped is release_module.secret_shaped


def test_every_listed_adapter_declares_the_classification() -> None:
    """Vacuous today, and it must not stay vacuous silently — the synthetic
    proofs above are what make the rule real before the first entry lands."""
    for distribution, entry in _adapters().items():
        dossier = tomllib.loads(
            (PROJECT_ROOT / entry["package_dir"] / "EXTRACTION.toml").read_text(
                encoding="utf-8"
            )
        )
        assert dossier["classification"] == CLASSIFICATION, distribution
        assert entry["tag_prefix"] == f"{distribution}-v"


def test_the_workflow_choice_list_appears_exactly_when_the_allowlist_does() -> None:
    """The ratchet that keeps the UI layer honest.

    A `workflow_dispatch` choice must offer at least one option, so while the
    allowlist is empty the input is free text and the ONLY gate is `resolve` —
    which is the enforced layer anyway. The moment an adapter is listed, the
    workflow must offer exactly those names, so the first entry cannot land
    without the convenience list following it.
    """
    source = WORKFLOW.read_text(encoding="utf-8")
    adapters = _adapters()
    if not adapters:
        assert "type: string" in source
        assert "type: choice" not in _executable(WORKFLOW)
        return
    options_block = source.split("options:", 1)[1].split("version:", 1)[0]
    offered = {
        line.strip().removeprefix("- ")
        for line in options_block.splitlines()
        if line.strip().startswith("- ")
    }
    assert offered == set(adapters)


# ── The security sequence is preserved, step for step ────────────────────────


def test_the_release_sequence_matches_the_module_workflow() -> None:
    """The adapter path may not be a weaker version of the module path.

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
    assert source.count("twine upload") == 1
    assert "release_adapter.py inspect" in source
    assert "release_adapter.py verify-wheel" in source

    verify = source.split("verify:", 1)[1]
    assert "git tag" in verify
    assert "verify-registry" in verify
    assert verify.index("verify-registry") < verify.index("git tag")


def test_publish_re_asserts_the_allowlist_after_approval() -> None:
    """Defence in depth: a tampered `build` output must not smuggle a target
    past the gate while the run sits pending approval."""
    publish = _executable(WORKFLOW).split("publish:", 1)[1].split("verify:", 1)[0]
    assert "release_adapter.py resolve" in publish


def test_publish_asserts_freshness_before_the_artifact_and_the_token() -> None:
    """Ordering is the point: fail before the artifact is downloaded and before
    the publish credential is used, not after."""
    import yaml

    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["publish"][
        "steps"
    ]
    guard_at = next(
        i
        for i, step in enumerate(steps)
        if "assert_current_main.sh" in str(step.get("run", ""))
    )
    for i, step in enumerate(steps):
        blob = (
            str(step.get("uses", ""))
            + str(step.get("run", ""))
            + str(step.get("env", ""))
        )
        if "download-artifact" in blob or "FORGEJO_PUBLISH_TOKEN" in blob:
            assert guard_at < i, (
                "the freshness check must run BEFORE the artifact download and "
                "before the publish token is referenced"
            )


def test_the_adapter_lane_does_not_pretend_to_compose() -> None:
    """An adapter owns no schema, no migration prefix and no branch label, so
    there is nothing for it to contest. A composition check over it would look
    like the module lane's level-2 proof while proving nothing at all — and a
    check that appears to assert something it does not is worse than its
    absence."""
    source = _executable(WORKFLOW)
    assert "compose_with" not in source
    assert "compose_kernel" not in source
    assert "NamespaceRegistry" not in source
    # And no kernel wheel is built for a smoke that registers nothing.
    assert "kernel-dist" not in source


def test_the_tag_message_states_what_was_verified() -> None:
    """A tag reading "verified" without saying verified HOW invites the reader
    to assume the stronger claim. There is no composition claim to make here."""
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "public surface proved alone" in source
    assert "composes with" not in source


# ── The ratchet against the alternative design ───────────────────────────────


@pytest.mark.parametrize("field", ["db_schema", "manifest_attr", "kernel_floor"])
def test_the_module_lane_kept_its_shape_facts_mandatory(field: str) -> None:
    """The other option was to make these OPTIONAL in `release-module.yml` when
    the classification is a stateless adapter. It was rejected because
    optionality is not scoped to the package that needs it: a stateful module
    whose `db_schema` was dropped in a bad merge would stop being refused and
    start being treated as an adapter, its namespace assertion silently skipped
    rather than failed.

    This is the ratchet. ``db_schema`` may now be explicit JSON null for a
    stateless *module*, but the KEY remains mandatory and the module resolver
    compares null to the real manifest. A stateful row that merely drops the
    field still fails here and in ``resolve``.
    """
    modules = _modules()
    assert modules, "the module allowlist is empty — the check would be vacuous"
    for distribution, entry in modules.items():
        assert field in entry, f"{distribution} lost its {field}"


def test_a_module_entry_missing_a_stateful_fact_does_not_resolve(
    tmp_path: Path,
) -> None:
    """Sensitivity proof for the ratchet above: the module lane must actually
    fail on a stripped entry, not merely be expected to."""
    import argparse

    entry = {
        key: value
        for key, value in _modules()["dotmac-application-directory"].items()
        if key != "db_schema"
    }
    allowlist = tmp_path / "release-modules.json"
    allowlist.write_text(
        json.dumps({"modules": {"dotmac-application-directory": entry}}),
        encoding="utf-8",
    )
    module = _load(MODULE_SCRIPT, "release_module_stripped")
    module.ALLOWLIST = allowlist
    with pytest.raises((KeyError, SystemExit)):
        module.cmd_resolve(
            argparse.Namespace(
                distribution="dotmac-application-directory", version="0.1.0a3"
            )
        )


def test_the_module_workflow_still_registers_through_the_namespace_registry() -> None:
    """The module lane's proof is registration. If it ever stopped registering,
    the reason to keep two lanes would have evaporated — and so would the
    protection this file assumes exists next door."""
    source = _executable(MODULE_WORKFLOW)
    assert "release_module.py verify-wheel" in source
    assert "release_module.py verify-registry" in source
