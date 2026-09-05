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
#: ``0.3.0a6`` — DECLARED 2026-09-03, NEVER BUILT, and RETIRED UNBUILT on
#: 2026-09-04 when the declared identity moved to ``0.4.0a1``. It is the first
#: name in this ledger to be retired without an artifact, and the shape is new
#: enough that the next allocator will meet it here before they meet it
#: anywhere else.
#:
#: **A spent NAME without a spent artifact.** Every earlier entry above was
#: spent by BYTES: a wheel existed, so a second build under the same name would
#: have produced two artifacts for one identity. ``0.3.0a6`` has no wheel and
#: no `CandidateArtifact.v1` receipt — nothing was ever built. What it does have
#: is publication in DOCUMENTS. While it was the declared identity, ``main``
#: advertised it in this package's `CHANGELOG.md`, in `docs/MODULE_CATALOG.md`,
#: in the `poetry.lock` path-package line, in
#: `docs/inventories/declared-publication-baseline.json`, and inside the
#: rendered `deploy/rendered/docker-compose.yml` labels by way of
#: ``io.dotmac.deployment.configuration.digest``. Re-declaring it would
#: therefore recreate the two-contracts shape of ``0.3.0a2`` — one version name
#: over two contracts — WITH THE DOCUMENTS RATHER THAN THE BYTES. That is the
#: same defect arriving through a different door, and the door is the reason
#: this paragraph exists.
#:
#: **It gets NO ``CandidateDisposition.v1``, and symmetry here would be wrong
#: rather than merely unnecessary.** The log dispositions built artifacts: every
#: entry is anchored by ``receipt_path``, ``receipt_digest`` and an ``artifact``
#: block, and entry 1 chains to the receipt's own digest rather than to a zero
#: genesis. There is no ``docs/inventories/foundation-candidate-0.3.0a6.json``
#: to anchor to and no digest to chain from. The proof is mechanical rather than
#: argued: adding ``"0.3.0a6"`` to ``SUPERSEDED`` in
#: `tests/architecture/test_version_binding_guard.py` FAILS
#: ``test_a_superseded_candidate_is_still_refused_for_a_second_build``, because
#: `version_binding_guard.bindings_for` reads tags, receipts and dispositions,
#: finds no record for a version that was never built, and returns nothing to
#: refuse with. So ``EXPECTED_ENTRIES`` stays 4, ``SUPERSEDED`` and
#: ``BUILT_CANDIDATES`` are unchanged, and
#: `tests/architecture/candidate_source_binding_baseline.json` stays empty —
#: which is still the healthy state, because the declared version again has no
#: candidate.
#:
#: **The residual gap, stated so it is not mistaken for coverage: NO MACHINE
#: ORACLE REFUSES ``0.3.0a6``.** The guard has three record sets and this name
#: is in none of them. `ABANDONED_UNBUILT` in the binding-guard test asserts the
#: freshness expectation longhand — the same shape ``PUBLISHED`` deliberately
#: uses, so that a stated expectation can be wrong and get caught rather than
#: agreeing with the guard for every input — but it is an EXPECTATION, not an
#: enforcement. A build of ``0.3.0a6`` dispatched today would not be refused by
#: `version_binding_guard.py`. This is an unmonitored population, recorded as
#: one (ADR-0018 § "a guard exemption states an enforceable premise, or the
#: region is unmonitored rather than exempt"), and it is not repaired by
#: pretending the guard sees it.
#:
#: ``0.4.0a1`` is the successor, allocated 2026-09-04. A MINOR bump rather than
#: a seventh alpha of the ``0.3.0`` line, because the change it names is a new
#: CAPABILITY rather than a repair: authorized recovery of a FAILED PRODUCTION
#: SYSTEM — an act that mutates something already existing, with its own
#: `RecoveryExecutionPlanV1` (a deployment-shaped plan is not a recovery plan),
#: an authorization binding, the replay coordinate echoed, a signed and
#: settleable result, and the three bindings a rehearsal does not take: a
#: captured prestate, the failed system's own observed state, and a desired
#: poststate. It WAS built once in run ``33920058598`` from ``753a004e`` as
#: artifact ``9954731961``, but the receipt never entered the tree and the
#: importable source later drifted. It is therefore spent and must not be
#: rebuilt or published. ``candidate_window_baseline.json`` is the authoritative
#: build-oracle record; allocation of a successor remains a separate decision.
#:
#: **WHICH AUTHORIZATION VOCABULARY CARRIES IT IS NOT DECIDED BY THIS BUMP, AND
#: ``recover`` IS NOT ADDED TO `authorization.OPERATIONS` HERE.** An earlier
#: draft of this paragraph said it was, and that was a commitment this file had
#: no standing to make. Michael's withdrawal of ``recover`` stands, and the
#: annotation at that constant — *"WAS a member for one commit and is
#: WITHDRAWN; that reversal is the record"* — exists precisely to stop the
#: member being re-added to close a gap. Read it before proposing otherwise.
#:
#: The gap is real and it is on the OTHER SIDE, measured 2026-09-04 against
#: `dotmac_deployment_control` at the peeled ``a11`` tag
#: ``98b2a257f4185ee134b54a0349ad09d76f05286b``:
#:
#:   * Control's operation vocabulary is ``{deploy, rollback, recover}``; this
#:     facility's is two. Control's own module docstring says its vocabulary is
#:     closed so that it cannot *"freeze, sign and dispatch an authorization the
#:     executor is structurally unable to honour"* — and at ``a11`` it can.
#:     ``recover`` went in at ``a10`` on the stated premise that this facility's
#:     ``a5`` was being built against the same three members; the Shape B ruling
#:     falsified that premise and nothing on Control's side refuses it.
#:   * **There is no recover-specific settlement contract to implement
#:     against.** Control's ``settle_attempt`` is operation-agnostic: it settles
#:     on OUTCOME (succeeded / failed / timed out / cancelled) and never reads
#:     ``operation``. So a recovery result is settled by the same path a deploy
#:     is, and the thing 0.4.0a1 must produce is a result that path can consume
#:     — not conformance to a recover receipt shape that does not exist.
#:
#: What happens TODAY if Control dispatches a ``recover`` authorization is worth
#: stating, because it is the one reassuring fact in the paragraph: it fails
#: LOUDLY and EARLY on this side. `AuthorizationReceipt.__post_init__` reads
#: ``OPERATIONS`` rather than respelling it (the #603 repair) and raises
#: `SpecError` on ``recover`` at construction — before a grant exists, before a
#: plan is rendered, before any effect. The divergence cannot produce a silent
#: admit; it produces an unusable authorization. That is the correct failure and
#: it is still a failure, which is why the repair is Control's rather than a
#: quiet widening here.
#:
#: This value is load-bearing beyond metadata. ``VERSION`` sits inside the
#: canonical descriptor document, so changing it moves
#: ``io.dotmac.deployment.configuration.digest`` and the rendered
#: ``deploy/rendered/docker-compose.yml`` with it. Any change here is therefore
#: a re-render in the same commit (`make deployment-render`), and
#: `make deployment-check` is what fails if it was not. The digest recorded in
#: a candidate receipt is a historical fact about THAT build and is not
#: re-derived from this tree, so a re-render never invalidates one.
VERSION: Final = "0.4.0a1"

__all__ = ["VERSION"]
