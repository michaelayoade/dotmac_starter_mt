# Tenant audit prerequisite source inventory

This source preparation supports the full Academy learning extraction. No release
identity, published Kernel floor, consumer adoption or database attestation is
allocated here. Existing Kernel storage and its public writer are the owners;
this adds installation verification, not a competing audit store or writer.

The qualifying consumer is Academy at
`0968881fc40ad86b3abfea2fc40044dd2dbf47c9`: its existing attempt-grant operation
requires audit after the grant flush. The prepared Learning port delegates to
Kernel `write_audit_event` with trusted, explicit actor/source context and declared
`attempt_granted` action; failures propagate for caller rollback. Its three
existing prerequisites certify neither audit storage nor append privileges.
Fulfillment and Template Studio already consume this same public tenant writer.
Their frozen caller set must not grow. The platform audit prerequisite explicitly
excludes tenant storage and remains a separate contract.

Authoritative Kernel source is Starter main
`77d54a14f87c43f37f50d65d263277fefa74c9a6`, including existing
`20261017_0029_audit_history_shape.py` and the accepted historical audit
[disposition](audit-events-disposition.md). Seed manifest SHA256 is
`2564bab79728e15d0f9ca75de2efe8f01e5fc6ed138b4951496764268a4a24ce`.
Local worktree HEAD is older; publish owned blobs only onto the actual remote
parent. The current provider shape must be inspected, never inferred from a stamp.

Kernel owns `tenant_audit_log.v1` and its public prerequisite/verifier. The expected
existing 18-column contract is:

| Columns | PostgreSQL storage contract |
|---|---|
| id, tenant_id | UUID, non-null; primary key id; tenant FK CASCADE to public.tenants.id |
| actor_type, actor_id, actor_label | nullable VARCHAR(32), VARCHAR(120), VARCHAR(160) |
| actor_party_id | nullable UUID; no actor FK |
| source_application | nullable VARCHAR(64), preserving historical rows |
| action, entity_type | non-null VARCHAR(120), VARCHAR(160), open vocabulary |
| entity_id, request_id | nullable VARCHAR(120) |
| status_code, is_success | nullable INTEGER, BOOLEAN |
| ip_address, user_agent | nullable VARCHAR(64), VARCHAR(255) |
| details | non-null JSONB; empty-object server default |
| occurred_at, created_at | nullable timezone-aware timestamps; transaction-time defaults |

Require existing nonunique, nonpartial indexes on tenant_id, actor_type, actor_id,
actor_label, actor_party_id, source_application, request_id and occurred_at.
Preserve historical unknown attribution/timestamps; no backfill or actor FK.
Verify ENABLE and FORCE tenant RLS, actual tenant-equality policy shape and
applicable roles/commands, both USING and WITH CHECK; reject OR-true or additional
permissive bypass policies. The verifier accepts the canonical Kernel 0001 tenant
helper body, with PL/pgSQL scalar UUID, STABLE, SECURITY INVOKER and no function-local
configuration. Matching words alone do not prove semantics: a fixed-tenant return
or GUC override can preserve those words and break isolation. Body line-boundary
trimming preserves literal contents; alternate helper implementations require
separate provider qualification. Verification observes only catalogs and does not
change the caller's GUCs or own transaction outcomes. Observe effective
table/column/PUBLIC/inherited grants
for app_user and platform_api: SELECT/INSERT permitted, UPDATE/DELETE/TRUNCATE/
REFERENCES/TRIGGER refused. Verify schema usage, tenant-function execution and
non-superuser/non-BYPASSRLS posture. Do not claim this substitutes for the existing
deployment owner of all role-reachability/SET ROLE closure. Reuse existing catalog
helpers, inspector and narrow observations, without a generic verifier framework.
If accepted DDL does not satisfy this complete contract, report the specific gap;
do not weaken the contract or invent passing catalog evidence.

Hosted qualification must exercise real lineage, missing/stamped/wrong provider,
pre-0029 shape, column/default/nullability/actor-FK drift, disabled or bypass RLS,
table/column/PUBLIC/inherited mutation grants, actual non-bypass two-tenant writes
and caller rollback. Unit contract tests need sensitivity to missing mapping or
verification. Local tests/imports/builds/installs/DB work remain forbidden.

Release ordering follows Kernel's existing extraction dossier: qualify/merge this
Kernel contract, publish through actual release authority, verify installed registry
artifact, then convert Learning/Fulfillment/Template Studio together with proven
published floors and new verification-only consumer migrations. Keep existing
frozen callers unchanged during the first step and correct stale publication
prose. Add the public-root-export detector and its sensitivity coverage without
weakening declaration/migration checks. Consumer floor probes must include all
three consumers. Existing published migrations/artifacts remain immutable.

A predecessor Kernel lacking the new effect cannot construct the real Learning
manifest. Same-wheel floor qualification must distinguish exact missing-API
refusal from separate installed shipped-ledger allocation evidence, then prove
actual default ModuleRegistry registration on a sufficient published Kernel.
No conditional fallback, substitute manifest, fake ledger or inferred a106 floor.
