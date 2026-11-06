"""L7 co-change tripwire for the single completion/certificate writer.

Since lr_0012 (0.1.0a6) completion recompute and certificate issuance
have exactly one implementation: the SQL capabilities
``mod_learning.recompute_completion_v1`` and
``mod_learning.issue_certificate_v1``. The lr_0011 lab function
delegates its recompute/certificate tail to the former (replaced body
in lr_0012; the released lr_0011 bytes are unchanged and are what the
lr_0012 downgrade restores). Python ``completion`` / ``certificates``
delegate and rebuild detached facts; ``projections`` still defines the
best-score read whose ordering the SQL winners must match, and
``events`` rebuilds the emissions the SQL writers report. Any edit to
one side of this seam must be reviewed against the others, so this
unit-tier test pins them together: a change to any pinned module, or
to any pinned function body, fails here until the hashes are updated
in the same reviewed change that re-qualifies parity (hosted tests).

No database is used: each prosrc is extracted from its migration
source constant by AST, not imported and not read back from a catalog.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEARNING = REPO_ROOT / "packages/dotmac-learning/src/dotmac_learning"
LR0011 = LEARNING / "migrations/versions/lr_0011_lab_result_capability.py"
LR0012 = LEARNING / "migrations/versions/lr_0012_completion_capability.py"

#: sha256 of each Python module forming the delegation seam, pinned
#: together with the SQL bodies below.
_EXPECTED_MODULE_SHA256 = {
    "completion.py": (
        "593d14334bc0b4af1634c3ce95d3c46c9aba05af15dcb4dc5d076b9f92496dc0"
    ),
    "projections.py": (
        "5ccb1f85a0e6fde31bd789a5055eea82d7636ca83fb9b50ca40dc4109230c31b"
    ),
    "certificates.py": (
        "e0e858a501d43c061137b94dc94d46490c23a696d653e85d2867ef91f5b29690"
    ),
    "events.py": "e4580b68556aea5f1acebcf7dba64be2221b7c736d8df24ee18a7369f02c31e9",
}

#: sha256 of the released lr_0011 function body (prosrc). lr_0012 must
#: never edit it; its downgrade restores exactly this body.
_EXPECTED_LR0011_PROSRC_SHA256 = (
    "c957df243328ee06219ceda0128c498c3b853d237a05c6d7465143094f55c4ec"
)

#: sha256 of each lr_0012 function body (prosrc), by source constant.
_EXPECTED_LR0012_PROSRC_SHA256 = {
    "_ISSUE_FUNCTION": (
        "e3786ff3ed0d7e3ed1056b70ece7b9d15f9990d78d6aa67ca91004dd427798c1"
    ),
    "_RECOMPUTE_FUNCTION": (
        "65a07689d9b9dbb34b3f81ea42c7bcc37c67eddb8cfe9d82978021cd0556f21a"
    ),
    "_LAB_FUNCTION": (
        "32789a0acea0ad0131dfb606a1dc326c346cca107a805eff349f2862f232cfee"
    ),
}


def _prosrc(path: Path, constant: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == constant
            for target in node.targets
        ):
            function = ast.literal_eval(node.value)
            parts = function.split("$func$")
            assert len(parts) == 3, f"{path.name} {constant} must use $func$ quoting"
            return parts[1]
    raise AssertionError(f"{path.name} has no {constant} constant")


def test_single_writer_cochange_tripwire() -> None:
    actual = {
        name: hashlib.sha256((LEARNING / name).read_bytes()).hexdigest()
        for name in _EXPECTED_MODULE_SHA256
    }
    assert actual == _EXPECTED_MODULE_SHA256, (
        "a Python completion/projection/certificate/event module changed; "
        "review the lr_0012 SQL writers for the same change (and vice "
        "versa), re-qualify hosted parity, then update these hashes together"
    )
    prosrc_sha256 = hashlib.sha256(
        _prosrc(LR0011, "_FUNCTION").encode("utf-8")
    ).hexdigest()
    assert prosrc_sha256 == _EXPECTED_LR0011_PROSRC_SHA256, (
        "the released lr_0011 lab-result function body changed; released "
        "bytes never edit — repair additively in a new revision"
    )
    actual_lr0012 = {
        constant: hashlib.sha256(_prosrc(LR0012, constant).encode("utf-8")).hexdigest()
        for constant in _EXPECTED_LR0012_PROSRC_SHA256
    }
    assert actual_lr0012 == _EXPECTED_LR0012_PROSRC_SHA256, (
        "an lr_0012 writer body changed; review completion.py, "
        "projections.py, certificates.py and events.py for the same "
        "change, re-qualify hosted parity, then update these hashes together"
    )
