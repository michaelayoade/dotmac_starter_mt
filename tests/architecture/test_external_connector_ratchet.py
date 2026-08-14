"""The fleet's direct external-connector surface is frozen and may only shrink.

Step 1 of the Integrator sequence (ADR-0024 § 6). Every product retires its
direct provider clients, credentials, webhook verification, connector
scheduling, checkpoints and delivery retries into the independently deployed
Dotmac Integrator. That is a long programme, and the thing that makes it a
programme rather than an intention is a number that cannot rise.

Two-directional, per ADR-0018: a RISE means a new direct connector landed; a
FALL without the baseline being lowered in the same change means the detector
stopped seeing something, which reads as progress and is exactly what a
one-directional ratchet cannot distinguish from real retirement.

The measurement lives in `scripts/external_connector_sweep.py` and the frozen
numbers in `docs/inventories/external-connector-baseline.json`; the prose and
the known blind spots are in
`docs/inventories/external-connector-sources.md`.

**Abstains when the fleet is not checked out.** These tests measure sibling
repositories, so a Starter-only checkout cannot see them. Abstaining is correct:
scoring a missing repo as zero would report the duplication as solved.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "external_connector_sweep.py"
BASELINE = PROJECT_ROOT / "docs" / "inventories" / "external-connector-baseline.json"
INVENTORY = PROJECT_ROOT / "docs" / "inventories" / "external-connector-sources.md"
FLEET_ROOT = PROJECT_ROOT.parent


def _sweep():
    spec = importlib.util.spec_from_file_location("external_connector_sweep", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


# ── The artifacts exist and agree ───────────────────────────────────────────


def test_the_baseline_and_its_inventory_exist() -> None:
    assert BASELINE.is_file()
    assert INVENTORY.is_file()
    assert "external-connector-baseline.json" in INVENTORY.read_text(encoding="utf-8")


def test_the_baseline_covers_every_measured_repository() -> None:
    """A baseline measured with a repo missing would freeze it at zero."""
    sweep = _sweep()
    baseline = _baseline()
    assert sorted(baseline["counts"]) == sorted(sweep.REPOS)
    assert baseline["categories"] == list(sweep.CATEGORIES)


def test_every_category_adr_0024_names_is_measured() -> None:
    """The six responsibilities § 6 moves to the Integrator. A category dropped
    from the sweep would silently stop being ratcheted."""
    sweep = _sweep()
    assert set(sweep.CATEGORIES) == {
        "http_client",
        "webhook_surface",
        "provider_credential",
        "connector_task",
        "sync_checkpoint",
        "delivery_retry",
    }


def test_the_vendor_control_plane_has_no_direct_connector_surface() -> None:
    """The one repo already at zero, asserted against the LIVE baseline rather
    than assumed: it is the shape every other product is migrating toward, so a
    regression there is the earliest possible warning."""
    assert _baseline()["counts"]["dotmac_vendor_control_plane"] == dict.fromkeys(
        _sweep().CATEGORIES, 0
    )


# ── The ratchet ─────────────────────────────────────────────────────────────


def test_no_repository_grew_a_new_direct_connector_surface() -> None:
    sweep = _sweep()
    measured, absent = sweep.measure(FLEET_ROOT)
    if absent:
        pytest.skip(f"fleet not checked out beside Starter; unmeasured: {absent}")
    failures = sweep._ratchet(measured, _baseline())
    assert not failures, "external-connector ratchet:\n" + "\n".join(failures)


# ── Sensitivity proofs (ADR-0018) ───────────────────────────────────────────


def test_the_ratchet_fires_when_a_count_rises() -> None:
    """Without this, "the surface cannot grow" is a claim about a file nobody
    proved is compared."""
    sweep = _sweep()
    baseline = _baseline()
    grown = json.loads(json.dumps(baseline))
    grown["counts"]["dotmac_sub"]["http_client"] += 1
    failures = sweep._ratchet(grown, baseline)
    assert any("a new direct connector surface landed" in f for f in failures)


def test_the_ratchet_fires_when_a_count_falls_without_a_rebaseline() -> None:
    """The half a one-directional ratchet misses. A detector that quietly stops
    matching looks exactly like a retirement, and only this direction tells them
    apart."""
    sweep = _sweep()
    baseline = _baseline()
    shrunk = json.loads(json.dumps(baseline))
    shrunk["counts"]["dotmac_sub"]["http_client"] -= 1
    failures = sweep._ratchet(shrunk, baseline)
    assert any("lower the baseline in the SAME change" in f for f in failures)


def test_the_detector_distinguishes_a_client_from_a_type_annotation() -> None:
    """SPECIFICITY. `import httpx` for an annotation is not a connector, and a
    rule that counted it would inflate every number and make the ratchet noise.
    """
    sweep = _sweep()
    annotation_only = "import httpx\n\ndef f(client: httpx.Client) -> None: ...\n"
    issues_request = "import httpx\n\ndef f():\n    return httpx.post('https://x')\n"

    import ast

    assert not (
        sweep._imports_http_client(ast.parse(annotation_only))
        and sweep._issues_a_request(ast.parse(annotation_only))
    )
    assert sweep._imports_http_client(ast.parse(issues_request))
    assert sweep._issues_a_request(ast.parse(issues_request))


def test_a_generic_api_key_is_not_counted_as_a_provider_credential() -> None:
    """A product's own `api_key` is not a provider's. Requiring the provider to
    be NAMED is what keeps this category about connector material."""
    import ast

    sweep = _sweep()
    own_key = "class S:\n    api_key: str = ''\n"
    provider_key = "class S:\n    stripe_secret_key: str = ''\n"
    assert not sweep._holds_provider_credential(ast.parse(own_key))
    assert sweep._holds_provider_credential(ast.parse(provider_key))


def test_a_rotation_cursor_is_not_a_sync_checkpoint() -> None:
    """SPECIFICITY, and a real miscount rather than a hypothetical one.

    `InboxTeamRoundRobinCursor` (dotmac_sub `app/models/team_inbox.py`, landed
    2026-08-13 with conversational AI intake) is durable per-team ROTATION state
    for assigning inbox conversations to staff: `service_team_id`,
    `last_assigned_person_id`, `rotation_count`. No feed, no watermark, no
    external system. It matched only because "cursor" is a substring of its
    name, and it drove `dotmac_sub.sync_checkpoint` to 9 against a baseline of
    8 — reporting a new direct connector surface where none had landed.

    A ratchet that fires on internal state is a ratchet that gets switched off,
    so a bare `*Cursor` now has to name the feed it is a position in.
    """
    import ast

    sweep = _sweep()
    rotation = (
        "class InboxTeamRoundRobinCursor:\n"
        "    service_team_id: int = 0\n"
        "    last_assigned_person_id: int = 0\n"
        "    rotation_count: int = 0\n"
    )
    assert not sweep._holds_a_checkpoint(ast.parse(rotation))
    assert not sweep._is_checkpoint_class_name("InboxTeamRoundRobinCursor")
    # The same overload elsewhere: pagination and DBAPI cursors.
    assert not sweep._is_checkpoint_class_name("PageCursor")
    assert not sweep._is_checkpoint_class_name("ResultCursor")


def test_narrowing_the_cursor_rule_did_not_blind_the_checkpoint_detector() -> None:
    """ANTI-BLINDING — the other half of ADR-0018, and the half a precision fix
    usually skips. Each assertion below is a surface that MUST still be counted.

    Three independent nets keep recall: a self-qualifying feed word, an
    external-feed qualifier on an ambiguous `*Cursor`, and — the important one —
    the watermark COLUMN rule, which is untouched and catches any class that
    actually stores a feed position no matter what the class is called.
    """
    import ast

    sweep = _sweep()

    # 1. Self-qualifying names: `checkpoint`/`syncstate` stand alone. These are
    #    the live surfaces in Sub and ERP; losing them would drop the baseline.
    assert sweep._is_checkpoint_class_name("IntegrationCheckpoint")
    assert sweep._is_checkpoint_class_name("EventHandlerCheckpoint")
    assert sweep._is_checkpoint_class_name("QuoteSyncState")

    # 2. An ambiguous `*Cursor` that names its feed — generically or by provider.
    assert sweep._is_checkpoint_class_name("ErpDomainSyncCursor")
    assert sweep._is_checkpoint_class_name("StripeCursor")
    assert sweep._is_checkpoint_class_name("WebhookIngestCursor")

    # 3. The column net is independent of the name. A cursor class the name
    #    rule now rejects is STILL counted the moment it stores a watermark —
    #    which is what makes narrowing the name rule cost no real recall.
    rejected_name_with_watermark = (
        "class RoundRobinCursor:\n    last_synced_at: str = ''\n"
    )
    assert not sweep._is_checkpoint_class_name("RoundRobinCursor")
    assert sweep._holds_a_checkpoint(ast.parse(rejected_name_with_watermark))


def test_the_sync_checkpoint_detector_still_bites(tmp_path: pathlib.Path) -> None:
    """A narrowed rule that matched NOTHING would also satisfy every assertion
    above about what it REJECTS. This drives `_classify` — the path `measure()`
    actually uses — over a file on disk and requires the category to come back,
    so it cannot quietly become dead code (and the frozen total is non-zero).
    """
    sweep = _sweep()
    counted = tmp_path / "erp_sync_cursor.py"
    counted.write_text(
        "class ErpSyncCursor:\n"
        "    __tablename__ = 'erp_sync_cursor'\n"
        "    last_event_id: str = ''\n",
        encoding="utf-8",
    )
    ignored = tmp_path / "round_robin.py"
    ignored.write_text(
        "class TeamRoundRobinCursor:\n    rotation_count: int = 0\n", encoding="utf-8"
    )

    assert "sync_checkpoint" in sweep._classify(counted)
    assert "sync_checkpoint" not in sweep._classify(ignored)
    assert _baseline()["totals"]["sync_checkpoint"] > 0


def test_a_retry_loop_without_a_connector_is_not_delivery_machinery() -> None:
    """`max_retries` around a local database write is not delivery retry, and
    counting it would make the category mean nothing."""
    import ast

    sweep = _sweep()
    local = "max_retries = 3\n\ndef save():\n    pass\n"
    delivery = (
        "import httpx\n\nmax_retries = 3\n\ndef push():\n"
        "    return httpx.post('https://x')\n"
    )
    assert not sweep._owns_delivery_retry(ast.parse(local), local)
    assert sweep._owns_delivery_retry(ast.parse(delivery), delivery)


def test_tests_and_migrations_are_not_application_runtime(
    tmp_path: pathlib.Path,
) -> None:
    """A test that fakes a provider is how a connector is VERIFIED, and a
    connector in a migration is history. Counting either would freeze a number
    that retirement work cannot lower."""
    sweep = _sweep()
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "real.py").write_text("x = 1\n")
    (tmp_path / "app" / "test_fake.py").write_text("x = 1\n")
    (tmp_path / "app" / "tests").mkdir()
    (tmp_path / "app" / "tests" / "conftest.py").write_text("x = 1\n")
    (tmp_path / "app" / "migrations").mkdir()
    (tmp_path / "app" / "migrations" / "0001.py").write_text("x = 1\n")

    found = {path.name for path in sweep._runtime_files(tmp_path / "app")}
    assert found == {"real.py"}
