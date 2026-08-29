# Exposure rehearsal — Lane 3, and what it must be authorized to touch

**Status 2026-08-29: PLANNED, NOT RUN. Nothing in this document is evidence.**
It is the execution plan for the gate that blocks publication of
`dotmac-deployment-foundation` `0.3.0a1`, and the explicit request for the two
authorizations it needs. Neither has been granted.

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

### Role B — independent IPv6-capable external probe vantage — **NOT NAMED**

**Authorization for Role A did not carry Role B**, and the target may not stand
in for it. A probe originating on the machine under test never leaves that
host's own stack, so it cannot distinguish a loopback bind from a routable one
— which is the single fact the external half exists to establish. Using the
target as its own vantage converts a two-role rehearsal into a one-role one
that looks complete, which is worse than not running it.

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

### The vantage probably does not exist yet — raising it now

Measured on 2026-08-29: **neither the workstation nor `observe`, `s3` nor
`db-primary` has IPv6 egress.** Only the product hosts do, and using production
as a probe source for a rehearsal is its own authorization rather than a
freebie — and a product host inside the fleet's own ranges is not a neutral
vantage anyway.

So the honest position is: **satisfying Role B likely needs a host that does
not currently exist.** That is a provisioning request, and it is better raised
here than discovered mid-rehearsal. What it needs is small — any disposable VM
with working IPv6 egress, at a provider whose address space is outside every
Dotmac allowlist, reachable for the duration of one rehearsal.

If Role B cannot be authorized or provisioned, the IPv4 half of Lane 3 can
still run and the **IPv6 external half is UNMONITORED** — recorded as such
(ADR-0018), never reported as covered by the on-host evidence. The on-host
`ss -tlnp` evidence remains valid and remains insufficient on its own: it shows
what the host bound, not what the internet can reach.

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

## What closes with Role A alone, and what does not

Two lists rather than a verdict. `0.3.0a1` publication is a separate
authorization either way.

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

### REMAINS OPEN — requires Role B

| # | Gate item | State |
|---|---|---|
| 13 | IPv6 external negative: the loopback-bound v6 socket is not reachable from outside | **UNMONITORED** |
| 14 | IPv6 external positive control: `tcp/22` over v6 to THIS target | **UNMONITORED** |
| 15 | IPv4 external negative + its positive control | **UNMONITORED** unless a vantage provably outside every applicable allowlist exists |
| 16 | A `private` exposure IS reachable from inside its declared source set | open; needs a host inside that set |

Items 13-16 are **UNMONITORED**, not "not applicable" and not omitted. An
unmonitored region is a stated gap with an owner; ADR-0018 requires
"grandfathered" to stay distinguishable from "reviewed and correct", and the
same distinction applies to "unproven" against "proven safe". Item 15 is
conditional rather than impossible: it opens the moment any vantage can be
shown outside the applicable allowlists.

**What items 13-15 would have caught.** The two ports that were actually open
to the internet on 2026-08-29 were open over IPv6 only, while their IPv4 rules
read as containment. On-host `ss` would have shown the wildcard bind — so
item 4 does cover the underlying defect. What the external half adds is
independent confirmation from the direction an attacker occupies, which is the
difference between "we believe the socket is loopback-bound" and "we have
watched it refuse the internet".

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
