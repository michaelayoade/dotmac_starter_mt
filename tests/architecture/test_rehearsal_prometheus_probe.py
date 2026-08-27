"""Architecture-level sensitivity fixtures for the rehearsal's Prometheus verdict.

These drive the same CLI the shell script invokes. A source scan that merely
finds ``json.load`` cannot prove the parser accepts the two real transitions or
refuses the missing/ambiguous evidence that can masquerade as recovery.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from typing import Any

PROBE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts"
    / "rehearsal"
    / "prometheus_probe.py"
)
RULE_NAME = "RehearsalTargetDown"
SCRAPE_URL = "http://app:8000/metrics"


def _run(*args: str, document: object | str) -> subprocess.CompletedProcess[str]:
    payload = document if isinstance(document, str) else json.dumps(document)
    return subprocess.run(  # noqa: S603 - fixed interpreter/helper; args are test literals
        [sys.executable, str(PROBE), *args],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )


def _rule(
    state: str,
    *,
    alerts: list[dict[str, Any]],
    name: str = RULE_NAME,
    health: str = "ok",
) -> dict[str, Any]:
    return {
        "state": state,
        "name": name,
        "type": "alerting",
        "health": health,
        "alerts": alerts,
    }


def _rules_document(*rules: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {"groups": [{"name": "rehearsal", "rules": list(rules)}]},
    }


def _target(
    health: str,
    *,
    scrape_url: str = SCRAPE_URL,
    last_error: str = "",
) -> dict[str, str]:
    return {
        "scrapeUrl": scrape_url,
        "health": health,
        "lastError": last_error,
    }


def _targets_document(*targets: dict[str, str]) -> dict[str, Any]:
    return {"status": "success", "data": {"activeTargets": list(targets)}}


def test_firing_requires_the_named_rule_and_an_active_alert_instance() -> None:
    firing = _rules_document(
        _rule("firing", alerts=[{"labels": {"alertname": RULE_NAME}}])
    )
    accepted = _run("rule-is", RULE_NAME, "firing", document=firing)
    assert accepted.returncode == 0, accepted.stderr

    incoherent = _rules_document(_rule("firing", alerts=[]))
    refused = _run("rule-is", RULE_NAME, "firing", document=incoherent)
    assert refused.returncode == 1


def test_recovery_uses_the_rule_name_after_the_alert_instance_disappears() -> None:
    recovered = _rules_document(_rule("inactive", alerts=[]))
    result = _run("rule-is", RULE_NAME, "inactive", document=recovered)
    assert result.returncode == 0, result.stderr
    assert "active_alerts=0" in result.stdout
    assert "alertname" not in json.dumps(recovered)


def test_absent_duplicate_and_unhealthy_rules_fail_closed() -> None:
    absent = _run(
        "rule-is",
        RULE_NAME,
        "inactive",
        document=_rules_document(_rule("inactive", alerts=[], name="OtherRule")),
    )
    assert absent.returncode == 2
    assert "found 0" in absent.stderr

    duplicate = _run(
        "rule-is",
        RULE_NAME,
        "inactive",
        document=_rules_document(
            _rule("inactive", alerts=[]),
            _rule("inactive", alerts=[]),
        ),
    )
    assert duplicate.returncode == 2
    assert "found 2" in duplicate.stderr

    unhealthy = _run(
        "rule-is",
        RULE_NAME,
        "inactive",
        document=_rules_document(_rule("inactive", alerts=[], health="err")),
    )
    assert unhealthy.returncode == 1


def test_malformed_or_unsuccessful_rule_documents_fail_closed() -> None:
    malformed = _run("rule-is", RULE_NAME, "inactive", document="not-json")
    assert malformed.returncode == 2
    assert "not valid JSON" in malformed.stderr

    unsuccessful = _run(
        "rule-is",
        RULE_NAME,
        "inactive",
        document={"status": "error", "data": {}},
    )
    assert unsuccessful.returncode == 2
    assert "not success" in unsuccessful.stderr


def test_target_must_be_uniquely_present_and_match_the_expected_health() -> None:
    up = _run(
        "target-is",
        SCRAPE_URL,
        "up",
        document=_targets_document(_target("up")),
    )
    assert up.returncode == 0, up.stderr

    down = _run(
        "target-is",
        SCRAPE_URL,
        "down",
        document=_targets_document(_target("down", last_error="connection refused")),
    )
    assert down.returncode == 0, down.stderr

    wrong_state = _run(
        "target-is",
        SCRAPE_URL,
        "up",
        document=_targets_document(_target("down", last_error="connection refused")),
    )
    assert wrong_state.returncode == 1


def test_absent_duplicate_and_erroring_up_targets_fail_closed() -> None:
    absent = _run(
        "target-is",
        SCRAPE_URL,
        "up",
        document=_targets_document(
            _target("up", scrape_url="http://other:8000/metrics")
        ),
    )
    assert absent.returncode == 2
    assert "found 0" in absent.stderr

    duplicate = _run(
        "target-is",
        SCRAPE_URL,
        "up",
        document=_targets_document(_target("up"), _target("up")),
    )
    assert duplicate.returncode == 2
    assert "found 2" in duplicate.stderr

    stale_error = _run(
        "target-is",
        SCRAPE_URL,
        "up",
        document=_targets_document(_target("up", last_error="stale scrape error")),
    )
    assert stale_error.returncode == 1


def test_summaries_are_bounded_and_single_line() -> None:
    long_error = "first line\n" + ("x" * 500)
    result = _run(
        "target-summary",
        SCRAPE_URL,
        document=_targets_document(_target("down", last_error=long_error)),
    )
    assert result.returncode == 0
    assert result.stdout.count("\n") == 1
    assert len(result.stdout) < 300
    assert result.stdout.rstrip().endswith("...")
