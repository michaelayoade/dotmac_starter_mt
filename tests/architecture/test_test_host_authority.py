"""The current test-host rule must not regress to a production control plane."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEDICATED_TEST_HOST = "85.190.246.211"


def _section(text: str, *, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _violations(agents: str, contributing: str) -> tuple[str, ...]:
    problems: list[str] = []
    for name, section in (("AGENTS", agents), ("CONTRIBUTING", contributing)):
        if DEDICATED_TEST_HOST not in section:
            problems.append(f"{name} does not name the dedicated test host")
        if "Observer" not in section or "not a general test runner" not in section:
            problems.append(f"{name} does not preserve the Observer boundary")
        if "fresh isolated" not in section:
            problems.append(f"{name} does not require an isolated test workspace")
    forbidden = (
        "Tests run on Dotmac Observer",
        "Run every focused, unit, architecture,\n"
        "integration, migration, browser, and full-suite test on the Dotmac Observer",
        "# On Observer only:",
        "disposable Observer port",
    )
    for fragment in forbidden:
        if fragment in agents or fragment in contributing:
            problems.append(f"superseded test-host instruction remains: {fragment!r}")
    return tuple(problems)


def _live_sections() -> tuple[str, str]:
    agents = _section(
        (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
        start="## Validation before any commit",
        end="## Process",
    )
    contributing = _section(
        (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"),
        start="## Gates before every commit",
        end="## Test-first expectations",
    )
    return agents, contributing


def test_test_host_authority_names_the_dedicated_server_and_excludes_observer() -> None:
    agents, contributing = _live_sections()

    assert _violations(agents, contributing) == ()


def test_test_host_authority_detector_rejects_the_superseded_observer_rule() -> None:
    agents, contributing = _live_sections()
    regressed = agents.replace(
        "the dedicated fleet\ntest server (`root@85.190.246.211`)",
        "the Dotmac Observer\nserver (SSH alias `observe`)",
    ).replace(
        "Dotmac Observer is the observability/OpenBao/Knowledge control plane,\n"
        "not a general test runner; ",
        "",
    )

    assert _violations(regressed, contributing)
