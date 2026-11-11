# Changelog — dotmac-service-orders

## 0.1.0a2 — unreleased

- Add `service_location_ref` (`so_0002_service_location_ref`, additive; released
  `so_0001` bytes are unchanged) and a new V2 open command,
  `OpenServiceOrderV2` / `open_service_order_v2`. For install, reconnect and
  change orders (`NEW_INSTALL`, `RECONNECT`, `CHANGE_SERVICE`) the location ref
  is required. The ref is an opaque token (1 to 160 letters, digits, `.`, `_`,
  `:`, `-`): address text and coordinate pairs are refused by construction, and
  the module stores no address or coordinates (ADR-0039). Address text and the
  promoted pin live in the composing product, together with the qualification
  warrant that justified promoting it.
- V2 open is idempotent on `request_key` and refuses a replay naming a
  different order type, customer or location. Other order types (`UPGRADE`,
  `DOWNGRADE`, `DISCONNECT`) are refused by V2 and are opened through V1.
- `open_service_order` (V1) and `OpenServiceOrder` are unchanged and leave the
  column NULL. The column is nullable so existing rows and V1 callers keep
  working; the application can roll back without rolling the schema back.

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
