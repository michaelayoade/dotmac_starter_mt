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

## The two roles, and why they must be different machines

Authorization for one does not imply authorization for the other, and the plan
is invalid if they are the same host.

### Role A — disposable target host

Where the plan is applied, snapshotted, re-observed and rolled back.

| Requirement | Why |
|---|---|
| disposable, and not carrying any other Dotmac service | the lane deliberately opens and closes real sockets and rewrites firewall chains |
| Docker with the Compose v2 plugin | long-syntax publication and `up -d --force-recreate` |
| **IPv6 enabled on the daemon and on the host** | the entire IPv6 half of the contract is unfalsifiable without it, and this is the half that was actually open in production |
| root, for `iptables`/`ip6tables`/`ss` | the derived rules and the re-observation |
| out-of-band recovery (console or equivalent) | the lane writes INPUT rules; a mistake that locks SSH out must be recoverable without SSH |
| a recorded pre-state | `iptables-save`, `ip6tables-save`, `ss -tlnp` and `docker ps` captured before anything runs |

### Role B — independent IPv6-capable external vantage

Where the outside probes originate. A **different machine**, and genuinely
outside every source set that could apply to Role A.

| Requirement | Why |
|---|---|
| **not** the workstation | its public IPv4 sits inside `160.119.124.0/22`, which several of this fleet's allowlists ACCEPT — this is the exact vantage that produced two false P0s on 2026-08-29 |
| **not** `observe`, `s3` or `db-primary` | none has IPv6 egress, so none can run the IPv6 half at all |
| IPv6 egress, verified against Role A specifically | a vantage's own egress check does not prove the path to one target |
| its source address **enumerated against Role A's rule set before any probe** | `ProbeVantage.membership_established` is `False` until this is done, and the verifier refuses to conclude from an unestablished vantage |
| `nc` available | bash `/dev/tcp` does not exist in this shell and reads every port closed — a silent all-green |

**In this fleet an IPv6-capable outside vantage is scarce.** Only product hosts
have IPv6 egress, and a product host is not a neutral vantage. If no
independent one can be authorized, the IPv6 external half of Lane 3 is
UNMONITORED and must be recorded as such (ADR-0018) rather than reported as
covered by the on-host evidence.

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
