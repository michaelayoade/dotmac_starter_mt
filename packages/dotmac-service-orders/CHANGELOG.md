# Changelog — dotmac-service-orders

## 0.1.0a1 — 2026-08-22

- Extracts Sub's service-order and provisioning-readiness boundary
  (`app/models/provisioning.py`, `app/services/provisioning_lifecycle.py`).
- Keeps Sub's decision rule intact: a failed delivery run is terminal, any other
  failed check blocks and carries the first failure's reason code, and every
  decision requires an in-flight order.
- Keeps Sub's append-only rule on readiness evidence, enforced by ORM events as
  well as by the service exposing no update path.
- Replaces Sub's direct reads of Projects, Project Tasks, Work Orders and IP
  Assignments with caller-supplied normalized checks — the module decides, the
  caller observes.
- Creates three directly tenant-scoped, forced-RLS tables.
