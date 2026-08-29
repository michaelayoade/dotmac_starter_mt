# dotmac-deployment-foundation

One build-and-deploy facility for every Dotmac product assembly. A product
declares one product-owned artifact, `deploy/product.toml`; the Compose file,
the ingress site, the collector configuration, the alert rules and the ordered
deployment plan are all rendered or derived from it. The independent
authorizer supplies a separate execution envelope, because the application
being deployed cannot be allowed to choose or downgrade its own controller.

Decision: [ADR-0070](../../docs/adr/0070-deployment-is-a-stateless-versioned-foundation.md).
Sources and defect list: [`EXTRACTION.toml`](EXTRACTION.toml) and
[`docs/inventories/deployment-foundation-sources.md`](../../docs/inventories/deployment-foundation-sources.md).

## What it is

A **universal facility**: no `ModuleManifest`, no models, no migrations, no
lineage, no tenant, and **zero runtime dependencies** — not the kernel, not
SQLAlchemy, not FastAPI, not Jinja, not a YAML library. Standard library only.

That is the same shape `dotmac-ui` holds and it exists for the same reason: a
build runner rendering a Compose file has no database and no web framework, and
must not acquire them in order to validate a descriptor. Two import-linter
contracts hold the boundary in both directions.

The host controller runs only the canonical typed deployment plan. The
executor talks to an injected `Effects` provider — which is what makes twenty failure cases
(wrong digest, failed backup, corrupt backup, candidate never ready, a
maintenance-required release attempted online) ordinary unit tests instead of
disposable-VM exercises. A gate that has never been shown to fire is a gate
nobody should trust.

## Quick start

```bash
dotmac-deploy validate                     # parse and check the descriptor
dotmac-deploy render -o deploy/rendered    # write every asset
dotmac-deploy render --check -o deploy/rendered   # fail on any difference
dotmac-deploy plan                         # the ordered plan, gates marked
dotmac-deploy preflight                    # only the steps that mutate nothing
dotmac-deploy backup                       # the policy, and what "verified" means
dotmac-deploy restore-rehearsal            # what a restore PROOF requires
dotmac-deploy image-audit REF --inspect i.json --history h.json --layers l.txt
dotmac-deploy observe --deployment-id 42 --host web1   # the resource stamp
dotmac-deploy ingress-policy               # declared exposure, plans, digest
dotmac-deploy ingress-policy --format digest      # what a plan carries
dotmac-deploy exposure-verify --sockets ss.txt --iptables-v4 f.txt
dotmac-deploy drift --observed observed.json
dotmac-deploy rollback --previous-image sha256:...     # or why it is refused
```

Exit codes are a contract: `0` ok, `1` refused (a gate said no, a check found
drift), `2` usage.

## The descriptor

`ProductDeploymentSpec.v1` holds **material names and approved pointers, never
secret values** — refused at parse time, not at review time. Unknown keys are
refused rather than ignored, because a typo in `read_only` that silently
disables a read-only filesystem is exactly the defect this facility removes.

Refusals worth knowing before you write one:

| Refused | Why |
|---|---|
| an image reference that is a tag | build once, promote the digest; a bare `docker compose up -d` against a tag once downgraded a production deployment by five weeks |
| a runtime role holding the migration owner material | that role could create, alter and drop any table for the life of the deployment |
| liveness and readiness at the same path | one of them is then wrong: liveness must not touch a dependency, readiness must fail when one is down |
| an ingress role with no readiness probe | a candidate with no readiness gate is handed traffic on a timer |
| `static = "volume"` | static assets belong to the image digest; a bind mount is how a host came to serve a different tree from its image |
| a websocket or SSE route with a read timeout under 300s | the proxy silently severs the stream and it looks like an application bug |
| `no_new_privileges = false` | there is no deployment shape that needs it |
| a relaxed security default with no declared `[[roles.security.exceptions]]` | the grant may stay; the silence may not |
| a postgres backup dataset that does not verify `schema` | a restore producing an empty database passes every other check |
| both `app_direct_shipping` and `logs` | every line stored twice, every rate threshold silently doubled |
| a published port with no `exposure` or no `address_family` | a short-form publish spawns one `docker-proxy` PER FAMILY, so a descriptor silent about IPv6 gets IPv6 anyway — and the `ip6tables DOCKER-USER` rules written to cover it are measurably inert |
| the removed `bind` field | an ignored value reads, in a diff, exactly like an honoured one |
| a wildcard bind, a hostname, an IPv4-mapped address, or an unresolved `${...}` reaching admission | `"${VM_BIND:-127.0.0.1:}8428:8428"` reads as loopback and becomes a wildcard the moment somebody sets `VM_BIND` without the trailing colon |
| an IP literal or CIDR anywhere in the descriptor, `trusted_proxies` included | a source set is a NAME the deployment-control plane resolves; topology in Git goes stale with nothing failing |
| a control no available provider enforces (`authentication = "bearer"` on a raw socket) | a control nothing enforces is worse than an absent one, because it reads as present in every review after this one |
| a role publishing a port its own edge already routes to | the edge publishes that upstream on loopback itself; a second declaration only makes the application reachable AROUND the edge |

## The execution envelope

ADR-0070 Amendment A1 keeps `ProductDeploymentSpec.v1` unchanged and accepts a
separate immutable `DeploymentExecutionEnvelope.v1` for Foundation `0.3.0a1`.
The product descriptor answers what one product needs; the envelope proves who
authorized one execution and which independent controller is allowed to judge
it.

