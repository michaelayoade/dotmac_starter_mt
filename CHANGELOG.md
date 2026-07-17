# Changelog

## 0.4.0 — 2026-07-17
- Phase 1 infrastructure foundation: app/core + feature registry, sub-derived
  CRUD/UoW/logging/errors, architecture governance, CI, Docker/deploy.
- BREAKING (API error bodies): all HTTP errors — including 401/403/404/422/429
  from guards and middleware, not just domain exceptions — now use the JSON
  envelope `{"code", "message", "details", "request_id"}` instead of FastAPI's
  `{"detail": ...}`. Clients parsing `detail` must migrate to `message`/`code`.
