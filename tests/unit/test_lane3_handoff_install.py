"""Offline installation contracts; root lifecycle runs only in hosted CI."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import pathlib
import stat
import sys
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_legacy_closure_is_prior_evidence_and_cannot_be_a_new_target():
    legacy = manifest()
    legacy["runtime"].pop("lane3_campaign_bootstrap.py")
    assert set(legacy["runtime"]) == set(hi.LEGACY_RUNTIME_MODULES)
    hi.validate_manifest(legacy, allow_legacy_runtime=True)
    with pytest.raises(hi.InstallRefused):
        hi.validate_manifest(legacy)
    incomplete = copy.deepcopy(legacy)
    incomplete["runtime"].pop("lane3_handoff_coordinator.py")
    with pytest.raises(hi.InstallRefused):
        hi.validate_manifest(incomplete, allow_legacy_runtime=True)


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
    assert " -I -B /opt/dotmac-lane3-handoff/" in rendered
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


def test_unit_is_wanted_not_required_by_the_boot_target():
    rendered = hi.unit_bytes("a" * 64, "nftables.service").decode()
    assert rendered.endswith("[Install]\nWantedBy=multi-user.target\n")
    assert "RequiredBy" not in rendered
    # Ordering is unchanged and the start timeout stays bounded.
    assert "Before=multi-user.target\n" in rendered
    assert "TimeoutStartSec=45\n" in rendered


def test_unit_runs_the_dispatcher_without_bytecode():
    rendered = hi.unit_bytes("a" * 64, "nftables.service").decode()
    assert (
        "ExecStart=/usr/bin/python3 -I -B /opt/dotmac-lane3-handoff/" + "a" * 64
    ) in rendered
    template = (
        ROOT / "deploy/lane3-handoff/dotmac-lane3-handoff-reconcile.service.in"
    ).read_text()
    assert "ExecStart=/usr/bin/python3 -I -B /opt/" in template
    assert "WantedBy=multi-user.target" in template and "RequiredBy" not in template


def test_uninstall_is_an_administrative_command_not_a_management_op():
    # The coordinator's permitted surface is exactly the dispatcher op set.
    assert "uninstall" not in hi.MANAGEMENT_OPS
    with pytest.raises(hi.InstallRefused):
        hi.management_operation(["uninstall"])
    source = inspect.getsource(hi.main)
    assert 'argv[1:] == ["uninstall"]' in source


def test_uninstall_checks_refusals_before_any_mutation():
    source = inspect.getsource(hi.Installer.uninstall)
    for guard in ("self.no_lease()", "self.no_transaction()", "self.verify_record("):
        assert guard in source
        assert source.index(guard) < source.index("write_file(")
    assert source.index("write_file(") < source.index("self.stop_reconcile_unit()")
    assert source.index("self.stop_reconcile_unit()") < source.index("unlink(")
    assert source.index("daemon-reload") < source.index("remove_partial_tree(")
    assert source.index("remove_partial_tree(") < source.index(
        "self.record() != record"
    )
    assert source.rindex("uninstall_readback(record)") > source.index(
        "uninstall_record_removed"
    )


def test_uninstall_never_touches_firewall_or_accounts():
    source = inspect.getsource(hi.Installer.uninstall)
    for forbidden in ("nft", "userdel", "useradd", "rmtree", "shutil"):
        assert forbidden not in source


@pytest.mark.parametrize(
    ("target_kind", "owner", "accepted"),
    [
        ("relative", 0, True),
        ("absolute", 0, True),
        ("foreign", 0, False),
        ("relative", 1000, False),
        ("absolute", 1000, False),
    ],
)
def test_wants_link_validation_preserves_only_exact_root_owned_links(
    monkeypatch, target_kind, owner, accepted
):
    installer = hi.Installer(hi.Paths(prefix=pathlib.Path("/synthetic-host")))
    link = installer.paths.wants
    target = {
        "relative": "../" + hi.UNIT_NAME,
        "absolute": str(installer.paths.unit),
        "foreign": "/nonexistent-synthetic-target",
    }[target_kind]
    # Model only the descriptor boundary; exercise the actual ownership and
    # exact-target decision without requiring root or touching systemd.
    descriptor = 17
    filesystem = SimpleNamespace(
        stat=Mock(return_value=SimpleNamespace(st_mode=stat.S_IFLNK, st_uid=owner)),
        readlink=Mock(return_value=target),
        unlink=Mock(),
        fsync=Mock(),
        close=Mock(),
    )
    parent = Mock(return_value=descriptor)
    monkeypatch.setattr(hi, "exists", lambda path: path == link)
    monkeypatch.setattr(hi, "directory", parent)
    monkeypatch.setattr(hi, "os", filesystem)
    if accepted:
        installer.remove_wants_link(remove=False)
    else:
        with pytest.raises(hi.InstallRefused, match="installation.drift"):
            installer.remove_wants_link(remove=False)
    parent.assert_called_once_with(link.parent)
    filesystem.stat.assert_called_once_with(
        link.name, dir_fd=descriptor, follow_symlinks=False
    )
    filesystem.readlink.assert_called_once_with(link.name, dir_fd=descriptor)
    filesystem.close.assert_called_once_with(descriptor)
    filesystem.unlink.assert_not_called()
    filesystem.fsync.assert_not_called()
    if accepted:
        installer.remove_wants_link()
        filesystem.unlink.assert_called_once_with(link.name, dir_fd=descriptor)
        filesystem.fsync.assert_called_once_with(descriptor)
