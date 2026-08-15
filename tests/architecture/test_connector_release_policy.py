"""A connector may be released only after it has proved it conforms.

The third release lane, and the one whose subject is discovered rather than
called. `.github/release-connectors.json` is the policy;
`scripts/release_connector.py` is the gate that fails closed on it; this file is
the proof the gate bites, because a gate nobody has driven with a violation is
an assertion about a file.

Three layers, as in the module and adapter lanes, and each fails differently:

1. `release_connector.py resolve` refuses an unlisted distribution, an entry
   carrying stateful facts, an incomplete entry, and — the check this lane adds
   — a floor no installer can resolve.
2. `conformance` checks statically what can be checked before installation.
3. `verify-wheel` runs the shipped SPI kit against the INSTALLED bytes.

**The lane is shut.** `connectors` is `{}` and no connector distribution exists,
so every proof below plants a synthetic entry rather than pointing at a real
package. That is deliberate: absence is the safety mechanism, and a guard tested
only against the entries that happen to exist stops being a guard the moment the
first one is added. It also means these tests cannot rot into "the lane is
empty, so everything passes".
"""

from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = PROJECT_ROOT / ".github" / "release-connectors.json"
SCRIPT = PROJECT_ROOT / "scripts" / "release_connector.py"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-connector.yml"
LEDGER = PROJECT_ROOT / "docs" / "inventories" / "declared-publication-baseline.json"


