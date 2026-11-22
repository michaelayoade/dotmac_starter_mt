# Deploy agent sources (dotmac-deploy-agent)

Inventory evidence for `packages/dotmac-deploy-agent/EXTRACTION.toml`. Read-only
inventory of the two existing deploy agents, ported (never imported) into one
product-neutral launcher. It claims no release, pin or adoption.

## Qualifying source: Sub pull agent

`dotmac_sub` worktree at `e571f9e90b68e37ae2567ece21481f10eafb5685`
(`deploy/agent/`).

| Capability | Sub source | Disposition |
|---|---|---|
| Outbound poll, terminal report, durable outbox | `agent.py`, `state.py` | Ported: outbound only, sealed observation kept until acknowledged, interrupted-run recovery marker |
| Non-root, single instance, root-owned config | `hostio.py` (`protected`, `Config.load`), `state.py` (`single_instance`) | Ported: dedicated user, flock, owner and mode checks, no symlinks |
| Ed25519 signing and key custody | `agent.py` (`Signer`) | Ported for the target PoP key; keys now arrive as systemd credentials |
| Hardened systemd unit and root-owned launcher | `systemd/dotmac-deploy-agent.service`, `launcher.sh` | Ported as an instance template, product-neutral; the launcher verifies a pinned digest and a pinned Sigstore identity read from root-owned files |
| Staging-only identity, `docker compose` executor, git checkout pinning | `hostio.py` | NOT ported: Foundation is the sole runner and the agent only launches it |

## Frozen source: CP deploy_agent

`dotmac_vendor_control_plane` worktree at `286f6f2b4544a10bd157cc84534df98968dc38dc`
(`src/vendor_cp/deploy_agent/`). Single-product, staging-only and frozen. Read
for the signed desired-state/observation contract shape only; the CP target API
this agent codes against (DESIGN-rev3 section 2.6) is not built yet, so the
client is proved against a test double that verifies requests and signs
responses.

## Other products

- ERP: `dotmac_erp` at `74fa788ae562905e4c8a1fcd46616585e4bf7694` was searched
  (`git grep`, case-insensitive: `deploy.agent`, `deploy_agent`, `pull agent`,
  `pull-agent`) outside `docs/` and returned no match; ERP deploys through
  `scripts/deploy.sh` and `scripts/deploy_production.py`. That is a statement
  about this search only.

## Not ported, by design

No CP database credential or reach, no lease DSN, no registry pull credential
and no registry egress on any host (DESIGN-rev3 section 7). Images arrive by
governed digest transfer through a loopback registry; the agent never pulls.
