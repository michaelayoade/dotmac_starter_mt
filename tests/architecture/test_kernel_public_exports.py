"""The installed Kernel wheel carries an inspectable export catalogue."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import dotmac_kernel
import pytest
from dotmac_kernel.public_exports import (
    PublicExportsFormatError,
    derive_public_exports,
    load_public_exports,
    parse_public_exports,
)

SOURCE_ROOT = (
    Path(__file__).resolve().parents[2] / "packages/dotmac-kernel/src/dotmac_kernel"
)


def test_catalogue_covers_supported_and_internal_modules() -> None:
    catalogue = load_public_exports()
    declared = dotmac_kernel.SUPPORTED_MODULES | dotmac_kernel.INTERNAL_MODULES

    assert set(catalogue.modules) == declared
    assert catalogue.digest.startswith("sha256:")
    assert len(catalogue.digest) == len("sha256:") + 64


def test_secret_sources_is_a_declared_public_module() -> None:
    catalogue = load_public_exports()

    assert "dotmac_kernel.secret_sources" in dotmac_kernel.SUPPORTED_MODULES
    assert "SecretSource" in catalogue.exports_for("dotmac_kernel.secret_sources")


def test_module_without_explicit_all_is_typed_unavailable() -> None:
    catalogue = load_public_exports()

    # `_transactions` is intentionally internal and has no __all__.  The
    # catalogue must preserve that fact rather than infer arbitrary exports.
    assert catalogue.modules["dotmac_kernel._transactions"] is None
    with pytest.raises(PublicExportsFormatError, match="no explicit __all__"):
        catalogue.exports_for("dotmac_kernel._transactions")


def test_package_data_is_byte_identical_to_source_derivation() -> None:
    catalogue = load_public_exports()

    assert catalogue.canonical_bytes == derive_public_exports(SOURCE_ROOT)


def test_source_derivation_sensitivity_plants(tmp_path: Path) -> None:
    planted = tmp_path / "dotmac_kernel"
    shutil.copytree(SOURCE_ROOT, planted)
    identity = planted / "identity.py"
    identity.write_text(
        identity.read_text().replace(
            '"normalize_email",', '"planted_export",\n    "normalize_email",'
        )
    )

    with pytest.raises(PublicExportsFormatError, match="planted_export|not defined"):
        derive_public_exports(planted)

    identity.unlink()
    with pytest.raises(
        PublicExportsFormatError,
        match=r"missing source modules: \['dotmac_kernel\.identity'\]",
    ):
        derive_public_exports(planted)


def test_source_derivation_rejects_unclassified_importable_module_and_package(
    tmp_path: Path,
) -> None:
    planted = tmp_path / "dotmac_kernel"
    shutil.copytree(SOURCE_ROOT, planted)
    (planted / "planted_module.py").write_text("__all__ = []\n")
    with pytest.raises(
        PublicExportsFormatError,
        match=r"omit importable modules: \['dotmac_kernel\.planted_module'\]",
    ):
        derive_public_exports(planted)

    (planted / "planted_module.py").unlink()
    package = planted / "planted_package"
    package.mkdir()
    (package / "__init__.py").write_text("__all__ = []\n")
    with pytest.raises(
        PublicExportsFormatError,
        match=r"omit importable modules: \['dotmac_kernel\.planted_package'\]",
    ):
        derive_public_exports(planted)


def test_source_derivation_ignores_non_package_near_miss(tmp_path: Path) -> None:
    planted = tmp_path / "dotmac_kernel"
    shutil.copytree(SOURCE_ROOT, planted)
    versions = planted / "migrations" / "versions"
    (versions / "near_miss.py").write_text("__all__ = []\n")

    derive_public_exports(planted)


def test_parser_rejects_unknown_and_duplicate_fields() -> None:
    valid = load_public_exports().canonical_bytes.decode()
    document = json.loads(valid)
    document["unexpected"] = True
    with pytest.raises(PublicExportsFormatError, match="unknown or missing root"):
        parse_public_exports(json.dumps(document).encode())

    duplicate = valid.replace(
        '"schema": "dotmac.kernel-public-exports.v1"',
        '"schema": "dotmac.kernel-public-exports.v1", '
        '"schema": "dotmac.kernel-public-exports.v1"',
        1,
    )
    with pytest.raises(PublicExportsFormatError, match="duplicate JSON field"):
        parse_public_exports(duplicate.encode())

    entry = valid.replace(
        '"status": "declared"', '"status": "declared", "unexpected": true', 1
    )
    with pytest.raises(PublicExportsFormatError, match="unknown or missing fields"):
        parse_public_exports(entry.encode())


def test_parser_rejects_empty_catalogue_and_near_miss_module_name() -> None:
    valid = load_public_exports().canonical_bytes.decode()
    empty_document = json.loads(valid)
    empty_document["modules"] = {}
    with pytest.raises(PublicExportsFormatError, match="modules must be"):
        parse_public_exports(json.dumps(empty_document).encode())

    evil_document = json.loads(valid)
    # Keep the classification list sorted and aligned with the modules mapping
    # so validation reaches the module-name refusal rather than an earlier
    # shape or consistency check.
    evil_document["internal_modules"] = sorted(
        "dotmac_kernel_evil" if module == "dotmac_kernel._transactions" else module
        for module in evil_document["internal_modules"]
    )
    evil_document["modules"]["dotmac_kernel_evil"] = evil_document["modules"].pop(
        "dotmac_kernel._transactions"
    )
    with pytest.raises(PublicExportsFormatError, match="must be kernel names"):
        parse_public_exports(json.dumps(evil_document).encode())


def test_source_derivation_rejects_malformed_all(tmp_path: Path) -> None:
    planted = tmp_path / "dotmac_kernel"
    shutil.copytree(SOURCE_ROOT, planted)
    identity = planted / "identity.py"
    source = identity.read_text()
    identity.write_text(
        source.replace(
            "__all__ = [", '__all__ = ["normalize_email", "normalize_email",', 1
        )
    )

    with pytest.raises(PublicExportsFormatError, match="duplicate __all__"):
        derive_public_exports(planted)

    identity.write_text(source + '\n__all__ = ["normalize_email"]\n')
    with pytest.raises(
        PublicExportsFormatError, match="declares __all__ more than once"
    ):
        derive_public_exports(planted)


def test_source_derivation_rejects_unresolved_export_name(tmp_path: Path) -> None:
    planted = tmp_path / "dotmac_kernel"
    shutil.copytree(SOURCE_ROOT, planted)
    identity = planted / "identity.py"
    identity.write_text(
        identity.read_text().replace(
            '"normalize_email",', '"not_defined",\n    "normalize_email",', 1
        )
    )
    with pytest.raises(PublicExportsFormatError, match="not defined"):
        derive_public_exports(planted)


def test_lazy_root_exports_resolve_from_the_getattr_comparison_not_prose(
    tmp_path: Path,
) -> None:
    planted = tmp_path / "dotmac_kernel"
    shutil.copytree(SOURCE_ROOT, planted)
    init = planted / "__init__.py"
    init.write_text(
        init.read_text().replace(
            '"create_app",',
            '"prose_only_name",\n    "create_app",',
            1,
        )
        + '\n"prose_only_name appears in prose but is not a binding"\n'
    )

    with pytest.raises(PublicExportsFormatError, match="prose_only_name"):
        derive_public_exports(planted)


def test_lazy_root_export_must_return_a_name_bound_in_its_branch(
    tmp_path: Path,
) -> None:
    planted = tmp_path / "dotmac_kernel"
    shutil.copytree(SOURCE_ROOT, planted)
    init = planted / "__init__.py"
    init.write_text(
        init.read_text().replace(
            "        return create_app",
            "        raise AttributeError(name)",
            1,
        )
    )

    with pytest.raises(PublicExportsFormatError, match="create_app"):
        derive_public_exports(planted)


def test_lazy_root_export_rejects_an_unreachable_import_and_return(
    tmp_path: Path,
) -> None:
    planted = tmp_path / "dotmac_kernel"
    shutil.copytree(SOURCE_ROOT, planted)
    init = planted / "__init__.py"
    init.write_text(
        init.read_text().replace(
            '    if name == "create_app":\n',
            '    if name == "create_app":\n        raise AttributeError(name)\n',
            1,
        )
    )

    with pytest.raises(PublicExportsFormatError, match="create_app"):
        derive_public_exports(planted)


def test_annotation_without_a_value_is_not_a_runtime_export(tmp_path: Path) -> None:
    planted = tmp_path / "dotmac_kernel"
    shutil.copytree(SOURCE_ROOT, planted)
    identity = planted / "identity.py"
    identity.write_text(
        identity.read_text().replace(
            '"normalize_email",', '"ghost",\n    "normalize_email",', 1
        )
        + "\nghost: str\n"
    )

    with pytest.raises(PublicExportsFormatError, match="ghost"):
        derive_public_exports(planted)
