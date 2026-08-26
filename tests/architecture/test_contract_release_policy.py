"""Contract catalogues have a closed release lane and exact artifact proof.

The lane is CLOSED AND EMPTY, so most of what it will ever refuse is proved
here on synthetic packages: an empty allowlist makes a sweep over real entries
vacuously green, and a vacuous gate is the one that lets the first entry land
unchecked. Every test below therefore either plants a defect and requires the
refusal, or is a ratchet that fires the moment the object stops being empty.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = PROJECT_ROOT / ".github" / "release-contracts.json"
SCRIPT = PROJECT_ROOT / "scripts" / "release_contract.py"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-contract.yml"
CLASSIFICATION = "stateless-contract-catalogue"


def _gate():
    spec = importlib.util.spec_from_file_location("release_contract", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _contracts() -> dict:
    return _policy()["contracts"]


def _executable(path: Path) -> str:
    """Workflow YAML with comment lines removed.

    A substring scan over the raw file matches the workflow's own prose — this
    one's header explains at length why a catalogue may not reach a network.
    Strip comments and the assertion is about what the workflow DOES, not about
    what it says about itself.
    """
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _valid_entry() -> dict:
    return {
        "package_dir": "packages/dotmac-managed-example-contracts",
        "import_name": "dotmac_managed_example_contracts",
        "owner_code": "dotmac-managed-example",
        "kernel_floor": "0.1.0a68",
        "composition_dependencies": {},
        "tag_prefix": "dotmac-managed-example-contracts-v",
        "wheel_contents": {
            "required": [
                "dotmac_managed_example_contracts/__init__.py",
                "dotmac_managed_example_contracts/py.typed",
            ],
            "forbidden_prefixes": ["app/", "alembic/", "scripts/", "tests/"],
            "allowed_requires": ["dotmac-kernel", "python"],
        },
    }


@pytest.fixture
def lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    gate = _gate()
    package = tmp_path / "packages" / "dotmac-managed-example-contracts"
    source = package / "src" / "dotmac_managed_example_contracts"
    source.mkdir(parents=True)
    (package / "pyproject.toml").write_text(
        "[tool.poetry]\n"
        'name = "dotmac-managed-example-contracts"\n'
        'version = "0.1.0a1"\n'
        "[tool.poetry.dependencies]\n"
        'python = ">=3.12,<4.0"\n'
        'dotmac-kernel = ">=0.1.0a68"\n',
        encoding="utf-8",
    )
    (package / "EXTRACTION.toml").write_text(
        f'classification = "{CLASSIFICATION}"\n', encoding="utf-8"
    )
    (source / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        gate, "git_tags", lambda *_args, **_kwargs: ["dotmac-kernel-v0.1.0a68"]
    )

    def configure(entry: dict | None = None):
        policy = _policy()
        policy["contracts"] = (
            {} if entry is None else {"dotmac-managed-example-contracts": entry}
        )
        allowlist = tmp_path / "release-contracts.json"
        allowlist.write_text(json.dumps(policy), encoding="utf-8")
        monkeypatch.setattr(gate, "ALLOWLIST", allowlist)
        return gate, package

    return configure


# ── The empty lane IS the lock ───────────────────────────────────────────────


def test_the_checked_in_lane_is_empty_and_refuses_every_name() -> None:
    """Absence is the publication lock, not the workflow's absence of a run.

    The seven candidate catalogues exist only on an archive ref and floor on a
    kernel capability grammar nobody published, so the reviewed object is `{}`
    and the real gate must refuse against the REAL file, not only a synthetic
    one.
    """
    assert _contracts() == {}

    gate = _gate()
    with pytest.raises(SystemExit, match="not an allowlisted contract catalogue"):
        gate.resolve("dotmac-domains-contracts")
    with pytest.raises(SystemExit, match="Absence is the publication lock"):
        gate.resolve("anything-at-all-contracts")


def test_unlisted_contract_catalogue_is_refused(lane) -> None:
    """Sensitivity: the checked-in empty set must actually be consulted."""
    gate, _ = lane(None)
    with pytest.raises(SystemExit, match="not an allowlisted contract catalogue"):
        gate.resolve("dotmac-managed-example-contracts")


# ── The classification, and why it is a fourth one ───────────────────────────


def test_the_catalogue_carries_its_own_extraction_classification() -> None:
    """The INVERSE of the archived ruling, and deliberately so.

    An earlier draft of this lane asserted "a release profile, not a new
    extraction classification", reasoning by analogy with the connector ruling.
    ADR-0006's 2026-08-26 amendment supersedes that: the four properties
    `stateless-protocol-adapter` governs are silent on NETWORK REACH, an adapter
    exists to reach a provider (`dotmac-auth-oidc` ships `transport.py` and
    declares `httpx`), and a catalogue reaches nothing. One word covering both
    would have to permit the import the adapter needs — so the word buys an
    enforceable property rather than a synonym.
    """
    conformance = _policy()["conformance"]
    assert conformance["classification"] == CLASSIFICATION
    assert conformance["classification"] != "stateless-protocol-adapter"
    assert _gate().CLASSIFICATION == CLASSIFICATION


def test_the_script_refuses_an_allowlist_that_names_another_classification(
    lane, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sensitivity: the constant must be load-bearing, not decorative.

    A relaxed allowlist that re-declared the adapter classification would make
    every dossier check pass against the wrong word.
    """
    gate, _ = lane(_valid_entry())
    policy = _policy()
    policy["conformance"]["classification"] = "stateless-protocol-adapter"
    policy["contracts"] = {"dotmac-managed-example-contracts": _valid_entry()}
    relaxed = tmp_path / "relaxed.json"
    relaxed.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(gate, "ALLOWLIST", relaxed)

    with pytest.raises(SystemExit, match=r"conformance\.classification"):
        gate.resolve("dotmac-managed-example-contracts")


