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

#: NO DEVELOPMENT MARKER HERE, DELIBERATELY. Do not "fix" this to
#: ``0.3.0a2+dev``; it was tried on 2026-08-31 and reverted, and the reason is
#: specific to this distribution rather than a matter of taste.
#:
#: The fleet rule that moves a declared version to a PEP 440 local marker after
#: a release is scoped to PUBLISHED versions: a tree claiming a published
#: version must be byte-identical to that version's published artifact, so a
#: diverged tree must stop claiming it. ``0.3.0a2`` is NOT published. It is a
#: frozen CANDIDATE (artifact 9740182233, built once from
#: e930f878ce400b766b4a50feb0369021a28ab2fa, never tagged, never uploaded).
#: There is no published artifact for this tree to make a false claim about, so
#: the rule's premise is absent.
#:
#: Applying the marker anyway does active harm, because THIS value is an input
#: to the deployment's identity. ``VERSION`` sits inside the canonical
#: descriptor document, so a local segment moves
#: ``io.dotmac.deployment.configuration.digest`` off the frozen
#: ``sha256:f481cfa2…`` and moves ``deploy/rendered/docker-compose.yml`` with
#: it -- two of the candidate's five frozen components -- invalidating the
#: artifact that the first authorization receipt, Lane 3 and both product
#: cutovers all bind to. Measured, not argued: with the marker, ``render
#: --check`` fails on the compose asset; without it, all three rendered assets
#: match the frozen digests.
#:
#: The marker would also have MANUFACTURED the divergence it exists to
#: announce. Main renders byte-identically to a2 in everything except the
#: marker itself, so the only thing making the tree differ from a2 was the
#: annotation saying it differs.
#:
#: What guards this version instead is the candidate's five-component frozen
#: identity (docs/inventories/foundation-candidate-0.3.0a2.json): source,
#: version, descriptor digest, three rendered asset digests, and the eight
#: ``io.dotmac.deployment.*`` label keys. Source drift from the candidate is
#: expected and is recorded there, not by a version string.
#:
#: When a2 is published or superseded, the ordinary rule resumes and a marker
#: becomes correct again.
VERSION: Final = "0.3.0a2"

__all__ = ["VERSION"]
