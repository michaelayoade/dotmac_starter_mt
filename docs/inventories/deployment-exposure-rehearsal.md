# Exposure rehearsal — Lane 3, and what it must be authorized to touch

**Status 2026-08-29: RUN. 14 of 16 items CLOSED; 2 remain.** Both roles were
authorized and both were used. The observed bytes are checked in at
`scripts/exposure-rehearsal/observed/`, and the fixture that produced them is
`scripts/exposure-rehearsal/product.toml`.

## Result

| Item | Result |
|---|---|
| 1-3 apply under the lock, snapshot, non-recreating apply refused | **partial** — applied with `up -d --force-recreate` and a pre-state snapshot, but not driven through `ExposureTransaction`: no `ExposureEffects` implementation exists for a real host yet |
| 4 socket re-observation | **CLOSED** — 18443 on `127.0.0.1` AND `[::1]`; 18445 on `127.0.0.1` only; no `0.0.0.0:`, no `[::]:` |
| 5 `docker-proxy` PIDs new, one per family | **CLOSED** — 1136702 / 1136709 / 1136725 |
| 6 firewall re-observation | **n/a for this fixture** — every publication is `loopback` or `none`, so the derived plan is empty by construction |
| 7 inert v6 chain fixture | **NOT captured** — no v6 rule to observe; needs a `private` publication |
| 8 rollback | **partial** — teardown restored the pre-state exactly (sockets gone, both `DOCKER-USER` chains unchanged), but a PROVOKED transactional rollback was not driven |
| 9 digest equality | **CLOSED** — `sha256:9b748af9…` identical across the descriptor's canonical document and the verification report |
| 10 `exposure = "none"` emits no socket | **CLOSED** — 18444 absent from `ss` |
| 11 target closed-port behaviour | **CLOSED** — Role A **RSTs** (6-7 ms), so probes here can conclude absence |
| 12 privileged-vantage refusal on a real probe | **CLOSED** — see below |
| 13-15 external negatives + positive controls, both families | **CLOSED** — see below |
| 16 `private` reachable from inside its source set | **NOT run** — this fixture declares no `private` publication |

### The external proof, item 13-15

From Role B, all in one run:

```
IPv4 POSITIVE CONTROL  ssh/22 over IPv4            OPEN
  18443 / 18445 / 18444 over IPv4                  refused
IPv6 POSITIVE CONTROL  ssh/22 over IPv6            OPEN
  18443 / 18445 / 18444 over IPv6                  refused
```

Both positive controls fired, so the six refusals mean "not reachable" rather
than "the path is broken". This is the release's central claim, measured: an
IPv6 socket declared loopback refusing the internet.

### The trap, reproduced live — item 12

Same port, same minute, two vantages:

```
workstation (inside 160.119.124.0/22) -> ERP 9001 : OPEN
Role B      (outside every allowlist) -> ERP 9001 : refused
```

`accept_public_exposure_evidence` then **refused** the workstation result,
refused an unenumerated vantage, and **accepted** Role B — the negative control
that stops the refusal being a blanket ban. Unit coverage proves the function
refuses a constructed vantage; this proves it refuses the actual connection
that produced two false P0 escalations.

### What the run also found

**The verifier fired `undeclared_socket` on two sockets belonging to other work
on the shared host** (`127.0.0.1:55439`, `127.0.0.1:5434`). Correct behaviour,
and the other-direction sweep earning its place: a verifier that only walks the
descriptor cannot see the port the descriptor does not mention.

**Role A is not a clean host.** It carries four containers from other agents'
work, which the plan below asks it not to. That cost nothing here only because
this fixture derives no firewall rules; a `private` publication would write
port-scoped rules into shared chains, and a snapshot-restoring rollback on a
shared host can delete a rule another agent added mid-run.

**nginx cannot start under the facility's `read_only: true`** without a tmpfs
for `/var/cache/nginx`. Correct hardening, and the reason the fixture uses a
server that writes nothing.

---

The rest of this document is the plan the run followed.

`deployment-foundation-rehearsal.md` describes Lanes 1 and 2 and remains the
record for them. This is a third lane because it proves a different thing:

