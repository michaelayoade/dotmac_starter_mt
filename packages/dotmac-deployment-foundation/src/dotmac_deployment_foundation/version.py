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

#: ``0.3.0a2`` IS GONE, AND MUST NOT COME BACK. Do not "fix" this number back
#: down, and do not add a local segment such as ``0.3.0a2+dev`` to it — that was
#: the previous shape here and it was withdrawn on 2026-09-01, because it was an
#: annotation on a claim that should not have been made at all.
#:
#: What happened: ``0.3.0a2`` was built ONCE as candidate artifact 9740182233
#: from ``e930f878ce400b766b4a50feb0369021a28ab2fa``, never tagged and never
#: uploaded. Commit ``0f390a9aa93b0bb1cb78621ab1e9febc90bc48d2`` (#551) then
#: changed the facility's source under that same declared version, so the name
#: ``0.3.0a2`` came to mean two different contracts — the frozen bytes, and this
#: tree. A version naming two contracts is exactly what `AGENTS.md` rule 34
#: exists to prevent, and it was live.
#:
#: The repair is a NEW version, not an annotation on the old one. The frozen
#: artifact stays exactly as recorded — `docs/inventories/
#: foundation-candidate-0.3.0a2.json` is `CandidateArtifact.v1` and is preserved
#: byte-for-byte — and the judgement about it is APPENDED to
#: `docs/inventories/foundation-candidate-dispositions.json` as
#: `CandidateDisposition.v1`: invalidated, `publishable: false`, with the
#: invalidating commit and the reason. Nothing edits the receipt, because the
#: restore proof, the issuer stand-up and Lane 3 all bind to it.
#:
#: There is deliberately no longer a rule here permitting this tree to diverge
#: from a candidate while keeping its version. That permission is what let one
#: name cover two contracts; it was removed with the version move rather than
#: re-argued.
#:
#: ``0.3.0a3`` has NOT been built. It is a declared identity awaiting one
#: candidate build, and it is unpublished — recorded as such in
#: `docs/inventories/declared-publication-baseline.json`. It carries no local
#: segment because there is no published artifact for it to misdescribe; the
#: fleet's ``+dev`` rule applies to a tree claiming a version that WAS
#: published, and that premise is absent here.
#:
#: This value is load-bearing beyond metadata. ``VERSION`` sits inside the
#: canonical descriptor document, so changing it moves
#: ``io.dotmac.deployment.configuration.digest`` and the rendered
#: ``deploy/rendered/docker-compose.yml`` with it. Any change here is therefore
#: a re-render in the same commit (`make deployment-render`), and
#: `make deployment-check` is what fails if it was not.
VERSION: Final = "0.3.0a3"

__all__ = ["VERSION"]
