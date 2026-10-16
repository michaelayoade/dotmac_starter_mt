# Kernel conflict-savepoint composition inventory

Audited 2026-10-14, before implementation. This extends the existing engine-free
Kernel transaction helper; it introduces no engine, session factory, transaction
boundary, product command owner, or persistence facility.

| Product and immutable revision | Implementation and tests | Finding |
| --- | --- | --- |
| Starter `bd8aa0d646eae382c7c8a0e99640f04920f0b716` | `packages/dotmac-kernel/src/dotmac_kernel/_transactions.py`, `transactions.py`, `db.py`; `tests/test_conflict_rls_context.py`, `tests/architecture/test_conflict_savepoint_engine_free_import.py`, `test_kernel_caller_session_independence.py` | Existing shared helper owns expected-conflict isolation and preserves the caller's transaction-local tenant context. Public and compatibility exports preserve function identity. Its implicit context-manager release has no typed lifecycle seam for a product's completion guard. |
| Sub `71374556bf3d1fe74f1b46f15c1113e6ad2f809a` | `app/services/owner_commands.py::_reject_helper_commit`, `_record_helper_rollback`, `execute_owner_savepoint`; `tests/test_owner_commands.py` | Qualifying product source for guarded completion: authorize only the helper's exact nested handle, validate it after the body, explicitly roll back an attached inactive handle after flush failure, retain authorization through rollback events, and clean up in `finally`. Production owner command infrastructure already consumes this helper. Its mutable product-private authorization slot is generalized into the typed Kernel lifecycle below. |
| ERP `93d7e111d9278b8377bac311e78308c99f51da79` | `app/db/__init__.py::transaction`, `atomic_operation`; repository search of `app/services`, `app/db`, and `tests` for `begin_nested`, `before_commit`, `after_soft_rollback` | Existing nested savepoint helpers have no guarded lifecycle observer or single-use release claim. `transaction` can own an outer commit; that behavior is outside this extension's contract. No qualifying lifecycle guard or dedicated parity suite was found. |

The named candidate scope is Kernel, Sub, ERP, Billing opening-window refusal
recovery and Sub's Numbering composition. Billing source at
`5d69eaa7fd9a9ef4848812f54a994d71558865c8` imports and calls
`conflict_savepoint_requires_rollback` from `dotmac_kernel.transactions` in
`packages/dotmac-billing/src/dotmac_billing/service.py` before its refusal
recheck. This is a real source consumer of the public rollback observer; it
grants no transaction completion authority. Billing's prepared kernel floor is
a105, while its namespace was allocated in a89. Published a98-a104 export only
`conflict_savepoint`; verified a105 publication and hosted positive/negative
floor probes remain prerequisites for Billing release or adoption. No product
adoption is established by this source update. Kernel's existing
`EXTRACTION.toml` remains the package dossier; this inventory records this
bounded lane's source audit and parity requirements rather than relabeling the
historical package as a new greenfield extraction.

Sub's current candidate is `e8f24fa23e30ea8ef22ec593592f5bcd1141b25b`;
`git diff 71374556bf3d1fe74f1b46f15c1113e6ad2f809a
e8f24fa23e30ea8ef22ec593592f5bcd1141b25b -- app/services/owner_commands.py`
is empty. Thus the pinned qualifying guard is unchanged in that candidate.

## Contract and deliberate generalizations

Kernel owns `conflict_savepoint` and a per-session stack of immutable lifecycle
observations: exact root transaction, exact nested savepoint, and BODY, RELEASE
or ROLLBACK phase. Observation grants no completion permission. A separate
single-use claim grants the composing guard permission only during the helper's
own release of those exact active handles. The guard supplies its stored
`expected_root` explicitly; a wrong root refuses without consuming permission.
The claim is consumed before return. `ConflictSavepointProtocolError` reports
participant completion or replacement of the helper's handles.
No product-private session-info keys, eager runtime imports, or broad permission
for arbitrary nested transactions cross the seam.

`begin_nested()` occurs before publishing lifecycle state: SQLAlchemy's entry
preflush is caller-owned. A real savepoint context remains active during the
body and release so transaction-context checks keep their established behavior.
An attached failed-flush savepoint is explicitly rolled back. Flush-child
rollback events observe BODY (explicit flush) or RELEASE (release-time flush),
with the inactive savepoint as their parent. ROLLBACK is visible during the
helper's subsequent exact-handle cleanup events. Nested Kernel helpers restore
their parent's observation; cleanup removes the session's stack after all exit
paths. The caller retains all authority over outer commit/rollback and retry.

Recovery is proven only when rollback of the exact attached savepoint returns
successfully, the original root is still active and current, and the enclosing
nested handle is restored. This includes a unique violation flushed during
release. A failure after SQL release (for example, an `after_commit` listener),
or a rollback/close/context cleanup failure, instead latches the exact root as
requiring owner rollback. The engine-free Kernel completion guard refuses
commit under that latch; `conflict_savepoint_requires_rollback` lets a product
boundary reject completion before enabling its own commit bypass. The latch
survives lifecycle stack cleanup, stores no error or callback, and clears when
that exact root ends. Kernel never rolls back the root. Cleanup errors preserve
the first exception, including across the real savepoint context's exit.

## Parity, first cutover and retirement

Focused Kernel tests port Sub's success, optional-body failure, actual unique
constraint flush failure and illegal participant completion cases, and add
session isolation, stale/wrong-root refusal, nested parent restoration,
commit-event failure cleanup, single-use claim and real PostgreSQL root/GUC
preservation. Reentrant root completion from listeners before and after the
composing guard, including release-time flush listeners, must be proved denied;
a single-use claim alone is not evidence of that property.
Persistent `roles` rows with unique identifiers provide independent-connection
visibility checks before owner rollback and after forced owner failure; no
temporary table is used for that proof. Post-release and secondary cleanup
listener failures assert original exception identity, refusal of caught-error
completion, latch isolation and clearing on root rollback/session reuse.

Billing opening-window recovery consumes the rollback observer in source; its
publication remains gated on the released a105 artifact and hosted floor proof.
Sub Numbering is a separate intended adopter of the guarded completion protocol
after a released Kernel dependency exports it and hosted composition tests pass.
No release, adoption, deployment or local-copy retirement is asserted by this
source change. Sub's
owner command executor remains the product boundary; its private optional-step
helper is not retired by the Numbering bridge. Drift evidence is the focused
Kernel contract suite plus Sub's owner-command/Numbering composition suite,
including planted service-owned completion and reentrant-listener violations.
All runtime tests run only on Git-hosted CI. This audit ran no local imports,
tests, dependencies, database, or Docker workload.
