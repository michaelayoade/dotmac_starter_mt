"""The facility's own version, in a LEAF module.

It used to be a literal in ``__init__.py``, which was fine while nothing needed
to read it. `IngressPolicy.v1` needs it: the exact facility version goes inside
the canonical ingress document, because ``exposure = "public"`` is a word whose
meaning is the socket THIS version's renderer emits, and a facility upgrade
must not be able to change a running exposure under an unchanged plan digest.

A submodule importing ``__version__`` from the package ``__init__`` would be a
cycle, so the value lives here and ``__init__`` re-exports it. One definition,
two names, no drift — and `test_deployment_foundation_ingress_policy.py` checks
it against ``pyproject.toml``.
"""

from __future__ import annotations

from typing import Final

VERSION: Final = "0.3.0a2+dev"

__all__ = ["VERSION"]