def test_the_network_import_refusal_is_the_discriminator() -> None:
    """The property that separates this lane from the adapter lane is checked,
    and a provider client is exactly what an adapter is allowed to declare."""
    forbidden = _gate().FORBIDDEN_IMPORT_ROOTS
    assert {"httpx", "requests", "socket", "urllib", "subprocess"} <= forbidden
    assert {"sqlalchemy", "psycopg", "asyncpg", "alembic"} <= forbidden


# ── Entry shape refusals ─────────────────────────────────────────────────────


def test_a_complete_listed_contract_catalogue_resolves(lane) -> None:
    gate, _ = lane(_valid_entry())

    resolved = gate.resolve(
        "dotmac-managed-example-contracts",
        tags={"dotmac-kernel-v0.1.0a68"},
    )

    assert resolved["owner_code"] == "dotmac-managed-example"


def test_every_contract_entry_field_is_required(lane) -> None:
    for field in _gate().REQUIRED_FIELDS:
        entry = _valid_entry()
        del entry[field]
        gate, _ = lane(entry)
        with pytest.raises(SystemExit) as refusal:
            gate.resolve(
                "dotmac-managed-example-contracts",
                tags={"dotmac-kernel-v0.1.0a68"},
            )
        assert field in str(refusal.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("db_schema", "mod_wrong"),
        ("manifest_attr", "module"),
        ("integration_floor", "0.1.0a5"),
        ("connector_key", "wrong.v1"),
        ("plugin_attr", "PLUGIN"),
    ],
)
def test_module_and_connector_facts_are_refused(lane, field: str, value: str) -> None:
    """A package with those facts belongs in the lane whose gates will actually
    ask it questions about them."""
    gate, _ = lane({**_valid_entry(), field: value})
    with pytest.raises(SystemExit, match="wrong release profile"):
        gate.resolve(
            "dotmac-managed-example-contracts",
            tags={"dotmac-kernel-v0.1.0a68"},
        )


def test_wrong_classification_and_package_shape_are_refused(lane) -> None:
    gate, package = lane(_valid_entry())
    (package / "EXTRACTION.toml").write_text(
        'classification = "optional-module"\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="classification"):
        gate.resolve(
            "dotmac-managed-example-contracts",
            tags={"dotmac-kernel-v0.1.0a68"},
        )

    entry = {**_valid_entry(), "package_dir": "packages/dotmac-managed-example"}
    gate, _ = lane(entry)
    with pytest.raises(SystemExit, match="-contracts"):
        gate.resolve(
            "dotmac-managed-example-contracts",
            tags={"dotmac-kernel-v0.1.0a68"},
        )


def test_owner_code_must_be_a_stable_code(lane) -> None:
    gate, _ = lane({**_valid_entry(), "owner_code": "Not A Stable Code"})
    with pytest.raises(SystemExit, match="stable code"):
        gate.resolve(
            "dotmac-managed-example-contracts",
            tags={"dotmac-kernel-v0.1.0a68"},
        )


def test_kernel_floor_must_be_published_and_match_dependency(lane) -> None:
    gate, package = lane(_valid_entry())
    with pytest.raises(SystemExit, match="has no release tag"):
        gate.resolve("dotmac-managed-example-contracts", tags=set())

    (package / "pyproject.toml").write_text(
        (package / "pyproject.toml")
        .read_text(encoding="utf-8")
        .replace(">=0.1.0a68", ">=0.1.0a67"),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="kernel_floor"):
        gate.resolve(
            "dotmac-managed-example-contracts",
            tags={"dotmac-kernel-v0.1.0a68"},
        )


def test_composition_dependencies_are_exact_package_pins(lane) -> None:
    entry = {
        **_valid_entry(),
        "composition_dependencies": {"dotmac-component-contracts": "0.1.0a1"},
    }
    gate, package = lane(entry)
    pyproject = package / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + 'dotmac-component-contracts = ">=0.1.0a1"\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="must be exactly"):
        gate.resolve(
            "dotmac-managed-example-contracts",
            tags={"dotmac-kernel-v0.1.0a68"},
        )

    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace('">=0.1.0a1"', '"0.1.0a1"'),
        encoding="utf-8",
    )
    gate.resolve(
        "dotmac-managed-example-contracts",
        tags={"dotmac-kernel-v0.1.0a68"},
    )


