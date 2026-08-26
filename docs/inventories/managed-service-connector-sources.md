# Managed-service connector sources

**As of:** 2026-08-17  
**Decision:** [ADR-0033](../adr/0033-exact-managed-service-connectors-are-authorized.md)  
**Scope:** Keycloak Admin, ERP identity/application lifecycle, Mailcow,
Nextcloud, Academy, authoritative DNS/PTR, IaaS, and a constrained deployment
host agent.

This is the Rule-24 inventory that precedes the first managed-service connector
distribution. It records sources to preserve, absences that permit greenfield
work, and the boundaries a connector may not cross. It authorizes no provider
behavior by itself.

## Revisions inspected

| Repository | Revision | Read discipline |
|---|---|---|
| `dotmac_starter_mt` | `aa5871c9840f` | tracked source and accepted ADRs; the in-flight SPI 1.2/a5 work was not counted as a released source |
| `dotmac_vendor_control_plane` | `e6b2bbee815c` | tracked provider laboratory inspected; the uncommitted managed-profile draft was excluded from Rule-24 evidence; no real provider SDK |
| `dotmac_integrator` | `783baf23cbf5` | held-secret resolver and thin assembly inspected |
| `dotmac_workspace` | `37189f8f3695` | released OIDC consumer and deployment evidence inspected |
| `dotmac_sub` | `05210fe3d00b` (`origin/main`) | Nextcloud Talk transport inspected at the clean published lineage |
| `dotmac_erp` | `2749ec5396cb` (`origin/main`) | revision-pinned reads; the dirty checkout was not used as evidence |
| `dotmac_academy_app` | `40423a07a4ea` | account lifecycle, integration security, and OIDC absence inspected |

Searches found no checked-in Keycloak Admin client, Nextcloud OCS user or
`user_oidc` client, Mailcow backup/update runner, authoritative-DNS client,
real IaaS client, or generic constrained host agent in these repositories.

`dotmac_workspace` commit `bfc121f33013` records defects found by the first
real deployment. Its `docs/PILOT-RUNBOOK.md` status line still says the
Workspace is undeployed; that line is stale and must not be read as the current
production-use verdict.

## Rule-24 rulings

