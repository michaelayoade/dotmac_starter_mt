"""ADR-0011 (static half): no read function touches the environment.

The runtime half (`tests/unit/test_settings_resolution_ignores_env.py`) catches
an environment read however it is spelled, but only on paths a test drives.
This covers every read function, including ones no test exercises — and names
the single function that IS allowed to read the environment, so that permission
is a listed exception rather than an accident.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dotmac_kernel import settings_resolver as sr

RESOLVER = Path(sr.__file__)

# Reading the environment is legitimate in exactly one place: the bootstrap
# that turns `env_var` into a real row at startup. Everything else on the
# settings path is a read, and a read answers from rows and defaults.
ENV_READERS_ALLOWED = {"seed_settings_from_env"}

ENV_ACCESS = ("environ", "getenv")


def _functions_touching_env(path: Path) -> set[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    offenders: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        segment = ast.get_source_segment(src, node) or ""
        if any(token in segment for token in ENV_ACCESS):
            offenders.add(node.name)
    return offenders


def test_only_the_bootstrap_reads_the_environment() -> None:
    touching = _functions_touching_env(RESOLVER)
    unexpected = touching - ENV_READERS_ALLOWED
    assert not unexpected, (
        f"{sorted(unexpected)} read the environment. Settings resolution "
        "answers from stored rows and spec defaults; `env_var` is a bootstrap "
        "input consumed once by `seed_settings_from_env` (ADR-0011). An env "
        "read on the resolution path makes the environment a second authority "
        "over a value the settings screen claims to own."
    )


def test_the_bootstrap_still_reads_it() -> None:
    """The allowlist must describe reality. If `seed_settings_from_env` stops
    reading the environment, `env_var` has quietly become inert and every spec
    declaring one is lying."""
    assert "seed_settings_from_env" in _functions_touching_env(RESOLVER)


def test_the_scan_would_notice_a_reintroduced_read(tmp_path: Path) -> None:
    """Sensitivity proof: a passing suite must mean the rule holds, not that
    the detector is broken."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import os\n\n\ndef _finish():\n    return os.environ.get('X')\n",
        encoding="utf-8",
    )
    assert _functions_touching_env(planted) == {"_finish"}


@pytest.mark.parametrize(
    "name",
    ["resolve", "resolve_value", "resolve_many", "resolve_with_source", "_finish"],
)
def test_named_read_functions_are_clean(name: str) -> None:
    """Spelled out as well as covered by the sweep above, so a reviewer sees
    exactly which functions carry the guarantee."""
    assert name not in _functions_touching_env(RESOLVER)