def test_the_registry_leg_may_only_pin_the_gated_floor(lane) -> None:
    """The floor pin exists so leg 1 executes the DECLARED grammar. A pin that
    is not the gated floor would certify a kernel nobody reviewed."""
    import argparse

    gate, _ = lane(_valid_entry())
    with pytest.raises(SystemExit, match="not the gated floor"):
        gate.cmd_verify_registry(
            argparse.Namespace(
                index="https://example.invalid/simple",
                pin="dotmac-managed-example-contracts==0.1.0a1",
                kernel_floor="0.1.0a99",
            )
        )


# ── Static conformance ───────────────────────────────────────────────────────


def test_source_conformance_rejects_io_persistence_and_connector_discovery(
    lane,
) -> None:
    gate, package = lane(_valid_entry())
    source = package / "src" / "dotmac_managed_example_contracts" / "__init__.py"
    for planted in (
        "import requests\n",
        "import httpx\n",
        "import subprocess\n",
        "import sqlalchemy\n",
        "import importlib\nimportlib.import_module('x')\n",
    ):
        source.write_text(planted, encoding="utf-8")
        with pytest.raises(SystemExit, match="must be data-only"):
            gate.conformance("dotmac-managed-example-contracts")

    source.write_text("", encoding="utf-8")
    migration = package / "src" / "dotmac_managed_example_contracts" / "migrations"
    migration.mkdir()
    (migration / "0001_initial.py").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="must be data-only"):
        gate.conformance("dotmac-managed-example-contracts")
    (migration / "0001_initial.py").unlink()
    migration.rmdir()

    pyproject = package / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + '[tool.poetry.plugins."dotmac_integration.connectors"]\n'
        + '"wrong.v1" = "dotmac_managed_example_contracts:PLUGIN"\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="connector entry point"):
        gate.conformance("dotmac-managed-example-contracts")


def test_the_secret_shape_predicate_is_shared_not_reimplemented() -> None:
    """Two copies of a name-shape list drift, and the drift is silent in the
    worst direction: the second copy relaxes, releases go green, and the first
    becomes the only real gate again. Identity, not similarity."""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import release_module

    assert _gate().secret_shaped is release_module.secret_shaped


# ── The ratchet against the empty lane going stale ───────────────────────────


def test_the_workflow_choice_list_appears_exactly_when_the_allowlist_does() -> None:
    """Two-directional, so the empty half cannot be the only half tested.

    A `workflow_dispatch` choice must offer at least one option, so while the
    allowlist is empty the input is free text and the ONLY gate is `resolve` —
    which is the enforced layer anyway. The moment a catalogue is listed, the
    workflow must offer exactly those names, so the first entry cannot land
    without the convenience list following it.
    """
    source = WORKFLOW.read_text(encoding="utf-8")
    contracts = _contracts()
    if not contracts:
        assert "type: string" in source
        assert "type: choice" not in _executable(WORKFLOW)
        return
    options_block = source.split("options:", 1)[1].split("version:", 1)[0]
    offered = {
        line.strip().removeprefix("- ")
        for line in options_block.splitlines()
        if line.strip().startswith("- ")
    }
    assert offered == set(contracts)


def test_every_listed_catalogue_declares_the_classification_and_tag_prefix() -> None:
    """Vacuous today, and it must not stay vacuous silently — the synthetic
    proofs above are what make the rule real before the first entry lands."""
    import tomllib

    for distribution, entry in _contracts().items():
        dossier = tomllib.loads(
            (PROJECT_ROOT / entry["package_dir"] / "EXTRACTION.toml").read_text(
                encoding="utf-8"
            )
        )
        assert dossier["classification"] == CLASSIFICATION, distribution
        assert entry["tag_prefix"] == f"{distribution}-v", distribution


