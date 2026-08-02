#!/usr/bin/env bash
# Prove the kernel's declared dependency FLOOR is real.
#
# Widening `fastapi`/`pydantic` floors is a SUPPORT CLAIM: it says the kernel
# works on those versions, not merely that pip can resolve them. Without this
# check the widening would move a clean resolve-time failure into a runtime one
# for a consumer sitting at the floor — the products this widening exists to
# unblock (dotmac_sub, dotmac_erp at fastapi 0.111.0 / pydantic 2.7.4) are
# exactly the ones that would hit it.
#
# So: build the wheel, install it into a CLEAN venv with the floor versions
# PINNED EXACTLY, and exercise the supported surface a product assembly is
# permitted to import. Anything that needs a newer runtime fails here, loudly,
# before release.
set -euo pipefail

FLOOR_FASTAPI="${FLOOR_FASTAPI:-0.111.0}"
FLOOR_PYDANTIC="${FLOOR_PYDANTIC:-2.7.4}"
FLOOR_PYDANTIC_SETTINGS="${FLOOR_PYDANTIC_SETTINGS:-2.2.1}"
# dotmac_sub's exact production pin — the reason the floor is >=42.
FLOOR_CRYPTOGRAPHY="${FLOOR_CRYPTOGRAPHY:-42.0.8}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
kernel_dir="${repo_root}/packages/dotmac-kernel"
workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

echo "==> Building the kernel wheel"
(cd "${kernel_dir}" && poetry build -f wheel -o "${workdir}/dist" >/dev/null)
wheel="$(ls "${workdir}"/dist/*.whl)"
echo "    ${wheel##*/}"

echo "==> Clean venv with floors pinned exactly"
# The floor pydantic (2.7.4) predates cpython 3.13 wheels and its Rust build
# refuses 3.13, so pin the interpreter to the version CI actually uses rather
# than whatever `python3` happens to be locally.
FLOOR_PYTHON="${FLOOR_PYTHON:-python3.12}"
if ! command -v "${FLOOR_PYTHON}" >/dev/null 2>&1; then
  echo "!! ${FLOOR_PYTHON} not found. The floor pydantic has no wheels for" >&2
  echo "   newer interpreters; install it or set FLOOR_PYTHON." >&2
  exit 1
fi
"${FLOOR_PYTHON}" -m venv "${workdir}/venv"
# shellcheck disable=SC1091
source "${workdir}/venv/bin/activate"
pip install --quiet --upgrade pip
# Pin the floors FIRST so the kernel install cannot silently upgrade past them;
# a resolver that pulled a newer fastapi would make this check vacuous.
pip install --quiet \
  "fastapi==${FLOOR_FASTAPI}" \
  "pydantic==${FLOOR_PYDANTIC}" \
  "pydantic-settings==${FLOOR_PYDANTIC_SETTINGS}" \
  "cryptography==${FLOOR_CRYPTOGRAPHY}"
pip install --quiet "${wheel}[licensing]"

echo "==> Verifying the floors actually held"
python - <<'PY'
import fastapi
import pydantic
import os

expected = (
    os.environ.get("FLOOR_FASTAPI", "0.111.0"),
    os.environ.get("FLOOR_PYDANTIC", "2.7.4"),
)
actual = (fastapi.__version__, pydantic.VERSION)
if actual != expected:
    raise SystemExit(
        f"floor drift: installed {actual}, expected {expected} — the resolver "
        "upgraded past the floor, so this check would prove nothing"
    )
print(f"    fastapi {actual[0]}, pydantic {actual[1]}")
PY

echo "==> Exercising the supported surface at the floor (no DATABASE_URL)"
env -u DATABASE_URL -u PLATFORM_DATABASE_URL python - <<'PY'
"""Import and USE every kernel module a product assembly may consume.

Importing alone is a weak check — a pydantic model only fails when it is built,
so each contract here is actually constructed.
"""
from datetime import UTC, datetime

import dotmac_kernel
from dotmac_kernel import (
    CapabilityCatalogue,
    DeploymentProfileRegistry,
    DeploymentProfileSpec,
    FeatureManifest,
    Money,
    currency,
)
from dotmac_kernel.licensing import (
    LicenceKey,
    LicenceKeyRing,
    ReceiverAppliedState,
    applied_state_payload,
    parse_applied_state,
)
from dotmac_kernel.testing import FakeLicenceSigner, create_test_engine

print(f"    kernel {dotmac_kernel.__version__}")

# WS1 — capability catalogue + profile registry
catalogue = CapabilityCatalogue.from_manifests(
    [FeatureManifest(name="m", capabilities=("m.use",))]
)
catalogue.require("m.use")
registry = DeploymentProfileRegistry(
    [
        DeploymentProfileSpec(
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
    ]
)
assert registry.is_valid_code("p")

# WS4 — exact money (a pydantic-adjacent value object the products need)
from decimal import Decimal

usd = currency("USD")
total = Money(Decimal("1.10"), usd) + Money(Decimal("2.20"), usd)
assert total.amount == Decimal("3.30"), total

# WS8 — licensing contracts, incl. the new applied-state report
signer = FakeLicenceSigner(key_id="floor")
ring = LicenceKeyRing([LicenceKey(key_id=signer.key_id, public_key_b64=signer.public_key_b64)])
assert ring.key_ids == {"floor"}
state = ReceiverAppliedState(
    report_id="r1",
    deployment_ref="d1",
    licence_id="l1",
    licence_version=1,
    digest="sha256:x",
    keyring_generation=1,
    revocation_list_version=None,
    observed_at=datetime.now(UTC),
    status="applied",
)
assert parse_applied_state(applied_state_payload(state)) == state

# The test kit must be usable WITHOUT a database — the defect that blocked
# dotmac_sub and dotmac_erp from adopting a7 at all.
create_test_engine()

print("    supported surface OK at the floor")
PY

echo "==> Floor check passed"
