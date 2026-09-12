"""GitHub Actions workflow scripts must not discard a failed pipeline producer.

GitHub Actions supplies each ``run`` value to a shell as one script.  Its
default pipeline status is the last command's status, so ``resolver | tee``
can publish an apparently valid output file after ``resolver`` has refused its
input.  The index polling shape is different: enabling ``pipefail`` on
``curl | grep -q`` makes grep's successful early exit capable of turning into
curl's SIGPIPE failure.  It therefore has to acquire before it predicates.

This is deliberately a small shell-surface parser, not a raw text/count scan:
workflow YAML is parsed first, only executable ``run`` values are inspected,
and the lexer recognises unquoted, unescaped ``|`` operators while ignoring
single/double-quoted literals, escaped characters, and shell comments.  It
also joins backslash continuations.  The guard does not claim to parse shell
functions, command substitutions, or heredocs; those require a full shell
parser and must not conceal a pipeline in this repository's workflow scripts.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"


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
    try:
        return list(lexer)
    except ValueError:
        # The operator lexer below can still decide whether this shell source
        # holds a pipeline; a token-level special case simply does not match.
        return []


def _pipefail_setting(line: str) -> bool | None:
    tokens = _tokens(line)
    if tokens[:1] != ["set"] or "pipefail" not in tokens:
        return None
    return "+o" not in tokens


def _pipeline_operators(line: str) -> list[int]:
    """Return real shell pipeline operators in one logical command.

    ``||`` is shell boolean control flow, not a pipeline.  This lexer supports
    the simple shell surface used in Actions ``run`` blocks: quotes, escapes,
    comments, and continuation-joined commands.
    """
    operators: list[int] = []
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "#":
            break
        if (
            char == "|"
            and (index == 0 or line[index - 1] != "|")
            and (index + 1 == len(line) or line[index + 1] != "|")
        ):
            operators.append(index)
    return operators


def _is_curl_grep_conditional(line: str) -> bool:
    tokens = _tokens(line)
    if tokens[:1] != ["if"] or not _pipeline_operators(line) or "|" not in tokens:
        return False
    pipe = tokens.index("|")
    producer, consumer = tokens[1:pipe], tokens[pipe + 1 :]
    return producer[:1] == ["curl"] and consumer[:1] == ["grep"] and "-q" in consumer


def _unsafe_pipelines(script: str) -> list[str]:
    """Find pipeline forms whose failure status is ambiguous or masked."""
    unsafe: list[str] = []
    pipefail_enabled = False
    for line in _logical_lines(script):
        setting = _pipefail_setting(line)
        if setting is not None:
            pipefail_enabled = setting
        conditional_curl_grep = _is_curl_grep_conditional(line)
        if (
            _pipeline_operators(line)
            and not pipefail_enabled
            and not conditional_curl_grep
        ):
            unsafe.append(f"pipeline without preceding pipefail: {line}")
        if conditional_curl_grep:
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


def test_workflows_have_no_unsafe_pipelines() -> None:
    unsafe = [
        reason
        for path in sorted(WORKFLOW_DIR.glob("*.yml"))
        for reason in _workflow_unsafe_pipelines(path)
    ]
    assert not unsafe, "\n".join(unsafe)


def test_guard_detects_a_failed_producer_hidden_by_tee() -> None:
    """Sensitivity plant: this exact shape used to let a resolver failure pass."""
    script = 'resolver "$input" | tee -a "$GITHUB_OUTPUT"\n'
    assert _unsafe_pipelines(script) == [
        "pipeline without preceding pipefail: "
        'resolver "$input" | tee -a "$GITHUB_OUTPUT"'
    ]


def test_guard_accepts_a_tee_pipeline_only_after_pipefail() -> None:
    script = 'set -euo pipefail\nresolver "$input" | tee -a "$GITHUB_OUTPUT"\n'
    assert _unsafe_pipelines(script) == []


def test_guard_detects_a_non_tee_pipeline_without_pipefail() -> None:
    """Sensitivity plant: guarding tee alone would miss this producer failure."""
    script = 'resolver "$input" | sed -n "s/^value=//p"\n'
    assert _unsafe_pipelines(script) == [
        "pipeline without preceding pipefail: "
        'resolver "$input" | sed -n "s/^value=//p"'
    ]


def test_guard_does_not_treat_a_quoted_pipe_literal_as_a_pipeline() -> None:
    script = 'echo "documentation literal: resolver | sed"\n'
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
