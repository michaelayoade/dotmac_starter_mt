# Changelog

## 0.1.0a1 — 2026-08-19

- Add the tenant-only fulfillment saga, step, attempt, outcome, compensation,
  convergence, participant-registry, migration, and public contract surfaces.
- Schedule durable re-observation with every dispatch and every uncertain
  participant/compensation outcome, so lost callbacks converge without a local
  reaper or retry loop.
- Add a derived repair-attention reader plus explicitly authorized and audited
  redrive, compensation, and reviewed-terminal commands. Repair appends new
  evidence and never rewrites a recorded attempt or receipt.
- Extend the kernel provisioning participant port with explicit scope,
  manifest-owned participant identity, asynchronous outcome envelopes, and
  participant-decided compensation.
