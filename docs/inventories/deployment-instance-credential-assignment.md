# Live deployment credential assignment: bounded source inventory

Status: source inventory with the startup-assignment source contract adopted on
2026-10-17. No producer is implemented or qualified by this document. An
application response, configured reference or signed self-report does not
establish independent live assignment.

The candidate scope is deployment-bound health for Sub, ERP and Academy, their
Integrator transport and existing Foundation/Control composition. This is not
a claim that no implementation exists anywhere in the fleet.

## Reviewed sources

| Product / immutable revision | Source and tests | Qualified behavior; remaining gap |
| --- | --- | --- |
| Sub `e119f08f720779fcb9c46797f568f140bad88168` | `app/services/integrations/installations.py` (`create_config_revision`), `runtime_execution.py`; versioned-installation and runtime-binding tests | Freezes config and secret-reference strings; resolves selected binding references. Does not bind the resolved credential version to a live process incarnation. |
| Sub `58050f9f005e67d2ba5821dd71f243cd1aa0df7f` | `app/services/deployment_secret_requirements.py`, `deployment_secret_schema.py`; `scripts/deployment_secret_readiness.py`; setup verifier scripts; their unit and PostgreSQL tests | Checks enabled connector pins and config-revision ownership and runs immutable-image candidate probes. Emits bounded material statuses/counts, not a live installation/revision/credential-version assignment record. |
| ERP `537f0ccba846c1ea3304805ed17a4d3b9711596b` | `scripts/verify_production_deployment_credentials.py` and its tests; runtime admission | Strong adjacent container/image plus migration-principal authentication proof. Its subject is the database migration credential, not Integration installation/config assignment. |
| Academy `a99a395e066db4eea3ec1d73219ab5dd006c1d1d` | `app/kernel_runtime.py`, reviewed runtime composition/install tests | Kernel runtime composition; no matching Integration assignment proof surfaced in the reviewed paths. Learning extraction is outside this change. |
| Integrator `ff6796aaa0d063b2d30327748e64627f0bccb394` | `src/dotmac_integrator/product_port.py`, descriptor/credential tests | Digest-pinned destination descriptors and assembly-held credential reference. The credential is outside the descriptor and is not version-bound to a live process. |
| Platform CP `1f7f8e8e093622013549a6682ff1e751d2ab7ffd` | `deployment/credential_bootstrap.py`; `test_authentication_is_proved_after_the_commit`, `test_a_record_at_another_version_is_refused` | Version-pinned secret bootstrap and post-install positive/negative database authentication. This proves a PostgreSQL role credential, not an Integration installation or live application assignment. |
| Starter `6b60c143333b213f8f3c8ede6fcd71c208c6f76c` | Foundation `EXTRACTION.toml`, `providers/compose_host.py`, `material_readiness.py`, material-readiness tests | Real container role/image/mount observation and disposable-candidate material probes. Existing role observations omit the live incarnation/config/Integration assignment join. |

The historical Sub revision was retrieved directly from GitHub and all eleven
relevant source/test blobs inspected. Earlier inability to lazy-fetch it was a
retrieval limitation, not evidence of absence; that uncertainty is resolved.
The relevant Foundation material parser and role/material observation behavior
was also compared with Starter `7a133895d611d9043eff10dc1b8271bdda493ff2`;
unrelated extraction/backup changes do not change this finding.

Historical tests explicitly examined include
`test_enabled_exact_pin_resolves_required_bindings_only`,
`test_declared_boot_components_pass_before_bootstrap_without_activating_beat`,
and `test_active_beat_uses_its_own_runtime_configuration`. These preserve the
candidate readiness reference; they must not be renamed into live assignment
proofs.

## Finding and bounded proposal

No qualifying implementation of the complete live-instance/immutable
Integration-credential assignment join was found in the reviewed bounds.
Reuse the qualified parts above; do not port database-principal or candidate
readiness evidence as though its subject were the live Integration credential.

The proposed next source contract extends Foundation's existing installed
execution provider with a read-only observation of one pinned live container
instance: actual image, immutable nonsecret config/mount digests, instance/start
identity, observation time and consumed dispatch coordinate. Recheck instance
identity after measurement; replacement, restart, mismatch or unsupported
sources refuses the result.

Credential assignment remains in the existing privileged custody provider. It
must independently confirm the same runtime instance received the exact
installation/config revision and immutable credential reference/version. No
secret value, secret-value hash or reusable credential enters the evidence.
Whether immutable assignment at process start is sufficient, versus proving
dynamically loaded material, remains an explicit contract decision. No new
service, signing authority or accepting verifier is proposed.

Control alone joins those independently obtained results to its frozen plan,
consumed dispatch and immutable enrollment. PH consumes only the eligible
opaque enrollment projection. Until the producer and custody join are concrete
and qualified, PH deployment-bound admission stays unavailable.

## Source contract decision — 2026-10-17

Michael chose immutable startup assignment: reuse Foundation's live-instance
inspection and the existing custody provider to bind the exact installation,
config revision and immutable credential version to that same runtime instance
at process start. This settles the previously open acceptance meaning;
dynamically loaded credential proof is outside this contract.

The adoption permits bounded source implementation under the existing owners.
It does not qualify an accepting producer, install trust, enable production
admission or claim any runtime/deploy/health/rollback/restore gate has passed.
