# Changelog

## UNRELEASED - 0.1.0a3 (job runner)

Source preparation only; not built or published. 0.1.0a2 is published and
immutable, so the runner cannot be folded into it.

- Job runner (Starter parity programme, Wave 0 lane B). Reads the assembly's
  `ModuleRegistry.jobs()` (`JobSpec`, kernel contract 2), keeps one live timer
  generation per (tenant, module, job), and runs each due generation through
  `JobDispatchTransport` for the new `durable_timers.job_due.v1` event: at most
  once per generation (`accept_trigger`), in the event tenant's RLS session,
  scheduling the next generation in the same transaction. Retry, backoff and
  dead-lettering use the kernel relay; a dead letter writes a tenant audit row
  and a health observation through an assembly-bound `JobHealthSink` port.
- Second process from the same image: `python -m dotmac_durable_timers.run
  --assembly module:attr`, two DSNs (`outbox_dispatcher` and `app_user`), clean
  SIGTERM. Tenant plane only; platform-plane jobs are refused at boot.
- Built-in jobs: `timer_history_purge`, `published_outbox_purge`.
- Contract-2 staff surface (API and page): job list, next due, last outcome,
  dead letters, and audited run-now / pause / resume under
  `durable_timers.jobs.read|run|manage`.
- Migration `dt_0002_job_states` (tenant plane): `mod_timers.job_states` with
  FORCE RLS; requires `tenant_audit_log.v1`.
- The manifest is now contract 2 and declares `fastapi` as a dependency; the
  kernel floor needs the allocation that publishes `dotmac_kernel.jobs`.

## UNRELEASED - 0.1.0a2 proposal

Proposed successor for a fresh build through the modern release provenance
lane, for the cutover-release-1 aggregate. This version has not been built or
published.

- Runtime behavior, public API and migrations remain unchanged from the
  published a1 source; the Kernel floor remains `>=0.1.0a72`.
- Declare a new `0.1.0a2` candidate identity rather than reissue a1 or relabel
  its tag. The three version surfaces, the catalogue row and the
  declared-publication baseline change; the a1 tag and its historical evidence
  remain unchanged.
- `EXTRACTION.toml`: the adoption plan (`shadow_and_drift`, `first_cutover`,
  `next_action`) is rewritten from a shadow comparison to saved tests plus a
  backtest. No shadow run or N-day gate is part of adoption.
- The ten PostgreSQL proofs and their sensitivity companions are to be re-run
  on the CI integration job for the exact candidate head; see the PR for the
  recorded result.
- A fresh build, publication, install-back verification, canonical tag
  evidence and an accepted release record remain required before a2 can be
  adopted. This proposal does not establish a product authority switch.

## 0.1.0a1

- Initial publication (tag `dotmac-durable-timers-v0.1.0a1`): generation-safe
  durable timers on selectable tenant and platform planes over the kernel
  outbox relay.