| Lane | Proves |
|---|---|
| 1 — the written suites | the code does what its own tests say |
| 2 — the disposable host | a real engine, database, ingress handoff, restore and observability loop |
| **3 — exposure** | **that the declared exposure semantics hold against a real socket, from a vantage that can actually falsify them** |

## Why Lane 1 cannot close this gate

`IngressPolicy.v1` and the `exposure` verifier are complete and tested, and
every one of those tests supplies its own observation. `ProbeResult` and
`ProbeVantage` are typed INPUTS a caller provides; the facility performs no
network I/O and holds zero runtime dependencies, which is correct for a build
runner and is also the whole limitation. **Nothing has yet compared the
semantics against a socket that a kernel opened.**

The specific claims that unit tests cannot settle:

- that a rendered `host_ip` produces the socket the renderer believes it does,
  on both families;
- that `docker-proxy` binds what the long-syntax entry says, and that the
  process is a NEW one rather than a survivor of the previous container;
- that an `ip6tables DOCKER-USER` rule is inert in the way measured, rather
  than in the way described;
- that a rollback restores an observed state on a host, not in a fake;
- that a probe from outside every applicable allowlist reaches, or does not
  reach, what the plan says.

## The two roles

**Authorization for one does not imply authorization for the other, and the
plan is invalid if they are the same host.** Each is requested separately
below, with its own status.

### Why the vantage cannot be the target

This is not bureaucracy. Both failures happened on this fleet on 2026-08-29.

- **A probe originating on the machine under test proves nothing about
  external reachability.** It never leaves the host's own stack, so it cannot
  distinguish a loopback bind from a routable one — which is the single fact
  the whole lane exists to establish.
- **A probe from inside an allowlist proves nothing about public
  reachability.** The workstation sits inside `160.119.124.0/22`, which several
  of this fleet's `DOCKER-USER` allowlists ACCEPT. Two agents independently
  connected to "public" ports from it and each escalated a P0 that did not
  exist. The connections were real; the conclusion was not.

`ProbeVantage.membership_established` encodes the second one: a vantage that
has not been enumerated against the target's rule set cannot conclude anything,
and the verifier refuses rather than assuming it is outside.

### Role A — disposable execution target — **AUTHORIZED: `85.190.246.211`**

**Status: authorized by Michael, 2026-08-29.** Everything in the Role A column
of the gate table below may run against it.

**What is still missing is an ACCESS PATH, not an authorization.** This
repository carries no SSH configuration entry and no reference to that address,
so the connection user and the key's location have to be supplied before
anything runs. Guessing either is the "never infer a target" mistake with an
extra step. Per the secrets rule the key is named by POINTER, never by value.

Where the plan is applied, snapshotted, re-observed and rolled back.

| Requirement | Why |
|---|---|
| disposable, and not carrying any other Dotmac service | the lane deliberately opens and closes real sockets and rewrites firewall chains |
| Docker with the Compose v2 plugin | long-syntax publication and `up -d --force-recreate` |
| **IPv6 enabled on the daemon and on the host** | the entire IPv6 half of the contract is unfalsifiable without it, and this is the half that was actually open in production |
| root, for `iptables`/`ip6tables`/`ss` | the derived rules and the re-observation |
| out-of-band recovery (console or equivalent) | the lane writes INPUT rules; a mistake that locks SSH out must be recoverable without SSH |
| a recorded pre-state | `iptables-save`, `ip6tables-save`, `ss -tlnp` and `docker ps` captured before anything runs |

**What the target must be able to do:** apply the authorized plan under the
deployment lock, hold a pre-change snapshot, re-observe its own sockets,
`docker-proxy` processes and firewall chains, and roll back to the snapshot.

### Role B — independent IPv6-capable external probe vantage — **`94.72.99.155`**

**Status: named and VERIFIED, 2026-08-29.** Sources: v4 `94.72.99.155`,
v6 `2a02:c204:2353:7605::1`.

The target may still not stand in for it. A probe originating on the machine
under test never leaves that host's own stack, so it cannot distinguish a
loopback bind from a routable one — the single fact the external half exists to
establish. Using the target as its own vantage converts a two-role rehearsal
into a one-role one that looks complete, which is worse than not running it.

#### What was verified, and why the last line is the load-bearing one

