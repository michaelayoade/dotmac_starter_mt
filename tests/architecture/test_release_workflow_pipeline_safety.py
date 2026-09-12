"""Release workflows must not discard a failed pipeline producer.

GitHub Actions supplies each ``run`` value to a shell as one script.  Its
default pipeline status is the last command's status, so ``resolver | tee``
can publish an apparently valid output file after ``resolver`` has refused its
input.  The index polling shape is different: enabling ``pipefail`` on
``curl | grep -q`` makes grep's successful early exit capable of turning into
curl's SIGPIPE failure.  It therefore has to acquire before it predicates.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    "release-module.yml",
    "recover-module-release.yml",
    "release-adapter.yml",
    "release-connector.yml",
    "release-contract.yml",
    "release-facility.yml",
    "release-ui.yml",
    "foundation-candidate.yml",
    "foundation-candidate-attestation.yml",
    "exposure-rehearsal.yml",
)


def _run_blocks(document: dict[str, Any]) -> list[str]:
    """Return executable step scripts from a parsed GitHub Actions document."""
    blocks: list[str] = []
    for job in document.get("jobs", {}).values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []):
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                blocks.append(step["run"])
    return blocks


def _logical_lines(script: str) -> list[str]:
    """Join shell continuations while retaining command order within a run block."""
    lines: list[str] = []
    pending = ""
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        lines.append(pending + line)
        pending = ""
    if pending:
        lines.append(pending)
    return lines


def _tokens(line: str) -> list[str]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars="|&;")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def _has_pipefail(line: str) -> bool:
    tokens = _tokens(line)
    return tokens[:1] == ["set"] and "pipefail" in tokens


def _is_tee_pipeline(line: str) -> bool:
    tokens = _tokens(line)
    return "|" in tokens and "tee" in tokens[tokens.index("|") + 1 :]


def _is_curl_grep_conditional(line: str) -> bool:
    tokens = _tokens(line)
    if tokens[:1] != ["if"] or "|" not in tokens:
        return False
    pipe = tokens.index("|")
    producer, consumer = tokens[1:pipe], tokens[pipe + 1 :]
    return producer[:1] == ["curl"] and consumer[:1] == ["grep"] and "-q" in consumer


def _unsafe_pipelines(script: str) -> list[str]:
    """Find pipeline forms whose failure status is ambiguous or masked."""
    unsafe: list[str] = []
    pipefail_enabled = False
    for line in _logical_lines(script):
        if _has_pipefail(line):
            pipefail_enabled = True
        if _is_tee_pipeline(line) and not pipefail_enabled:
            unsafe.append(f"tee pipeline without preceding pipefail: {line}")
        if _is_curl_grep_conditional(line):
            unsafe.append(f"conditional curl|grep pipeline: {line}")
    return unsafe


def _workflow_unsafe_pipelines(path: Path) -> list[str]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), path
    return [
        f"{path.name}: {reason}"
        for block in _run_blocks(document)
        for reason in _unsafe_pipelines(block)
    ]


def test_release_workflows_have_no_unsafe_pipelines() -> None:
    unsafe = [
        reason
        for name in WORKFLOWS
        for reason in _workflow_unsafe_pipelines(ROOT / ".github" / "workflows" / name)
    ]
    assert not unsafe, "\n".join(unsafe)


def test_guard_detects_a_failed_producer_hidden_by_tee() -> None:
    """Sensitivity plant: this exact shape used to let a resolver failure pass."""
    script = 'resolver "$input" | tee -a "$GITHUB_OUTPUT"\n'
    assert _unsafe_pipelines(script) == [
        "tee pipeline without preceding pipefail: "
        'resolver "$input" | tee -a "$GITHUB_OUTPUT"'
    ]


def test_guard_accepts_a_tee_pipeline_only_after_pipefail() -> None:
    script = 'set -euo pipefail\nresolver "$input" | tee -a "$GITHUB_OUTPUT"\n'
    assert _unsafe_pipelines(script) == []


def test_guard_detects_the_sigpipe_prone_conditional_pipeline() -> None:
    """Sensitivity plant: this must be acquisition plus predicate, not pipefail."""
    script = 'if curl -sf "$IDX" | grep -q "$VERSION"; then\n  exit 0\nfi\n'
    assert _unsafe_pipelines(script) == [
        'conditional curl|grep pipeline: if curl -sf "$IDX" | grep -q "$VERSION"; then'
    ]


def test_guard_accepts_acquisition_followed_by_a_predicate() -> None:
    script = (
        'if INDEX_PAGE="$(curl -sf "$IDX")" \\\n'
        '  && grep -q "$VERSION" <<<"${INDEX_PAGE}"; then\n'
        "  exit 0\n"
        "fi\n"
    )
    assert _unsafe_pipelines(script) == []
