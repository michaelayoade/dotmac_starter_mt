"""Offline installation contracts; root lifecycle runs only in hosted CI."""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import lane3_handoff_install as hi  # noqa: E402


def manifest():
    return {
        "schema": hi.BUNDLE_SCHEMA,
        "runtime_commit": "a" * 40,
        "supplier_commit": hi.SUPPLIER_COMMIT,
        "runtime": {name: "b" * 64 for name in hi.RUNTIME_MODULES},
        "supplier": dict(hi.SUPPLIER_HASHES),
    }


def test_bundle_requires_exact_original_supplier_and_complete_runtime():
    hi.validate_manifest(manifest())
    for field in ("runtime", "supplier"):
        changed = manifest()
        changed[field].pop(next(iter(changed[field])))
        with pytest.raises(hi.InstallRefused):
            hi.validate_manifest(changed)
    changed = manifest()
    changed["supplier"][next(iter(changed["supplier"]))] = "c" * 64
    with pytest.raises(hi.InstallRefused):
        hi.validate_manifest(changed)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]"])
def test_strict_json_rejects_ambiguous_records(raw):
    with pytest.raises(hi.InstallRefused):
        hi.strict_json(raw)


def test_configuration_binding_preserves_independent_admission():
    admission = b'{"independently":"reviewed"}\n'
    modules = dict(hi.SUPPLIER_HASHES)
    installation = {
        "schema": "dotmac.lane3.supplier-installation.v1",
        "starter_commit": hi.SUPPLIER_COMMIT,
        "admission_digest": hashlib.sha256(admission).hexdigest(),
        "supplier_digest": hi.digest(hi.canonical(modules)),
        "modules": modules,
    }
    hi.validate_supplier_installation(installation, admission)
    bad = copy.deepcopy(installation)
    bad["admission_digest"] = "0" * 64
    with pytest.raises(hi.InstallRefused):
        hi.validate_supplier_installation(bad, admission)


def test_management_surface_has_no_caller_path_or_shell():
    assert hi.management_operation(["prepare"]) == "prepare"
    for args in (["prepare", "/untrusted/config"], ["serve", "1" * 32], ["shell"], []):
        with pytest.raises(hi.InstallRefused):
            hi.management_operation(args)


def test_unit_requires_real_explicit_firewall_owner_and_fixed_dispatcher():
    rendered = hi.unit_bytes("a" * 64, "nftables.service").decode()
    assert "Requires=nftables.service" in rendered
    assert "After=local-fs.target nftables.service" in rendered
    assert " -I /opt/dotmac-lane3-handoff/" in rendered
    assert "boot-reconcile" in rendered
    assert "RemainAfterExit=yes" in rendered
    for unit in ("", "../nftables.service", "nftables.service\nExecStart=/bin/sh"):
        with pytest.raises(hi.InstallRefused):
            hi.unit_bytes("a" * 64, unit)


def test_readiness_binds_current_boot_and_complete_installation():
    record = {"bundle_digest": "a" * 64, "config_digest": "b" * 64}
    receipt = hi.readiness(record, "synthetic-boot")
    assert hi.valid_readiness(receipt, record, "synthetic-boot")
    assert not hi.valid_readiness(receipt, record, "other-boot")
    changed = dict(record, config_digest="c" * 64)
    assert not hi.valid_readiness(receipt, changed, "synthetic-boot")


def test_canonical_digest_is_order_independent():
    assert hi.digest(hi.canonical({"a": 1, "b": 2})) == hi.digest(
        hi.canonical({"b": 2, "a": 1})
    )
    assert json.loads(hi.canonical(manifest())) == manifest()


def inputs():
    return {
        "schema": hi.INPUT_SCHEMA,
        "baseline_unit": "nftables.service",
        "admission": {
            "repository": "synthetic/admission",
            "path": "config/admission.json",
            "commit": "a" * 40,
            "sha256": "b" * 64,
        },
        "files": {name: "b" * 64 for name in hi.CONFIG_FILES},
    }


def test_installation_input_never_accepts_approval_or_caller_file_names():
    installer = hi.Installer()
    installer.validate_input(inputs())
    for name in ("approval.json", "../host.json", "token.json"):
        changed = inputs()
        changed["files"][name] = "b" * 64
        with pytest.raises(hi.InstallRefused):
            installer.validate_input(changed)


def test_installation_input_requires_exact_independent_admission_binding():
    installer = hi.Installer()
    for key, value in (("sha256", "c" * 64), ("path", "../admission.json")):
        changed = inputs()
        changed["admission"][key] = value
        with pytest.raises(hi.InstallRefused):
            installer.validate_input(changed)
    changed = inputs()
    changed["admission"]["token"] = "synthetic-forbidden-field"
    with pytest.raises(hi.InstallRefused):
        installer.validate_input(changed)


def test_installed_record_cannot_expand_the_deletion_authority():
    bundle, config = manifest(), inputs()
    bundle_digest = hi.digest(hi.canonical(bundle))
    record = {
        "schema": hi.RECORD_SCHEMA,
        "bundle_digest": bundle_digest,
        "config_digest": hi.digest(hi.canonical(config)),
        "manifest": bundle,
        "input": config,
        "config_files": {
            **config["files"],
            **{"supplier/" + name: value for name, value in hi.SUPPLIER_HASHES.items()},
        },
        "unit_sha256": hi.digest(hi.unit_bytes(bundle_digest, "nftables.service")),
    }
    hi.Installer().validate_record_value(record)
    record["config_files"]["../../unrelated"] = "b" * 64
    with pytest.raises(hi.InstallRefused):
        hi.Installer().validate_record_value(record)
