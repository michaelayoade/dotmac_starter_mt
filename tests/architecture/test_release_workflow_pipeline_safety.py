"""A closed failure-propagation policy for executable workflow pipelines.

Workflow YAML is parsed before inspecting only ``run`` scripts.  If a pipeline
executes in the outer shell, a command substitution, or a function body, the
first executable outer command is required to be exactly ``set -euo pipefail``.
No later ``set +o pipefail`` is allowed.  This deliberately avoids attempting
to infer shell-option scope through subshells, conditionals, or background
commands.

The lightweight lexer recognises executable unquoted ``|`` and ``|&``, shell
comments, quotes, and command substitutions.  Nested Bash/sh interpreters are
a separate closed boundary: pipeline-bearing ``-c`` or heredoc bodies require
their own ``-o pipefail`` and may not disable it.  Indirect ``-c`` source and
dynamic ``eval`` are refused rather than interpreted.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
PROLOGUE = "set -euo pipefail"
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_STRUCTURAL_PREFIXES = {"{", "}", "then", "do", "else", "elif", "if", "!", "("}


def _run_blocks(document: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    for job in document.get("jobs", {}).values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []):
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                blocks.append(step["run"])
    return blocks


def _logical_lines(script: str) -> list[str]:
    """Return continuation-joined executable outer-shell lines, skipping heredocs."""
    lines: list[str] = []
    pending = ""
    delimiter: str | None = None
    for raw_line in script.splitlines():
        if delimiter is not None:
            if raw_line.strip() == delimiter:
                delimiter = None
            continue
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        complete = pending + line
        pending = ""
        lines.append(complete)
        match = _HEREDOC.search(complete)
        if match is not None:
            delimiter = match.group(2)
    if pending:
        lines.append(pending)
    return lines


def _comment_starts(line: str, index: int) -> bool:
    return index == 0 or line[index - 1].isspace() or line[index - 1] in ";|&(){}"


def _command_substitution_end(line: str, start: int) -> int | None:
    depth = 1
    quote: str | None = None
    escaped = False
    for index in range(start + 2, len(line)):
        char = line[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and _comment_starts(line, index):
            break
        elif line.startswith("$(", index):
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _pipeline_operators(line: str) -> list[int]:
    """Find unquoted pipeline operators; ``||`` remains boolean control flow."""
    operators: list[int] = []
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
            break
        elif line.startswith("$(", index):
            end = _command_substitution_end(line, index)
            if end is not None:
                index = end
        elif (
            char == "|"
            and (index == 0 or line[index - 1] != "|")
            and (index + 1 == len(line) or line[index + 1] != "|")
        ):
            operators.append(index)
        index += 1
    return operators


def _command_substitutions(line: str) -> list[str]:
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
        elif quote == "'":
            if char == quote:
                quote = None
        elif quote:
            if line.startswith("$(", index):
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


def _commands(line: str) -> list[str]:
    """Split simple top-level command lists; this is not a shell evaluator."""
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


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        return list(lexer)
    except ValueError:
        return []


def _command_word(command: str) -> tuple[list[str], list[str]]:
    tokens = _tokens(command)
    index = 0
    while index < len(tokens) and tokens[index] in _STRUCTURAL_PREFIXES:
        index += 1
    return tokens[index : index + 1], tokens[index + 1 :]


def _is_pipefail_disable(command: str) -> bool:
    word, arguments = _command_word(command)
    return word == ["set"] and arguments[:2] == ["+o", "pipefail"]


def _is_eval(command: str) -> bool:
    word, _ = _command_word(command)
    return word == ["eval"]


def _is_curl_grep_conditional(command: str) -> bool:
    tokens = _tokens(command)
    if tokens[:1] != ["if"]:
        return False
    for operator in ("|", "|&"):
        if operator not in tokens:
            continue
        index = tokens.index(operator)
        return (
            tokens[1:2] == ["curl"]
            and tokens[index + 1 : index + 2] == ["grep"]
            and "-q" in tokens[index + 1 :]
        )
    return False


def _heredocs(script: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    lines = script.splitlines()
    index = 0
    while index < len(lines):
        opener = lines[index]
        match = _HEREDOC.search(opener)
        if match is None:
            index += 1
            continue
        delimiter = match.group(2)
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index].strip() != delimiter:
            body.append(lines[index])
            index += 1
        found.append((opener, "\n".join(body)))
        index += 1
    return found


def _child_shell(command: str) -> tuple[bool, str | None]:
    tokens = _tokens(command)
    for index, token in enumerate(tokens):
        if PurePosixPath(token).name not in {"bash", "sh"}:
            continue
        if "-c" not in tokens[index + 1 :]:
            continue
        command_index = tokens.index("-c", index + 1)
        child = tokens[command_index + 1] if command_index + 1 < len(tokens) else None
        options = tokens[index + 1 : command_index]
        hardened = any(
            options[position : position + 2] == ["-o", "pipefail"]
            for position in range(len(options))
        )
        return hardened, child
    return False, None


def _child_issues(command: str) -> list[str]:
    hardened, child = _child_shell(command)
    if child is None:
        return []
    if re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*|\$\{[^}]+\}|\$\(.*\)|`.*`", child):
        return [f"dynamic nested shell command is unsupported: {command}"]
    if not _contains_outer_pipeline(child):
        return []
    if not hardened:
        return [f"nested shell pipeline without child pipefail: {command}"]
    return _inner_issues(child)


def _contains_outer_pipeline(script: str) -> bool:
    return any(_pipeline_operators(line) for line in _logical_lines(script)) or any(
        _contains_outer_pipeline(body)
        for line in _logical_lines(script)
        for body in _command_substitutions(line)
    )


def _inner_issues(script: str) -> list[str]:
    """Inspect inherited outer shell contexts and a hardened child shell body."""
    unsafe: list[str] = []
    for line in _logical_lines(script):
        for command in _commands(line):
            if _is_pipefail_disable(command):
                unsafe.append(f"pipefail disable is forbidden: {command}")
            if _is_eval(command):
                unsafe.append(f"dynamic shell evaluation is unsupported: {command}")
            if _is_curl_grep_conditional(command):
                unsafe.append(f"conditional curl|grep pipeline: {command}")
            unsafe.extend(_child_issues(command))
        for body in _command_substitutions(line):
            unsafe.extend(_inner_issues(body))
    for opener, body in _heredocs(script):
        if not _contains_outer_pipeline(body):
            continue
        tokens = _tokens(opener)
        shell_index = next(
            (
                index
                for index, token in enumerate(tokens)
                if PurePosixPath(token).name in {"bash", "sh"}
            ),
            None,
        )
        options = tokens[shell_index + 1 :] if shell_index is not None else []
        hardened = any(
            options[index : index + 2] == ["-o", "pipefail"]
            for index in range(len(options))
        )
        if not hardened:
            unsafe.append(f"heredoc pipeline without child pipefail: {opener.strip()}")
        else:
            unsafe.extend(_inner_issues(body))
    return unsafe


def _unsafe_pipelines(script: str) -> list[str]:
    has_pipeline = _contains_outer_pipeline(script)
    unsafe = _inner_issues(script)
    if has_pipeline and _logical_lines(script)[:1] != [PROLOGUE]:
        unsafe.insert(0, f"pipeline-bearing run block must begin with {PROLOGUE!r}")
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


def test_guard_rejects_pipeline_without_the_first_command_prologue() -> None:
    script = 'resolver "$input" | tee -a "$GITHUB_OUTPUT"\n'
    assert _unsafe_pipelines(script) == [
        "pipeline-bearing run block must begin with 'set -euo pipefail'"
    ]


def test_guard_accepts_a_hardened_outer_pipeline() -> None:
    script = 'set -euo pipefail\nresolver "$input" | sed -n "s/^value=//p"\n'
    assert _unsafe_pipelines(script) == []


def test_guard_rejects_all_second_review_scope_plants() -> None:
    plants = (
        '( set -o pipefail ); false | tee -a "$GITHUB_OUTPUT"\n',
        'set -o pipefail & false | tee -a "$GITHUB_OUTPUT"\n',
        'if ( set -o pipefail ); then false | tee -a "$GITHUB_OUTPUT"; fi\n',
        'set -o pipefail | cat; false | tee -a "$GITHUB_OUTPUT"\n',
    )
    for script in plants:
        assert _unsafe_pipelines(script) == [
            "pipeline-bearing run block must begin with 'set -euo pipefail'"
        ]


def test_guard_accepts_no_later_prologue_and_rejects_any_disable() -> None:
    safe = 'set -euo pipefail\nfalse | tee -a "$GITHUB_OUTPUT"\n'
    disabled = safe + "set +o pipefail\n"
    assert _unsafe_pipelines(safe) == []
    assert _unsafe_pipelines(disabled) == [
        "pipefail disable is forbidden: set +o pipefail"
    ]


def test_guard_refuses_curl_grep_and_pipe_and_stderr_forms() -> None:
    for operator in ("|", "|&"):
        script = (
            "set -euo pipefail\n"
            f'if curl -sf "$IDX" {operator} grep -q "$VERSION"; then\n'
            "fi\n"
        )
        assert _unsafe_pipelines(script) == [
            "conditional curl|grep pipeline: "
            f'if curl -sf "$IDX" {operator} grep -q "$VERSION"'
        ]


def test_guard_recurses_into_quoted_command_substitutions() -> None:
    script = 'set -euo pipefail\nTAG="$(false | tee -a "$GITHUB_OUTPUT")"\n'
    assert _unsafe_pipelines(script) == []


def test_guard_keeps_a_genuinely_inert_quoted_pipe_literal() -> None:
    assert _unsafe_pipelines('echo "documentation literal: resolver | sed"\n') == []


def test_guard_refuses_nested_shell_pipeline_without_its_own_pipefail() -> None:
    for shell in ("bash", "/bin/bash", "/bin/sh"):
        script = f"{shell} -c 'false | tee -a \"$GITHUB_OUTPUT\"'\n"
        assert _unsafe_pipelines(script) == [
            f"nested shell pipeline without child pipefail: {shell} -c "
            "'false | tee -a \"$GITHUB_OUTPUT\"'"
        ]


def test_guard_accepts_hardened_child_and_rejects_its_later_disable() -> None:
    safe = "bash -o pipefail -c 'false | tee -a \"$GITHUB_OUTPUT\"'\n"
    disabled = "bash -o pipefail -c 'set +o pipefail; false | tee'\n"
    assert _unsafe_pipelines(safe) == []
    assert _unsafe_pipelines(disabled) == [
        "pipefail disable is forbidden: set +o pipefail"
    ]


def test_guard_refuses_indirect_nested_shell_source() -> None:
    for child in ('"$SCRIPT"', '"${SCRIPT}"', "\"$(printf 'false | tee')\""):
        script = f"SCRIPT='false | tee'; bash -c {child}\n"
        assert _unsafe_pipelines(script) == [
            f"dynamic nested shell command is unsupported: bash -c {child}"
        ]


def test_guard_keeps_literal_child_source_with_ordinary_data_expansion() -> None:
    script = "bash -o pipefail -c 'false | tee -a \"$VAR\"'\n"
    assert _unsafe_pipelines(script) == []


def test_guard_refuses_pipeline_heredoc_without_a_hardened_child() -> None:
    script = "bash <<'SCRIPT'\nfalse | tee -a \"$GITHUB_OUTPUT\"\nSCRIPT\n"
    assert _unsafe_pipelines(script) == [
        "heredoc pipeline without child pipefail: bash <<'SCRIPT'"
    ]


def test_guard_accepts_hardened_pipeline_heredoc() -> None:
    script = "bash -o pipefail <<'SCRIPT'\nfalse | tee -a \"$GITHUB_OUTPUT\"\nSCRIPT\n"
    assert _unsafe_pipelines(script) == []


def test_guard_refuses_dynamic_eval() -> None:
    assert _unsafe_pipelines("eval 'false | tee'\n") == [
        "dynamic shell evaluation is unsupported: eval 'false | tee'"
    ]


def test_recover_workflow_requires_its_first_line_pipefail_prologue() -> None:
    document = yaml.safe_load(
        (WORKFLOW_DIR / "recover-module-release.yml").read_text(encoding="utf-8")
    )
    runs = [
        step["run"]
        for step in document["jobs"]["recover"]["steps"]
        if step.get("name") == "Tag the recovered release"
    ]
    assert len(runs) == 1
    mutated = runs[0].replace("set -euo pipefail\n", "", 1)
    assert _unsafe_pipelines(mutated)[0] == (
        "pipeline-bearing run block must begin with 'set -euo pipefail'"
    )