| Surface | Exact source and tests | Production/test evidence | Ruling |
|---|---|---|---|
| OIDC relying party and immutable application binding | `packages/dotmac-auth-oidc/src/dotmac_auth_oidc/{client,discovery,state,transport}.py`; `tests/unit/test_auth_oidc_{discovery,id_token,request_bound_store,state}.py`; `packages/dotmac-kernel/src/dotmac_kernel/external_identity.py`; `tests/unit/test_external_identity.py`, `tests/test_external_identity_isolation.py`, `tests/test_external_identity_login_race.py`, `tests/test_session_provenance.py` | Workspace consumes the released packages; `dotmac_workspace/src/dotmac_workspace/identity/{relying_party,service,state_store,session}.py` and its login/database tests prove the consumer path. | **Product-first.** Every managed application reuses PKCE S256, issuer/audience/azp validation, opaque single-use state, exact issuer/subject binding, takeover refusal, and provenance-bound revocation. This is relying-party behavior, not a Keycloak Admin implementation. |
| Keycloak Admin | No qualifying tracked Admin REST or `kcadm` implementation existed at inventory time. `dotmac_workspace/docs/PILOT-RUNBOOK.md` references the host-local `idp:/opt/keycloak/verify.sh`, but that script is not checked in. The first candidate is now `packages/dotmac-connector-keycloak-admin`, implementing the managed-identity contract through constructor-injected transport. | The live realm/client remain operational evidence only. The candidate has static realm/client/stable-reference-user source and canary coverage but cannot enter the release allowlist or installed-wheel CI until kernel a69, Integration a6 and the identity-contract a1 wheels are all published. | **Greenfield-after-inventory, implementation candidate.** Preserve the proven issuer/RS256/S256/audience invariants; pre-create non-master realms; locate users only by an owner reference, return exact issuer/subject, deliver revisioned credential enrollment without carrying a password, revoke IdP sessions on disable; and run disposable Seabone conformance after the dependency train and connector wheel are published. |
| ERP application and identity lifecycle | `dotmac_erp/app/api/{persons,auth,auth_flow}.py`; `tests/test_api_persons.py`, `tests/test_api_auth_flow.py`, `tests/integration/test_person_services.py`. OIDC deletion and target boundary: `docs/oidc_identity_contract.md`; reintroduction guard: `tests/architecture/test_identity_protocol_boundary.py`. | Local people/session behavior is tested. ERP OIDC was never enabled, held zero bindings, and was deleted. Existing API authentication is not an Integrator service-assignment contract. | **Product-first for ERP-owned people/RBAC/session decisions; greenfield for the external port.** ERP must publish a versioned service port and adopt `dotmac-auth-oidc`; a connector never uses admin-bypass routes or ERP tables. |
| Mailcow API | `dotmac_erp/app/services/mailcow/client.py`, `app/services/people/hr/offboarding.py`; `tests/services/test_mailcow_offboarding.py`. | The orchestration substitutes `MailcowClient`; the real `/get/mailbox` and `/edit/mailbox` wire mapping has no direct test. Production enablement is unverified and defaults off. | **Greenfield-after-inventory.** The source is a behavior note, not qualifying code to port. Use only supported Mailcow APIs for domain, mailbox, alias, quota, delivery, and app-password operations. |
| Mailcow SOGo cleanup | `dotmac_erp/scripts/mailcow_sogo_cleanup/{sogo_cleanup_receiver,sogo_forward_cleanup,sogo_cleanup_queue}.py`; `tests/services/test_mailcow_sogo_cleanup_scripts.py`. | One mocked no-op processor test; receiver authentication, queue races, MariaDB mutation, restart ambiguity, and adoption are unproven. | **Do not port as the host agent.** It is one product-specific exact event, directly invokes Compose/MariaDB, and does not satisfy the managed-host contract. |
| Mailcow backup/update | No checked-in implementation or conformance test. | None. | **Greenfield-after-inventory**, gated on the constrained host agent. |
| Nextcloud transport | `dotmac_sub/app/services/integrations/connectors/nextcloud_talk.py`, `nextcloud_talk_capability.py`, `nextcloud_talk_staff.py`; `tests/test_nextcloud_talk_staff_notifications.py`. | Production-used for Nextcloud Talk and tested for HTTPS/SSRF refusal, no redirects, OCS envelopes, exact user ids, and retryable versus ambiguous outcomes. | **Product-first for common Nextcloud HTTP/OCS transport.** User provisioning, groups, quota, files, and `user_oidc` are greenfield endpoint contracts because none exists in the fleet. |
| Academy | `dotmac_academy_app/app/services/accounts.py`; `tests/services/test_accounts.py`; request authentication precedent in `app/services/erp_integration_security.py` and `tests/services/test_erp_integration_security.py`. | Tenant-local account creation is tested; no external service lifecycle port or OIDC consumer exists. | **Product-first for Academy-owned learner/role decisions; greenfield for the external port.** Curriculum, enrolment, assessment, and completion remain Academy decisions. |
| Authoritative DNS/PTR | `docs/inventories/provider-capability-sources.md` sections 4 and 8.3; `docs/inventories/domains-sources.md`. | The audited fleet contains no DNS provider implementation or parity test. | **Greenfield-after-inventory.** The one independently bindable family is `dns.authoritative.v1`; it uses the SPI's `plan`/`apply`/`observe`/`cancel` operations and types `zone`, `recordset`, and `observation` as resource kinds in those schemas. PTR is represented inside recordset/observation, not as a provider-specific capability. |
| IaaS | Contract shape: `packages/dotmac-kernel/src/dotmac_kernel/providers/provisioning.py`; fake and contract suite: `dotmac_kernel/testing/provisioning.py`, `tests/unit/test_provisioning_provider.py`, `tests/unit/test_testing_kit.py`. Vendor laboratory: `dotmac_vendor_control_plane/src/vendor_cp/provisioning/laboratory.py`, `tests/unit/test_provisioning_contract.py`; `src/vendor_cp/providers.py` refuses real mode. | The plan/apply/observe/cancel semantics are tested; only simulation exists. No real provider is production-used. | **Product-first for the conversation semantics; greenfield for Contabo wire behavior.** No provider SDK or infrastructure decision moves into Vendor CP. |
| Constrained host agent | No generic implementation. The Mailcow cleanup scripts and Academy lab-engine subprocess runner are product-specific and accept shapes a fleet agent must refuse. | None. | **Greenfield-after-inventory.** It accepts only versioned allowlisted bundle operations; there is no generic command, argv, script, SSH, or arbitrary file-execution surface. |

## Minimum owner contracts before connector code

The capability id and typed meaning belong to the named domain/product owner;
the connector only implements them. One capability covers one independently
bindable lifecycle and declares its supported operations within that family.

- `dns.authoritative.v1`: the SPI operations `plan`, `apply`, `observe`, and
  `cancel`, with `zone`, `recordset`, and `observation` as typed resource kinds;
  canonical IDNA FQDN/RRsets in, opaque provider refs, assigned nameservers,
  observed RRsets, and change evidence out.
- Identity realm/client/user lifecycle: exact realm/client refs, redirect URIs,
  Authorization Code, S256, RS256 and audience/azp policy in; stable user refs,
  revisioned no-password enrollment and active/disabled intent in; issuer,
  discovery, JWKS, client id, immutable subject, session-revocation result and
  observed configuration digest out. Email is a mutable attribute, never the
  lookup or binding key.
