# dotmac-hosting

Provider-neutral, tenant-only owner of Dotmac hosting-account lifecycle.

It owns immutable technical specification versions, the hosting-service
aggregate, desired account/package revisions, reason-scoped suspension and
restoration, retention holds, guarded termination, immutable panel observations,
business outcomes, attention and reconciliation. It does not own price,
subscription recurrence, collections policy, approval decisions, provider
credentials or delivery retry.

The public write-side contracts are `PublishHostingSpecificationVersion`,
`ProvisionHostingService`, `ChangeHostingPackage`, `SuspensionRequest`,
`RestoreSuspensionRequest`, `RetentionHoldRequest`, `ClearRetentionHold` and
`RequestTermination`, plus the closed `TerminationApprovalObservationV1`
inbound approved-event observation. Specification versions publish a structural package rank;
Hosting derives upgrade, downgrade, same-level or incomparable direction and
enforces the current version's frozen change rules. Review-required and
incomparable changes are `manual_required` refusals in V1. The public provider
family is `hosting.account.v1` with
exactly `provision`, `package`, `suspension`, `termination`, `observation` and
`reconcile`. Provider delivery payloads contain only closed semantic snapshots;
they never contain Hosting/Cloud row identities, provider credentials or
secrets.

Suspension and restoration are two-phase. Accepting the business request opens
or clears the reason lock and emits a command with disposition `deferred`; it
does not claim the panel changed. An independently identified observation moves
`suspension_requested -> suspended` or `restoration_requested -> active`, then
appends an `applied` outcome for every still-deferred relevant command. Earlier
outcomes and uncorrelated observations remain immutable. Each suspension lock
freezes its allowed restorer codes when opened, so a later vocabulary change
cannot retroactively change authority. An inverse request appends a separate
`superseded` outcome for the earlier deferred consequence. Package changes use
the same rule: only an observation of the target package appends `applied`; a
later desired revision supersedes an older pending command and a provider
failure remains immutable failure evidence.

First provider correlation requires the immutable operation reference of a
command belonging to the service. Later correlation is only by the frozen,
tenant-unique `(capability_binding_ref, provider_account_ref)` pair; an account
string alone is never identity. Termination consumes an immutable local mirror
of the released `approval.approved` event together with its trusted delivery
source-event identity, recomputes the exact tenant/operation/subject
`sha256:` digest, then emits a secret-free request without approval internals.
It remains `deferred` until a separately identified `terminated` observation
appends the final `applied` outcome. Every customer-impacting decision takes a
typed actor and writes its declared audit action.

The online tenant role has no direct `UPDATE` or `DELETE` authority on the
hosting-service aggregate. Every aggregate mutation passes through one
module-owned `SECURITY DEFINER` function with a fixed search path, tenant-GUC
match, expected row version, closed transition grammar and immutable
observation/desired-state evidence. Retention-hold and termination refusals are
durable command outcomes and audits, including missing/wrong-source hold clears
and termination blocked by an active hold.

Capability `hosting.account.v1` is implemented by independently released
Integrator connector plugins. V1 observes aggregate mailbox count only; mailbox
identity, address, quota and lifecycle require a separate accepted owner.
