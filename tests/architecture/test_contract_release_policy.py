"""Contract catalogues have a closed release lane and exact artifact proof."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = PROJECT_ROOT / ".github" / "release-contracts.json"
SCRIPT = PROJECT_ROOT / "scripts" / "release_contract.py"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-contract.yml"


def _gate():
    spec = importlib.util.spec_from_file_location("release_contract", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


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
        'classification = "stateless-protocol-adapter"\n', encoding="utf-8"
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


def test_unlisted_contract_catalogue_is_refused(lane) -> None:
    """Sensitivity: the checked-in empty set must actually be consulted."""
    gate, _ = lane(None)
    with pytest.raises(SystemExit, match="not an allowlisted contract catalogue"):
        gate.resolve("dotmac-managed-example-contracts")


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
    gate, _ = lane({**_valid_entry(), field: value})
    with pytest.raises(SystemExit, match="wrong release profile"):
        gate.resolve(
            "dotmac-managed-example-contracts",
            tags={"dotmac-kernel-v0.1.0a68"},
        )


def test_contract_profile_is_not_a_new_extraction_classification() -> None:
    assert _policy()["conformance"]["classification"] == ("stateless-protocol-adapter")


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


def test_source_conformance_rejects_io_persistence_and_connector_discovery(
    lane,
) -> None:
    gate, package = lane(_valid_entry())
    source = package / "src" / "dotmac_managed_example_contracts" / "__init__.py"
    for planted in (
        "import requests\n",
        "import subprocess\n",
        "import sqlalchemy\n",
    ):
        source.write_text(planted, encoding="utf-8")
        with pytest.raises(SystemExit, match="must be data-only"):
            gate.conformance("dotmac-managed-example-contracts")

    source.write_text("", encoding="utf-8")
    pyproject = package / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + '[tool.poetry.plugins."dotmac_integration.connectors"]\n'
        + '"wrong.v1" = "dotmac_managed_example_contracts:PLUGIN"\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="connector entry point"):
        gate.conformance("dotmac-managed-example-contracts")


def test_lane_lists_only_complete_accepted_owner_and_suite_catalogues() -> None:
    policy = _policy()
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    assert set(policy["contracts"]) == {
        "dotmac-domains-contracts",
        "dotmac-managed-collaboration-contracts",
        "dotmac-managed-email-contracts",
        "dotmac-managed-host-contracts",
        "dotmac-managed-identity-contracts",
        "dotmac-managed-infrastructure-contracts",
        "dotmac-managed-suite-contracts",
    }
    assert set(policy["conformance"]["required_exports"]) == {
        "PRODUCT_MANIFEST",
        "CAPABILITY_CONTRACTS",
        "CAPABILITY_SCHEMAS",
        "CAPABILITY_COMPOSITIONS",
        "COMPOSITION_DEPENDENCY_CONTRACTS",
        "COMPOSITION_DEPENDENCY_SCHEMAS",
    }
    assert workflow[True]["workflow_dispatch"]["inputs"]["contract"]["type"] == (
        "string"
    )
    workflow_source = WORKFLOW.read_text(encoding="utf-8")
    assert "registry-release" in workflow_source
    assert "--kernel-dist" in workflow_source
    assert "composition_dependency_dirs" in workflow_source
