"""Installed-source graph canaries; hosted only, no replay claim."""

from dataclasses import replace
from pathlib import Path
from shutil import copytree

import pytest
from dotmac_kernel.migrations import identity_history as history
from dotmac_kernel.migrations import versions_dir
from dotmac_kernel.modules import ModuleRegistry
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_AUDIT_LOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    PrerequisiteBinding,
    install_prerequisite_bindings,
    installed_bindings,
)

from alembic.script import ScriptDirectory
from alembic.util import CommandError


@pytest.fixture(autouse=True)
def isolated_prerequisite_globals():
    previous = installed_bindings()
    install_prerequisite_bindings(())
    try:
        yield
    finally:
        install_prerequisite_bindings(previous)


class _RecordedHeads:
    def __init__(self, heads):
        self.heads = heads

    def scalars(self, statement):
        assert str(statement) == "SELECT version_num FROM public.alembic_version"
        return self.heads


@pytest.fixture
def installed_graph():
    directory = versions_dir()
    scripts = ScriptDirectory(str(directory.parent), version_locations=[str(directory)])
    head = scripts.get_heads()[0]
    older = scripts.get_revision(head).down_revision
    assert isinstance(older, str)
    return head, older


def _assembly(tmp_path: Path, dependency: str, *, branch="assembly") -> Path:
    location = tmp_path / "assembly_versions"
    location.mkdir()
    (location / "a001_identity_extensions.py").write_text(
        f'revision = "a001_identity_extensions"\n'
        f"down_revision = None\nbranch_labels = ({branch!r},)\n"
        f"depends_on = {dependency!r}\n"
        "def upgrade():\n    pass\ndef downgrade():\n    pass\n"
    )
    return location


def test_installed_kernel_literal_head_still_supported(installed_graph):
    head, _ = installed_graph
    history._installed_kernel_coverage(_RecordedHeads({head}), head, None)


def test_assembly_dependency_covers_consumed_kernel_head(tmp_path, installed_graph):
    head, _ = installed_graph
    assembly = _assembly(tmp_path, head)
    # Only assembly is recorded: no invented/stamped Kernel head row.
    history._installed_kernel_coverage(
        _RecordedHeads({"a001_identity_extensions"}), head, assembly
    )


def test_older_dependency_cannot_cover_installed_kernel_head(tmp_path, installed_graph):
    head, older = installed_graph
    assembly = _assembly(tmp_path, older)
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(
            _RecordedHeads({"a001_identity_extensions"}), head, assembly
        )


@pytest.mark.parametrize("live_heads", [set(), {"unknown-live-revision"}])
def test_missing_or_unknown_live_heads_refused(installed_graph, live_heads):
    head, _ = installed_graph
    with pytest.raises((history.IdentityHistoryImportRefused, CommandError)):
        history._installed_kernel_coverage(_RecordedHeads(live_heads), head, None)


def test_installed_source_head_mismatch_refused(installed_graph):
    head, older = installed_graph
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(_RecordedHeads({head}), older, None)


@pytest.mark.parametrize("alias", ["a001", "assembly@head"])
def test_partial_and_symbolic_live_rows_are_not_revision_ids(
    tmp_path, installed_graph, alias
):
    head, _ = installed_graph
    assembly = _assembly(tmp_path, head)
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(_RecordedHeads({alias}), head, assembly)


@pytest.mark.parametrize("branch", ["kernel", "unallocated_host"])
def test_nonassembly_owner_cannot_supply_host_lineage(
    tmp_path, installed_graph, branch
):
    head, _ = installed_graph
    assembly = _assembly(tmp_path, head, branch=branch)
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(
            _RecordedHeads({"a001_identity_extensions"}), head, assembly
        )


def test_unknown_dependency_fails_real_composition_gate(tmp_path, installed_graph):
    head, _ = installed_graph
    assembly = _assembly(tmp_path, "not-an-installed-revision")
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(
            _RecordedHeads({"a001_identity_extensions"}), head, assembly
        )


def test_kernel_location_cannot_be_reused_as_assembly(installed_graph):
    head, _ = installed_graph
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(_RecordedHeads({head}), head, versions_dir())


def test_copied_kernel_sources_cannot_substitute_assembly(tmp_path, installed_graph):
    head, _ = installed_graph
    copied = tmp_path / "copied_kernel"
    copytree(versions_dir(), copied)
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(_RecordedHeads({head}), head, copied)


def test_assembly_down_revision_and_dependency_edges_both_walked(
    tmp_path, installed_graph
):
    head, _ = installed_graph
    assembly = _assembly(tmp_path, head)
    (assembly / "a002_later.py").write_text(
        'revision = "a002_later"\n'
        'down_revision = "a001_identity_extensions"\n'
        "branch_labels = None\ndepends_on = None\n"
        "def upgrade():\n    pass\ndef downgrade():\n    pass\n"
    )
    history._installed_kernel_coverage(_RecordedHeads({"a002_later"}), head, assembly)