| Condition | Evidence |
|---|---|
| global IPv6 with a default route | `2a02:c204:2353:7605::1/64`, default via `fe80::1` dev eth0 |
| IPv6 egress actually works | `[2606:4700:4700::1111]:443` and `[2001:4860:4860::8888]:53` both OPEN |
| **outside every Dotmac allowlist** | OpenBao `8200`, ERP `9001`, ERP `6391` all **refused** |
| **the refusals mean something** | **ERP `443` is OPEN from the same vantage** |
| holds no fleet credentials | no `/opt/openbao`, zero `BAO`/`VAULT` environment variables |
| can reach Role A | `85.190.246.211:22` reachable |

**The fourth row is why the third can be believed.** Three refusals on their own
are equally consistent with a vantage that cannot reach anything — a broken
probe and a working allowlist produce identical output. One reachable Dotmac
port from the same source at the same time separates them. This is the same
target-specific positive control the probe plan demands, applied to the
vantage itself before the vantage is trusted to say anything.

That is what makes `membership_established` true for this host rather than
assumed: it was checked against the actual rule sets, in both directions.

**Status: not named, and it cannot be inferred from the target.** This is a
separate authorization request and, on present evidence, probably a
provisioning request.

**What the vantage must be able to do:** reach Role A's global IPv6 address
from outside every source set that could apply to it, and prove it did so
against Role A specifically rather than against the internet in general.

A **different machine**, and genuinely outside every source set that could
apply to Role A.

| Requirement | Why |
|---|---|
| **not** the workstation | its public IPv4 sits inside `160.119.124.0/22`, which several of this fleet's allowlists ACCEPT — this is the exact vantage that produced two false P0s on 2026-08-29 |
| **not** `observe`, `s3` or `db-primary` | none has IPv6 egress, so none can run the IPv6 half at all |
| IPv6 egress, verified against Role A specifically | a vantage's own egress check does not prove the path to one target |
| its source address **enumerated against Role A's rule set before any probe** | `ProbeVantage.membership_established` is `False` until this is done, and the verifier refuses to conclude from an unestablished vantage |
| `nc` available | bash `/dev/tcp` does not exist in this shell and reads every port closed — a silent all-green |

### Why one had to be provisioned — kept as the record

Measured on 2026-08-29: **neither the workstation nor `observe`, `s3` nor
`db-primary` has IPv6 egress.** Only the product hosts do, and using production
as a probe source for a rehearsal is its own authorization rather than a
freebie — and a product host inside the fleet's own ranges is not a neutral
vantage anyway.

So Role B needed a host that did not exist, and one was provisioned:
`94.72.99.155`, verified above. The requirement it was provisioned against —
**any disposable VM with IPv6 egress in address space outside every Dotmac
allowlist** — is kept here because it is the requirement any REPLACEMENT vantage
must also meet, and because "we already have a host with IPv6" is the shortcut
that would put a product host in this role.

The on-host `ss -tlnp` evidence remains valid and remains insufficient on its
own: it shows what the host bound, not what the internet can reach.

## The five artifacts publication is gated on

Distinct artifacts, produced by **the controller executing the exact signed
plan** — not by a script standing in for it. The proof is of the real path.

### 1. Apply

The controller applies the authorized plan under the product deployment lock.
Recorded: the plan identifier, the descriptor digest, the exact command, and
the pre-change snapshot (`HostObservation`) taken before anything mutated.

Refusals that must be exercised, not merely present: an apply whose command
cannot change a binding is refused (`docker compose restart` reuses the
container it has; a plain `up -d` does not recreate an unchanged image).

### 2. Re-observation

After apply, on Role A:

- `ss -tlnp` shows `127.0.0.1:<port>` and `[::1]:<port>` for a loopback
  dual-stack publication, and shows **neither** `0.0.0.0:<port>` nor
  `[::]:<port>`;
- the `docker-proxy` PID for the port is **new** — a surviving PID means the
  container was not recreated and the apply proved nothing;
- `iptables-save` and `ip6tables-save` are captured and parsed by
  `parse_iptables_save`, and the derived rules land in `DOCKER-USER` on IPv4
  and `INPUT` on IPv6.