def test_the_required_exports_are_the_coverage_surface() -> None:
    assert set(_policy()["conformance"]["required_exports"]) == {
        "PRODUCT_MANIFEST",
        "CAPABILITY_CONTRACTS",
        "CAPABILITY_SCHEMAS",
        "CAPABILITY_COMPOSITIONS",
        "COMPOSITION_DEPENDENCY_CONTRACTS",
        "COMPOSITION_DEPENDENCY_SCHEMAS",
    }


# ── The security sequence is preserved, step for step ────────────────────────


def test_the_release_sequence_matches_the_other_release_workflows() -> None:
    """The contract path may not be a weaker version of the module path.

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
    assert source.index("poetry build") < source.index("twine upload")
    assert source.index("download-artifact") < source.index("twine upload")
    assert source.index("release_contract.py conformance") < source.index(
        "poetry build"
    ), "a defect findable from source must not wait for a wheel"
    assert source.index("twine upload") < source.index("verify-registry")
    assert source.rindex("verify-registry") < source.index("git tag")


def test_the_kernel_floor_is_built_from_its_tag_not_from_the_current_tree() -> None:
    """The defect this restores over.

    A step named "the exact kernel floor" that runs `poetry build` in
    `packages/dotmac-kernel` builds whatever main happens to be today. It proves
    the catalogue against the CURRENT capability grammar and says nothing about
    the one its metadata promises — while reading, in the run log, as though it
    had proved exactly that.
    """
    source = _executable(WORKFLOW)

    assert 'git archive "dotmac-kernel-v${FLOOR}"' in source
    floor_at = source.index("git archive")
    current_at = source.index('poetry build --output "${GITHUB_WORKSPACE}/kernel-dist"')
    assert floor_at < current_at
    assert "--kernel-dist /tmp/kernel-floor/dist" in source


def test_conformance_runs_at_the_floor_and_at_the_current_release() -> None:
    """A catalogue is installable across `>=floor`, so proving one end proves
    only that end — and the floor end is the one no run had ever executed."""
    source = _executable(WORKFLOW)

    assert source.count("verify-wheel") == 2, "pre-publish smoke must run both legs"
    assert source.count("verify-registry") == 2, "registry proof must run both legs"
    assert source.count("--kernel-floor") == 1, (
        "exactly one registry leg pins the declared floor; the other resolves "
        "the way an ordinary installer would"
    )
    assert source.rindex("verify-wheel") < source.index("twine upload")
    assert source.rindex("verify-registry") < source.index("git tag")


def test_the_floor_is_carried_from_resolve_not_respelled() -> None:
    """`resolve` is the one place the floor is read from the allowlist and
    proved tagged, so the pin must come from there."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    build = workflow["jobs"]["build"]

    assert build["outputs"]["kernel_floor"] == (
        "${{ steps.resolve.outputs.kernel_floor }}"
    )
    verify = workflow["jobs"]["verify"]
    floor_steps = [
        step
        for step in verify["steps"]
        if "${{ needs.build.outputs.kernel_floor }}" in str(step.get("env", {}))
    ]
    assert len(floor_steps) == 1


def test_the_dispatch_inputs_are_exact() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    inputs = workflow[True]["workflow_dispatch"]["inputs"]

    assert set(inputs) == {"contract", "version"}
    assert all(field["required"] for field in inputs.values())
    assert inputs["version"]["type"] == "string"
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["verify"]["permissions"] == {"contents": "write"}


def test_the_lane_is_never_laxer_than_the_classification_it_publishes() -> None:
    """The classification checker and the lane checker must not drift apart.

    ADR-0006's fifth property names the network roots a contract catalogue may
    not import, and `contract_catalogue_violations` enforces it on the DECLARED
    classification — i.e. on every catalogue, listed or not. This lane sees
    only the subset that reaches publication, so it must forbid at least as
    much: a lane laxer than the classification would make the classification
    the weaker of the two checks, and a package could clear the stricter gate
    on its way to the registry.

    Containment, not equality — the lane additionally refuses persistence and
    remote-execution roots, which is the direction that is allowed.
    """
    spec = importlib.util.spec_from_file_location(
        "test_product_first_extraction",
        PROJECT_ROOT / "tests" / "architecture" / "test_product_first_extraction.py",
    )
    assert spec is not None and spec.loader is not None
    extraction = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extraction)

    lane = _gate().FORBIDDEN_IMPORT_ROOTS
    classification = extraction.NETWORK_ROOTS
    missing = sorted(classification - lane)
    assert not missing, (
        f"{missing} are refused by the classification but permitted by the "
        "release lane — the lane must be a superset"
    )
