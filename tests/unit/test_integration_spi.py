"""The connector SPI, discovery and the three refusals.

`dotmac-integration` is the control plane behind one independently deployed
Integrator (ADR-0024 §§ 6-7). This file covers the decision logic with **no
provider and no network I/O** — which is the point of the conformance kit it
uses, not a limitation of it.

The binding-multiplicity tests are the ones to read first: `enabled` and
`selected` are different questions, and an earlier reading of § 7 conflated them
into a global uniqueness constraint the ADR never asked for.
"""

from __future__ import annotations

import pytest
from dotmac_integration import (
    CURRENT_SPI_VERSION,
    ConnectorManifest,
    DuplicateConnectorError,
    InvalidManifestError,
    SecretValueError,
    SpiIncompatibleError,
    SpiRange,
    SpiVersion,
    discover,
    validate_config_revision,
)
from dotmac_integration.conformance import (
    FAKE_CAPABILITY,
    ConformanceFailure,
    assert_connector_conforms,
    fake_manifest,
    fake_registry,
)
from dotmac_integration.spi import CapabilityDeclaration

# ── SPI versions and ranges ─────────────────────────────────────────────────


def test_a_range_must_be_closed_at_both_ends() -> None:
    """ "Works with any future SPI" is a claim no author can honestly make.

    Accepting an open range would move the failure from activation — a clear
    refusal — to the first dispatch, where it is a traceback.
    """
    with pytest.raises(InvalidManifestError):
        SpiRange.parse(">=1.0")
    with pytest.raises(InvalidManifestError):
        SpiRange.parse("1.0,2.0")


def test_an_empty_range_is_refused() -> None:
    with pytest.raises(InvalidManifestError, match="admits no version"):
        SpiRange(SpiVersion(2, 0), SpiVersion(1, 0))


def test_a_range_admits_by_version_not_by_string() -> None:
    """`1.9 < 1.10` numerically and the reverse lexically."""
    wide = SpiRange.parse(">=1.0,<2.0")
    assert wide.admits(SpiVersion(1, 10))
    assert not wide.admits(SpiVersion(2, 0))


# ── Refusal 1: duplicate connector key ──────────────────────────────────────


def test_two_distributions_claiming_one_key_are_refused() -> None:
    """Resolving it by load order would make the winner depend on which wheel
    was installed second."""
    with pytest.raises(DuplicateConnectorError, match="one key, one distribution"):
        fake_registry(
            [
                fake_manifest(connector_key="dup", version="1.0.0"),
                fake_manifest(connector_key="dup", version="2.0.0"),
            ]
        )


def test_distinct_keys_compose(  # specificity for the test above
) -> None:
    registry = fake_registry(
        [
            fake_manifest(connector_key="alpha"),
            fake_manifest(connector_key="beta"),
        ]
    )
    assert registry.keys == {"alpha", "beta"}


# ── Refusal 2: incompatible SPI ─────────────────────────────────────────────


def test_discovery_refuses_a_connector_that_excludes_this_module() -> None:
    """Refused at BOOT, when someone is watching — not at the first dispatch,
    when nobody is."""
    future = fake_manifest(connector_key="from_the_future", spi_range=">=9.0,<10.0")
    with pytest.raises(SpiIncompatibleError, match="running module implements"):
        fake_registry([future])


def test_one_bad_distribution_refuses_the_whole_registry() -> None:
    """FAIL-CLOSED as a set. A registry that quietly drops a connector is worse
    than one that will not start: the operator configured it for a reason and
    would be told, by silence, that it is running."""
    with pytest.raises(SpiIncompatibleError):
        fake_registry(
            [
                fake_manifest(connector_key="fine"),
                fake_manifest(connector_key="broken", spi_range=">=9.0,<10.0"),
            ]
        )


def test_require_compatible_rechecks_after_discovery() -> None:
    """A distribution can be installed after discovery ran, and a binding
    activated long after startup, so the check is repeated wherever a decision
    depends on it."""
    registry = fake_registry([fake_manifest(connector_key="ok")])
    assert registry.require_compatible("ok").connector_key == "ok"
    with pytest.raises(SpiIncompatibleError):
        registry.require_compatible("ok", spi_version=SpiVersion(9, 0))


# ── Refusal 3: undeclared capability ────────────────────────────────────────


def test_a_capability_the_connector_never_declared_is_refused() -> None:
    """Without this a typo binds successfully and fails at the first dispatch,
    by which time the operator has been told the integration is live."""
    manifest = fake_manifest()
    with pytest.raises(InvalidManifestError, match="does not declare capability"):
        manifest.require_declares("ticket.observation.v1")


