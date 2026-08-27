"""The descriptor holds NAMES, never secret VALUES.

`deploy/product.toml` is checked in and then rendered into configuration a host
reads, so a DSN with an embedded password in it is a credential in Git history
whether or not review caught it. The guard runs at PARSE time, over every string
in the raw document, before any field is interpreted — which is what lets it see
a secret pasted into a field the schema does not define, and that is exactly
where a mistake lands.

Every rule has a planted-value proof below, and the file opens with a negative
control. A scanner over a clean tree passes for the wrong reason otherwise, and
this module is nothing but a scanner (ADR-0018).
"""

from __future__ import annotations

import pytest
from dotmac_deployment_foundation.errors import SecretValueError
from dotmac_deployment_foundation.secrets_guard import (
    find_secrets,
    require_no_secrets,
    scan_value,
)

# ── the negative control, first, because everything else depends on it ───────

CLEAN = {
    "schema": "ProductDeploymentSpec.v1",
    "product": "dotmac_erp",
    "image": {
        # 64 hex characters. The single most important value in the whole file,
        # and precisely what an entropy heuristic would refuse — which is why
        # every rule in the guard is STRUCTURAL rather than statistical.
        "reference": "ghcr.io/x/y@sha256:" + "a" * 64,
        "source_revision": "c" * 40,
    },
    "runtime_materials": {"names": ["DATABASE_URL", "REDIS_URL", "METRICS_TOKEN"]},
    "migration": {"owner_material": "MIGRATION_DATABASE_URL"},
    "ingress": {"host": "erp.dotmac.io"},
    "note": "the migration role is app_admin and the online role is app_user",
}


def test_a_clean_descriptor_has_no_findings() -> None:
    """Without this, every "is caught" test below could pass on a scanner that
    refuses literally any string — the most common way a guard stops being one."""
    assert find_secrets(CLEAN) == []
    require_no_secrets(CLEAN, source="<clean>")


def test_a_material_name_is_not_a_secret() -> None:
    """`METRICS_TOKEN` contains the word TOKEN and is a NAME.

    The assignment rule looks for `token: value`, not for the word appearing in
    an identifier. Getting this wrong would make the guard refuse the very
    declarations the descriptor exists to hold.
    """
    assert scan_value("runtime_materials.names[2]", "METRICS_TOKEN") is None
    assert scan_value("migration.owner_material", "MIGRATION_DATABASE_URL") is None


def test_a_credential_free_dsn_is_allowed() -> None:
    """A DSN without userinfo is a legitimate value; the userinfo is the defect."""
    assert scan_value("x", "postgresql+psycopg://db.internal:5432/erp") is None


def test_an_image_digest_is_allowed() -> None:
    assert scan_value("image.reference", "ghcr.io/x/y@sha256:" + "f" * 64) is None


def test_an_approved_pointer_is_a_name_and_not_a_value() -> None:
    """`bao://…` is public by construction.

    Knowing where a secret lives grants nothing without the authority to read
    it, and refusing pointers would force the location into an untracked host
    file — the drift that twice took a staging host down.
    """
    assert scan_value("x", "bao://secret/dotmac/erp#migration_dsn") is None
    assert scan_value("x", "env://INTEGRATOR_SECRET_PAYSTACK") is None
    assert scan_value("x", "file:///run/secrets/paystack") is None


def test_a_pointer_whose_fragment_is_named_api_key_is_still_a_name() -> None:
    """Order matters inside the scanner.

    Pointer recognition runs BEFORE the assignment rule, so a pointer whose
    fragment happens to be `api_key` is a location rather than an assignment.
    """
    assert scan_value("x", "bao://secret/erp/paystack#api_key") is None


