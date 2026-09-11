from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tools.composition_contract import observations as observation_contract
from tools.composition_contract.observations import (
    CANONICAL_RECORD_PATH,
    CONTRACT_REPOSITORY,
    OBSERVATION_SCHEMA_VERSION,
    ObservationAcquisitionError,
    ObservationRefusal,
    ObservationSpec,
    PoetryInstallCommandLocator,
    ProductObservationSpec,
    PythonAssignmentKeywordLocator,
    PythonStringAssignmentLocator,
    WholeFileLocator,
    build_product_checkout_document,
    extract_observation,
    load_and_verify_product_checkout_envelope,
    read_checkout_json_document,
    verify_observation_envelope,
    verify_product_checkout_envelope,
)
from tools.composition_contract.specs import (
    ERP_OBSERVATION_SPEC,
    PRODUCT_OBSERVATION_SPECS,
)

CONTRACT_REVISION = "a" * 40


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603 - test-owned repository and argv
        ["git", "-C", str(repo), *arguments],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def product_repository(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "composition@example.invalid")
    _git(tmp_path, "config", "user.name", "Composition Test")
    _write(
        tmp_path,
        "Dockerfile",
        "FROM python:3.13\n"
        "RUN --mount=type=secret,id=registry \\\n"
        '    REGISTRY_PASSWORD="$(cat /run/secrets/registry)" \\\n'
        "    poetry install --only main --no-root --no-ansi\n",
    )
    _write(
        tmp_path,
        "app/product_assembly.py",
        "from dotmac_kernel.assembly import ProductAssemblySpec\n"
        "PRODUCT_CODE = 'dotmac-erp'\n"
        "COMPOSED_MODULE_MANIFESTS = (accounting_module, files_module)\n"
        "ERP_PRODUCT_ASSEMBLY = ProductAssemblySpec(\n"
        "    name='dotmac-erp',\n"
        "    modules=COMPOSED_MODULE_MANIFESTS,\n"
        ")\n",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial product evidence")
    _git(tmp_path, "branch", "-M", "main")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def _identity_observation() -> ObservationSpec:
    return ObservationSpec(
        "product-identity",
        "app/product_assembly.py",
        PythonStringAssignmentLocator("PRODUCT_CODE"),
    )


def _spec(*observations: ObservationSpec) -> ProductObservationSpec:
    selected = observations or (
        ObservationSpec(
            "production-install",
            "Dockerfile",
            PoetryInstallCommandLocator("dockerfile"),
        ),
        ObservationSpec(
            "module-registration",
            "app/product_assembly.py",
            PythonAssignmentKeywordLocator(
                "ERP_PRODUCT_ASSEMBLY", "ProductAssemblySpec", "modules"
            ),
        ),
    )
    return ProductObservationSpec(
        repository="dotmac_erp",
        product_id="dotmac-erp",
        subject="erp-kernel-successor-composition",
        observations=(_identity_observation(), *selected),
    )


def _claim(repo: Path, spec: ObservationSpec) -> dict[str, object]:
    source = (repo / spec.source_path).read_bytes()
    selected = extract_observation(spec, source)
    return {
        "id": spec.observation_id,
        "source_path": spec.source_path,
        "source_blob_sha256": hashlib.sha256(source).hexdigest(),
        "selector": spec.selector,
        "extract_sha256": hashlib.sha256(selected).hexdigest(),
    }


def _document(repo: Path, spec: ProductObservationSpec) -> dict[str, object]:
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "repository": spec.repository,
        "product_id": spec.product_id,
        "subject": spec.subject,
        "contract_revision": {
            "repository": CONTRACT_REPOSITORY,
            "commit": CONTRACT_REVISION,
        },
        "observations": [_claim(repo, item) for item in spec.observations],
    }


def _verify(
    document: dict[str, object],
    spec: ProductObservationSpec,
    repo: Path,
    revision: str,
):
    return verify_observation_envelope(
        document,
        spec=spec,
        product_clone=repo,
        product_revision=revision,
        trusted_contract_revision=CONTRACT_REVISION,
        protected_main_ref="main",
    )


