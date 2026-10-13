# Lane 3 handoff installation

This is a source-only installer and recovery mechanism. Its presence does not
establish a host installation, protected Environment approval, production
WireGuard proof, Gate 0 acceptance, or permission to run a proof job.

## Reviewed inputs and host prerequisites

An administrator must independently review and retain two pins: the SHA-256
of canonical `bundle.json`, and the SHA-256 of canonical
`installation-input.json`. Transfer locations are locators, not authority.
Retain the previous approved bundle and configuration with their original
pins outside the active installation; reinstalling those exact inputs is the
supported rollback of an already completed replacement.

The build command reads exact Git objects from a clean checkout whose HEAD is
the supplied full commit. It never fetches, installs dependencies or executes
supplier code. Runtime is the controller's eight-file closure, coordinator,
bootstrap manifest generator,
and installer/dispatcher. Supplier is a separate nine-file bundle at original
Starter `f867f20a2d17372172ca3a0d09a5eb4a72b62961`, with the exact published
launcher hashes. A newer controller does not silently repin the supplier.

Replacement and interrupted-upgrade recovery explicitly recognize the previous
ten-file runtime as prior evidence, retaining all protected file, hash, unit and
record checks. Every new target and transaction target requires the current
eleven-file closure. The new dispatcher does not accept the legacy closure as
an execution target; rollback restores the exact prior bundle and its own
dispatcher. Target coordinator configuration parsing remains strict.

Use Linux with systemd, cgroup v2 and `cgroup.kill`, Python 3.12, nftables,
`systemd-run`, `useradd`, `userdel`, `mount`, `umount` and
`/usr/sbin/nologin`. The actual WireGuard read path additionally needs `ip`,
`wg`, and the separately provisioned protected configuration at
`/etc/dotmac-lane3/openbao-wireguard.json`. Existing management connectivity,
firewall baseline, runner archive and its digest, coordinator authentication
and SSH authorization remain separately provisioned prerequisites. Coordinator
operation also needs `gh` and `ssh`. Installation never obtains credentials.

The `baseline_unit` input must name the real systemd owner that restores the
host's existing firewall baseline. Do not name `nftables.service` unless that
is the actual owner. Installation neither creates nor modifies firewall rules,
WireGuard configuration, credentials, runner accounts or per-run approvals.

## Build and stage

```sh
/usr/bin/python3 -I -B scripts/lane3_handoff_install.py build \
  --source /reviewed/clean/checkout \
  --revision REVIEWED_FULL_COMMIT \
  --output /reviewed/new-bundle
```

The output prints only the bundle digest. The output directory must not exist.
Prepare the separate configuration input described in
[`deploy/lane3-handoff`](../deploy/lane3-handoff/README.md). The input schemas
are the existing controller's `parse_host_config` and `parse_policy`, and the
existing coordinator's `parse_config`; no permissive substitute schema is
introduced. Supplier installation must bind the raw independent admission
digest, original supplier commit, exact nine-file hash map and canonical map
digest. Host policy and controller digests must match the staged bytes.

The input directory contains the four required configuration files and
optional `coordinator.json`, plus `installation-input.json`. The latter binds
the raw file hashes, immutable admission source coordinates and baseline unit.
The admission bytes are supplied unchanged; no installer-generated admission
or human decision exists. If supplied, coordinator configuration must agree
on admission coordinates, supplier pins and the installed dispatcher path.
Credential material must stay in its established external channel.

Transfer the reviewed bundle and configuration to real root-owned directories
with mode `0700`, files `0600`, UID/GID `0:0`, single links, and root-owned
ancestors without group or other write access. Symlinks, extra entries,
unexpected modes, hardlinks and wrong hashes refuse. Do not stage under an
untrusted or symlinked temporary directory. The host's parent directories
`/etc`, `/etc/systemd/system`, `/var/lib`, `/opt` and `/run` must already exist.

## Install and management boundary

