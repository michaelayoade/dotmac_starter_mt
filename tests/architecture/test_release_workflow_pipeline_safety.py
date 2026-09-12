"""GitHub Actions workflow scripts must not discard a failed pipeline producer.

GitHub Actions supplies each ``run`` value to a shell as one script.  Its
default pipeline status is the last command's status, so ``resolver | tee``
can publish an apparently valid output file after ``resolver`` has refused its
input.  The index polling shape is different: enabling ``pipefail`` on
``curl | grep -q`` makes grep's successful early exit capable of turning into
curl's SIGPIPE failure.  It therefore has to acquire before it predicates.

This is deliberately a small shell-surface parser, not a raw text/count scan:
workflow YAML is parsed first, only executable ``run`` values are inspected,
and the lexer recognises unquoted, unescaped ``|``/``|&`` operators while
ignoring genuinely inert single/double-quoted literals, escaped characters,
and shell comments.  It recursively inspects command substitutions, joins
backslash continuations, and follows ``set +/-o pipefail`` in command order.

This is deliberately a bounded policy rather than an attempt to execute or
fully parse Bash.  A nested ``bash``/``sh -c`` or a heredoc whose body has a
pipeline is refused unless that child invocation explicitly enables pipefail;
functions and other opaque shell evaluation are not accepted as an exemption.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


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
    heredoc_delimiter: str | None = None
    for raw_line in script.splitlines():
        if heredoc_delimiter is not None:
            if raw_line.strip() == heredoc_delimiter:
                heredoc_delimiter = None
            continue
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        lines.append(pending + line)
        pending = ""
        match = _HEREDOC.search(line)
        if match is not None:
            heredoc_delimiter = match.group(2)
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
    prefixes = {"{", "}", "then", "do", "else", "elif", "if", "!", "("}
    command_index = 0
    while command_index < len(tokens) and tokens[command_index] in prefixes:
        command_index += 1
    if command_index >= len(tokens) or tokens[command_index] != "set":
        return None
    following = tokens[command_index + 1 :]
    if "pipefail" in following:
        return "+o" not in following[: following.index("pipefail")]
    return None


def _comment_starts(line: str, index: int) -> bool:
    """Bash starts an unquoted comment only at the beginning of a word."""
    return index == 0 or line[index - 1].isspace() or line[index - 1] in ";|&(){}"


def _command_substitution_end(line: str, start: int) -> int | None:
    """Find the close of ``$(`` while honouring its nested quote surface."""
    depth = 1
    quote: str | None = None
    escaped = False
    for index in range(start + 2, len(line)):
        char = line[index]
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
        if char == "#" and _comment_starts(line, index):
            break
        if line.startswith("$(", index):
            depth += 1
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


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
            if quote == '"' and line.startswith("$(", index):
                end = _command_substitution_end(line, index)
                if end is not None:
                    continue
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "#" and _comment_starts(line, index):
            break
        if (
            char == "|"
            and (index == 0 or line[index - 1] != "|")
            and (index + 1 == len(line) or line[index + 1] != "|")
        ):
            operators.append(index)
    return operators


def _top_level_commands(line: str) -> list[str]:
    """Split a logical shell line at executable ``;``, ``&&``, ``||``, and ``&``."""
    commands: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if quote == '"' and line.startswith("$(", index):
                end = _command_substitution_end(line, index)
                if end is not None:
                    index = end
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and _comment_starts(line, index):
            commands.append(line[start:index])
            break
        elif line.startswith("$(", index):
            end = _command_substitution_end(line, index)
            if end is not None:
                index = end
        elif char == ";":
            commands.append(line[start:index])
            start = index + 1
        elif line.startswith("&&", index) or line.startswith("||", index):
            commands.append(line[start:index])
            index += 1
            start = index + 1
        elif char == "&" and (index == 0 or line[index - 1] != "|"):
            commands.append(line[start:index])
            start = index + 1
        index += 1
    else:
        commands.append(line[start:])
    return [command.strip() for command in commands if command.strip()]


def _command_substitutions(line: str) -> list[str]:
    """Return executable ``$(...)`` bodies, including ones inside double quotes."""
    bodies: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if quote == '"' and line.startswith("$(", index):
                end = _command_substitution_end(line, index)
                if end is not None:
                    bodies.append(line[index + 2 : end])
                    index = end
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and _comment_starts(line, index):
            break
        elif line.startswith("$(", index):
            end = _command_substitution_end(line, index)
            if end is not None:
                bodies.append(line[index + 2 : end])
                index = end
        index += 1
    return bodies


def _is_curl_grep_conditional(line: str) -> bool:
    tokens = _tokens(line)
    pipeline_tokens = ("|", "|&")
    if (
        tokens[:1] != ["if"]
        or not _pipeline_operators(line)
        or not any(token in pipeline_tokens for token in tokens)
    ):
        return False
    pipe = next(index for index, token in enumerate(tokens) if token in pipeline_tokens)
    producer, consumer = tokens[1:pipe], tokens[pipe + 1 :]
    return producer[:1] == ["curl"] and consumer[:1] == ["grep"] and "-q" in consumer


def _nested_shell_issues(line: str) -> list[str]:
    """Inspect a pipeline passed to another shell from that child's option state."""
    unsafe: list[str] = []
    tokens = _tokens(line)
    for index, token in enumerate(tokens):
        if token not in {"bash", "sh"} or "-c" not in tokens[index + 1 :]:
            continue
        command_index = tokens.index("-c", index + 1)
        if command_index + 1 == len(tokens):
            continue
        child_script = tokens[command_index + 1]
        if _pipeline_operators(child_script):
            options = tokens[index + 1 : command_index]
            child_pipefail = any(
                options[position : position + 2] == ["-o", "pipefail"]
                for position in range(len(options))
            )
            if not child_pipefail:
                unsafe.append(f"nested shell pipeline without child pipefail: {line}")
            else:
                unsafe.extend(_unsafe_pipelines_with_state(child_script, True))
    return unsafe


_FUNCTION = re.compile(
    r"(?:^|[;\n])\s*(?:function\s+)?[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*\{"
)


def _unsafe_heredoc_pipelines(script: str) -> list[str]:
    """Refuse pipeline-bearing heredocs without an explicitly hardened child."""
    unsafe: list[str] = []
    lines = script.splitlines()
    index = 0
    while index < len(lines):
        opener = lines[index]
        match = _HEREDOC.search(opener)
        if match is None:
            index += 1
            continue
        delimiter = match.group(2)
        body: list[str] = []
        index += 1
        while index < len(lines) and lines[index].strip() != delimiter:
            body.append(lines[index])
            index += 1
        if any(_pipeline_operators(line) for line in body):
            if "bash -o pipefail" in opener or "sh -o pipefail" in opener:
                unsafe.extend(_unsafe_pipelines_with_state("\n".join(body), True))
            else:
                unsafe.append(
                    f"heredoc pipeline without child pipefail: {opener.strip()}"
                )
        index += 1
    return unsafe


def _opaque_shell_issues(script: str) -> list[str]:
    """Fail closed for pipeline-bearing function bodies and dynamic evaluation."""
    unsafe: list[str] = []
    if _FUNCTION.search(script) and any(
        _pipeline_operators(line) for line in _logical_lines(script)
    ):
        unsafe.append("pipeline in function definition is unsupported")
    for line in _logical_lines(script):
        if "eval" in _tokens(line):
            unsafe.append(f"dynamic shell evaluation is unsupported: {line}")
    return unsafe


def _unsafe_pipelines(script: str) -> list[str]:
    """Find pipeline forms whose failure status is ambiguous or masked."""
    unsafe: list[str] = []
    pipefail_enabled = False
    for line in _logical_lines(script):
        for command in _top_level_commands(line):
            setting = _pipefail_setting(command)
            if setting is not None:
                pipefail_enabled = setting
            conditional_curl_grep = _is_curl_grep_conditional(command)
            if (
                _pipeline_operators(command)
                and not pipefail_enabled
                and not conditional_curl_grep
            ):
                unsafe.append(f"pipeline without preceding pipefail: {command}")
            if conditional_curl_grep:
                unsafe.append(f"conditional curl|grep pipeline: {command}")
            unsafe.extend(_nested_shell_issues(command))
            for body in _command_substitutions(command):
                unsafe.extend(_unsafe_pipelines_with_state(body, pipefail_enabled))
    unsafe.extend(_unsafe_heredoc_pipelines(script))
    unsafe.extend(_opaque_shell_issues(script))
    return unsafe


def _unsafe_pipelines_with_state(script: str, pipefail_enabled: bool) -> list[str]:
    """Apply the same policy to a command substitution's inherited shell state."""
    unsafe: list[str] = []
    for line in _logical_lines(script):
        for command in _top_level_commands(line):
            setting = _pipefail_setting(command)
            if setting is not None:
                pipefail_enabled = setting
            if _pipeline_operators(command) and not pipefail_enabled:
                unsafe.append(f"pipeline without preceding pipefail: {command}")
            if _is_curl_grep_conditional(command):
                unsafe.append(f"conditional curl|grep pipeline: {command}")
            unsafe.extend(_nested_shell_issues(command))
            for body in _command_substitutions(command):
                unsafe.extend(_unsafe_pipelines_with_state(body, pipefail_enabled))
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


def test_guard_tracks_pipefail_in_command_order_across_and_lists() -> None:
    unsafe_first = 'false | tee -a "$GITHUB_OUTPUT" && set -o pipefail\n'
    safe_first = 'set -o pipefail && false | tee -a "$GITHUB_OUTPUT"\n'
    assert _unsafe_pipelines(unsafe_first) == [
        'pipeline without preceding pipefail: false | tee -a "$GITHUB_OUTPUT"'
    ]
    assert _unsafe_pipelines(safe_first) == []


def test_guard_does_not_treat_a_set_argument_as_the_set_builtin() -> None:
    script = 'echo set -o pipefail; false | tee -a "$GITHUB_OUTPUT"\n'
    assert _unsafe_pipelines(script) == [
        'pipeline without preceding pipefail: false | tee -a "$GITHUB_OUTPUT"'
    ]


def test_guard_does_not_treat_pipeline_data_as_the_set_builtin() -> None:
    script = 'printf "%s" set -o pipefail | tee -a "$GITHUB_OUTPUT"\n'
    assert _unsafe_pipelines(script) == [
        "pipeline without preceding pipefail: "
        'printf "%s" set -o pipefail | tee -a "$GITHUB_OUTPUT"'
    ]


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


def test_guard_detects_a_pipeline_inside_a_quoted_command_substitution() -> None:
    script = 'TAG="$(false | tee -a "$GITHUB_OUTPUT")"\n'
    assert _unsafe_pipelines(script) == [
        'pipeline without preceding pipefail: false | tee -a "$GITHUB_OUTPUT"'
    ]


def test_guard_refuses_a_pipeline_hidden_in_a_nested_shell() -> None:
    script = "bash -c 'false | tee -a \"$GITHUB_OUTPUT\"'\n"
    assert _unsafe_pipelines(script) == [
        "nested shell pipeline without child pipefail: "
        "bash -c 'false | tee -a \"$GITHUB_OUTPUT\"'"
    ]


def test_guard_tracks_a_same_line_pipefail_disable_inside_a_group() -> None:
    script = 'set -o pipefail; { set +o pipefail; false | tee -a "$GITHUB_OUTPUT"; }\n'
    assert _unsafe_pipelines(script) == [
        'pipeline without preceding pipefail: false | tee -a "$GITHUB_OUTPUT"'
    ]


def test_guard_refuses_a_pipe_and_stderr_curl_conditional_even_with_pipefail() -> None:
    script = (
        'set -o pipefail; if curl -sf "$IDX" |& grep -q "$VERSION"; '
        "then exit 0; fi\n"
    )
    assert _unsafe_pipelines(script) == [
        'conditional curl|grep pipeline: if curl -sf "$IDX" |& grep -q "$VERSION"'
    ]


def test_guard_does_not_treat_a_midword_hash_as_a_comment() -> None:
    script = 'false#masked | tee -a "$GITHUB_OUTPUT"\n'
    assert _unsafe_pipelines(script) == [
        'pipeline without preceding pipefail: false#masked | tee -a "$GITHUB_OUTPUT"'
    ]


def test_guard_refuses_a_heredoc_pipeline_without_child_pipefail() -> None:
    script = "bash <<'SCRIPT'\nfalse | tee -a \"$GITHUB_OUTPUT\"\nSCRIPT\n"
    assert _unsafe_pipelines(script) == [
        "heredoc pipeline without child pipefail: bash <<'SCRIPT'",
    ]


def test_guard_accepts_a_heredoc_pipeline_with_child_pipefail() -> None:
    script = "bash -o pipefail <<'SCRIPT'\nfalse | tee -a \"$GITHUB_OUTPUT\"\nSCRIPT\n"
    assert _unsafe_pipelines(script) == []


def test_guard_refuses_a_pipeline_in_a_function_even_if_outer_pipefail_is_set() -> None:
    script = 'set -o pipefail\nresolver() { false | tee -a "$GITHUB_OUTPUT"; }\n'
    assert _unsafe_pipelines(script) == [
        "pipeline in function definition is unsupported"
    ]


def test_recover_workflow_pipefail_protects_command_substitution() -> None:
    document = yaml.safe_load(
        (WORKFLOW_DIR / "recover-module-release.yml").read_text(encoding="utf-8")
    )
    recovery_runs = [
        step["run"]
        for step in document["jobs"]["recover"]["steps"]
        if step.get("name") == "Tag the recovered release"
    ]
    assert len(recovery_runs) == 1
    mutated = recovery_runs[0].replace("set -euo pipefail\n", "", 1)
    assert "set -euo pipefail" not in mutated
    assert any(
        reason.startswith("pipeline without preceding pipefail: python ")
        for reason in _unsafe_pipelines(mutated)
    )


def test_guard_detects_the_sigpipe_prone_conditional_pipeline() -> None:
    """Sensitivity plant: this must be acquisition plus predicate, not pipefail."""
    script = 'if curl -sf "$IDX" | grep -q "$VERSION"; then\n  exit 0\nfi\n'
    assert _unsafe_pipelines(script) == [
        'conditional curl|grep pipeline: if curl -sf "$IDX" | grep -q "$VERSION"'
    ]


def test_guard_accepts_acquisition_followed_by_a_predicate() -> None:
    script = (
        'if INDEX_PAGE="$(curl -sf "$IDX")" \\\n'
        '  && grep -q "$VERSION" <<<"${INDEX_PAGE}"; then\n'
        "  exit 0\n"
        "fi\n"
    )
    assert _unsafe_pipelines(script) == []