def test_real_git_blobs_and_both_structural_extractors_verify(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    spec = _spec()

    verified = _verify(_document(repo, spec), spec, repo, revision)

    assert verified.repository == "dotmac_erp"
    assert verified.product_id == "dotmac-erp"
    assert verified.contract_revision == CONTRACT_REVISION
    assert verified.product_revision == revision
    by_id = {item.claim.observation_id: item for item in verified.observations}
    assert by_id["production-install"].extracted == (
        b"RUN --mount=type=secret,id=registry \\\n"
        b'    REGISTRY_PASSWORD="$(cat /run/secrets/registry)" \\\n'
        b"    poetry install --only main --no-root --no-ansi"
    )
    assert by_id["module-registration"].extracted == b"COMPOSED_MODULE_MANIFESTS"


def test_record_contains_digests_but_never_selected_source_text(
    product_repository: tuple[Path, str],
) -> None:
    repo, _ = product_repository
    encoded = json.dumps(_document(repo, _spec()))

    assert "REGISTRY_PASSWORD" not in encoded
    assert "cat /run/secrets" not in encoded
    assert "COMPOSED_MODULE_MANIFESTS" not in encoded
    assert encoded.count("extract_sha256") == 3


def test_v3_refuses_a_source_text_field_and_verified_repr_hides_extracted_bytes(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    spec = _spec()
    document = _document(repo, spec)
    verified = _verify(document, spec, repo, revision)

    assert "REGISTRY_PASSWORD" not in repr(verified)
    observations = document["observations"]
    assert isinstance(observations, list)
    assert isinstance(observations[0], dict)
    observations[0]["verbatim"] = ["credential-shaped source text"]
    with pytest.raises(ObservationRefusal, match="unknown=.*verbatim"):
        _verify(document, spec, repo, revision)


def test_repository_and_product_id_use_disjoint_namespaces(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    spec = _spec()
    document = _document(repo, spec)
    document["repository"] = "dotmac-erp"

    with pytest.raises(ObservationRefusal, match="repository"):
        _verify(document, spec, repo, revision)

    with pytest.raises(ValueError, match="underscore"):
        ProductObservationSpec("dotmac-erp", "dotmac-erp", "subject", spec.observations)
    with pytest.raises(ValueError, match="hyphen"):
        ProductObservationSpec("dotmac_erp", "dotmac_erp", "subject", spec.observations)


def test_old_dimension_envelope_is_refused_without_an_adapter(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    spec = _spec()
    document = _document(repo, spec)
    document["schema_version"] = "dimensional-composition.v2"

    with pytest.raises(ObservationRefusal, match="no adapter"):
        _verify(document, spec, repo, revision)


def test_contract_revision_is_fixed_but_need_not_equal_starter_head(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    spec = _spec()
    document = _document(repo, spec)

    verified = _verify(document, spec, repo, revision)
    assert verified.contract_revision == CONTRACT_REVISION

    contract = document["contract_revision"]
    assert isinstance(contract, dict)
    contract["commit"] = "b" * 40
    with pytest.raises(ObservationRefusal, match="loaded contract"):
        _verify(document, spec, repo, revision)


def test_checkout_builder_emits_only_rederived_coordinates_and_digests(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    spec = _spec()

    document = build_product_checkout_document(
        spec=spec,
        product_clone=repo,
        contract_revision=CONTRACT_REVISION,
    )
    verified = verify_product_checkout_envelope(
        document,
        spec=spec,
        product_clone=repo,
        trusted_contract_revision=CONTRACT_REVISION,
    )

    assert verified.product_revision == revision
    assert "REGISTRY_PASSWORD" not in json.dumps(document)
    assert set(document) == {
        "schema_version",
        "repository",
        "product_id",
        "subject",
        "contract_revision",
        "observations",
    }


def test_checked_in_product_identity_must_equal_starter_fixed_identity(
    product_repository: tuple[Path, str],
) -> None:
    repo, _ = product_repository
    assembly = repo / "app/product_assembly.py"
    assembly.write_text(
        assembly.read_text().replace("dotmac-erp", "dotmac-erp-other"),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "drift product identity")
    spec = _spec()
    document = build_product_checkout_document(
        spec=spec,
        product_clone=repo,
        contract_revision=CONTRACT_REVISION,
    )

    with pytest.raises(ObservationRefusal, match="product identity source declares"):
        verify_product_checkout_envelope(
            document,
            spec=spec,
            product_clone=repo,
            trusted_contract_revision=CONTRACT_REVISION,
        )


def test_action_path_resolves_head_once_for_record_and_sources(
    product_repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = product_repository
    spec = _spec()
    document = build_product_checkout_document(
        spec=spec,
        product_clone=repo,
        contract_revision=CONTRACT_REVISION,
    )
    _write(repo, CANONICAL_RECORD_PATH, json.dumps(document) + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add verified record")
    pinned_revision = _git(repo, "rev-parse", "HEAD")
    calls = 0

    def resolve_once_then_move_head(product_clone: Path) -> str:
        nonlocal calls
        calls += 1
        assert product_clone == repo
        _write(repo, "Dockerfile", "RUN poetry install --only dev\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "move head during verification")
        return pinned_revision

    monkeypatch.setattr(
        observation_contract,
        "checkout_head_revision",
        resolve_once_then_move_head,
    )

    verified = load_and_verify_product_checkout_envelope(
        spec=spec,
        product_clone=repo,
        trusted_contract_revision=CONTRACT_REVISION,
    )

    assert calls == 1
    assert verified.product_revision == pinned_revision


def test_checkout_json_reader_refuses_duplicate_fields_and_symlinks(
    product_repository: tuple[Path, str],
) -> None:
    repo, _ = product_repository
    _write(
        repo,
        "docs/kernel-runtime-composition.json",
        '{"schema_version":"one","schema_version":"two"}\n',
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add duplicate record")

    with pytest.raises(ObservationRefusal, match="duplicated"):
        read_checkout_json_document(repo)

    (repo / "docs/kernel-runtime-composition.json").unlink()
    (repo / "docs/kernel-runtime-composition.json").symlink_to("../target.json")
    _write(repo, "docs/target.json", "{}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "replace record with symlink")

    with pytest.raises(ObservationRefusal, match="not a regular file"):
        read_checkout_json_document(repo)


def test_product_specs_fix_identity_subject_and_observation_coordinates() -> None:
    assert set(PRODUCT_OBSERVATION_SPECS) == {"academy", "erp", "sub"}
    assert {
        key: (spec.repository, spec.product_id, spec.subject)
        for key, spec in PRODUCT_OBSERVATION_SPECS.items()
    } == {
        "academy": (
            "dotmac_academy_app",
            "dotmac-academy",
            "academy-kernel-successor-composition",
        ),
        "erp": (
            "dotmac_erp",
            "dotmac-erp",
            "erp-kernel-successor-composition",
        ),
        "sub": (
            "dotmac_sub",
            "dotmac-sub",
            "sub-kernel-successor-composition",
        ),
    }
    for spec in PRODUCT_OBSERVATION_SPECS.values():
        assert {item.observation_id for item in spec.observations} >= {
            "product-identity",
            "dependency-manifest",
            "dependency-lock",
            "production-install",
            "product-assembly-source",
            "module-registration",
            "boot-entry-point",
            "migration-config",
        }

    with pytest.raises(ValueError, match="product-identity"):
        ProductObservationSpec(
            repository="dotmac_erp",
            product_id="fake-product",
            subject="subject",
            observations=(
                ObservationSpec("dependency", "pyproject.toml", WholeFileLocator()),
            ),
        )


def test_public_cli_verifies_a_real_candidate_checkout(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "composition@example.invalid")
    _git(tmp_path, "config", "user.name", "Composition Test")
    contents = {
        "pyproject.toml": "[tool.poetry]\nname='dotmac_erp'\n",
        "poetry.lock": "package = []\n",
        "Dockerfile": "RUN poetry install --only main --no-root --no-ansi\n",
        "app/product_assembly.py": (
            "PRODUCT_CODE = 'dotmac-erp'\n"
            "ERP_PRODUCT_ASSEMBLY = ProductAssemblySpec(\n"
            "    name='dotmac-erp', modules=COMPOSED_MODULE_MANIFESTS\n"
            ")\n"
        ),
        "app/main.py": "app = object()\n",
        "alembic.ini": "[alembic]\n",
        "app/migration_bindings.py": "MIGRATION_BINDINGS = ()\n",
    }
    for path, content in contents.items():
        _write(tmp_path, path, content)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "add product sources")
    document = build_product_checkout_document(
        spec=ERP_OBSERVATION_SPEC,
        product_clone=tmp_path,
        contract_revision=CONTRACT_REVISION,
    )
    _write(
        tmp_path,
        CANONICAL_RECORD_PATH,
        json.dumps(document, indent=2) + "\n",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "add product observations")

    cli = (
        Path(__file__).resolve().parents[2]
        / "tools"
        / "composition_contract"
        / "check_product_observations.py"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and test CLI
        [
            sys.executable,
            str(cli),
            "--product",
            "erp",
            "--workspace",
            str(tmp_path),
            "--action-repository",
            "michaelayoade/dotmac_starter_mt",
            "--action-ref",
            CONTRACT_REVISION,
            "--caller-repository",
            "michaelayoade/dotmac_erp",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "product=dotmac-erp" in result.stdout
    assert "observations=9" in result.stdout


def test_public_cli_reports_git_launch_failure_as_acquisition_exit_two(
    product_repository: tuple[Path, str], tmp_path: Path
) -> None:
    repo, _ = product_repository
    cli = (
        Path(__file__).resolve().parents[2]
        / "tools"
        / "composition_contract"
        / "check_product_observations.py"
    )
    environment = {**os.environ, "PATH": str(tmp_path / "no-executables")}

    result = subprocess.run(  # noqa: S603 - fixed interpreter and test CLI
        [
            sys.executable,
            str(cli),
            "--product",
            "erp",
            "--workspace",
            str(repo),
            "--action-repository",
            "michaelayoade/dotmac_starter_mt",
            "--action-ref",
            CONTRACT_REVISION,
            "--caller-repository",
            "michaelayoade/dotmac_erp",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 2
    assert "acquisition failure" in result.stderr
    assert "could not launch git" in result.stderr
    assert "Traceback" not in result.stderr


def test_source_changed_while_record_stayed_fixed_is_refused(
    product_repository: tuple[Path, str],
) -> None:
    repo, _ = product_repository
    spec = _spec()
    document = _document(repo, spec)
    _write(repo, "Dockerfile", (repo / "Dockerfile").read_text().replace("main", "dev"))
    _git(repo, "add", "Dockerfile")
    _git(repo, "commit", "-qm", "change source without evidence")
    revision = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(ObservationRefusal, match="source digest disagrees"):
        _verify(document, spec, repo, revision)


def test_record_changed_while_source_stayed_fixed_is_refused(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    spec = _spec()
    document = _document(repo, spec)
    observations = document["observations"]
    assert isinstance(observations, list)
    assert isinstance(observations[0], dict)
    observations[0]["extract_sha256"] = "0" * 64

    with pytest.raises(ObservationRefusal, match="extract digest disagrees"):
        _verify(document, spec, repo, revision)


@pytest.mark.parametrize("field", ["source_path", "selector"])
def test_payload_cannot_select_a_coordinate_before_any_git_access(
    product_repository: tuple[Path, str], field: str
) -> None:
    repo, revision = product_repository
    spec = _spec()
    document = _document(repo, spec)
    observations = document["observations"]
    assert isinstance(observations, list)
    assert isinstance(observations[0], dict)
    observations[0][field] = "../../etc/passwd"

    with pytest.raises(ObservationRefusal, match="Starter fixes it"):
        _verify(document, spec, Path("/clone-must-not-be-read"), revision)


@pytest.mark.parametrize(
    "source_path",
    [
        "/etc/passwd",
        "../outside",
        "app//main.py",
        r"app\main.py",
        ".git/config",
        ":(glob)**",
        "line\nfeed",
    ],
)
def test_starter_owned_source_paths_are_canonical_and_bounded(source_path: str) -> None:
    with pytest.raises(ValueError):
        ObservationSpec("source", source_path, WholeFileLocator())


def test_a_symlink_git_object_is_refused_without_following_it(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "composition@example.invalid")
    _git(tmp_path, "config", "user.name", "Composition Test")
    _write(tmp_path, "app/product_assembly.py", "PRODUCT_CODE = 'dotmac-erp'\n")
    os.symlink("/etc/passwd", tmp_path / "evidence")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "symlink plant")
    _git(tmp_path, "branch", "-M", "main")
    revision = _git(tmp_path, "rev-parse", "HEAD")
    observation = ObservationSpec("source", "evidence", WholeFileLocator())
    spec = _spec(observation)
    document = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "repository": spec.repository,
        "product_id": spec.product_id,
        "subject": spec.subject,
        "contract_revision": {
            "repository": CONTRACT_REPOSITORY,
            "commit": CONTRACT_REVISION,
        },
        "observations": [
            _claim(tmp_path, _identity_observation()),
            {
                "id": "source",
                "source_path": "evidence",
                "source_blob_sha256": "0" * 64,
                "selector": observation.selector,
                "extract_sha256": "0" * 64,
            },
        ],
    }

    with pytest.raises(ObservationRefusal, match="not a regular file"):
        _verify(document, spec, tmp_path, revision)


def test_oversized_blob_refuses_before_content_is_read(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    observation = ObservationSpec(
        "small-source", "Dockerfile", WholeFileLocator(), max_source_bytes=8
    )
    spec = _spec(observation)
    source = (repo / "Dockerfile").read_bytes()
    document = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "repository": spec.repository,
        "product_id": spec.product_id,
        "subject": spec.subject,
        "contract_revision": {
            "repository": CONTRACT_REPOSITORY,
            "commit": CONTRACT_REVISION,
        },
        "observations": [
            _claim(repo, _identity_observation()),
            {
                "id": "small-source",
                "source_path": "Dockerfile",
                "source_blob_sha256": hashlib.sha256(source).hexdigest(),
                "selector": observation.selector,
                "extract_sha256": hashlib.sha256(source).hexdigest(),
            },
        ],
    }

    with pytest.raises(ObservationRefusal, match="limit is 8"):
        _verify(document, spec, repo, revision)


def test_ambiguous_python_locator_refuses(
    product_repository: tuple[Path, str],
) -> None:
    repo, _ = product_repository
    spec = _spec()
    path = repo / "app/product_assembly.py"
    path.write_text(
        path.read_text() + "ERP_PRODUCT_ASSEMBLY = ProductAssemblySpec(modules=())\n"
    )

    with pytest.raises(ObservationRefusal, match="extracted 2 value"):
        registration = next(
            item
            for item in spec.observations
            if item.observation_id == "module-registration"
        )
        extract_observation(registration, path.read_bytes())


def test_shell_locator_ignores_version_probes_but_selects_the_install() -> None:
    source = (
        b"command -v poetry >/dev/null\n"
        b"poetry --version\n"
        b"# poetry install --only dev is documentation, not execution\n"
        b"poetry sync --only main --no-root\n"
    )
    spec = ObservationSpec(
        "install", "scripts/install.sh", PoetryInstallCommandLocator("shell")
    )

    assert extract_observation(spec, source) == b"poetry sync --only main --no-root"


def test_poetry_install_behind_an_unmodelled_shell_wrapper_refuses() -> None:
    source = b'sh -c "poetry install --only main"\n'
    spec = ObservationSpec(
        "install", "scripts/install.sh", PoetryInstallCommandLocator("shell")
    )

    with pytest.raises(ObservationRefusal, match="unmodelled shell shape"):
        extract_observation(spec, source)


def test_poetry_words_passed_to_another_command_are_not_an_invocation() -> None:
    source = b"echo poetry install --only main\n"
    spec = ObservationSpec(
        "install", "scripts/install.sh", PoetryInstallCommandLocator("shell")
    )

    with pytest.raises(ObservationRefusal, match="unmodelled shell shape"):
        extract_observation(spec, source)


def test_missing_source_blob_is_evidence_refusal(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    observation = ObservationSpec("missing", "absent.py", WholeFileLocator())
    spec = _spec(observation)
    document = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "repository": spec.repository,
        "product_id": spec.product_id,
        "subject": spec.subject,
        "contract_revision": {
            "repository": CONTRACT_REPOSITORY,
            "commit": CONTRACT_REVISION,
        },
        "observations": [
            _claim(repo, _identity_observation()),
            {
                "id": "missing",
                "source_path": "absent.py",
                "source_blob_sha256": "0" * 64,
                "selector": observation.selector,
                "extract_sha256": "0" * 64,
            },
        ],
    }

    with pytest.raises(ObservationRefusal, match="found 0"):
        _verify(document, spec, repo, revision)


def test_git_failure_is_not_reported_as_evidence_absence(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    spec = _spec()

    with pytest.raises(ObservationAcquisitionError):
        _verify(_document(repo, spec), spec, repo / "not-a-repository", revision)


def test_revision_off_protected_main_is_a_distinct_refusal(
    product_repository: tuple[Path, str],
) -> None:
    repo, main_revision = product_repository
    spec = _spec()
    document = _document(repo, spec)
    _git(repo, "checkout", "-qb", "side")
    _write(repo, "side.txt", "not on main\n")
    _git(repo, "add", "side.txt")
    _git(repo, "commit", "-qm", "side revision")
    side_revision = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(ObservationRefusal, match="not an ancestor"):
        _verify(document, spec, repo, side_revision)

    assert main_revision != side_revision


def test_unexpected_observation_id_is_refused(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    spec = _spec()
    document = _document(repo, spec)
    rows = deepcopy(document["observations"])
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    rows[0]["id"] = "caller-selected"
    document["observations"] = rows

    with pytest.raises(ObservationRefusal, match="observation set differs"):
        _verify(document, spec, repo, revision)


def test_non_string_observation_id_refuses_cleanly(
    product_repository: tuple[Path, str],
) -> None:
    repo, revision = product_repository
    spec = _spec()
    document = _document(repo, spec)
    rows = document["observations"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    rows[0]["id"] = ["not", "hashable"]

    with pytest.raises(ObservationRefusal, match="present strings"):
        _verify(document, spec, repo, revision)
