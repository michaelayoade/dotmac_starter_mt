# Lane 3 composed proof campaign

The selected test strategy is A: hosted tests qualify changed mechanics, then a
bounded live campaign supplies composition evidence. Synthetic tests do not
claim live GitHub OIDC, OpenBao, certificate or fleet behavior.

## Ownership and sequence

The management helper verifies a replacement removal backstop with a maximum
900-second lifetime before
stopping the original timer. Its deadline is fixed to the initial host monotonic
clock, and receipts cannot be reconstructed to resume or extend access. Cleanup
starts early enough to reserve its supervised termination budget inside the cap;
gates measure the actual timer deadline, not the nominal maximum.

After a run-specific human Environment review, the coordinator qualifies the
exact queued job and runner group before registration. The installed handoff
controller remains the lease owner. The OIDC-to-KV proof must finish and cleanup
must prove no active lease before certificate issuance or B8 transport begins.

Custody readiness precedes generation of ephemeral client keys. The client runs
under its dedicated, proven-owned non-root identity, with no supplementary
privilege, capabilities or privilege escalation. Its material lifetime is at
most 240 seconds, including a 45-second cleanup reserve. Usable execution ends
earlier so worker termination cannot consume that reserve. The absolute
monotonic deadline is fixed before key creation. Only verified first-attempt
destruction within that deadline attests the material cap; failed or interrupted
destruction remains unproven even if later cleanup closes all resources. Issuer,
root-token and temporary-listener cleanup must be verified before the matrix runs.

The transport helper permits only reviewed literal target addresses on TCP 22
for that exact UID. It records state and arms an independent five-minute cleanup
before adding rules. Cleanup refuses changed or foreign rules. There is no
wildcard, HTTPS admission or timer extension in this SSH-only phase.

## Evidence limits

Only authenticated channel-open proves forwarding. Authenticated connection
refused is a separate observation; authenticated administratively prohibited is
policy refusal. Timeouts, local errors and unreachable routes are indeterminate.
Port 18443 remains denied. Identity cleanup comes last: contain traffic, stop the client and prove process
and socket absence, remove material and transport rules and verify the baseline,
then remove the account. Unknown closure retains ownership and containment so
the UID cannot be recycled while its access survives. All helpers bind the same
session and manifest; this is one fixed cleanup contract, not a resumable engine.

A failed required stage ends the campaign, followed
only by cleanup; a retry needs fresh run identity and evidence.

Hosted Linux suites exercise real temporary systemd units, explicit disposable
network namespaces, non-root processes and nftables. Unit fixtures cover refusal
and response-loss paths. Neither suite contacts production or submits protected
Environment approval. Private adapters must pin the exact reviewed helper
bytes, host configuration, manifests and proof inputs before execution.

The reverse-CA witness still requires CP's actual runtime host. CP contract
answers, the remaining A8 matrix, A9, independent N3 governance and Gate-0
acceptance remain separate obligations. Admission evidence stays null until the
required acceptance evidence exists; this campaign does not allocate a release.

## Canonical campaign adapters and deployment bindings

The campaign adapter source is now reviewable beside its four public helpers.
Only these canonical files may be tested and deployed; private campaign drafts
are superseded candidates until their deployed bytes match the accepted source:

| Canonical module | Preserved narrow interface |
| --- | --- |
| `scripts/lane3_campaign_issuer.py` | `custody_available`, `prepare_custody`, one-shot `PreparedCustody`, `issue`, `remote_main` |
| `scripts/lane3_campaign_bridge.py` | `Bridge`, `parameters`, ordered `operate`, `main` |
| `scripts/lane3_campaign_runner.py` | `pinned_modules`, `WrapperView`, `main` |
| `scripts/lane3_campaign_remote.py` | `modules`, `inputs`, `issuer_exchange`, `execute`, `main` |
| `scripts/lane3_campaign_composition.py` | `run_b8` |

The issuer supervisor sent over SSH is extracted from the canonical
`remote_main` function in a single source read verified against the externally
accepted `issuer_source_sha256`; hosted tests exercise the same function directly. There is no
second fixture implementation. Importing any adapter performs no custody,
network, timer, account or firewall operation. Hosted tests inject synthetic
process, filesystem, clock, protocol and helper boundaries into these same
functions. Production loaders permit fixed module roles only and execute the
measured bytes after hash verification; there is no generic command input.

Each executable adapter accepts an exact-key `configure(deployment)` record
once per process. Successful configuration freezes a defensive copy; callers
receive copies when reading the bindings. This is installation of reviewed
bindings, never proof of authorization.
Bridge, remote-session and nonroot runner CLIs read only their respective fixed
`/etc/dotmac-lane3-campaign/{bridge,remote,runner}-deployment.json` file when
unconfigured. These records must be root-owned, group root, mode `0444`, regular,
single-link files beneath root-owned nonwritable directories. The issuer is
configured by its separately reviewed Mac launcher; its custody paths and SSH
route remain protected private inputs. Missing configuration refuses execution.

Deployment owners must supply and review the following bindings before any
live campaign; this source change does not manufacture deployment acceptance:

- Issuer: externally accepted canonical issuer source digest, exact client and
  approved custody module paths/digests, two distinct
  custody-note paths, encrypted-share bundle path, fixed SSH destination, issuer
  output directory, configure-script path and seven fixed-role remote source
  digests. Existing A/B decryption and cleanup are preserved. A stable custody-B
  encryption redesign remains a separate future change.
- Bridge: protected protocol input and lock paths, canonical management helper,
  accepted installer path, bundle/config digests, original unit/owner and remover
  path. Protocol input separately pins helper and installer bytes.
- Runner: protected code directory and exact digests for `b8-proof-matrix.py`
  and `b8-source-qualification.py`. Those existing reviewed implementations
  remain explicit deployment dependencies; this change neither duplicates them
  nor publishes their private endpoint configuration.
- Remote session: accepted source head, protected libexec/input/manifest/lock
  paths and exact pins for the three public client/identity/transport helpers,
  canonical composition and runner, matrix and source qualifier. Its protected
  session input binds actual endpoints, manifest and approval digest.

No adapter embeds a digest of its own source or derives its own trusted pin.
Acceptance binds canonical source externally, then generates deployment pins
for dependencies in that accepted tree. The issuer additionally receives that
external acceptance digest as `issuer_source_sha256` and verifies it before
extracting its fixed supervisor function; no source digest is written back into
the canonical file. The source-head binding and source hashes must be refreshed
for the accepted revision; stale private draft hashes confer no acceptance.
Root-only deployment records must be installed before the nonroot worker starts.

The known lost final root-response gap remains a live blocker: loss or timeout
cannot prove root cleanup. The 300-second supervisor recovery ceiling is not a
claim of total ceremony closure; the existing issuer's nested cleanup can exceed
it and still needs separately approved independent root readback/recovery. No
issuer recovery framework or automatic retry is introduced here. Static source
checks are not hosted test evidence or permission to begin the live ceremony.
