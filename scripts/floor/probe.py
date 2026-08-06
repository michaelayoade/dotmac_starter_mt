"""Exercise the UNION of the products' kernel allowlists at the floor.

Sub (S2) allows: assembly, capabilities, features, money, profiles, providers,
providers.provisioning. ERP (E2) adds: licensing, testing. This probe covers
that union — anything a product may import must work here, or the floor is a
claim about a surface nobody actually checked. `modules` is covered too: it is
the same category (a pure, FastAPI-light contract both products will consume
next), and adding it to the probe when it ships is cheaper than discovering at
adoption that the floor claim never included it.

Importing is a weak check: a pydantic model only fails when it is BUILT, and a
crypto backend only fails when it SIGNS. So each contract is constructed and,
where it has behaviour, exercised.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import dotmac_kernel
from dotmac_kernel import (
    UNVERSIONED,
    AuditActionRegistry,
    CapabilityCatalogue,
    DeploymentProfileRegistry,
    DeploymentProfileSpec,
    ExchangeRate,
    FeatureManifest,
    MissingModuleDependencyError,
    ModuleManifest,
    ModuleRegistry,
    Money,
    PermissionCatalogue,
    PermissionSpec,
    ProductAssemblySpec,
    UndeclaredAuditActionError,
    UndeclaredPermissionError,
    currency,
)
from dotmac_kernel.licensing import (
    UNKNOWN_DIGEST,
    LicenceKey,
    LicenceKeyRing,
    ReceiverAppliedState,
    applied_state_payload,
    parse_applied_state,
    payload_digest,
    verify_licence,
    verify_revocation_list,
)
from dotmac_kernel.providers.provisioning import (
    ProvisioningProvider,
    ProvisioningRequest,
)
from dotmac_kernel.testing import (
    FakeClock,
    FakeLicenceSigner,
    FakeProvisioningProvider,
    FakeSeeder,
    InMemoryRateLimitStore,
    check_provisioning_provider_contract,
    create_test_engine,
    fake_branding,
    isolated_session,
)

print(f"    kernel {dotmac_kernel.__version__}")
now = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)

# WS1: capabilities + profiles
manifest = FeatureManifest(name="m", capabilities=("m.use",))
CapabilityCatalogue.from_manifests([manifest]).require("m.use")
profile = DeploymentProfileSpec(
    code="p",
    version="1.0.0",
    required_modules=frozenset({"m"}),
    commercial_provider="signed_license",
    provisioning_provider="local",
    identity_provider="local",
    telemetry_provider="disabled",
    update_provider="offline_bundle",
    ingress_provider="manual",
    dns_verification_provider="manual",
    tls_provider="customer_pki",
    default_locale="en",
    supported_locales=frozenset({"en"}),
    allowed_currencies=frozenset({"USD"}),
    legal_authority="dotmac",
    data_residency="eu",
)
registry = DeploymentProfileRegistry([profile])
assert registry.is_valid_code("p")
assert profile.provider_selections()["identity"] == "local"

# Module manifest + registry: build a real dependency graph, prove the order is
# dependency-first, and serialize the inventory (a dataclass only fails when it
# is BUILT, and the payload only fails when it is walked).
module = ModuleManifest(code="m2", version="1.4.0", dependencies=("m",))
module_registry = ModuleRegistry([manifest, module])
assert [m.code for m in module_registry.startup_order()] == ["m", "m2"]
assert module_registry.get("m").version == UNVERSIONED
payload = module_registry.inventory_payload()
assert payload["startup_order"] == ["m", "m2"], payload
json.dumps(payload)
try:
    ModuleRegistry([module])  # dependency 'm' absent → must fail closed
except MissingModuleDependencyError:
    pass
else:  # pragma: no cover - the probe fails loudly instead
    raise AssertionError("ModuleRegistry accepted a missing dependency")

# Manifest declaration catalogues (module control-plane step 3): a spec only
# fails when it is BUILT and a catalogue only fails when it is QUERIED, so build
# both, resolve a declared code, and prove an undeclared one fails closed.
declaring = FeatureManifest(
    name="m3",
    permissions=(PermissionSpec(code="m3.read", default_roles=("admin",)),),
    audit_actions=("m3.happened",),
)
permissions = PermissionCatalogue.from_manifests([declaring])
assert permissions.require("m3.read").default_roles == ("admin",)
audit_actions = AuditActionRegistry.from_manifests([declaring])
audit_actions.require("m3.happened")
for catalogue, code, error in (
    (permissions, "m3.write", UndeclaredPermissionError),
    (audit_actions, "m3.never", UndeclaredAuditActionError),
):
    try:
        catalogue.require(code)
    except error:
        pass
    else:  # pragma: no cover - the probe fails loudly instead
        raise AssertionError(f"{catalogue!r} accepted the undeclared {code!r}")

# Assembly composition — a FeatureManifest and a ModuleManifest, mixed.
spec = ProductAssemblySpec(name="floor-probe", modules=(manifest, module))
assert spec.name == "floor-probe"

# WS4: exact money AND FX
usd, eur = currency("USD"), currency("EUR")
total = Money(Decimal("1.10"), usd) + Money(Decimal("2.20"), usd)
assert total.amount == Decimal("3.30"), total
rate = ExchangeRate(
    base=usd,
    quote=eur,
    rate=Decimal("0.90"),
    as_of=now,
    # An FX snapshot records WHERE it came from — the immutability the
    # products need at their ERP-facing boundaries.
    source="floor-probe",
)
converted = rate.convert(Money(Decimal("10.00"), usd))
assert converted.currency == eur, converted

# Provisioning contract + its fake, incl. the reusable contract suite
fake = FakeProvisioningProvider()
assert isinstance(fake, ProvisioningProvider)
plan = fake.plan(ProvisioningRequest(intent_id="i1", spec={"nodes": 1}))
assert plan.plan_hash
check_provisioning_provider_contract(FakeProvisioningProvider)

# WS8: SIGN and VERIFY, not merely construct keys
signer = FakeLicenceSigner(key_id="floor")
ring = LicenceKeyRing(
    [LicenceKey(key_id=signer.key_id, public_key_b64=signer.public_key_b64)]
)
envelope = signer.envelope(
    licence_id="lic-floor",
    licence_version=1,
    capabilities=[{"code": "m.use"}],
    expires_at=(now + timedelta(days=30)).isoformat(),
)
verified = verify_licence(envelope, keyring=ring, now=now)
assert verified.document.licence_id == "lic-floor"
assert verified.digest.startswith("sha256:")
revocation = signer.sign_revocation_list(list_version=1, revoked_licence_ids=["x"])
assert verify_revocation_list(revocation, keyring=ring).list_version == 1
assert payload_digest(b"abc").startswith("sha256:")

state = ReceiverAppliedState(
    report_id="r1",
    deployment_ref="d1",
    licence_id="lic-floor",
    licence_version=1,
    digest=verified.digest,
    keyring_generation=1,
    revocation_list_version=None,
    observed_at=now,
    status="applied",
)
assert parse_applied_state(applied_state_payload(state)) == state
assert UNKNOWN_DIGEST == "unknown"

# The test kit, WITHOUT a database (the defect that blocked a7)
engine = create_test_engine()
with isolated_session(engine) as session:
    assert session is not None
clock = FakeClock(now)
clock.advance(3600)  # seconds, not a timedelta
assert clock.now() == now + timedelta(hours=1)
assert FakeSeeder() is not None
assert InMemoryRateLimitStore() is not None
assert fake_branding()

print("    supported surface OK at the floor")