The envelope binds controller provenance (Foundation release, immutable source
coordinate, exact released-wheel SHA-256 and exact launcher SHA-256), authorizer provenance, the
candidate and expected current release identities, Git-relation evidence, the
ordered-plan digest and at most one exact typed override. The override is bound
to the refused relation, both releases, controller wheel and launcher, plan and authorizing
decision; a reusable `--force` boolean is not part of the contract.

The candidate's `configuration_digest` hashes the exact descriptor bytes. The
plan digest is separate because it also contains transition context such as the
previous image; one candidate must not acquire a different configuration
identity merely because two hosts upgrade to it from different releases.

An independent launcher verifies its own envelope-pinned hash and the released
wheel hash, then runs the wheel with an isolated Python interpreter outside the
staged, current and rollback application checkouts. The controller acquires the
deployment lock before observing the current release, then keeps observation,
expected-current comparison, Git-relation evaluation, plan verification,
authorization, typed-plan execution and post-effect observation under that lock.
The launch environment drops `DOCKER_HOST`/`DOCKER_CONTEXT`; the controller
operates on the local host daemon selected by the authorizer's runner, then
selects only the product's Compose project within that daemon.

The transition rule is fail-closed:

| Relation | Default |
|---|---|
| first deployment | allow |
| same release, including identical image/configuration/manifest digests | allow |
| proved forward descendant | allow |
| rollback/ancestor | refuse without the exact override |
| diverged histories | refuse without the exact override |
| relation cannot be proved | refuse without the exact override |
| same source but a different image, configuration or manifest digest | refuse as a conflicting rebuild |

An override never bypasses migration compatibility, never makes a
`maintenance_required` release eligible for online deployment and never
authorizes an automatic migration downgrade.

### Authenticated launch and host provisioning

`scripts/run_authenticated_deployment.py` is the trust bootstrap. It is
deliberately standalone and is **not** imported from, or distributed inside,
the Foundation wheel it authenticates. Host provisioning installs that file,
the strict trust policy and at least one Ed25519 public key for each release and
authorization purpose as root-owned, non-writable files. Key files are pinned
by byte digest and normalized SPKI digest; one SPKI identity cannot cross
purposes. The policy exact-allowlists API origins, repository identities,
protected refs, release/authorization workflows, any reusable-workflow code and
application repositories, and pins absolute OpenSSL, Git and Docker executables
by SHA-256. Private signing keys never enter a deployment host or this package.

The protected Foundation release is finalized only after its workflow has
completed successfully. The post-completion finalizer re-reads the exact
repository, workflow bytes, run, tag, Actions artifact and registry bytes,
then signs the wheel, launcher and receipt identities. The authorizing
Deployment Control repository owes an equivalent post-completion finalizer:
its signed evidence binds the execution envelope, that exact controller
release evidence and an application-history bundle. The authorizer checkout
and application-history checkout have separate purposes and are always
materialized separately.

The bootstrap verifies both signatures and every binding before it executes
the released launcher. The launcher re-verifies the sealed inputs, installs
the exact wheel into an isolated environment, and passes the signed
authorization through a root-owned mode-0400 inherited file descriptor. A
receipt that merely agrees with caller-supplied bytes is insufficient.

The bootstrap, trust policy/public-key installation, and the Deployment
Control authorization finalizer are provisioning/adoption obligations. Their
source contracts existing here do not claim that a host has installed them.

## Layout

```
spec.py          ProductDeploymentSpec.v1 — the one thing a product declares
ingress.py       IngressPolicy.v1 — exposure vocabulary, address admission,
                 the provider capability matrix, the derived firewall rule
policy.py        the IngressPolicy.v1 section and the provider-neutral edge
                 and firewall plans
document.py      DeploymentDescriptorDocument.v1 — the canonical projection
                 an authorization binds to, and the digest over it
exposure.py      apply the plan under the lock, re-observe the host, and
                 refuse a probe taken from inside the allowlist
secrets_guard.py the descriptor holds names, never values (ADR-0009)
render/          deterministic text emitters: compose, nginx
alerts.py        64 common infrastructure alerts + the product's own
telemetry.py     resource attributes, deployment annotations, collector config
image/           the hardened OCI contract, audited against `docker inspect`
engine/          the plan as data, the executor, the exclusive lock
controller.py    independent transition decision, execution and state evidence
execution.py     strict envelope, provenance, override and Git relation policy
authenticity.py  signed release/authorization evidence and trust-policy types
providers/       concrete Effects implementations; compose_host is the only one
backup.py        four assurance levels, because "backed up" is not "restorable"
drift.py         image + config + manifest digests vs the approved plan
conformance.py   the checks a product runs in its OWN CI
cli.py           dotmac-deploy
```

## Status

`0.2.0a2` is the latest published Foundation release. ADR-0070 Amendment A1
declares the independent-controller extension for `0.3.0a1`. Its source,
strict release/envelope/state contracts and adversarial canaries are present,
but `0.3.0a1` is **unpublished**: source and static checks are not a wheel, a
release tag, CI acceptance, a product pin or a host rehearsal.

No product has completed adoption or retired its existing deployment engine.
The `0.3.0a1` release requires the sensitivity proofs and independent-launcher
rehearsal to pass in CI, followed by protected publication and install/hash
verification; product adoption and production deployment remain separate later
evidence.
See `docs/inventories/deployment-foundation-rehearsal.md`.