# ── planted values: one per rule ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("postgresql://app:hunter2@db.internal:5432/erp", "url-with-credentials"),
        ("https://user:s3cr3t@api.example.com/hook", "url-with-credentials"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIE...", "pem-armour"),
        ("-----BEGIN CERTIFICATE-----\nMIID...", "pem-armour"),
        # The vendor-token rule matches on PREFIX plus a minimum LENGTH — it
        # never looks at the body — so these fixtures deliberately carry an
        # obviously-fake body rather than a realistic-looking one. Two reasons,
        # and the second is not cosmetic:
        #   1. a realistic body proves nothing the length alone does not, and
        #   2. GitHub push protection rejected this very file when the bodies
        #      were realistic, which means the branch carrying the secrets
        #      guard could not be pushed. A test fixture that blocks its own
        #      delivery is a defect in the fixture.
        # Do NOT "improve" these back into realistic keys. If the rule ever
        # starts inspecting the body, the fixture must change with it — but
        # then it needs a synthetic body the scanners do not claim, not a real
        # vendor shape.
        ("sk_live_NOT-A-REAL-KEY-0000", "vendor-token-prefix"),
        ("AKIA-NOT-A-REAL-KEY-0000", "vendor-token-prefix"),
        ("ghp_NOT-A-REAL-TOKEN-0000", "vendor-token-prefix"),
        ("xoxb-NOT-A-REAL-TOKEN-0000", "vendor-token-prefix"),
        ("hvs.NOT-A-REAL-TOKEN-0000", "vendor-token-prefix"),
        ("DATABASE_PASSWORD=hunter2correcthorse", "secret-assignment"),
        ("api_key: 9f8e7d6c5b4a39281706", "secret-assignment"),
        ("client_secret=abcdef0123456789", "secret-assignment"),
    ],
)
def test_a_planted_secret_is_caught(value: str, rule: str) -> None:
    finding = scan_value("planted", value)
    assert finding is not None, f"{value!r} was not caught"
    assert finding.rule.startswith(rule), finding.rule


def test_a_planted_jwt_is_caught() -> None:
    """Recognised by DECODING the header, not by shape alone.

    A shape-only rule would refuse any dotted base64-looking string; requiring
    the first segment to decode to a JSON object carrying `alg` is what makes
    this specific.
    """
    header = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    token = f"{header}.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"
    finding = scan_value("planted", token)
    assert finding is not None
    assert finding.rule == "jwt"


def test_a_dotted_base64_string_that_is_not_a_jwt_is_not_flagged() -> None:
    """The discriminator for the rule above.

    Without it, "the JWT rule fires" would be indistinguishable from "the JWT
    rule fires on everything with two dots in it".
    """
    assert scan_value("x", "eyeglasses.something.elsewhere") is None


# ── the reported finding is safe to put in a CI log ─────────────────────────


def test_the_finding_never_echoes_the_secret_back() -> None:
    """A guard that prints the credential it found has copied it somewhere new.

    Six characters is enough to locate the line and not enough to use, and a
    short value is described by length alone.
    """
    secret = "sk_live_NOT-A-REAL-KEY-0000-REDACTME"
    finding = scan_value("planted", secret)
    assert finding is not None
    assert secret not in str(finding)
    assert "…" in finding.excerpt or "chars" in finding.excerpt


# ── the walk sees fields the schema does not define ─────────────────────────


def test_a_secret_in_an_unknown_field_is_still_found() -> None:
    """The reason the scan runs before any field is interpreted.

    A guard that only inspects the fields it understands cannot see a secret
    pasted into one it does not — and an unknown field is exactly where a
    mistake lands.
    """
    document = dict(CLEAN)
    document["totally_unknown_section"] = {"pasted": "PASSWORD=hunter2correcthorse"}
    findings = find_secrets(document)
    assert len(findings) == 1
    assert findings[0].path == "totally_unknown_section.pasted"


def test_a_secret_nested_in_an_array_of_tables_is_found() -> None:
    document = dict(CLEAN)
    document["roles"] = [
        {"code": "app", "materials": ["DATABASE_URL"]},
        {"code": "worker", "note": "postgresql://app:hunter2@db/erp"},
    ]
    findings = find_secrets(document)
    assert [finding.path for finding in findings] == ["roles[1].note"]


def test_every_finding_is_reported_at_once_not_one_per_run() -> None:
    """An operator who pasted one environment block usually pasted several, and
    one-at-a-time refusal turns a single fix into five review cycles."""
    document = dict(CLEAN)
    document["a"] = "PASSWORD=hunter2correcthorse"
    document["b"] = "postgresql://app:hunter2@db/erp"
    document["c"] = "AKIA-NOT-A-REAL-KEY-0000"
    with pytest.raises(SecretValueError) as caught:
        require_no_secrets(document, source="<planted>")
    message = str(caught.value)
    assert "3 secret-shaped value(s)" in message
    for path in ("a", "b", "c"):
        assert path in message


def test_the_refusal_names_the_file() -> None:
    with pytest.raises(SecretValueError) as caught:
        require_no_secrets(
            {"x": "PASSWORD=hunter2correcthorse"}, source="deploy/product.toml"
        )
    assert caught.value.where == "deploy/product.toml"