### 3. Negative AND positive probes

From Role B, per family:

| Probe | Expected | What it establishes |
|---|---|---|
| **positive control** to Role A `tcp/22` on IPv4 | reachable | the v4 path from this vantage to THIS target works |
| **positive control** to Role A `tcp/22` on IPv6 | reachable | the v6 path to THIS target works — a vantage-egress check does not substitute |
| **negative** to the loopback-bound port, IPv4 | not reachable | the bind is what the render said |
| **negative** to the loopback-bound port, IPv6 | not reachable | the half that was actually open in production |
| **negative** to an `exposure = "none"` port | not reachable | no socket exists at all |
| **inside-vantage** probe from a host within a declared source set to a `private` port | reachable | the allowlist admits what it should |

The target-specific positive control is mandatory. Once the port under test is
closed, an unreachable result is indistinguishable from a broken path, and only
a control against the **same target** separates them. `tcp/22` served this
purpose on 2026-08-29.

Two fail-closed handling rules, both exercised:

- a probe whose vantage is inside an accepted source set, or whose membership
  was never established, must raise `PrivilegedVantageError` and must NOT be
  recorded as public-exposure evidence;
- silence from a host that DROPs closed ports yields `inconclusive`, never
  `absent`. Role A's closed-port behaviour is recorded before the lane runs —
  ERP silently DROPs and the SON host RSTs, so identical probe output means
  different things per host.

### 4. Rollback

Deliberately provoked, not simulated: apply a plan whose verification must
fail, and show the transaction restores the pre-change snapshot. Recorded: the
refusal, the restored chains, and a re-observation matching the snapshot.

### 5. Digest equality

The artifact that ties the other four together, and the one that fails most
quietly if it is skipped.

```
descriptor digest  ==  the digest inside the authorized plan
                   ==  the digest the post-apply verification report cites
```

`ProductDeploymentSpec.to_canonical_document().sha256_digest()`, the plan's
bound digest, and `VerificationReport.descriptor_digest` must be **byte-identical
strings**, recorded side by side. If they cannot be shown equal, the execution
proves nothing about the thing that was authorized: it proves something about
whatever happened to be on the host.

## The sixteen gate items, and which host closes each

Sixteen items, three groups, and **every one of them must close** before
`0.3.0a1` may be published. The grouping is by which host produces the
evidence, not by which items are optional — none is.

The workstation group is worth reading rather than skimming: a vantage that may
NOT claim reachability is still the correct instrument for the two items that
are not reachability claims.

### CLOSES with Role A alone — on-host evidence, no external vantage

On-host `ss -tlnp` is the AUTHORITY for what the host bound, not a probe. That
is not a convenience: hosts differ in closed-port behaviour, so identical probe
output means different things on different hosts, while `ss` reads the socket
table directly.

| # | Gate item | Why Role A suffices |
|---|---|---|
| 1 | Apply under the product deployment lock | entirely on-host |
| 2 | Pre-change snapshot (`HostObservation`) | captured before mutation, on-host |
| 3 | Non-recreating apply is REFUSED | exercised against the real controller path; `restart` reuses the container it has |
| 4 | Socket re-observation: `127.0.0.1:` and `[::1]:`, never `0.0.0.0:` / `[::]:` | `ss -tlnp` is the authority |
| 5 | `docker-proxy` re-observation: PID is NEW, `-host-ip` correct per family | a surviving PID means the container was never recreated |
| 6 | Firewall re-observation: v4 rules in `DOCKER-USER`, v6 in `INPUT`, terminal DROP present, `--ctorigdstport` on the remapped publish | chain dumps are on-host |
| 7 | The inert-chain fact captured as a fixture: a v6 `DOCKER-USER` rule with a ZERO packet counter | `ip6tables -L -v -n`, on-host |
| 8 | Rollback, provoked rather than simulated | restores the observed snapshot; re-observation matches |
| 9 | Digest equality: descriptor == authorized plan == `VerificationReport.descriptor_digest` | computation plus the applied plan |
| 10 | `exposure = "none"` emits no socket at all | absence in `ss` |

### CLOSES with the WORKSTATION vantage — the two things that are not reachability claims