Run the independently reviewed installer bytes from the staged bundle under
root and isolated Python:

```sh
/usr/bin/python3 -I -B /root/reviewed-bundle/runtime/lane3_handoff_install.py install \
  --bundle /root/reviewed-bundle --bundle-digest REVIEWED_BUNDLE_SHA256 \
  --config-input /root/reviewed-config --config-digest REVIEWED_CONFIG_SHA256
```

Runtime becomes a real directory
`/opt/dotmac-lane3-handoff/<bundle-digest>/`, directories `0755`, files `0444`.
The fixed configuration directory `/etc/dotmac-lane3-handoff` and its supplier
subdirectory are `0700`, files `0600`. The root-private durable state is
`/var/lib/dotmac-lane3-handoff`, directory `0700`, files `0600`.

The installer and dispatcher share the controller's persistent `lock` inode.
Installation and recovery refuse any existing `journal.json`, including
terminal or cleanup-blocked journals; they never clear a lease to make an
upgrade succeed. Existing installations are measured before any replacement.
Unowned configuration/unit paths and unexpected staging/backup paths refuse.
Retire a separately provisioned `approval.json` through its existing owner
before installing or recovering; installation never removes it.

Configure the coordinator's `management.controller_path` and the existing
restricted SSH/sudo boundary to authorize ONLY the exact installed immutable
dispatcher path with one of `prepare`, `timing`, `bootstrap`, `status`, `grant`,
`refuse`, or `cleanup`. The coordinator's fixed remote command is
`sudo -n /usr/bin/python3 -I -B <controller_path> <op>` (the coordinator accepts only `/opt/dotmac-lane3-handoff/<bundle-digest>/lane3_handoff_install.py`
as `controller_path`, never the raw controller); `-B` keeps the
isolated interpreter from writing bytecode into the measured runtime, so any
sudoers or `ForceCommand` text must match that exact form. It accepts a bounded protocol document on stdin. There
is no caller-selected executable, config path, lease argument, shell fragment
or arbitrary Python permission. Updating that external authorization is a
separate reviewed operation; the installer does not edit sudoers or SSH keys.
An older dispatcher refuses after the installed bundle changes.

The dispatcher verifies the complete installed runtime before importing it,
then calls the original controller inside the same held lock, including its
configuration preflight. The original pinned `serve` and `expire` processes
remain internal controller lifecycle operations; do not expose those paths or
the raw controller as alternative management authority. Per-run
`approval.json` is a dynamic, opaque entry for installation measurement;
the original controller validates it when an operation needs approval.
Emergency cleanup does not acquire a new approval dependency.

### Bootstrap union and observation-age profile

This profile is source preparation for hosted qualification. It requires new
independently measured private proposal bytes; the historical v1 proposal is
preserved and cannot select the new policy.

The campaign owner measures the private external proposal with its independently
approved SHA-256, calls `lane3_campaign_bootstrap.capture` with an absolute parent
deadline, and reviews/freezes its canonical `FrozenBootstrap.manifest_bytes`
before activation. The proposal retains its historical
`PROPOSED_NOT_ADMITTED` status; that string and its descriptive prose confer no
authority. No origin, alias or resolver inventory is committed to this tree.

Coordinator configuration adds `bootstrap_proposal_path`,
`bootstrap_proposal_sha256` and `bootstrap_manifest_sha256` alongside the existing
`bootstrap_manifest_path`. Both external files are owner-held regular mode-0600
files with one link and measured stable bytes. The frozen manifest is canonical
protocol JSON. The closed proposal schema is
`dotmac.bootstrap-completion-proposal.v2`, with profile
`dotmac.lane3.bootstrap-union-observation-age.v1`, `minimum_ttl_seconds: 1`,
`maximum_observation_age_seconds: 60` and
`resolver_comparison: equal-normalized-chain-address-union`. Legacy TTL-policy
fields or a different profile refuse.

