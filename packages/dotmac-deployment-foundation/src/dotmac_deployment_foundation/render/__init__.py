"""Deterministic renderers.

Every renderer is a pure function from the descriptor to TEXT, with no template
engine anywhere. Two consequences, both deliberate:

- The package keeps zero runtime dependencies, so a build runner adopts it
  without adopting Jinja, PyYAML or a runtime.
- `dotmac-deploy render --check` is a byte comparison, and a reviewer reads a
  drift as an ordinary diff rather than as a re-render they have to trust.
"""

from __future__ import annotations

__all__ = ["compose", "nginx"]