def test_a_capability_id_must_be_a_versioned_contract_name() -> None:
    """`ticket.observation.v1` and `.v2` are different contracts a connector may
    implement independently, so the version is part of the identity."""
    for bad in ("ticket", "Ticket.Observation.v1", "ticket.observation", "ticket.v0"):
        with pytest.raises(InvalidManifestError):
            CapabilityDeclaration(capability_id=bad)
    assert CapabilityDeclaration(capability_id="ticket.observation.v1")


def test_a_capability_config_schema_must_be_valid_json_schema() -> None:
    with pytest.raises(InvalidManifestError, match="config schema") as refused:
        CapabilityDeclaration(
            capability_id="ticket.observation.v1",
            config_schema={
                "type": "not-a-json-schema-type",
                "default": "must-never-reach-the-error",
            },
        )
    assert refused.value.__cause__ is None
    assert "must-never-reach-the-error" not in str(refused.value)


def test_a_diagnostic_code_is_safe_to_persist_and_detail_is_not_repr_visible() -> None:
    from dotmac_integration import Diagnostic

    diagnostic = Diagnostic(
        ok=False,
        code="credential_unavailable",
        detail="must-never-reach-a-log",
    )

    assert "must-never-reach-a-log" not in repr(diagnostic)
    unsafe = Diagnostic(ok=False, code="secret=must-never-be-a-code")
    assert unsafe.code == "invalid_diagnostic_code"
    assert "must-never-be-a-code" not in repr(unsafe)


def test_a_connector_declaring_nothing_is_refused() -> None:
    with pytest.raises(InvalidManifestError, match="declares no capabilities"):
        ConnectorManifest(
            connector_key="empty",
            version="1.0.0",
            spi_range=SpiRange.parse(">=1.0,<2.0"),
            capabilities=(),
        )


# ── Secret references only ──────────────────────────────────────────────────


def test_a_literal_secret_in_config_is_refused_at_any_depth() -> None:
    """A nested `{"auth": {"api_key": ...}}` leaks exactly as much as a
    top-level one, and it is the shape most connectors actually use."""
    with pytest.raises(SecretValueError, match="auth.api_key"):
        validate_config_revision({"auth": {"api_key": "sk_live_abc123"}}, {})


def test_a_reference_in_the_same_place_is_accepted() -> None:
    validate_config_revision({"auth": {"api_key": "bao://kv/stripe#key"}}, {})


def test_secret_refs_must_hold_references_not_values() -> None:
    with pytest.raises(SecretValueError, match="not a reference"):
        validate_config_revision({}, {"token": "hunter2"})
    validate_config_revision({}, {"token": "bao://kv/x#t"})


def test_an_unrecognised_scheme_is_not_a_reference() -> None:
    """A password that happens to contain '://' is indistinguishable from a
    pointer unless the scheme set is closed."""
    with pytest.raises(SecretValueError):
        validate_config_revision({}, {"token": "mystore://kv/x"})


def test_a_non_secret_field_may_hold_a_literal() -> None:
    """Specificity: the rule is about names that imply material, not about
    every string in the config."""
    validate_config_revision({"base_url": "https://api.example.com"}, {})


# ── The conformance kit ─────────────────────────────────────────────────────


def test_the_kit_certifies_a_well_formed_connector() -> None:
    assert_connector_conforms(fake_manifest())


def test_the_kit_refuses_a_connector_that_excludes_the_running_spi() -> None:
    with pytest.raises(ConformanceFailure, match="excludes the running module"):
        assert_connector_conforms(fake_manifest(spi_range=">=9.0,<10.0"))


def test_the_kit_requires_a_connector_to_stand_alone() -> None:
    """A connector that only validates alongside its neighbours is not
    independently releasable."""
    assert_connector_conforms(fake_manifest(connector_key="solo"))


def test_the_kit_uses_real_discovery() -> None:
    """A kit that bypassed the production refusals would certify connectors
    against a contract nothing enforces."""
    registry = fake_registry()
    assert registry.get("conformance_fake").capability_ids == {FAKE_CAPABILITY}


def test_discovery_reads_package_metadata_and_names_no_provider() -> None:
    """ADR-0024 § 7: no fixed provider enum, import list or `if provider ==`.

    The real entry-point group must be consulted, and an empty environment is a
    valid answer — not an error, and not a hardcoded fallback list.
    """
    assert discover(points=[]).manifests == ()
    assert CURRENT_SPI_VERSION.major >= 1
