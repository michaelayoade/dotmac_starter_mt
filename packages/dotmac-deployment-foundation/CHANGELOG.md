# Changelog — dotmac-deployment-foundation

## 0.3.0a1 — unreleased, and HELD

`IngressPolicy.v1` — the typed exposure contract, and the non-mutating
projection that makes an exposure authorizable.

`PortPublication` gains mandatory `exposure` (`none` | `loopback` | `private` |
`public`) and mandatory `address_family` (`ipv4` | `ipv6` | `dual_stack`). The
free-form `bind` is REMOVED and declaring it is fatal; the bind address is
DERIVED from the two declarations, so a loopback publication renders an explicit
`127.0.0.1` and/or `::1` while anything routable renders a required, no-default
promotion-time variable. `none` emits no publication at all. Compose renders in
LONG SYNTAX for roles and managed dependencies alike, one entry per declared
family, so `host_ip` is a field that cannot be omitted rather than a string
position that can.

`[ingress]` declares its own `exposure` and `address_family`, because an edge is
where a `public` exposure legitimately lives; a role may no longer also publish
a port its edge already routes to. Source policy is a NAMED source set that
`dotmac-deployment-control` resolves at authorization — no product IP literal is
accepted anywhere, `trusted_proxies` included. A provider capability matrix
fails a publication closed when it claims a control no available provider
enforces.

`ingress_policy_document()` / `ingress_policy_digest()` produce the canonical
normalized document, with every default materialized and BOTH the schema string
and the exact facility version inside the digest — without the version, a
facility upgrade would change a running exposure under an unchanged approved
plan digest. `build_edge_plan()` and `build_firewall_plan()` are
provider-neutral; the firewall plan is derived defense-in-depth and never a
substitute for a correct socket binding. `dotmac-deploy ingress-policy` prints
all of it and mutates nothing.

Three measured facts are encoded rather than described. An `ip6tables`
`DOCKER-USER` rule for a published port is INERT — that chain is jumped only
from `FORWARD` while an IPv6 publish terminates on `INPUT` in `docker-proxy` —
so rules derive into `INPUT` on IPv6 and `DOCKER-USER` on IPv4, and emitting v6
into `DOCKER-USER` is refused. The IPv4 rule matches `--ctorigdstport` because
the packet there is already DNATed and its `--dport` is the container port. And
every allowlist ends in a terminal DROP, because one whose last rule is an
ACCEPT enforces nothing.

MAJOR-shaped, released as a pre-release, and the warning-phase deviation is
recorded with its four premises in `COMPATIBILITY.md`. **Publication is HELD**
while OpenBao containment and credential rotation settle — see
`docs/inventories/declared-publication-baseline.json`.

## 0.2.0a2 — unreleased

Make the strict image-audit filesystem collector run as an inspection-only
uid/gid 0 while continuing to validate the image's configured runtime
`Config.User` as numeric and non-root. A failed filesystem walk now refuses
the gate explicitly and preserves its partial output and diagnostics instead
of truncating the listing to empty evidence. Add executable negative controls
and one planted failure for every hardened-image rule.

## 0.2.0a1 — 2026-08-28

Normalize the Nginx renderer to exactly one trailing newline. The first ERP
adopter proved that `end-of-file-fixer` rewrites the 0.1 output while
`render --check` requires those original bytes, making the consumer's two gates
mutually exclusive. This is a minor release because rendered bytes are public
contract even when the behavioral configuration is unchanged.

## 0.1.0a1 — 2026-08-28

First cut. `ProductDeploymentSpec.v1`, the deterministic renderers, the
hardened image contract and its audit, the deployment state machine as data
with its executor, backup/restore assurance levels, drift comparison, the
64-alert common catalogue, the telemetry resource-attribute stamp, the
conformance kit, and the `dotmac-deploy` CLI.

Extracted product-first from three qualifying sources — `dotmac_sub`'s
deployment state machine, `dotmac_integrator`'s image and migration-ordering
contract, `dotmac_erp`'s migration-role preflight and backup-before-migrate —
with eighteen defects recorded as deliberate non-goals in `EXTRACTION.toml` and
the inventory.

Published through the protected facility lane after an exact-main disposable
host rehearsal completed with 34 passes, zero failures and zero skips.
