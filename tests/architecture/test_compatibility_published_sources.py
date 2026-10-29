"""Published lock identities, never repaired main, own compatibility debt."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.architecture.test_compatibility_gate import _git, _repo
from tools.composition_contract import compatibility_gate as gate
from tools.composition_contract.composition_schema import DimensionValue


def _document() -> dict:
    return json.loads(gate.DEFAULT_BINDINGS_PATH.read_bytes())


def test_configuration_captures_bytes_and_freezes_every_source() -> None:
    document = _document()
    config = gate.parse_bindings(json.dumps(document).encode())
    document["published_sources"][0]["peeled_commit"] = "0" * 40
    assert config.published_sources[0].peeled_commit != "0" * 40
    document["kernel_subject"]["version"] = "999"
    assert config.kernel_subject.version == "0.1.0a107"
    with pytest.raises(TypeError):
        config.bindings["erp"] = config["sub"]


@pytest.mark.parametrize("plant", ["extra", "duplicate", "path", "tag", "commit"])
def test_configuration_refuses_untrusted_source_shapes(plant: str) -> None:
    document = _document()
    row = document["published_sources"][0]
    if plant == "extra":
        document["satisfied"] = True
    elif plant == "duplicate":
        document["published_sources"].append(dict(row))
    elif plant == "path":
        row["source_path"] = "../other"
    elif plant == "tag":
        row["tag"] = "main"
    else:
        row["peeled_commit"] = "main"
    with pytest.raises(gate.GateConfigurationError):
        gate.parse_bindings(json.dumps(document).encode())


@pytest.mark.parametrize(
    "packages",
    [
        [],
        [{"name": "dotmac-files"}],
        [
            {"name": "dotmac-files", "version": "1.0"},
            {"name": "dotmac-files", "version": "2.0"},
        ],
    ],
)
def test_missing_unresolved_or_ambiguous_lock_version_refuses(packages: list) -> None:
    with pytest.raises(gate.GateDerivationError, match="exactly one"):
        gate._locked_versions({"package": packages}, frozenset({"dotmac-files"}))


def _published_fixture(tmp_path: Path):
    prefix = "packages/dotmac-files"
    service = f"{prefix}/src/dotmac_files/service.py"
    repo = _repo(
        tmp_path,
        {
            f"{prefix}/pyproject.toml": (
                '[project]\nname="dotmac-files"\nversion="1.0"\n'
            ),
            f"{prefix}/src/dotmac_files/__init__.py": "from . import service\n",
            service: "from dotmac_kernel.db import conflict_savepoint\n",
        },
    )
    old = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "dotmac-files-v1.0")
    (repo / service).write_text(
        "from dotmac_kernel.transactions import conflict_savepoint\n"
    )
    (repo / prefix / "pyproject.toml").write_text(
        '[project]\nname="dotmac-files"\nversion="2.0"\n'
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "repair source and publish successor")
    new = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "dotmac-files-v2.0")
    return repo, service, old, new


def _measure(repo: Path, source: gate.PublishedSource):
    return gate._reached_deferred_debt(
        repository_root=repo,
        records=(
            SimpleNamespace(
                distribution="dotmac-files", runtime_consumption=DimensionValue.TRUE
            ),
        ),
        product_external_modules=frozenset({"dotmac_files.service"}),
        catalogue=gate._TrustedCatalogue(
            repo / "packages", {"dotmac-files": "dotmac_files"}
        ),
        sources={"dotmac-files": source},
    )


def test_old_publication_retains_debt_after_main_repair_and_new_publication_clears_it(
    tmp_path: Path,
) -> None:
    repo, service, old, new = _published_fixture(tmp_path)
    old_source = gate.PublishedSource("dotmac-files", "1.0", "dotmac-files-v1.0", old)
    new_source = gate.PublishedSource("dotmac-files", "2.0", "dotmac-files-v2.0", new)
    gate._verify_published_source(repo, old_source)
    gate._verify_published_source(repo, new_source)
    debt = _measure(repo, old_source)
    assert [
        (item.source_path, item.sites, item.version, item.source_commit)
        for item in debt
    ] == [(service, 1, "1.0", old)]
    assert _measure(repo, new_source) == ()


@pytest.mark.parametrize(
    "plant", ["wrong_commit", "wrong_version", "unpublished", "missing"]
)
def test_published_source_requires_actual_tag_metadata_and_closed_map(
    tmp_path: Path, plant: str
) -> None:
    repo, _, old, new = _published_fixture(tmp_path)
    if plant == "missing":
        with pytest.raises(gate.GateDerivationError, match="missing published source"):
            gate._select_published_sources({"dotmac-files": "1.0"}, ())
        return
    source = gate.PublishedSource(
        "dotmac-files",
        "1.0",
        "dotmac-files-v1.0",
        new if plant == "wrong_commit" else old,
    )
    if plant == "wrong_version":
        # A real tag with a dishonest package version must still refuse.
        _git(repo, "tag", "-f", "dotmac-files-v1.0", new)
        source = gate.PublishedSource("dotmac-files", "1.0", "dotmac-files-v1.0", new)
    if plant == "unpublished":
        _git(repo, "tag", "-d", "dotmac-files-v1.0")
    with pytest.raises((gate.GateDerivationError, gate.GateAcquisitionError)):
        gate._verify_published_source(repo, source)


def test_stale_product_contract_refuses_before_any_source_derivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = gate.load_bindings()
    spec = gate.PRODUCT_OBSERVATION_SPECS["erp"]
    document = {
        "schema_version": "dimensional-composition.v3",
        "repository": spec.repository,
        "product_id": spec.product_id,
        "subject": spec.subject,
        "contract_revision": {
            "repository": "dotmac_starter_mt",
            "commit": "0" * 40,
        },
        "observations": [],
    }
    monkeypatch.setattr(gate, "read_checkout_json_document", lambda *a, **k: document)

    def must_not_derive(**kwargs):
        raise AssertionError("unverified observations reached derivation")

    monkeypatch.setattr(gate, "_derive_records", must_not_derive)
    result = gate._evaluate_product(
        binding=configuration["erp"],
        clone=tmp_path,
        repository_root=tmp_path,
        catalogue=gate._TrustedCatalogue(tmp_path, {}),
        configuration=configuration,
    )
    assert result.compatibility_status == "evidence_refused"
    assert not result.evidence_verified
    assert not result.compatibility_satisfied
    assert result.adoption_status == "not_evaluated"
    assert "contract revision" in result.findings[0]


def test_transitive_package_cannot_escape_through_missing_source(
    tmp_path: Path,
) -> None:
    repo, service, _, _ = _published_fixture(tmp_path)
    (repo / service).write_text("import dotmac_people.service\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "plant undeclared runtime package")
    revision = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(gate.GateDerivationError, match="lack published lock sources"):
        gate._reached_deferred_debt(
            repository_root=repo,
            records=(),
            product_external_modules=frozenset({"dotmac_files.service"}),
            catalogue=gate._TrustedCatalogue(
                repo / "packages",
                {"dotmac-files": "dotmac_files", "dotmac-people": "dotmac_people"},
            ),
            sources={
                "dotmac-files": gate.PublishedSource(
                    "dotmac-files", "2.0", "dotmac-files-v2.0", revision
                )
            },
        )


def test_transitive_submodule_runs_its_published_package_initializer(
    tmp_path: Path,
) -> None:
    people_init = "packages/dotmac-people/src/dotmac_people/__init__.py"
    repo = _repo(
        tmp_path,
        {
            "packages/dotmac-files/src/dotmac_files/__init__.py": "",
            "packages/dotmac-files/src/dotmac_files/service.py": (
                "import dotmac_people.service\n"
            ),
            people_init: "from dotmac_kernel.db import conflict_savepoint\n",
            "packages/dotmac-people/src/dotmac_people/service.py": "",
        },
    )
    revision = _git(repo, "rev-parse", "HEAD")
    sources = {
        name: gate.PublishedSource(name, "1.0", f"{name}-v1.0", revision)
        for name in ("dotmac-files", "dotmac-people")
    }
    debt = gate._reached_deferred_debt(
        repository_root=repo,
        records=(),
        product_external_modules=frozenset({"dotmac_files.service"}),
        catalogue=gate._TrustedCatalogue(
            repo / "packages",
            {"dotmac-files": "dotmac_files", "dotmac-people": "dotmac_people"},
        ),
        sources=sources,
    )
    assert debt == (
        gate.DeferredDebtReach("dotmac-people", people_init, 1, "1.0", revision),
    )


def test_kernel_subject_is_required_closed_and_kernel_owned() -> None:
    for plant in ("missing", "extra", "owner"):
        document = _document()
        if plant == "missing":
            del document["kernel_subject"]
        elif plant == "extra":
            document["kernel_subject"]["debt"] = []
        else:
            document["kernel_subject"] = dict(document["published_sources"][0])
        with pytest.raises(gate.GateConfigurationError):
            gate.parse_bindings(json.dumps(document).encode())


def _kernel_history(tmp_path: Path, non_target: str = "dotmac-accounting"):
    root = "packages/dotmac-kernel/src/dotmac_kernel"
    non_target_root = non_target.replace("-", "_")
    repo = _repo(
        tmp_path,
        {
            f"{root}/__init__.py": "from . import service\n",
            f"{root}/service.py": "from dotmac_kernel.db import conflict_savepoint\n",
            f"{root}/db.py": "def conflict_savepoint(): pass\n",
            f"{root}/transactions.py": "def conflict_savepoint(): pass\n",
            f"{root}/legacy.py": "",
            f"packages/{non_target}/src/{non_target_root}/__init__.py": "",
            f"packages/{non_target}/src/{non_target_root}/service.py": (
                "from dotmac_kernel.db import conflict_savepoint\n"
            ),
        },
    )
    old = _git(repo, "rev-parse", "HEAD")
    (repo / root / "service.py").write_text(
        "from dotmac_kernel.transactions import conflict_savepoint\n"
    )
    (repo / root / "legacy.py").unlink()
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "prospective Kernel subject")
    new = _git(repo, "rev-parse", "HEAD")
    installed = gate.PublishedSource("dotmac-kernel", "1.0", "dotmac-kernel-v1.0", old)
    subject = gate.PublishedSource("dotmac-kernel", "2.0", "dotmac-kernel-v2.0", new)
    other = gate.PublishedSource(non_target, "1.0", f"{non_target}-v1.0", old)
    catalogue = gate._TrustedCatalogue(
        repo / "packages",
        {
            "dotmac-kernel": "dotmac_kernel",
            non_target: non_target_root,
        },
    )
    return repo, catalogue, installed, subject, other


def test_prospective_subject_replaces_only_installed_kernel_debt(
    tmp_path: Path,
) -> None:
    repo, catalogue, installed, subject, other = _kernel_history(tmp_path)
    versions = {
        installed.distribution: installed.version,
        other.distribution: other.version,
    }
    observed = gate._select_published_sources(versions, (installed, other))
    effective = gate._effective_published_sources(versions, (installed, other), subject)
    assert observed["dotmac-kernel"] == installed
    assert effective["dotmac-kernel"] == subject
    assert effective[other.distribution] == other
    arguments = {
        "repository_root": repo,
        "records": (),
        "product_external_modules": frozenset({"dotmac_kernel.service"}),
        "catalogue": catalogue,
    }
    assert len(gate._reached_deferred_debt(**arguments, sources=observed)) == 1
    assert gate._reached_deferred_debt(**arguments, sources=effective) == ()
    assert "dotmac-kernel" not in gate._effective_published_sources(
        {other.distribution: other.version}, (installed, other), subject
    )


@pytest.mark.parametrize("non_target", ["dotmac-accounting", "dotmac-inbox"])
def test_non_target_locked_publication_debt_survives_kernel_substitution(
    tmp_path: Path,
    non_target: str,
) -> None:
    repo, catalogue, installed, subject, other = _kernel_history(tmp_path, non_target)
    effective = gate._effective_published_sources(
        {installed.distribution: installed.version, other.distribution: other.version},
        (installed, other),
        subject,
    )
    debt = gate._reached_deferred_debt(
        repository_root=repo,
        records=(),
        product_external_modules=frozenset({non_target.replace("-", "_") + ".service"}),
        catalogue=catalogue,
        sources=effective,
    )
    assert [(item.distribution, item.version, item.source_commit) for item in debt] == [
        (non_target, "1.0", other.peeled_commit),
    ]


def test_subject_introduced_debt_and_missing_target_module_refuse(
    tmp_path: Path,
) -> None:
    repo, catalogue, installed, subject, other = _kernel_history(tmp_path)
    versions = {
        installed.distribution: installed.version,
        other.distribution: other.version,
    }
    effective = gate._effective_published_sources(versions, (installed, other), subject)
    with pytest.raises(gate.GateDerivationError, match="explicit product imports"):
        gate._reached_deferred_debt(
            repository_root=repo,
            records=(),
            product_external_modules=frozenset({"dotmac_kernel.legacy"}),
            catalogue=catalogue,
            sources=effective,
        )
    path = repo / "packages/dotmac-kernel/src/dotmac_kernel/service.py"
    path.write_text("from dotmac_kernel.db import conflict_savepoint\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "plant prospective subject debt")
    planted = gate.PublishedSource(
        "dotmac-kernel", "3.0", "dotmac-kernel-v3.0", _git(repo, "rev-parse", "HEAD")
    )
    effective = gate._effective_published_sources(versions, (installed, other), planted)
    debt = gate._reached_deferred_debt(
        repository_root=repo,
        records=(),
        product_external_modules=frozenset({"dotmac_kernel.service"}),
        catalogue=catalogue,
        sources=effective,
    )
    assert [(item.distribution, item.version, item.source_commit) for item in debt] == [
        ("dotmac-kernel", "3.0", planted.peeled_commit),
    ]


def test_explicit_known_owner_module_missing_in_selected_release_refuses(
    tmp_path: Path,
) -> None:
    repo, _, old, _ = _published_fixture(tmp_path)
    with pytest.raises(gate.GateDerivationError, match="dotmac_files.future"):
        gate._reached_deferred_debt(
            repository_root=repo,
            records=(),
            product_external_modules=frozenset({"dotmac_files.future"}),
            catalogue=gate._TrustedCatalogue(
                repo / "packages", {"dotmac-files": "dotmac_files"}
            ),
            sources={
                "dotmac-files": gate.PublishedSource(
                    "dotmac-files", "1.0", "dotmac-files-v1.0", old
                )
            },
        )


def _multi_root_publication(tmp_path: Path):
    distribution = "dotmac-learning"
    primary = "dotmac_learning"
    sibling = "dotmac_learning_bank_rules"
    sibling_service = f"packages/{distribution}/src/{sibling}/service.py"
    repo = _repo(
        tmp_path,
        {
            f"packages/{distribution}/EXTRACTION.toml": (
                'package = "dotmac-learning"\nclassification = "optional-module"\n'
            ),
            f"packages/{distribution}/pyproject.toml": (
                '[project]\nname = "dotmac-learning"\nversion = "1.0"\n'
            ),
            f"packages/{distribution}/src/{primary}/__init__.py": "",
            f"packages/{distribution}/src/{primary}/service.py": "",
            f"packages/{distribution}/src/{primary}/manifest.py": "module = None\n",
            f"packages/{distribution}/src/{sibling}/__init__.py": "",
            sibling_service: "from dotmac_kernel.db import conflict_savepoint\n",
        },
    )
    revision = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "dotmac-learning-v1.0")
    _git(repo, "update-ref", "refs/remotes/origin/main", revision)
    source = gate.PublishedSource(distribution, "1.0", "dotmac-learning-v1.0", revision)
    catalogue = gate._TrustedCatalogue(
        repo / "packages", {distribution: (primary, sibling)}
    )
    return repo, catalogue, source, sibling_service


def test_catalogue_materializes_every_owner_import_package_and_canonical_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, source, _ = _multi_root_publication(tmp_path)
    monkeypatch.setattr(gate, "TRUSTED_CONTRACT_REVISION", source.peeled_commit)
    with gate._trusted_catalogue(repo, source.peeled_commit) as catalogue:
        assert catalogue.import_roots["dotmac-learning"] == (
            "dotmac_learning",
            "dotmac_learning_bank_rules",
        )
        assert (
            catalogue.packages_root / "dotmac-learning/src/dotmac_learning_bank_rules"
        ).is_dir()
        assert (
            catalogue.packages_root / "dotmac-learning/src/dotmac_learning/manifest.py"
        ).read_text() == "module = None\n"


def test_published_sibling_import_retains_its_owner_debt_without_inventing_reach(
    tmp_path: Path,
) -> None:
    repo, catalogue, source, sibling_service = _multi_root_publication(tmp_path)
    gate._verify_published_source(repo, source)

    def measure(module):
        return gate._reached_deferred_debt(
            repository_root=repo,
            records=(),
            product_external_modules=frozenset({module}),
            catalogue=catalogue,
            sources={"dotmac-learning": source},
        )

    assert measure("dotmac_learning.service") == ()
    assert measure("dotmac_learning_bank_rules.service") == (
        gate.DeferredDebtReach(
            "dotmac-learning", sibling_service, 1, "1.0", source.peeled_commit
        ),
    )


def test_published_roots_are_discovered_at_the_actual_source_not_filtered_by_catalogue(
    tmp_path: Path,
) -> None:
    repo, _, source, sibling_service = _multi_root_publication(tmp_path)
    catalogue = gate._TrustedCatalogue(
        repo / "packages", {"dotmac-learning": "dotmac_learning"}
    )
    debt = gate._reached_deferred_debt(
        repository_root=repo,
        records=(),
        product_external_modules=frozenset({"dotmac_learning_bank_rules.service"}),
        catalogue=catalogue,
        sources={"dotmac-learning": source},
    )
    assert [(item.source_path, item.sites) for item in debt] == [(sibling_service, 1)]


def test_missing_module_under_an_owner_sibling_root_refuses(tmp_path: Path) -> None:
    repo, catalogue, source, _ = _multi_root_publication(tmp_path)
    with pytest.raises(
        gate.GateDerivationError, match="dotmac_learning_bank_rules.future"
    ):
        gate._reached_deferred_debt(
            repository_root=repo,
            records=(),
            product_external_modules=frozenset({"dotmac_learning_bank_rules.future"}),
            catalogue=catalogue,
            sources={"dotmac-learning": source},
        )


def test_sibling_root_registration_and_lineage_resolve_to_the_same_owner() -> None:
    roots = {"dotmac-learning": ("dotmac_learning", "dotmac_learning_bank_rules")}
    assert gate._module_registrations(
        "from dotmac_learning_bank_rules.manifest import module as sibling\n",
        "(sibling,)",
        roots,
    ) == frozenset({"dotmac-learning"})
    assert gate._migration_lineages(
        "[alembic]\nversion_locations = "
        "dotmac_learning_bank_rules.migrations:versions\n",
        roots,
    ) == frozenset({"dotmac-learning"})
    with pytest.raises(
        gate.GateConfigurationError, match="duplicate import root owner"
    ):
        gate._TrustedCatalogue(
            Path("packages"),
            {
                "dotmac-learning": ("dotmac_learning", "dotmac_learning_bank_rules"),
                "dotmac-other": "dotmac_learning_bank_rules",
            },
        )


def test_catalogue_sibling_cannot_supply_a_package_missing_from_published_source(
    tmp_path: Path,
) -> None:
    repo, catalogue, _, _ = _multi_root_publication(tmp_path)
    sibling = repo / "packages/dotmac-learning/src/dotmac_learning_bank_rules"
    (sibling / "service.py").unlink()
    (sibling / "__init__.py").unlink()
    (repo / "packages/dotmac-learning/pyproject.toml").write_text(
        '[project]\nname = "dotmac-learning"\nversion = "2.0"\n'
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "publish layout without the sibling package")
    _git(repo, "tag", "dotmac-learning-v2.0")
    source = gate.PublishedSource(
        "dotmac-learning",
        "2.0",
        "dotmac-learning-v2.0",
        _git(repo, "rev-parse", "HEAD"),
    )
    gate._verify_published_source(repo, source)
    with pytest.raises(gate.GateDerivationError, match="explicit product imports"):
        gate._reached_deferred_debt(
            repository_root=repo,
            records=(),
            product_external_modules=frozenset({"dotmac_learning_bank_rules.service"}),
            catalogue=catalogue,
            sources={"dotmac-learning": source},
        )
