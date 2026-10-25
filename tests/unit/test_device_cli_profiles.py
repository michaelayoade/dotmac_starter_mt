"""Layered profiles, registry, detection, dialects and fail-closed operations."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotmac_device_cli import (
    DetectOutcome,
    NoProfile,
    OntRef,
    ProfileError,
    ProfileRegistry,
    Reason,
    RefError,
    Unreadable,
    Unsupported,
    builtin_registry,
)
from dotmac_device_cli.core.dialect import dialect_for, registered_dialects
from dotmac_device_cli.core.layers import discover, merge, parse_layer, resolve_chain
from dotmac_device_cli.core.profile import CommandRejected, check_line, render

REGISTRY = builtin_registry()
MA5608T = REGISTRY.get("huawei-ma5608t-v800r013")
MA5800 = REGISTRY.get("huawei-ma5800-x2-v100r019")
assert MA5608T is not None and MA5800 is not None


def test_the_shipped_layers_resolve_to_two_profiles() -> None:
    assert sorted(p.id for p in REGISTRY.profiles) == [
        "huawei-ma5608t-v800r013",
        "huawei-ma5800-x2-v100r019",
    ]
    assert MA5608T.layer_path == ("huawei", "ma56xx", "ma5608t", "v800r013")


def test_paging_off_is_unsupported_on_both_models() -> None:
    assert not MA5608T.paging.off_supported
    assert not MA5800.paging.off_supported
    assert MA5608T.paging.off_command == "unsupported"


def test_a_layer_overrides_only_what_differs() -> None:
    # inherited from the vendor layer
    assert MA5608T.prompts == MA5800.prompts
    assert MA5608T.logout == MA5800.logout
    # family and model layers override
    assert MA5608T.limits.session_slots == 2 and MA5800.limits.session_slots == 4
    assert (
        MA5608T.send.inter_command_delay == 0.1
        and MA5800.send.inter_command_delay == 0.0
    )
    assert (
        MA5608T.read_templates["ont_info_all"].command
        != MA5800.read_templates["ont_info_all"].command
    )
    # a firmware layer adds quirks without touching the rest
    assert "spaced_fsp" in MA5608T.quirks and "spaced_fsp" not in MA5800.quirks
    assert (
        "paging_off_unproven" in MA5608T.quirks
        and "paging_off_unproven" in MA5800.quirks
    )


def test_merge_is_a_deep_ordered_merge() -> None:
    assert merge({"a": {"x": 1, "y": [1]}, "b": 1}, {"a": {"y": [2]}, "c": 3}) == {
        "a": {"x": 1, "y": [2]},
        "b": 1,
        "c": 3,
    }


def test_operations_are_declared_per_profile_and_undeclared_is_unsupported() -> None:
    assert MA5608T.claims("list_onts") and MA5608T.claims("version")
    assert not MA5608T.claims("ont_detail")
    assert not MA5608T.claims("not_an_operation")


def _layer(kind: str, extra: str = "", lid: str = "x") -> str:
    return f'[layer]\nid = "{lid}"\nkind = "{kind}"\n{extra}'


@pytest.mark.parametrize(
    "text, kind",
    [
        ('[layer]\nid="x"\nkind="vendor"\n[bogus]\na=1\n', "vendor"),
        (_layer("vendor", "[ssh]\nnot_a_key = 1\n"), "vendor"),
        (_layer("firmware", '[profile]\nvendor = "evil"\n'), "firmware"),
        (_layer("model", '[profile]\nfirmware_patterns = ["x"]\n'), "model"),
        ('[layer]\nid="x"\nkind="model"\n', "vendor"),  # kind mismatch
        ('[layer]\nkind = "vendor"\n', "vendor"),  # no id
        ("not toml [", "vendor"),
    ],
)
def test_unknown_or_misplaced_layer_keys_fail(text: str, kind: str) -> None:
    with pytest.raises(ProfileError):
        parse_layer(text, kind=kind, path=("t",))


def _copy_tree(tmp_path: Path) -> Path:
    import shutil

    root = (
        Path(__file__).resolve().parents[2]
        / "packages/dotmac-device-cli/src/dotmac_device_cli/profiles"
    )
    dest = tmp_path / "profiles"
    shutil.copytree(root, dest)
    return dest


def test_the_merged_profile_is_validated_in_full(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    fw = root / "huawei/ma56xx/ma5608t/v800r013.toml"
    fw.write_text(
        fw.read_text().replace('status = "prod_observed"', 'status = "lab_verified"')
    )
    with pytest.raises(ProfileError, match="receipt"):
        discover(root)


def test_a_template_with_an_unknown_placeholder_is_refused(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    fam = root / "huawei/ma56xx/family.toml"
    fam.write_text(fam.read_text() + 'bad = "display ont {nope}"\n')
    with pytest.raises(ProfileError, match="placeholder"):
        discover(root)


def test_an_unknown_operation_name_is_refused(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    fw = root / "huawei/ma56xx/ma5608t/v800r013.toml"
    fw.write_text(fw.read_text().replace("[operations]", "[operations]\nreboot = true"))
    with pytest.raises(ProfileError, match="not a known operation"):
        discover(root)


def test_a_missing_vendor_layer_is_refused(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    (root / "huawei/vendor.toml").unlink()
    with pytest.raises(ProfileError, match="missing vendor.toml"):
        discover(root)


def test_a_chain_must_be_vendor_family_model_firmware() -> None:
    with pytest.raises(ProfileError):
        resolve_chain(())


def test_registry_resolves_by_configured_vendor_model_firmware() -> None:
    assert REGISTRY.resolve("huawei", "MA5608T", "V800R013C10") is MA5608T
    assert REGISTRY.resolve("Huawei", "MA5800-X2", "V100R019C10") is MA5800
    for miss in (
        ("huawei", "MA5608T", "V800R011"),  # firmware not covered
        ("huawei", "MA5800-X7", "V100R019"),  # model not covered (data to add)
        ("zte", "C300", "V4.0"),
    ):
        assert isinstance(REGISTRY.resolve(*miss), NoProfile)


def test_ambiguous_profiles_are_an_error() -> None:
    twin = ProfileRegistry((MA5608T, MA5608T))
    with pytest.raises(ProfileError, match="ambiguous"):
        twin.resolve("huawei", "MA5608T", "V800R013")


def test_detection_reports_and_never_switches_profile() -> None:
    ok = "  VERSION : MA5608TV800R013C10\n  PRODUCT : MA5608T\n"
    assert REGISTRY.detect(MA5608T, ok).outcome is DetectOutcome.AGREES
    wrong_fw = "  VERSION : MA5608TV800R011C10\n  PRODUCT : MA5608T\n"
    d = REGISTRY.detect(MA5608T, wrong_fw)
    assert d.outcome is DetectOutcome.MISMATCH and "firmware" in d.detail
    other_model = "  VERSION : MA5800V100R019C10\n  PRODUCT : MA5800-X7\n"
    assert REGISTRY.detect(MA5800, other_model).outcome is DetectOutcome.MISMATCH
    assert REGISTRY.detect(MA5608T, "garbage").outcome is DetectOutcome.UNDETERMINED


# ── dialects and OntRef ──────────────────────────────────────────────────────


def test_huawei_ref_round_trip_and_tolerance() -> None:
    d = dialect_for("huawei")
    assert d is not None
    ref = d.parse_ref("0/ 2/1 5")
    assert (ref.shelf, ref.slot, ref.port, ref.ont_id) == (0, 2, 1, 5)
    assert ref.native == "0/2/1 5" and d.parse_ref(ref.native) == ref
    assert d.parse_ref("0/2/1:5") == ref
    port = d.parse_ref("0/2/1")
    assert port.is_port and port.native == "0/2/1"
    for bad in ("0/2", "gpon-onu_0/1/2:3", "", "0/2/1/9"):
        with pytest.raises(RefError):
            d.parse_ref(bad)


def test_zte_stub_proves_a_second_addressing_scheme() -> None:
    d = dialect_for("zte")
    assert d is not None
    onu = d.parse_ref("gpon-onu_1/2/3:4")
    assert (onu.shelf, onu.slot, onu.port, onu.ont_id) == (1, 2, 3, 4)
    assert onu.native == "gpon-onu_1/2/3:4" and d.parse_ref(onu.native) == onu
    olt = d.parse_ref("gpon-olt_1/2/3")
    assert olt.is_port and olt.native == "gpon-olt_1/2/3"
    with pytest.raises(RefError):
        d.parse_ref("0/2/1")
    # it declares no capabilities and invents no command
    assert d.operations() == frozenset()
    with pytest.raises(KeyError):
        d.compose("list_onts", MA5608T, olt)
    assert sorted(registered_dialects()) == ["huawei", "zte"]


def test_a_neutral_ref_keeps_the_vendor_native_string() -> None:
    ref = OntRef("huawei", 0, 2, 1, 7, "0/2/1 7")
    assert ref.native == "0/2/1 7" and not ref.is_port


def test_operations_fail_closed_with_unsupported() -> None:
    from dotmac_device_cli import CliSession, list_onts, ont_detail, service_ports
    from dotmac_device_cli.testing import FakeClock, ReplayChannel

    clock = FakeClock()
    session = CliSession.attach(ReplayChannel(clock), MA5608T, clock=clock)
    huawei = dialect_for("huawei")
    zte = dialect_for("zte")
    assert huawei is not None and zte is not None
    ont = huawei.parse_ref("0/2/1 5")
    port = huawei.parse_ref("0/2/1")
    assert isinstance(ont_detail(session, ont), Unsupported)  # not declared
    assert isinstance(service_ports(session, ont), Unsupported)
    # a reference from another dialect never reaches a command
    assert isinstance(list_onts(session, zte.parse_ref("gpon-olt_0/2/1")), Unsupported)
    # a port operation refuses an ONT reference
    assert isinstance(list_onts(session, ont), Unsupported)
    unprepared = list_onts(session, port)  # supported, but the session is not primed
    assert (
        isinstance(unprepared, Unreadable)
        and unprepared.reason is Reason.UNRECOGNIZED_RESPONSE
    )


def test_a_profile_naming_the_zte_stub_can_claim_nothing() -> None:
    import dataclasses

    from dotmac_device_cli import CliSession, list_onts
    from dotmac_device_cli.testing import FakeClock, ReplayChannel

    zte_profile = dataclasses.replace(
        MA5608T, id="zte-test", dialect="zte", operations={"list_onts": True}
    )
    clock = FakeClock()
    session = CliSession.attach(ReplayChannel(clock), zte_profile, clock=clock)
    zte = dialect_for("zte")
    assert zte is not None
    out = list_onts(session, zte.parse_ref("gpon-olt_0/2/1"))
    assert isinstance(out, Unsupported)  # claimed, but the dialect cannot do it


def test_rendered_lines_are_typed_and_rejected_not_stripped() -> None:
    tpl = MA5608T.read_templates["ont_info_all"]
    assert render(tpl, MA5608T, frame=0, slot=2, port=1) == "display ont info 0 2 1 all"
    for bad in ("0;reboot", "1 2", "a", "-1", "1\n2"):
        with pytest.raises(CommandRejected):
            render(tpl, MA5608T, frame=bad, slot=2, port=1)
    with pytest.raises(CommandRejected):
        render(tpl, MA5608T, frame=0, slot=2)  # missing parameter
    for line in ("a;b", "a\nb", "café", "", "x" * 201):
        with pytest.raises(CommandRejected):
            check_line(line, max_length=200)