The canonical resolver's `observe_bootstrap` entrypoint shares DNS acquisition,
strict parsing, finite per-original exact alias and public-address validation
with broker resolution. It requires positive TTLs, complete A/AAAA observations
from both configured resolvers, and identical normalized chains. The generator
uses their deduplicated address union; neither resolver can substitute for a
failed observation. Shared addresses select the lexically smallest approved origin
that actually resolved to them. Complete observations for every original and
resolver, including actual record TTLs and acquisition times, remain in private
provenance; deduplication only affects nft rows. Bootstrap observation age is
bounded by 60 seconds from the earliest original pre-query capture, including
later queued work. Collection completion never resets it.
The existing grant path still enforces the 32-address union with the actual
broker snapshot.

The actual invoker/operator sequence retains its original single 60-second
deadline. After preparing the frozen rows, `launch` obtains a PREPARED timing
challenge, captures fresh DNS under that inherited deadline and requires the
same manifest digest before any JIT POST. The exact GitHub admission binding
is rechecked after DNS before registration under the same deadline. It
exclusively writes the complete
mode-0600 provenance at
`<state-name>.<lease-id>.bootstrap-provenance.json` beside the local journal,
including the measured proposal digest, closed profile and all original capture
and acquisition times.
The journal binds its digest. This retained evidence is not a resume token.

The closed bootstrap receipt requires the profile, challenge, manifest digest,
provenance digest and floored `observation_remaining_ms` from that original age
deadline. The former `ttl_remaining_ms` bootstrap field is refused.
The controller binds its ticket to phase, lease and boot and uses the existing
`snapshot_deadline` calculation to subtract the full host interval, including
both management hops. Bootstrap permits one original PREPARED timing challenge;
another cannot extend the budget. It checks that same separate age deadline before and
after installing rules and around runner launch. Expiry refuses and invokes
the existing exact-owned cleanup. A fresh completed installation keeps the
original 300-second bootstrap rollback window; the later broker grant retains
its minimum DNS TTL of 45, lifetime `min(TTL, 60 seconds)` and 45-second usable
floor. Cleanup does not resolve
DNS or require the provenance file to exist.

This bootstrap age is owner-authorized observation age, not DNS validity.
An address supplied by only one resolver may enter the union, and it may have
expired from DNS before installation or remain permitted during the existing
300-second rule window. Matching chains do not corroborate every address.

These changes require a newly reviewed immutable runtime bundle and a reviewed
dispatcher authorization update. The previously installed bundle cannot
enforce the new receipt. The invoker's harness takes its bundle identity from
the measured operator/coordinator binding, avoiding a hardcoded future bundle
hash. Source implementation and authored hosted canaries alone do not establish
host installation or campaign acceptance.

## Boot reconciliation and interrupted installation

The generated `dotmac-lane3-handoff-reconcile.service` is a root oneshot unit,
enabled as `WantedBy=multi-user.target` (a want, not a requirement), ordered
`Before=multi-user.target` and after the explicitly named firewall baseline
owner, with a 45 second start timeout. A failed or timed-out reconcile
therefore does not fail `multi-user.target` or block host boot; safety stays
fail-closed in the dispatcher instead. Its immutable dispatcher runs original
controller reconciliation before writing a root-private readiness receipt
under `/run`, bound to the current boot ID and both installed digests.
`prepare` additionally requires that receipt and an active reconciliation
unit. Every lease, and so every egress grant, starts at `prepare`: `bootstrap`,
`grant` and `serve` act only on an existing lease bound to the current boot. A stale boot receipt, failed service, byte drift or pending transaction
refuses preparation.

Before the first runtime or live-configuration mutation, the installer fsyncs
`install-transaction.json`. All management refuses while that marker exists.
Configuration replacement uses same-parent directory renames with a retained
prior backup, followed by atomic unit/record updates. The marker transitions
to `awaiting_reconcile` before service start. The installer releases the host
lock while waiting for systemd; only the root boot entry may reconcile that
exact pending target. The installer then reacquires the lock and verifies the
target, successful service and current readiness before recording `committed`.
It retires the prior backup and finally removes the marker.

