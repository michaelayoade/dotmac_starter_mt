"""Parser/evidence canaries only; these mocks never prove an installed floor."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "module_floor_probe", Path(__file__).parents[2] / "scripts/module_floor_probe.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.mark.parametrize(
    "entry",
    [
        "",
        "a",
        "a:b:c:d:e",
        "a:b::typo",
        "a:b:c,",
        "a:b::",
        "packages/a:dotmac_a:unrelated.service",
    ],
)
def test_malformed_entries_fail(entry):
    with pytest.raises(ValueError):
        probe.parse_entry(entry)


def test_default_and_explicit_modes():
    assert probe.parse_entry("packages/a:dotmac_a")[3] == "manifest"
    assert probe.parse_entry("packages/a:dotmac_learning::registry")[3] == "registry"
    assert (
        probe.parse_entry("packages/a:dotmac_fulfillment::tenant-audit")[3]
        == "tenant-audit"
    )
    for entry in (
        "packages/a:dotmac_fulfillment::registry",
        "packages/a:dotmac_learning::tenant-audit",
    ):
        with pytest.raises(ValueError):
            probe.parse_entry(entry)


@pytest.mark.parametrize(
    "symbol,owner",
    [("OTHER", "dotmac_kernel.prerequisites"), ("TENANT_AUDIT_LOG_V1", "other")],
)
def test_wrong_missing_symbol_is_not_a_registry_refusal(symbol, owner):
    exc = ImportError(f"cannot import name '{symbol}' from '{owner}'")
    assert not probe.audit_refusal(exc)


def test_exact_audit_refusal():
    assert probe.audit_refusal(
        ImportError(
            "cannot import name 'TENANT_AUDIT_LOG_V1' "
            "from 'dotmac_kernel.prerequisites' (/venv/p.py)"
        )
    )
    assert not probe.audit_refusal(AttributeError("TENANT_AUDIT_LOG_V1"))


def test_origin_outside_venv_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="origin"):
        probe.check_origin(
            tmp_path / "checkout/pkg.py", tmp_path / "venv", tmp_path / "checkout"
        )


def test_version_mismatch_is_rejected():
    with pytest.raises(ValueError, match="version"):
        probe.check_version("1", "2", "module")


@pytest.mark.parametrize(
    "change",
    ["tables", "owner", "platform", "registration", "catalog", "namespace", "copy"],
)
def test_registry_evidence_sensitivity(change):
    module = SimpleNamespace(
        code="learning",
        short_code="learning",
        migration_prefix="lr",
        migration_branch="learning",
        tables=tuple(probe.LEARNING_TABLES),
        platform_tables=(),
    )
    owner = SimpleNamespace(
        owner="learning", db_schema="mod_learning", prefix="lr", branch_label="learning"
    )
    namespaces = SimpleNamespace(
        get=lambda code: owner,
        declared_tables=lambda schema: frozenset(module.tables),
        declared_platform_tables=lambda schema: frozenset(),
    )
    registry = SimpleNamespace(get=lambda code: module, namespaces=lambda: namespaces)
    if change == "tables":
        module.tables = ()
    elif change == "owner":
        owner.prefix = "wrong"
    elif change == "platform":
        namespaces.declared_platform_tables = lambda schema: frozenset({"stray"})
    elif change == "catalog":
        namespaces.declared_tables = lambda schema: frozenset({"stray"})
    elif change == "namespace":
        registry.namespaces = lambda: object()
    elif change == "copy":
        registry.get = lambda code: SimpleNamespace(**vars(module))
    else:
        registry.get = lambda code: None
    with pytest.raises(ValueError):
        probe.check_registration(module, registry, SimpleNamespace)


def test_registry_evidence_uses_public_kernel_namespace_method():
    from dotmac_kernel.modules import ModuleManifest, ModuleRegistry
    from dotmac_kernel.namespaces import NamespaceRegistry

    module = ModuleManifest(
        code="learning",
        version="0.1.0a1",
        core=False,
        short_code="learning",
        migration_prefix="lr",
        migration_branch="learning",
        tables=tuple(sorted(probe.LEARNING_TABLES)),
    )
    registry = ModuleRegistry((module,))
    assert probe.check_registration(module, registry, NamespaceRegistry) == {
        "owner": "learning",
        "schema": "mod_learning",
        "prefix": "lr",
        "branch": "learning",
        "tables": sorted(probe.LEARNING_TABLES),
        "platform_tables": [],
    }


def test_noisy_or_broad_status_cannot_pass():
    for result in ({"status": "REFUSED", "reason": "anything"}, {"status": "OK"}):
        with pytest.raises(ValueError):
            probe.validate_evidence(result, "REFUSED", "registry", "dotmac_learning")


def test_full_refusal_evidence_rejects_wrong_symbol_or_missing_allocation():
    result = {
        "status": "REFUSED",
        "reason": "missing TENANT_AUDIT_LOG_V1",
        "kernel_version": "0.1.0a105",
        "module_version": "0.1.0a1",
        "mode": "registry",
        "origins": {
            "dotmac_kernel": "/v/site-packages/k.py",
            "dotmac_learning": "/v/site-packages/l.py",
            "dotmac_learning_bank_rules": "/v/site-packages/pure/__init__.py",
        },
        "pure_provenance": {
            "distribution": "dotmac-learning",
            "record": "dotmac_learning_bank_rules/__init__.py",
        },
        "module_wheel_sha256": "a" * 64,
        "kernel_wheel_sha256": "b" * 64,
        "kernel_tag_commit": "c" * 40,
        "allocation": {"present": False, "rows": []},
        "refusal": {
            "type": "ImportError",
            "message": "cannot import name 'OTHER' from 'dotmac_kernel.prerequisites'",
        },
    }
    with pytest.raises(ValueError, match="unrelated"):
        probe.validate_evidence(result, "REFUSED", "registry", "dotmac_learning")
    result["refusal"]["message"] = (
        "cannot import name 'TENANT_AUDIT_LOG_V1' from 'dotmac_kernel.prerequisites'"
    )
    probe.validate_evidence(result, "REFUSED", "registry", "dotmac_learning")
    del result["allocation"]
    with pytest.raises(ValueError, match="ledger"):
        probe.validate_evidence(result, "REFUSED", "registry", "dotmac_learning")


def test_site_packages_named_directory_outside_real_install_root_is_rejected(tmp_path):
    venv = tmp_path / "venv"
    with pytest.raises(ValueError, match="origin"):
        probe.check_origin(
            venv / "arbitrary/site-packages/pkg.py", venv, tmp_path / "checkout"
        )


@pytest.mark.parametrize("status", ["OK", "REFUSED"])
@pytest.mark.parametrize("missing", ["origin", "record", "distribution"])
@pytest.mark.parametrize("mode", ["manifest", "registry"])
def test_learning_receipt_requires_pure_provenance(status, missing, mode):
    result = {
        "status": status,
        "reason": "installed manifest loaded",
        "kernel_version": "0.1.0a105",
        "module_version": "0.1.0a1",
        "mode": "manifest",
        "origins": {
            "dotmac_kernel": "/v/site-packages/kernel/__init__.py",
            "dotmac_learning": "/v/site-packages/learning/__init__.py",
            "dotmac_learning_bank_rules": "/v/site-packages/pure/__init__.py",
        },
        "pure_provenance": {
            "distribution": "dotmac-learning",
            "record": "dotmac_learning_bank_rules/__init__.py",
        },
        "module_wheel_sha256": "a" * 64,
        "kernel_wheel_sha256": "b" * 64,
        "kernel_tag_commit": "c" * 40,
    }
    result["mode"] = mode
    if mode == "registry":
        result["allocation"] = {"present": status == "OK", "rows": []}
        if status == "OK":
            result["registration"] = {}
        else:
            result["reason"] = "missing TENANT_AUDIT_LOG_V1"
            result["refusal"] = {
                "type": "ImportError",
                "message": "cannot import name 'TENANT_AUDIT_LOG_V1' "
                "from 'dotmac_kernel.prerequisites'",
            }
    probe.validate_evidence(result, status, mode, "dotmac_learning")
    if missing == "origin":
        del result["origins"]["dotmac_learning_bank_rules"]
    else:
        del result["pure_provenance"][missing]
    with pytest.raises(ValueError, match="pure"):
        probe.validate_evidence(result, status, mode, "dotmac_learning")
    # Other module receipt contracts remain unchanged.
    result["mode"] = "manifest"
    result["origins"]["dotmac_other"] = "/v/site-packages/other/__init__.py"
    probe.validate_evidence(result, status, "manifest", "dotmac_other")


@pytest.mark.parametrize(
    "change",
    ["none", "outside", "checkout", "unrecorded", "duplicate", "no-record", "owner"],
)
def test_pure_origin_requires_learning_installed_record(tmp_path, monkeypatch, change):
    venv, checkout = tmp_path / "venv", tmp_path / "checkout"
    site = venv / "lib/site-packages"
    monkeypatch.setattr(probe.sysconfig, "get_path", lambda key, vars: str(site))
    entry = "dotmac_learning_bank_rules/__init__.py"
    origin = site / entry
    record = f"{entry},,\n"
    owner = "dotmac-learning"
    if change == "outside":
        origin = tmp_path / "source" / entry
    elif change == "checkout":
        checkout = site / "source"
        origin = checkout / entry
    elif change == "unrecorded":
        record = "dotmac_learning/__init__.py,,\n"
    elif change == "duplicate":
        record *= 2
    elif change == "no-record":
        record = None
    elif change == "owner":
        owner = "unrelated-distribution"
    distribution = SimpleNamespace(
        metadata={"Name": owner},
        read_text=lambda name: record,
        locate_file=lambda path: site / path,
    )
    if change == "none":
        assert probe.check_learning_pure_origin(
            origin, distribution, venv, checkout
        ) == (
            str(origin),
            entry,
        )
    else:
        with pytest.raises(ValueError):
            probe.check_learning_pure_origin(origin, distribution, venv, checkout)


@pytest.mark.parametrize("refused", [False, True])
@pytest.mark.parametrize("child", ["none", "outside", "unrecorded"])
def test_installed_learning_records_pure_before_root_refusal(
    tmp_path, monkeypatch, refused, child
):
    venv, checkout = tmp_path / "venv", tmp_path / "checkout"
    site = venv / "lib/site-packages"
    monkeypatch.setattr(probe, "sys", SimpleNamespace(prefix=str(venv), modules={}))
    monkeypatch.setattr(probe.sysconfig, "get_path", lambda key, vars: str(site))
    monkeypatch.setattr(probe, "digest", lambda path: "a" * 64)
    monkeypatch.setattr(
        probe,
        "wheel_metadata",
        lambda path: {
            "Name": "dotmac-kernel" if path == "kernel.whl" else "dotmac-learning",
            "Version": "1",
        },
    )
    entry = "dotmac_learning_bank_rules/__init__.py"
    monkeypatch.setattr(
        probe.importlib.metadata,
        "distribution",
        lambda name: SimpleNamespace(
            version="1",
            metadata={"Name": name},
            locate_file=lambda path: site / path,
            read_text=lambda name: f"{entry},,\n",
        ),
    )
    monkeypatch.setattr(
        probe.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(origin=str(site / name / "__init__.py")),
    )
    imports = []
    manifest = SimpleNamespace(version="1", requires=("tenant_audit_log.v1",))

    def import_module(name):
        imports.append(name)
        if name == "dotmac_learning" and refused:
            raise ImportError(
                "cannot import name 'TENANT_AUDIT_LOG_V1' "
                "from 'dotmac_kernel.prerequisites'"
            )
        return SimpleNamespace(
            __name__=name,
            __file__=str(site / name.replace(".", "/") / "__init__.py"),
            __version__="1",
            module=manifest,
            TENANT_AUDIT_LOG_V1=SimpleNamespace(name="tenant_audit_log.v1"),
            PrerequisiteError=RuntimeError,
            MIGRATION_OWNER_LEDGER=(),
        )

    monkeypatch.setattr(probe.importlib, "import_module", import_module)
    # A controlled module census avoids treating test-runner imports as proof.
    if child != "none":
        child_root = tmp_path / "source" if child == "outside" else site
        probe.sys.modules["dotmac_learning_bank_rules.stray"] = SimpleNamespace(
            __file__=str(child_root / "dotmac_learning_bank_rules/stray.py")
        )
    args = SimpleNamespace(
        name="dotmac_learning",
        distribution="dotmac-learning",
        kernel_version="1",
        module_version="1",
        kernel_wheel="kernel.whl",
        module_wheel="learning.whl",
        commit="c" * 40,
        checkout=str(checkout),
        mode="registry" if refused else "manifest",
        extra="",
    )
    if child != "none":
        with pytest.raises(ValueError, match="origin"):
            probe.installed_probe(args)
        return
    result = probe.installed_probe(args)
    expected = "REFUSED" if refused else "OK"
    assert result["status"] == expected
    assert imports.index("dotmac_learning_bank_rules") < imports.index(
        "dotmac_learning"
    )
    assert result["origins"]["dotmac_learning_bank_rules"] == str(site / entry)
    assert result["pure_provenance"] == {
        "distribution": "dotmac-learning",
        "record": entry,
    }
    probe.validate_evidence(result, expected, args.mode, args.name)
