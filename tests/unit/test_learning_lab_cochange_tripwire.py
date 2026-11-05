"""L7 co-change tripwire for the dual completion/certificate implementation.

Completion recompute and certificate issuance exist twice by design
(Addendum 3, D7): Python (``completion`` / ``projections`` /
``certificates`` / ``events``) serves MCQ and override writes, while
the lr_0011 SQL function ``mod_learning.record_lab_result_v1`` is the
single lab ledger writer. Any edit to either implementation must be
reviewed against the other, so this unit-tier test pins both sides
together: a change to any pinned file, or to the lr_0011 function
body, fails here until the hashes are updated in the same reviewed
change that re-qualifies parity (hosted test L1).

No database is used: the lr_0011 prosrc is extracted from the
migration source constant by AST, not imported and not read back
from a catalog.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEARNING = REPO_ROOT / "packages/dotmac-learning/src/dotmac_learning"
MIGRATION = LEARNING / "migrations/versions/lr_0011_lab_result_capability.py"

#: sha256 of each Python module whose semantics the lr_0011 SQL port
#: mirrors, pinned together with the SQL body below.
_EXPECTED_MODULE_SHA256 = {
    "completion.py": (
        "a45d7a7fee18bb12526b2814486b1bcab00de38a4718a43aa96ecf18fb2b6217"
    ),
    "projections.py": (
        "5ccb1f85a0e6fde31bd789a5055eea82d7636ca83fb9b50ca40dc4109230c31b"
    ),
    "certificates.py": (
        "88fe7085c56fb8fbb33edb4f62732ae56c902f092e4ba4e281cc8fe2a041e92d"
    ),
    "events.py": "0579b9d39767d51af2389d1e19380868508ccc396f329f50d09d899c2b76430c",
}

#: sha256 of the lr_0011 function body (the text between the $func$
#: markers of the migration's ``_FUNCTION`` constant, i.e. prosrc).
_EXPECTED_LR0011_PROSRC_SHA256 = (
    "c957df243328ee06219ceda0128c498c3b853d237a05c6d7465143094f55c4ec"
)


def _lr0011_prosrc() -> str:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_FUNCTION"
            for target in node.targets
        ):
            function = ast.literal_eval(node.value)
            parts = function.split("$func$")
            assert len(parts) == 3, "lr_0011 _FUNCTION must use $func$ quoting"
            return parts[1]
    raise AssertionError("lr_0011 migration has no _FUNCTION constant")


def test_lab_dual_implementation_cochange_tripwire() -> None:
    actual = {
        name: hashlib.sha256((LEARNING / name).read_bytes()).hexdigest()
        for name in _EXPECTED_MODULE_SHA256
    }
    assert actual == _EXPECTED_MODULE_SHA256, (
        "a Python completion/projection/certificate/event module changed; "
        "review the lr_0011 SQL port for the same change (and vice versa), "
        "re-qualify hosted parity L1, then update these hashes together"
    )
    prosrc_sha256 = hashlib.sha256(_lr0011_prosrc().encode("utf-8")).hexdigest()
    assert prosrc_sha256 == _EXPECTED_LR0011_PROSRC_SHA256, (
        "the lr_0011 lab-result function body changed; review "
        "completion.py, projections.py, certificates.py and events.py "
        "for the same change, re-qualify hosted parity L1, then update "
        "these hashes together"
    )
