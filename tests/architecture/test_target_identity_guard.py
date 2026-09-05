"""A target host reaches this facility through the invocation, never the artifact.

Ruling 3. The census this facility carries is pointed at named production hosts;
those names belong in the operator's command line and in no compiled artifact.

## Why the two sensitivity controls are BOTH load-bearing

A guard that refused every address literal would pass "a planted production
literal is refused" perfectly, and would be useless: rendering needs a wildcard
bind, and the operator's own legitimate invocation must survive. So this file
asserts the refusal AND the permission, and treats an over-broad guard as a
failure in its own right (`test_private_and_documentation_space_are_told_apart`,
`test_a_runtime_injected_target_passes`).

## Why the artifact is scanned and not only the tree

`dotmac_observability`'s comparable guard iterates `SRC.glob("*.py")` — top
level, source only, Python only. Three blind spots, each covered here by a test
that fails against that shape: `test_the_scan_is_recursive`,
`test_scripts_templates_and_config_are_inspected`, and
`test_a_member_present_only_in_the_artifact_is_still_inspected` — the last being
the one a tree scan can never reach, because what executes on a host is the
wheel and the wheel is not the checkout.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.ingress import refuse_address_literal
from dotmac_deployment_foundation.target_identity_guard import (
    EMBEDDED_TARGET_DEBT,
    ESTATE_SUFFIXES,
    INSPECTED_SUFFIXES,
    LOOPBACK_CONSTANTS,
    EmbeddedTargetError,
    TargetIdentityFinding,
    check_debt,
    ledger_key,
    require_no_embedded_target,
    scan_archive,
    scan_text,
    scan_tree,
)
from dotmac_deployment_foundation.vantage import PRIVATE_RANGES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = PROJECT_ROOT / "packages" / "dotmac-deployment-foundation"

# A globally routable address, used ONLY as a planted defect. It is not an
# estate host: it is IANA-reserved documentation space's opposite number, chosen
# from a range that is routable so the test proves the guard bites on the shape
# that matters. Written as octets so this file does not itself carry a literal
# the guard would have to refuse if the ledger ever covered tests/.
_PLANTED_V4 = ".".join(("198", "18", "7", "42"))
_PLANTED_V6 = "2400:cb00:2049:1::a29f:1804"


def _kinds(findings) -> set[str]:
    return {finding.kind for finding in findings}


def _values(findings) -> set[str]:
    return {finding.value for finding in findings}


# ── Sensitivity control 1: a planted production literal refuses ─────────────


def test_a_planted_production_ipv4_literal_is_refused():
    """The defect this guard exists for, planted. Fails before the module existed."""
    findings = scan_text("provider.py", f'TARGET = "{_PLANTED_V4}"\n')
    assert _values(findings) == {_PLANTED_V4}
    assert _kinds(findings) == {"address-as-identity"}


def test_a_planted_production_ipv6_literal_is_refused():
    """IPv6 is not an afterthought, and this test earned its keep.

    The first cut of `_V6` was a hand-written address grammar. It missed BOTH a
    full global address and a unique-local one while looking entirely
    plausible, and both read as clean. The pattern now finds a hex-and-colon
    RUN and lets `ipaddress` decide, which is what the v4 rule already did.
    """
    findings = scan_text("provider.py", f'TARGET = "{_PLANTED_V6}"\n')
    assert _kinds(findings) == {"address-as-identity"}
    assert _values(findings) == {_PLANTED_V6}


@pytest.mark.parametrize(
    "planted",
    [
        "2a00:1450:4009:81f::200e",  # global, ends in a hex group
        "fd00::1",  # unique-local: one hex group before the elision
        "2400:cb00:2049:1::a29f:1804",  # global, elision mid-address
    ],
)
def test_the_ipv6_shapes_that_the_first_pattern_missed(planted: str):
    """Each of these walked straight through the hand-written grammar."""
    assert scan_text("x.py", f'A = "{planted}"\n'), f"{planted} was not refused"


@pytest.mark.parametrize(
    "text",
    [
        "t = data[1:2:3]\n",  # a slice
        'x = "sha256:abcd:ef01"\n',  # a digest-ish prefix
        "at 12:30:45 UTC\n",  # a timestamp
        "https://example.com:8080/a\n",  # an authority with a port
        "key: value\n",  # ordinary mapping syntax
    ],
)
def test_colon_shapes_that_are_not_addresses_are_not_findings(text: str):
    """The near-miss surface the loosened v6 candidate pattern creates.

    Widening the pattern to a hex-and-colon run is only safe because
    `ipaddress` rejects every one of these. If the parser ever stopped being
    the decider, this is what would start crying wolf.
    """
    assert scan_text("x.py", text) == []


def test_an_ipv4_mapped_ipv6_address_is_still_refused():
    """`::ffff:<v4>` is a routable target wearing a v6 spelling. It is reported
    under the v4 rule because the embedded quad matches first; which rule names
    it does not matter, that it is named does."""
    assert scan_text("x.py", f'A = "::ffff:{_PLANTED_V4}"\n')


def test_a_planted_estate_hostname_is_refused():
    suffix = ESTATE_SUFFIXES[0]
    findings = scan_text("provider.py", f'TARGET = "erp.{suffix}"\n')
    assert _kinds(findings) == {"estate-hostname"}


def test_an_estate_hostname_wearing_a_file_extension_is_still_refused():
    """Regression. `endswith` walked past `selfcare.<estate>.conf` and the
    ledger is what caught it: the count for `render/nginx.py` came back one
    short of the literals actually in the file. Suffix matching is dot-BOUNDED
    containment for exactly this reason."""
    suffix = ESTATE_SUFFIXES[0]
    findings = scan_text("render/nginx.py", f"ported from selfcare.{suffix}.conf\n")
    assert _kinds(findings) == {
        "estate-hostname"
    }, "a hostname followed by a file extension is still a hostname"


# ── Sensitivity control 2: a runtime-injected target passes ─────────────────


def test_a_runtime_injected_target_passes():
    """The control that stops this guard being 'refuse every address'.

    A provider that takes its target as an argument is the SHAPE the ruling
    requires, and it must be clean. If this ever fails, the guard has started
    refusing the legitimate invocation and the operator will get an exemption
    written for it within the week.
    """
    source = (
        "def inspect(target: str, *, port: int) -> None:\n"
        '    """Target arrives from the authorized invocation, never a literal."""\n'
        "    connect(host=target, port=port)\n"
    )
    assert scan_text("provider.py", source) == []


def test_permitted_addresses_state_premises_that_ipaddress_can_decide():
    """Each permission is decided structurally, not by spelling."""
    permitted = [
        "0.0.0.0",  # noqa: S104 -- the point: a bind is not a host
        "::",  # unspecified, v6
        *LOOPBACK_CONSTANTS,  # the two exact constants, never the range
        "192.0.2.10",  # RFC 5737 documentation space
        "198.51.100.10",
        "203.0.113.10",
        "2001:db8::1",  # RFC 3849 documentation space
    ]
    for value in permitted:
        assert (
            scan_text("x.py", f'A = "{value}"\n') == []
        ), f"{value} should be permitted"


def test_private_and_documentation_space_are_told_apart():
    """The near-miss for the permission, and the one most likely to be widened.

    RFC 1918 is NOT documentation space. A `10.x` is a reachable estate target
    -- this package's own `vantage.py` records a probe host whose second NIC
    routed into a private network -- so permitting private space to quieten the
    ledger would hollow the guard out while leaving every test green.
    """
    private = ["10.0.0.4", "172.16.4.4", "192.168.1.10", "fd00::1"]
    for value in private:
        assert scan_text("x.py", f'A = "{value}"\n'), f"{value} must not be permitted"


def test_a_version_string_is_not_mistaken_for_an_address():
    """Near-miss: four dotted numbers that are not an address, and one that is
    out of octet range. Validity is `ipaddress`'s decision, not the pattern's."""
    assert scan_text("x.py", 'V = "1.2.3.4.5"\n') == []
    assert scan_text("x.py", 'V = "999.1.1.1"\n') == []
    assert scan_text("x.py", "t = data[1:2]\n") == []


def test_an_unrelated_public_domain_is_not_an_estate_host():
    """The over-breadth near-miss for the hostname rule. An 'any FQDN' rule
    would refuse the index, the registry and every schema URL."""
    for host in ("pypi.org", "ghcr.io", "json-schema.org", "example.com"):
        assert scan_text("x.py", f'URL = "https://{host}/a"\n') == [], host


# ── Coverage shape: the three blind spots in the guard this replaces ────────


def test_the_scan_is_recursive(tmp_path: Path):
    """`SRC.glob('*.py')` cannot see one directory down. `rglob` can."""
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "deep.py").write_text(f'TARGET = "{_PLANTED_V4}"\n', encoding="utf-8")
    findings = scan_tree(tmp_path)
    assert [f.where for f in findings] == ["a/b/c/deep.py"]


def test_scripts_templates_and_config_are_inspected(tmp_path: Path):
    """A literal in a packaged template ships and executes exactly like one in
    a module. A Python-only scan reports on a document nobody runs."""
    for name in ("run.sh", "unit.service", "nginx.conf", "compose.yml", "site.j2"):
        (tmp_path / name).write_text(f"server {_PLANTED_V4};\n", encoding="utf-8")
    findings = scan_tree(tmp_path)
    assert {f.where for f in findings} == {
        "run.sh",
        "unit.service",
        "nginx.conf",
        "compose.yml",
        "site.j2",
    }


def test_every_inspected_suffix_is_actually_reachable(tmp_path: Path):
    """An entry in INSPECTED_SUFFIXES that the walker skips is a lie in a
    constant. Each is proven to carry a planted literal out."""
    for index, suffix in enumerate(INSPECTED_SUFFIXES):
        (tmp_path / f"f{index}{suffix}").write_text(
            f"x {_PLANTED_V4}\n", encoding="utf-8"
        )
    findings = scan_tree(tmp_path)
    assert len(findings) == len(INSPECTED_SUFFIXES)


# ── The artifact, which a tree scan can never reach ─────────────────────────


def _wheel_with(tmp_path: Path, members: dict[str, str]) -> Path:
    wheel = tmp_path / "dotmac_deployment_foundation-0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return wheel


def test_a_planted_literal_in_the_built_artifact_is_refused(tmp_path: Path):
    wheel = _wheel_with(
        tmp_path, {"dotmac_deployment_foundation/provider.py": f'T = "{_PLANTED_V4}"\n'}
    )
    findings = scan_archive(wheel)
    assert _values(findings) == {_PLANTED_V4}


def test_a_member_present_only_in_the_artifact_is_still_inspected(tmp_path: Path):
    """THE property that makes artifact scanning necessary rather than tidy.

    A build step that generates or vendors a file puts it in the wheel without
    ever putting it in the checkout. A tree scan is structurally blind to it,
    and it is the member most likely to carry a baked-in host, because nobody
    reviewed it as source.
    """
    tree = tmp_path / "tree"
    (tree / "dotmac_deployment_foundation").mkdir(parents=True)
    (tree / "dotmac_deployment_foundation" / "clean.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    assert scan_tree(tree) == [], "the tree is clean, which is the whole point"

    wheel = _wheel_with(
        tmp_path,
        {
            "dotmac_deployment_foundation/clean.py": "x = 1\n",
            "dotmac_deployment_foundation/_generated.py": f'HOST = "{_PLANTED_V4}"\n',
        },
    )
    findings = scan_archive(wheel)
    assert [f.where for f in findings] == ["dotmac_deployment_foundation/_generated.py"]


def test_a_non_utf8_member_is_reported_rather_than_counted_clean(tmp_path: Path):
    """A member the scanner could not read is not a member with nothing in it.

    Same discipline the attribution battery applies to a refused walk: the one
    thing a scan may never do is turn "could not look" into zero.
    """
    wheel = tmp_path / "w.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "dotmac_deployment_foundation/data.json", b"\xff\xfe\x00binary"
        )
    findings = scan_archive(wheel)
    assert _kinds(findings) == {"unreadable"}


# ── The ledger ─────────────────────────────────────────────────────────────


def test_the_package_tree_matches_the_debt_ledger():
    """The live assertion. Every literal in the package is either absent or
    frozen at an exact count with a comment saying why it is still there."""
    complaints = check_debt(scan_tree(PACKAGE_DIR))
    assert complaints == [], "\n".join(complaints)


def test_the_ratchet_fails_when_debt_RISES():
    where = next(iter(EMBEDDED_TARGET_DEBT))
    allowed = EMBEDDED_TARGET_DEBT[where]
    findings = [
        TargetIdentityFinding(where, 1, "ipv4-literal", _PLANTED_V4)
        for _ in range(allowed + 1)
    ]
    complaints = check_debt(findings)
    assert any("ledger allows" in complaint for complaint in complaints)


def test_the_ratchet_fails_when_debt_FALLS_without_being_lowered():
    """The second direction, and the one usually left out. A count that drifts
    down through unrelated edits leaves a ledger that has stopped describing
    the tree, and the next real literal hides under the slack."""
    complaints = check_debt([])
    assert complaints, "an empty scan against a non-empty ledger must fail"
    assert all("still claims" in complaint for complaint in complaints)


def test_a_path_with_no_ledger_entry_fails_immediately():
    finding = TargetIdentityFinding("src/new_module.py", 1, "ipv4-literal", _PLANTED_V4)
    assert any("new_module" in c for c in check_debt([finding]))


def test_require_no_embedded_target_raises_with_every_complaint_at_once():
    with pytest.raises(EmbeddedTargetError) as excinfo:
        require_no_embedded_target([])
    assert str(excinfo.value).count("- ") >= len(EMBEDDED_TARGET_DEBT)


# ── The exemption ──────────────────────────────────────────────────────────


def test_the_estate_suffix_exemption_states_an_enforceable_premise():
    """The detector may spell the suffix because it matches on it. That premise
    is only true while the name lives in `ESTATE_SUFFIXES` -- if it ever becomes
    a stray literal, the exemption has stopped describing anything."""
    package = PACKAGE_DIR / "src" / "dotmac_deployment_foundation"
    module = package / "target_identity_guard.py"
    text = module.read_text(encoding="utf-8")
    for suffix in ESTATE_SUFFIXES:
        assert f'"{suffix}"' in text
    assert "ESTATE_SUFFIXES: tuple[str, ...] = (" in text


def test_the_exemption_covers_hostnames_only_and_not_addresses():
    """The near-miss for the exemption: a blanket file exemption would let an
    address hide in the detector. It already caught one in its own docstring."""
    findings = scan_text("target_identity_guard.py", f'A = "{_PLANTED_V4}"\n')
    assert _kinds(findings) == {"ipv4-literal"}


# ── The narrowed loopback ruling ───────────────────────────────────────────


def test_only_the_two_exact_loopback_constants_are_permitted():
    """`is_loopback` would admit all of `127.0.0.0/8`. The ruling is narrower:
    two exact constants, because those are what the typed exposure policy
    declares and anything else in that range is an address somebody chose."""
    assert set(LOOPBACK_CONSTANTS) == {"127.0.0.1", "::1"}
    for permitted in LOOPBACK_CONSTANTS:
        assert scan_text("ingress.py", f'LOOPBACK = "{permitted}"\n') == []


@pytest.mark.parametrize("refused", ["127.0.0.53", "127.1.2.3", "127.0.0.2", "::2"])
def test_the_rest_of_the_loopback_range_is_refused(refused: str):
    """The near-miss that a `is_loopback` rule would have let through."""
    assert scan_text("x.py", f'A = "{refused}"\n'), f"{refused} was permitted"


@pytest.mark.parametrize(
    "line",
    [
        'target = "127.0.0.1"',
        'vantage_host = "127.0.0.1"',
        'TARGET: str = "127.0.0.1"',
        'probe_host = "::1"',
        'inspect_host = "127.0.0.1"',
    ],
)
def test_a_permitted_constant_may_still_not_IDENTIFY_a_target(line: str):
    """ "The target defaults to localhost" is a default, and there are none.

    This is checked BEFORE the permission rather than after, so the loopback
    exemption cannot be the thing that hides it.
    """
    findings = scan_text("provider.py", line + "\n")
    assert _kinds(findings) == {"address-as-identity"}


@pytest.mark.parametrize(
    "line",
    [
        'LOOPBACK = {"ipv4": "127.0.0.1", "ipv6": "::1"}',
        'loopback: str = "127.0.0.1"',
        'bind = "127.0.0.1"',
    ],
)
def test_the_exposure_policy_may_still_declare_loopback(line: str):
    """The near-miss for the rule above. If declaring loopback in the exposure
    policy started failing, the guard would have made the legitimate use
    impossible and an exemption would be written for it within the week."""
    assert scan_text("ingress.py", line + "\n") == []


# ── CIDR policy constants are topology, not identity ───────────────────────


@pytest.mark.parametrize(
    "network",
    [
        *PRIVATE_RANGES,
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "2001:db8::/32",
    ],
)
def test_exact_policy_and_documentation_networks_are_permitted(network: str):
    """Only the owner-defined private ranges and IANA examples are topology."""
    assert (
        scan_text(
            "src/dotmac_deployment_foundation/vantage.py",
            f'PRIVATE_RANGES = ("{network}",)\n',
        )
        == []
    )


def test_a_private_policy_network_is_not_an_exemption_in_another_module():
    assert scan_text("provider.py", f'NETWORK = "{PRIVATE_RANGES[0]}"\n')


def test_an_arbitrary_multi_address_cidr_is_refused():
    assert scan_text("x.py", 'NETWORK = "100.64.0.0/10"\n')


def test_a_cidr_bound_as_target_or_vantage_is_refused_before_permission():
    for binding in ("target", "vantage"):
        findings = scan_text("x.py", f'{binding} = "{PRIVATE_RANGES[0]}"\n')
        assert _kinds(findings) == {"address-as-identity"}


@pytest.mark.parametrize(
    "host", ["10.0.0.4/32", "198.18.7.42/32", "2001:4860:4860::8888/128"]
)
def test_a_single_address_prefix_is_a_host_wearing_a_prefix(host: str):
    """The near-miss for the rule above, and the way it would be abused: a /32
    is not a network, it is one address with a suffix stuck on it."""
    assert scan_text("x.py", f'A = "{host}"\n'), f"{host} was permitted"


@pytest.mark.parametrize("cidr", ["100.64.0.0/10", "192.0.2.0/24"])
def test_product_descriptor_cidr_is_still_refused(cidr: str):
    with pytest.raises(SpecError):
        refuse_address_literal(cidr, field="source_set", where="<test>")


def test_expanded_loopback_prose_has_no_debt_entry():
    assert "src/dotmac_deployment_foundation/ingress.py" not in EMBEDDED_TARGET_DEBT


def test_the_prefix_must_be_adjacent_to_the_address():
    """An unrelated `/24` elsewhere on the line may not launder a host literal."""
    planted = _PLANTED_V4
    assert scan_text("x.py", f'A = "{planted}"  # see the /24 above\n')


# ── The estate-address ruling, asserted directly ───────────────────────────


@pytest.mark.parametrize("module", ["lease.py", "vantage.py"])
def test_the_relocated_modules_carry_no_embedded_identity(module: str):
    """Ruling: both real addresses come out of the reusable wheel. Their
    provenance is preserved in `docs/inventories/`, outside the artifact."""
    path = PACKAGE_DIR / "src" / "dotmac_deployment_foundation" / module
    where = f"src/dotmac_deployment_foundation/{module}"
    assert scan_text(where, path.read_text(encoding="utf-8")) == []


def test_the_relocated_provenance_record_exists_and_names_both_modules():
    """Relocation, not deletion. If the record vanishes, the addresses were
    deleted rather than moved and two design decisions became uncheckable."""
    record = (
        PROJECT_ROOT
        / "docs"
        / "inventories"
        / "deployment-foundation-measurement-provenance.md"
    )
    assert record.is_file(), "the provenance was deleted rather than relocated"
    text = record.read_text(encoding="utf-8")
    assert "lease.py" in text and "vantage.py" in text


def test_no_module_supplies_a_default_target_or_vantage():
    """The load-bearing half of the ruling. A module that stops NAMING an
    address but keeps a fallback to one has moved the problem, not fixed it."""
    package = PACKAGE_DIR / "src" / "dotmac_deployment_foundation"
    offenders = [
        f"{path.name}:{finding.line}"
        for path in sorted(package.rglob("*.py"))
        for finding in scan_text(path.name, path.read_text(encoding="utf-8"))
        if finding.kind == "address-as-identity"
    ]
    assert not offenders, f"a target or vantage identity has a default: {offenders}"


# ── One ledger key for three spellings ─────────────────────────────────────


def test_one_ledger_key_covers_tree_wheel_and_sdist_spellings():
    """A ledger keyed by only the tree spelling would silently allow the other
    two, which is exactly what the artifact scan exists to close."""
    tree = "src/dotmac_deployment_foundation/lease.py"
    wheel = "dotmac_deployment_foundation/lease.py"
    sdist = (
        "dotmac-deployment-foundation-0.3.0a1/src/dotmac_deployment_foundation/lease.py"
    )
    assert ledger_key(tree) == ledger_key(wheel) == ledger_key(sdist) == tree


def test_a_root_level_member_keeps_its_own_name():
    assert ledger_key("EXTRACTION.toml") == "EXTRACTION.toml"
    assert (
        ledger_key("dotmac-deployment-foundation-0.3.0a1/EXTRACTION.toml")
        == "EXTRACTION.toml"
    )


# ── Both artifact kinds, and every member of them ──────────────────────────


def _sdist_with(tmp_path: Path, members: dict[str, str]) -> Path:
    import io
    import tarfile

    root = "dotmac-deployment-foundation-0.3.0a1"
    path = tmp_path / f"{root}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, text in members.items():
            raw = text.encode("utf-8")
            info = tarfile.TarInfo(f"{root}/src/{name}")
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return path


def test_an_sdist_is_scanned_as_well_as_a_wheel(tmp_path: Path):
    """An sdist is a perfectly good way to ship a literal that never reaches a
    wheel. Scanning only the wheel would leave that route open."""
    sdist = _sdist_with(
        tmp_path, {"dotmac_deployment_foundation/x.py": f'T = "{_PLANTED_V4}"\n'}
    )
    assert _values(scan_archive(sdist)) == {_PLANTED_V4}


@pytest.mark.parametrize(
    "member",
    [
        "dotmac_deployment_foundation/render/site.j2",
        "dotmac_deployment_foundation/data/collector.json",
        "dotmac_deployment_foundation/conf/nginx.conf",
        "dotmac_deployment_foundation/data/hosts",
        "dotmac_deployment_foundation/scripts/run",
    ],
)
def test_a_literal_in_NON_PYTHON_package_data_is_caught_in_the_artifact(
    tmp_path: Path, member: str
):
    """THE case the ruling singles out.

    A scanner that walks `*.py` inside a wheel and calls it done is the shape
    already found in `dotmac_observability`'s guard. A packaged template, a
    config file and a plain data file with no extension at all ship and are
    read on the host exactly as a module is. Note the last two names: an
    extension allowlist would miss both.
    """
    wheel = _wheel_with(tmp_path, {member: f"endpoint: {_PLANTED_V4}:4317\n"})
    findings = scan_archive(wheel)
    assert [f.where for f in findings] == [member]


def test_an_archive_the_scanner_cannot_open_is_refused_not_reported_clean(
    tmp_path: Path,
):
    """An artifact nobody could inspect is not an artifact with nothing in it."""
    bogus = tmp_path / "not-an-archive.whl"
    bogus.write_bytes(b"this is not a zip or a tar")
    with pytest.raises(EmbeddedTargetError):
        scan_archive(bogus)
