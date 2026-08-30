"""A descriptor-supplied manifest path must stay inside the staged release.

`manifest_digest` answers a gate: it says what THIS HOST is about to run, and
the executor compares it against the digest the plan approved. The path it
reads comes from the descriptor, which travels with the release and is exactly
the file an attacker edits if they can edit anything.

So the path decides *which file answers the gate*, and a gate that can be
pointed at a file of the writer's choosing is not a gate — it is a lookup that
always agrees, because the same actor supplies both the expectation and the
evidence.

Two halves, and neither is sufficient alone:

- **parse** (`spec.py`) refuses `..`, absolute paths and backslash separators.
  It fails loudly, on the way in, naming the offending key. It cannot see the
  filesystem.
- **read** (`providers/compose_host.py`) re-checks containment *after*
  `.resolve()`. Only this side can see a **symlink planted inside the deploy
  directory** — and `.resolve()` follows those, so a link named
  `manifest.json` pointing at `/etc/anything` sails past every syntactic check.

This file covers the PARSE half. The read half lives in
`test_deployment_foundation_compose_host.py`, beside the spec fixture and the
`make_effects` helper its assertions need.

Found by the #507 supersession audit. Written fresh against current `main`,
where both halves are absent.
"""

from __future__ import annotations

import pytest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.spec import _contained_relative_path

# ── the parse half ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "../../etc/passwd",
        "..",
        "sub/../../escape.json",
        "/etc/passwd",
        "/",
        "windows\\manifest.json",
        "",
    ],
)
def test_an_escaping_manifest_path_is_refused_at_parse(value: str) -> None:
    with pytest.raises(SpecError):
        _contained_relative_path(value, key="manifest_path", where="product.toml")


@pytest.mark.parametrize(
    "value",
    ["manifest.json", "./manifest.json", "nested/dir/manifest.json", "a.b/c.json"],
)
def test_an_ordinary_relative_manifest_path_is_accepted(value: str) -> None:
    """Sensitivity's other half — the validator must not refuse everything."""
    assert _contained_relative_path(value, key="manifest_path", where="p.toml") == value


def test_the_refusal_names_the_key_and_the_value() -> None:
    """A refusal a reader cannot act on sends them to the wrong file."""
    with pytest.raises(SpecError) as caught:
        _contained_relative_path("../x.json", key="manifest_path", where="p.toml")
    message = str(caught.value)
    assert "manifest_path" in message
    assert "../x.json" in message