@pytest.fixture
def learning_graph(tmp_path, installed_graph):
    # Genuine installed module source, including audit9's fourth requirement.
    # These tests fail if the containing coupled artifacts were not installed.
    from dotmac_learning.manifest import module
    from dotmac_learning.migrations import versions_dir as learning_versions

    head, _ = installed_graph
    assert head == "0030_credential_tenant_key"
    assert set(module.requires) == {
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
        PARTY_PERSON_CATALOG_V1.name,
        TENANT_AUDIT_LOG_V1.name,
    }
    registry = ModuleRegistry((replace(module, enabled_by_default=False),))
    assert registry.enabled_codes() == frozenset()
    assert registry.startup_order()[0].code == "learning"
    bindings = tuple(
        PrerequisiteBinding(name, head, "kernel") for name in module.requires
    )
    install_prerequisite_bindings(bindings)
    assembly = _assembly(tmp_path, head)
    (assembly / "a002_later.py").write_text(
        'revision="a002_later"\ndown_revision="a001_identity_extensions"\n'
        "branch_labels=None\ndepends_on=None\n"
        "def upgrade():\n    pass\ndef downgrade():\n    pass\n"
    )
    return head, assembly, registry, {"learning": learning_versions()}, bindings


@pytest.mark.parametrize("assembly_head", ["a001_identity_extensions", "a002_later"])
def test_real_learning_graph_with_whole_assembly_and_unapplied_future_source(
    learning_graph,
    assembly_head,
):
    head, assembly, registry, locations, bindings = learning_graph
    history._installed_kernel_coverage(
        _RecordedHeads({assembly_head, "lr_0006_delivery_support"}),
        head,
        assembly,
        registry,
        locations,
        bindings,
    )


@pytest.mark.parametrize(
    "mapping", ["missing", "extra", "wrong_owner", "overlap", "symlink", "nested"]
)
def test_module_source_locations_must_exactly_match_installed_owners(
    learning_graph,
    tmp_path,
    mapping,
):
    head, assembly, registry, locations, bindings = learning_graph
    if mapping == "missing":
        locations = {}
    elif mapping == "extra":
        locations = {**locations, "not_installed": assembly}
    elif mapping == "wrong_owner":
        wrong = tmp_path / "wrong_owner"
        copytree(assembly, wrong)
        locations = {"learning": wrong}
    elif mapping == "overlap":
        locations = {"learning": assembly}
    elif mapping == "nested":
        nested = assembly / "nested_module"
        copytree(locations["learning"], nested)
        locations = {"learning": nested}
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(assembly, target_is_directory=True)
        locations = {"learning": alias}
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(
            _RecordedHeads({"a001_identity_extensions", "lr_0006_delivery_support"}),
            head,
            assembly,
            registry,
            locations,
            bindings,
        )


@pytest.mark.parametrize("global_state", ["absent", "different"])
def test_global_bindings_must_match_before_any_script_loading(
    learning_graph,
    monkeypatch,
    global_state,
):
    head, assembly, registry, locations, bindings = learning_graph
    installed = (
        ()
        if global_state == "absent"
        else (
            replace(bindings[0], provider_revision="0001_initial_tenant_schema"),
            *bindings[1:],
        )
    )
    install_prerequisite_bindings(installed)

    def forbidden_script_loading(*args, **kwargs):
        pytest.fail("ScriptDirectory reached before binding agreement")

    monkeypatch.setattr(history, "ScriptDirectory", forbidden_script_loading)
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(
            _RecordedHeads({"a001_identity_extensions", "lr_0006_delivery_support"}),
            head,
            assembly,
            registry,
            locations,
            bindings,
        )


def test_binding_order_is_normalized_without_installing_globals(learning_graph):
    head, assembly, registry, locations, bindings = learning_graph
    before = installed_bindings()
    history._installed_kernel_coverage(
        _RecordedHeads({"a001_identity_extensions", "lr_0006_delivery_support"}),
        head,
        assembly,
        registry,
        locations,
        tuple(reversed(bindings)),
    )
    assert installed_bindings() == before


def test_duplicate_explicit_binding_refused_before_script_loading(
    learning_graph, monkeypatch
):
    head, assembly, registry, locations, bindings = learning_graph

    def forbidden_script_loading(*args, **kwargs):
        pytest.fail("ScriptDirectory reached with duplicate explicit binding")

    monkeypatch.setattr(history, "ScriptDirectory", forbidden_script_loading)
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(
            _RecordedHeads({"a001_identity_extensions", "lr_0006_delivery_support"}),
            head,
            assembly,
            registry,
            locations,
            (*bindings, bindings[0]),
        )


def test_script_loading_cannot_change_caller_bindings(learning_graph, monkeypatch):
    head, assembly, registry, locations, bindings = learning_graph
    actual_directory = history.ScriptDirectory

    def changing_directory(*args, **kwargs):
        result = actual_directory(*args, **kwargs)
        result.get_heads()
        install_prerequisite_bindings(())
        return result

    monkeypatch.setattr(history, "ScriptDirectory", changing_directory)
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(
            _RecordedHeads({"a001_identity_extensions", "lr_0006_delivery_support"}),
            head,
            assembly,
            registry,
            locations,
            bindings,
        )


@pytest.mark.parametrize("alias", ["lr_0006", "learning@head", "unknown_live_revision"])
def test_module_live_rows_must_be_exact_real_revision_ids(learning_graph, alias):
    head, assembly, registry, locations, bindings = learning_graph
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._installed_kernel_coverage(
            _RecordedHeads({"a001_identity_extensions", alias}),
            head,
            assembly,
            registry,
            locations,
            bindings,
        )