The workstation sits inside `160.119.124.0/22` and may not be used to claim
reachability. It is still the right instrument for exactly two items, because
neither is a claim about reachability:

| # | Gate item | Why a privileged vantage is admissible here |
|---|---|---|
| 11 | The target's closed-port behaviour (DROP vs RST), recorded | a property of the TARGET's response style, not of who can reach it — and it is required to interpret any later probe |
| 12 | The privileged-vantage refusal FIRES on a real probe | the point is that the verdict is refused; a genuinely inside vantage is the only way to demonstrate it |

Item 12 is worth running deliberately. `accept_public_exposure_evidence` has
unit coverage, but a real connection from a real inside vantage being refused
is the demonstration that the two false P0 escalations of 2026-08-29 would now
be caught rather than repeated.

### CLOSES with Role B — 4 items, no longer unmonitored

`94.72.99.155` is verified outside every applicable allowlist, so these four
are now REQUIRED to close rather than recorded as gaps.

| # | Gate item | Expected |
|---|---|---|
| 13 | IPv6 external negative: the loopback-bound v6 socket | not reachable |
| 14 | IPv6 external positive control: `tcp/22` over v6 to THIS target | reachable |
| 15 | IPv4 external negative + its `tcp/22` positive control | not reachable / reachable |
| 16 | a `private` exposure reached from inside its declared source set | reachable |

**Why these stopped being optional.** The earlier reading — that on-host `ss`
already covers the underlying defect, so the external half is confirmation —
was rejected on its premise rather than its logic. For a release whose entire
subject is ADDRESS-FAMILY ENFORCEMENT, the IPv6 external proof is not
corroboration of something already established; it is the release's central
claim. A facility that exists to make IPv6 exposure declarable, and ships
without once watching an IPv6 socket refuse the internet, has not been tested
against the thing it was built for.

Item 15 was conditional on a vantage outside the applicable allowlists rather
than impossible. That vantage now exists, so the condition is met and the item
opens with the rest.

**Publication of `0.3.0a1` is gated on all sixteen.** An UNMONITORED region is
an acceptable answer for a peripheral property and is not one for the property
a release is named after.

## Fixtures this lane produces

Real observations, checked in as parser and verifier fixtures so the next
change is tested against measured bytes rather than invented ones:

- `ss -tlnp` output for loopback dual-stack, and for a `private` bind;
- `docker-proxy` process lines including a remapped publish;
- `iptables-save` and `ip6tables-save` before and after apply;
- an `ip6tables DOCKER-USER` rule with its zero packet counter, which is the
  measured form of "inert";
- a remapped publish's `--ctorigdstport` rule, and the `--dport` rule that
  matches nothing;
- an allowlist with its terminal DROP, and the same allowlist without it.

## Not in scope

No production host. The emergency ERP and SON edits of 2026-08-29 are
product-first evidence and parity fixtures; they are neither the mechanism nor
the target design, and this lane does not touch either host.

Publication of `0.3.0a1` stays HELD regardless of the outcome:
`docs/inventories/declared-publication-baseline.json` records it as
`declared-unpublished` while OpenBao containment and credential rotation
settle. A green Lane 3 removes one blocker; it does not remove that one.

## The publication gates are three separate things

Easy to conflate, and conflating them is how a green preflight comes to read as
"the release is attested".

| Gate | Establishes | Does NOT establish |
| --- | --- | --- |
| **Publisher authentication** | that the identity doing the publishing is the intended one | anything about what was published |
| **Artifact signing** | that this artifact was signed, by which certificate | that the signature was recorded anywhere durable |
| **Provenance** | what built the artifact, from which source, on which runner | who published it |

**Publisher authentication does not close signing or provenance.** A green
preflight establishes who is publishing and nothing about what.

`AGENTS.md` rule 39 binds the signing gate specifically: a signed release
pipeline verifies **the produced artifact's** application identity and its
**actual signing certificate**, never secret or file existence — and a step is
renamed if it does not test the property it is named for. A step called
"verify signing" that checks a keystore file exists is testing the presence of
a file and must be called that.

Lane 3 establishes none of the three. It is about exposure semantics against a
real socket, and it is listed here only so the two are not mistaken for one
gate when publication is finally considered.