Failure or process death leaves the marker as a refusal barrier. Do not delete
it or invoke the raw controller to bypass it. An administrator uses the same
reviewed installer under isolated Python:

```sh
/usr/bin/python3 -I -B /root/reviewed-bundle/runtime/lane3_handoff_install.py recover
```

Before the durable `committed` point, recovery restores the exact prior
configuration bytes, modes, unit and installation record, retires only exact
owned new/staged files, and restarts prior reconciliation. With no prior
installation it removes only the new owned configuration/unit/record and
exact owned enablement link. Recovery of a durable `committed` transaction
finishes idempotent prior-backup retirement and reports `COMMIT_FINISHED`;
it does not claim to have rolled back an already committed target. Unknown
drift or unsafe objects retain the barrier for existing management recovery.
Previous readiness is deliberately regenerated, never carried across rollback.

## Uninstall

```sh
/usr/bin/python3 -I -B /opt/dotmac-lane3-handoff/<bundle-digest>/lane3_handoff_install.py uninstall
```

`uninstall` removes a completed installation. It refuses with
`installation.lease` if any `journal.json` exists (active or terminal) and
with `installation.pending` if an install transaction is pending. Under the
shared lock it first verifies the installation record and the runtime,
configuration and unit trees exactly; any drift (including a present
`approval.json`, a leftover `multi-user.target.requires` link, or a
non-exact enablement link) refuses before anything is deleted. Retire a
separately provisioned `approval.json` through its owner first.

It then persists `uninstall-intent.json` and, in order: disables and stops the
reconcile unit (a positively observed `not-found` is tolerated); removes the
unit file and its `multi-user.target.wants` link, only if exactly owned; runs
`daemon-reload`; removes the installed runtime directory, configuration
directory, readiness receipt and installation record, exact-owned files only;
and reads back that all are absent before removing the intent. The empty
`/opt/dotmac-lane3-handoff` parent is removed only if empty.

The intent makes the operation idempotent and SIGKILL-safe: while it exists,
install, verify, boot reconciliation and management dispatch refuse with
`installation.pending`, and rerunning `uninstall` finishes the remaining steps,
re-measuring whatever is left. With nothing installed it reports `ABSENT`;
success reports `UNINSTALLED`. Uninstall deletes the installed dispatcher, so
rerun an interrupted uninstall from the reviewed bundle copy instead:
`/usr/bin/python3 -I -B /root/reviewed-bundle/runtime/lane3_handoff_install.py uninstall`.
Never delete `uninstall-intent.json` by hand. Uninstall never touches nftables state, users, the
state directory and its lock, or anything the installer did not record.
Records written by older bundles (with the previous `RequiredBy` unit) do not
validate under this code and must be retired by the bundle that wrote them.

`verify` performs read-only installed byte/mode/census checks under the shared
lock; it does not establish readiness or approve a run. Root-only
`boot-reconcile` is reserved for the unit. Administrative commands are not
part of the coordinator's permitted management surface.

## Evidence required

All test execution is hosted CI only. Unit contracts cover strict input
schemas, original supplier pins, management command shape, generated unit
ordering and readiness binding. The hosted installation lifecycle suite must
exercise real protected filesystem state, the actual controller reconciliation
and a real systemd oneshot: install, refusal before readiness, any active
journal, process death at durable intent and live activation, exact rollback,
successful prior reinstallation, committed-finalization recovery, mode/symlink
drift and shared-lock collision. Preserve exact-head CI evidence.

These tests are synthetic installation evidence. Separate controlled-host
acceptance still requires actual kernel WireGuard proof, the full hardened
runner/systemd composition, expiry of established connections, interruption
and reboot reconciliation, and the remaining admission/CP prerequisites.