- Managed Email owns `email.lifecycle.v1`: application, domain, mailbox, alias,
  quota, delivery, app-password and DKIM resource operations in one coherent
  family. Backup/restore and update remain managed-host capabilities because
  their credential and failure boundaries differ from the administrative API;
  a suite/profile composes them without making Managed Email a second owner.
- Collaboration lifecycle is four exact owner families:
  `collaboration.application.lifecycle.v1` (ensure-active, backup, restore,
  upgrade, suspend, resume and decommission with health/rollback evidence),
  `collaboration.user-oidc.configuration.lifecycle.v1` (immutable
  issuer/subject mapping, preprovisioned-only accounts, no email linking, S256,
  audience/azp, backchannel logout and provenance-bound revocation),
  `collaboration.user-group-quota.lifecycle.v1` (stable ids only), and
  `collaboration.file-roundtrip.lifecycle.v1` (write/read/digest/cleanup of a
  bounded public probe). Endpoint and credential references are held
  installation configuration and never repeated in operation request schemas.
  All four schema pairs are runtime gates: plan/apply validate the desired step
  target, while observe/cancel targets are derived from that immutable step and
  restricted to their declared fields; outer command, operation and plan pins
  stay in the Integration envelope. Successful results must validate before
  public/non-secret evidence projection.
  Talk/share remain outside the initial owner contract instead of being
  inferred from provider API names.
- ERP and Academy application lifecycle: pinned application/configuration plus
  their own versioned user/binding ports. Their connectors transport commands;
  they never decide authorization, employment, enrolment, or session policy.
- Managed Infrastructure owns four independently bindable families:
  `infrastructure.instance.lifecycle.v1`,
  `infrastructure.network.lifecycle.v1`,
  `infrastructure.volume.lifecycle.v1` and
  `infrastructure.firewall.lifecycle.v1`. Each uses plan/apply/observe/cancel
  with exact artifact/configuration hashes and opaque resource references;
  signed plan identity remains in Integration's orchestration envelope.
- Managed Host owns `host.deployment-bundle.lifecycle.v1` (the only location
  of upgrade/update semantics), `host.backup-restore.lifecycle.v1` and
  `host.health-probe.lifecycle.v1`: closed bundle operation code and version,
  exact artifact/configuration hashes, installed version, backup
  object/version, restore validation, health and rollback facts out.

All version-one connectors consume **pre-created held secret material** through
immutable secret references resolved by Integrator. A generated client secret,
mailbox password, app password, token, or private key is never returned in a
receipt, evidence payload, log, exception, or API response. A provider flow
that can only generate and return a secret is unsupported until a separately
approved typed secret-write boundary exists.

## Admission gates

ADR-0033 names the only authorized distributions. Authorization is not
completion: each distribution still waits for its owner contract, SPI 1.2
release/adoption, provider-free fake, SPI and owner-port conformance, planted
redaction/provider-leak sensitivities, and an isolated Seabone acceptance run.
Seabone is evidence infrastructure only; it is not a production target and this
inventory authorizes no host access.

## Official provider contracts pinned for implementation

The connector wave uses provider-owned documentation or source, not an
unofficial SDK's interpretation:

- Keycloak realm/client behavior is pinned to the current
  [Admin REST API](https://www.keycloak.org/docs-api/latest/rest-api/index.html)
  and [Server Administration Guide](https://www.keycloak.org/docs/latest/server_admin/).
  The connector uses realm-scoped Admin REST and never master-realm authority.
- Nextcloud provisioning is pinned to the official
  [OCS User Provisioning API](https://docs.nextcloud.com/server/stable/admin_manual/configuration_user/user_provisioning_api.html)
  and the maintained
  [`user_oidc` contract](https://github.com/nextcloud/user_oidc). The latter is
  the authority for PKCE, audience/azp validation, soft provisioning without
  account creation, immutable user-id mapping, direct-login break glass and
  backchannel logout.
- Mailcow browser identity, backup/restore and upgrades are pinned to the
  official [Generic OIDC guide](https://docs.mailcow.email/manual-guides/mailcow-UI/u_e-mailcow_ui-generic-oidc/),
  [restore guide](https://docs.mailcow.email/backup_restore/b_n_r-restore/) and
  [update guide](https://docs.mailcow.email/maintenance/update/). Mailbox,
  domain and alias request shapes are corroborated against the maintained
  [`mailcow-dockerized` source](https://github.com/mailcow/mailcow-dockerized),
  because the instance-served API reference is generated from that source.
- Contabo compute/private-network behavior and request identity are pinned to
  the official [Contabo API OpenAPI reference](https://api.contabo.com/).

These references do not authorize a live call. Connector tests use injected
transports and disposable Seabone provider doubles until a separately named
non-production provider account is in scope.