def _gate():
    spec = importlib.util.spec_from_file_location("release_connector", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


# A synthetic connector entry: everything the policy requires, nothing it
# forbids. Each proof below breaks exactly one thing about it, so a refusal can
# only be attributed to the thing that was broken.
def _valid_entry() -> dict:
    return {
        "package_dir": "packages/dotmac-connector-example",
        "import_name": "dotmac_connector_example",
        "plugin_attr": "PLUGIN",
        "connector_key": "example.v1",
        "spi_range": ">=1.0,<2.0",
        "integration_floor": "0.1.0a1",
        "tag_prefix": "dotmac-connector-example-v",
        "wheel_contents": {"required": [], "forbidden_prefixes": []},
    }


@pytest.fixture
def lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The gate, pointed at a synthetic repository with one planted connector.

    Returns a callable taking allowlist overrides, so each proof states only the
    difference it is testing.
    """
    gate = _gate()
    package = tmp_path / "packages" / "dotmac-connector-example"
    (package / "src" / "dotmac_connector_example").mkdir(parents=True)
    (package / "pyproject.toml").write_text(
        "[tool.poetry]\n"
        'name = "dotmac-connector-example"\n'
        'version = "0.1.0a1"\n'
        "[tool.poetry.dependencies]\n"
        'dotmac-integration = ">=0.1.0a1"\n'
        '[tool.poetry.plugins."dotmac_integration.connectors"]\n'
        '"example.v1" = "dotmac_connector_example:PLUGIN"\n',
        encoding="utf-8",
    )
    (package / "EXTRACTION.toml").write_text(
        'classification = "connector-plugin"\n', encoding="utf-8"
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    # The synthetic repository has no tags of its own. Publication is supplied
    # rather than read from git so each proof states the release history it
    # depends on — the whole point of the floor check is a version that exists
    # in a checkout and nowhere else, which a checkout cannot demonstrate.
    monkeypatch.setattr(
        gate, "git_tags", lambda *_args, **_kwargs: ["dotmac-integration-v0.1.0a1"]
    )

    def configure(entry: dict | None = None, *, connectors: dict | None = None):
        policy = _policy()
        if connectors is None:
            connectors = {} if entry is None else {"dotmac-connector-example": entry}
        policy["connectors"] = connectors
        allowlist = tmp_path / "release-connectors.json"
        allowlist.write_text(json.dumps(policy), encoding="utf-8")
        monkeypatch.setattr(gate, "ALLOWLIST", allowlist)
        return gate, package

    return configure


# ── The gate refuses ─────────────────────────────────────────────────────────


def test_an_unlisted_connector_cannot_be_resolved(lane) -> None:
    """SENSITIVITY PROOF. Without it, "the allowlist gates the lane" is a claim
    about a file nobody proved is consulted."""
    gate, _ = lane(None)
    for impostor in (
        "dotmac-integration",  # the control plane, not a connector
        "dotmac-connector-example",  # real shape, not listed
        "../../etc/passwd",
        "",
    ):
        with pytest.raises(SystemExit) as refusal:
            gate.resolve(impostor, tags={"dotmac-integration-v0.1.0a1"})
        assert "not an allowlisted connector plugin" in str(refusal.value)


def test_a_listed_connector_resolves(lane) -> None:
    """SPECIFICITY for the test above: the gate must refuse because the entry is
    absent, not because it refuses everything."""
    gate, _ = lane(_valid_entry())
    resolved = gate.resolve(
        "dotmac-connector-example", tags={"dotmac-integration-v0.1.0a1"}
    )
    assert resolved["connector_key"] == "example.v1"


def test_an_entry_carrying_stateful_facts_is_refused(lane) -> None:
    """A package with a schema, a manifest attribute or a kernel floor is a
    MODULE. Publishing it here would skip every namespace, lineage and
    dual-plane gate the module lane performs."""
    for field, value in (
        ("db_schema", "mod_example"),
        ("manifest_attr", "module"),
        ("kernel_floor", "0.1.0a61"),
    ):
        gate, _ = lane({**_valid_entry(), field: value})
        with pytest.raises(SystemExit) as refusal:
            gate.resolve(
                "dotmac-connector-example", tags={"dotmac-integration-v0.1.0a1"}
            )
        assert "STATEFUL facts" in str(refusal.value)
        assert field in str(refusal.value)


def test_an_incomplete_entry_is_refused_field_by_field(lane) -> None:
    """Every field is required. An absent one would be read downstream as
    "unknown" rather than "refused" — the optionality trap the adapter lane
    already records about the module lane."""
    for field in _gate().REQUIRED_FIELDS:
        entry = _valid_entry()
        del entry[field]
        gate, _ = lane(entry)
        with pytest.raises(SystemExit) as refusal:
            gate.resolve(
                "dotmac-connector-example", tags={"dotmac-integration-v0.1.0a1"}
            )
        assert field in str(refusal.value), field


def test_a_wrong_classification_is_refused(lane, tmp_path: Path) -> None:
    """The lane is tied to the governed CLASSIFICATION, read from the package,
    not to a name chosen in the allowlist."""
    gate, package = lane(_valid_entry())
    (package / "EXTRACTION.toml").write_text(
        'classification = "optional-module"\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit) as refusal:
        gate.resolve("dotmac-connector-example", tags={"dotmac-integration-v0.1.0a1"})
    assert "classification" in str(refusal.value)


# ── The floor must be installable, not merely declared ──────────────────────


def test_a_floor_naming_an_unpublished_version_is_refused(lane) -> None:
    """THE check this lane adds, and it fires on the live state of the
    repository rather than a hypothetical.

    `dotmac-integration` declares 0.1.0a2 and has published 0.1.0a1. A connector
    flooring at a2 would produce a wheel whose dependency resolution fails for
    every consumer — discovered at install time, by someone who did not write
    it. `release-modules.json` already states the rule for kernels ("a floor
    naming an unpublished version cannot be resolved by an installer, so it is
    not a floor at all"); here it is enforced.
    """
    gate, _ = lane({**_valid_entry(), "integration_floor": "0.1.0a2"})
    with pytest.raises(SystemExit) as refusal:
        gate.resolve("dotmac-connector-example", tags={"dotmac-integration-v0.1.0a1"})
    message = str(refusal.value)
    assert "has no release tag" in message
    assert "not a floor" in message
    # It must name what IS installable, or the author cannot act on the refusal.
    assert "0.1.0a1" in message


def test_a_published_floor_is_accepted(lane) -> None:
    """SPECIFICITY: the refusal above is about publication, not about the
    version being unfamiliar."""
    gate, _ = lane(_valid_entry())
    assert gate.resolve(
        "dotmac-connector-example",
        tags={"dotmac-integration-v0.1.0a1", "dotmac-integration-v0.1.0a2"},
    )


def test_the_live_integration_floor_gap_is_recorded_rather_than_hidden() -> None:
    """The evidence the test above is built on, asserted against the checked-in
    ledger so the two cannot drift.

    If `dotmac-integration` is ever released at its declared version this entry
    must disappear — and the declared-publication guard fails until it does, so
    the ledger cannot quietly outlive the problem it records.
    """
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))["unpublished"]
    assert "dotmac-integration" in ledger
    assert ledger["dotmac-integration"]["state"] == "declared-unpublished"


# ── Discovery registration ──────────────────────────────────────────────────


def test_a_key_mismatch_between_entry_point_and_allowlist_is_refused(
    lane, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invisible until two connectors collide in a live registry, where the
    winner depends on which wheel was installed second."""
    import argparse

    gate, _ = lane({**_valid_entry(), "connector_key": "something.else.v1"})
    with pytest.raises(SystemExit) as refusal:
        gate.cmd_resolve(
            argparse.Namespace(distribution="dotmac-connector-example", version="")
        )
    assert "connector_key" in str(refusal.value)


def test_a_distribution_must_register_exactly_one_connector(lane, tmp_path) -> None:
    """Two makes "which one failed" unanswerable at boot — discovery is
    fail-closed as a SET. Zero is invisible to the control plane it was built
    for."""
    import argparse

    gate, package = lane(_valid_entry())
    for plugins, expected in (
        ("", "0 entry points"),
        (
            '[tool.poetry.plugins."dotmac_integration.connectors"]\n'
            '"example.v1" = "dotmac_connector_example:PLUGIN"\n'
            '"example.v2" = "dotmac_connector_example:OTHER"\n',
            "2 entry points",
        ),
    ):
        (package / "pyproject.toml").write_text(
            "[tool.poetry]\n"
            'name = "dotmac-connector-example"\n'
            'version = "0.1.0a1"\n'
            "[tool.poetry.dependencies]\n"
            'dotmac-integration = ">=0.1.0a1"\n' + plugins,
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as refusal:
            gate.cmd_resolve(
                argparse.Namespace(distribution="dotmac-connector-example", version="")
            )
        assert expected in str(refusal.value)


# ── Static conformance ──────────────────────────────────────────────────────


def test_static_conformance_refuses_a_lineage_and_a_mismatched_floor(
    lane,
) -> None:
    """A connector owns no schema and no lineage: its state lives in the control
    plane's `mod_intg`. And the floor a CONSUMER resolves must be the floor this
    gate checked — a pyproject that says something else makes the gate's answer
    about a different package than the one that ships."""
    import argparse

    gate, package = lane(_valid_entry())
    namespace = argparse.Namespace(distribution="dotmac-connector-example")
    gate.cmd_conformance(namespace)  # clean, so the refusals below mean something

    (package / "src" / "dotmac_connector_example" / "migrations").mkdir()
    with pytest.raises(SystemExit) as refusal:
        gate.cmd_conformance(namespace)
    assert "migrations/" in str(refusal.value)


def test_static_conformance_refuses_a_floor_the_package_does_not_declare(
    lane,
) -> None:
    import argparse

    gate, package = lane(_valid_entry())
    (package / "pyproject.toml").write_text(
        "[tool.poetry]\n"
        'name = "dotmac-connector-example"\n'
        'version = "0.1.0a1"\n'
        "[tool.poetry.dependencies]\n"
        'dotmac-integration = "*"\n'
        '[tool.poetry.plugins."dotmac_integration.connectors"]\n'
        '"example.v1" = "dotmac_connector_example:PLUGIN"\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as refusal:
        gate.cmd_conformance(
            argparse.Namespace(distribution="dotmac-connector-example")
        )
    assert "the floor a consumer resolves" in str(refusal.value)


def test_static_conformance_refuses_a_secret_shaped_file(lane) -> None:
    """ADR-0024 section 7: a connector holds a REFERENCE to credential material,
    never the value. Uses the module lane's shared name-shape list rather than a
    second copy — two copies drift, silently and in the worst direction."""
    import argparse

    gate, package = lane(_valid_entry())
    (package / "src" / "dotmac_connector_example" / "id_rsa").write_text("x")
    with pytest.raises(SystemExit) as refusal:
        gate.cmd_conformance(
            argparse.Namespace(distribution="dotmac-connector-example")
        )
    assert "secret-shaped" in str(refusal.value)


# ── The policy names a contract that actually exists ────────────────────────


def test_every_named_conformance_assertion_resolves_in_the_shipped_kit() -> None:
    """The policy names its obligations as CALLABLES rather than describing
    them, so a rename in `dotmac-integration` fails here instead of leaving this
    file pointing at a contract that moved. A policy referring to a function
    nobody can call is the governance equivalent of an unpublished version."""
    import importlib

    conformance = _policy()["conformance"]
    kit = importlib.import_module(conformance["kit_module"])
    for name in conformance["required_assertions"]:
        assert callable(getattr(kit, name, None)), name
        assert name in kit.__all__, f"{name} is not part of the kit's public surface"


def test_the_policy_names_the_real_discovery_group() -> None:
    """A connector registering in the wrong group is not discovered at all, and
    nothing in a release run would notice — the wheel installs cleanly and the
    control plane simply never sees it."""
    import importlib

    conformance = _policy()["conformance"]
    discovery = importlib.import_module(conformance["discovery_module"])
    assert conformance["entry_point_group"] == discovery.ENTRY_POINT_GROUP


def test_the_executable_conformance_actually_calls_the_kit() -> None:
    """The `verify-wheel` program is a string, so nothing else would catch it
    silently degrading into a check that imports the package and asserts
    nothing. It must call the STRONGER of the two assertions: metadata alone
    passes for a connector that cannot hand back a handler, which then fails at
    the first dispatch."""
    program = _gate().CONFORMANCE_PROGRAM
    assert "assert_plugin_conforms" in program
    assert "connector_key" in program
    # …and it must certify the INSTALLED bytes, not the checkout.
    assert "source tree" in program


# ── The lane is shut, and opening it is one complete diff ───────────────────


def test_the_lane_is_shut_and_has_no_workflow(lane) -> None:
    """TWO-DIRECTIONAL, and the reason there is no shut workflow to review.

    While `connectors` is empty there must be no `release-connector.yml`: a lane
    whose gate exists and whose workflow does not cannot publish at all, which
    is a stronger closure than an empty allowlist behind a live workflow. The
    moment an entry is added the workflow becomes REQUIRED, so opening the lane
    is one complete, reviewable change rather than a merge that has already
    half-opened it.
    """
    connectors = _policy()["connectors"]
    if connectors:
        assert WORKFLOW.is_file(), (
            "a connector is allowlisted but .github/workflows/release-connector.yml "
            "does not exist — the entry claims a publish path that is not there"
        )
    else:
        assert not WORKFLOW.exists(), (
            "the connector lane is empty but a release workflow exists. Either "
            "list a connector or delete the workflow; a live workflow over an "
            "empty allowlist is a door that only looks shut"
        )


def test_the_policy_states_what_an_entry_does_not_prove() -> None:
    """The failure mode this lane is most exposed to is a reader taking an
    allowlist row for an endorsement of a provider integration. The three
    non-claims are asserted present because prose is the only place they can
    live and the only thing that keeps them there is a test."""
    prose = " ".join(_policy()["$comment"]).lower()
    assert "not that a version was published" in prose
    assert "not that anything adopted it" in prose
    assert "not that the provider works" in prose


def test_the_three_lanes_do_not_overlap() -> None:
    """A distribution in two lanes would be publishable by whichever gate asks
    the fewest questions."""
    modules = set(
        json.loads(
            (PROJECT_ROOT / ".github" / "release-modules.json").read_text(
                encoding="utf-8"
            )
        )["modules"]
    )
    adapters = set(
        json.loads(
            (PROJECT_ROOT / ".github" / "release-adapters.json").read_text(
                encoding="utf-8"
            )
        )["adapters"]
    )
    connectors = set(_policy()["connectors"])
    assert not modules & adapters
    assert not modules & connectors
    assert not adapters & connectors


def test_a_connector_package_would_declare_the_governed_classification() -> None:
    """`connector-plugin` is a fourth classification alongside the three
    ADR-0006 governs today. No package declares it yet — asserted, so that when
    the first one does, this file is where the reviewer is sent."""
    declared = {
        tomllib.loads(dossier.read_text(encoding="utf-8")).get("classification")
        for dossier in (PROJECT_ROOT / "packages").glob("*/EXTRACTION.toml")
    }
    assert _policy()["conformance"]["classification"] == "connector-plugin"
    assert "connector-plugin" not in declared, (
        "a package now declares `connector-plugin`; it must be listed in "
        ".github/release-connectors.json or explicitly held back, and this "
        "assertion updated to say which"
    )
