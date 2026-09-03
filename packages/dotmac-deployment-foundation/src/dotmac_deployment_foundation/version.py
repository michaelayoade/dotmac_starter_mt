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

#: ``0.3.0a2`` AND ``0.3.0a3`` ARE BOTH SPENT NAMES. Do not "fix" this number
#: back down to either, and do not add a local segment such as ``0.3.0a4+dev``
#: — that shape was withdrawn on 2026-09-01, because it is an annotation on a
#: claim that should not have been made at all.
#:
#: The rule this file exists to hold: **a tree that diverges from a built
#: artifact allocates a new version.** It has now been applied twice, and the
#: second application is the one to read, because the first was a repair and
#: the second was the rule working as intended.
#:
#: ``0.3.0a2`` — built ONCE as candidate artifact 9740182233 from
#: ``e930f878ce400b766b4a50feb0369021a28ab2fa``, never tagged and never
#: uploaded. Commit ``0f390a9aa93b0bb1cb78621ab1e9febc90bc48d2`` (#551) then
#: changed the facility's source under that same declared version, so the name
#: ``0.3.0a2`` came to mean two different contracts — the frozen bytes, and
#: that tree. A version naming two contracts is exactly what `AGENTS.md` rule
#: 34 exists to prevent, and it was live. Recorded `invalidated` and
#: `publishable: false` in
#: `docs/inventories/foundation-candidate-dispositions.json`; the
#: `CandidateArtifact.v1` receipt is preserved byte-for-byte, because a restore
#: proof, an issuer stand-up and Lane 3 all bind to it.
#:
#: ``0.3.0a3`` — built ONCE as candidate artifact 9830633429 from
#: ``005490b278be73112fa9600bffb6e00a37c77a59`` (run 33587629491, wheel sha256
#: ``11978d919f1e910ae16d9b8262ffd3c473b074b4815067ab210fbe88e009d990``,
#: expires 2026-12-01). Unpublished and untagged, and it is the **Platform CP
#: cutover's bootstrap input**. Those bytes stay valid and stay the bootstrap
#: input: Platform CP resolves the wheel by RUN AND ARTIFACT ID out of the
#: committed `CandidateArtifact.v1`, never from the version this tree happens
#: to declare. Nothing about this bump reaches it, and it must never be
#: rebuilt — a second build under that name produces different bytes with the
#: same identity.
#:
#: ``0.3.0a4`` is the successor, allocated 2026-09-02 when
#: `observability_promotion.py` was added. This time the rule was applied
#: BEFORE the divergence shipped rather than after somebody found it: the
#: package source changed, so the declared identity moved, and `0.3.0a3`'s one
#: candidate is left describing exactly the tree it was built from. It has NOT
#: been built. It is unpublished, and recorded as such in
#: `docs/inventories/declared-publication-baseline.json`.
#:
#: There is deliberately no rule here permitting this tree to diverge from a
#: candidate while keeping its version. That permission is what let one name
#: cover two contracts; it was removed with the ``0.3.0a3`` move rather than
#: re-argued, and it stays removed.
#:
#: ``0.3.0a4`` was built exactly once (2026-09-02, candidate artifact
#: 9880868637 from ``14f7d9fe``, expires 2026-12-02) and is recorded
#: **superseded and unpublishable** in the disposition log (2026-09-03):
#: Michael's audit found its installed CLI cannot load an assembly's effects
#: or verifiers, and its release-evidence reader stringified signed envelopes
#: at the ``Mapping[str, str]`` seam — a verifier judging a restatement
#: verifies nothing. The bytes stay preserved and are never rebuilt; the
#: repairs are ``0.3.0a5``'s subject.
#:
#: ``0.3.0a5`` was built exactly once (candidate artifact 9903418260, run
#: 33780438726, from ``27bee8fc43919a5ed7f4853ccdedc2f996ad8d86``, expires
#: 2026-12-02) and is recorded **superseded** in the disposition log
#: (2026-09-03). NOT invalidated: those bytes were never wrong. They are exactly
#: the nine-of-eleven contract their own receipt describes, they stay preserved,
#: and they are never rebuilt.
#:
#: What superseded them is the third application of this file's rule, and it is
#: the one that was caught LATE rather than early. PR #600 added 405 lines and
#: removed 9 across ``authorization.py``, ``provenance.py`` and
#: ``recovery_execution.py`` — widening ``OPERATIONS`` to three members and
#: landing the restore executor — while this constant still said ``0.3.0a5``. So
#: for the length of that merge the name covered two contracts, and the second
#: one contradicted the first in writing: the a5 receipt's ``item_scope`` records
#: ``items_absent: [10]`` and argues, in a committed field tagged
#: ``IS_A_DECISION_NOT_AN_OVERSIGHT``, that ``("deploy", "rollback")`` is the
#: correct vocabulary for what the facility can perform.
#:
#: The a2 divergence was found by an audit ten days later. This one was found by
#: reading the receipt against the tree — and nothing mechanical would have
#: found it, which is the part worth recording here. ``version_binding_guard.py``
#: only runs when a build is requested, and
#: ``test_declared_version_matches_published_tree.py`` compares against a git
#: TAG that a candidate does not have. Two real guards, one blind spot between
#: them, described in ``scripts/candidate_source_binding.py`` — which now closes
#: it and is the reason a fourth application of this rule should fail CI rather
#: than wait for an audit.
#:
#: This value is load-bearing beyond metadata. ``VERSION`` sits inside the
#: canonical descriptor document, so changing it moves
#: ``io.dotmac.deployment.configuration.digest`` and the rendered
#: ``deploy/rendered/docker-compose.yml`` with it. Any change here is therefore
#: a re-render in the same commit (`make deployment-render`), and
#: `make deployment-check` is what fails if it was not. The digest recorded in
#: a candidate receipt is a historical fact about THAT build and is not
#: re-derived from this tree, so a re-render never invalidates one.
VERSION: Final = "0.3.0a6"

__all__ = ["VERSION"]
