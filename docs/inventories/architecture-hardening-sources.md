# Architecture hardening source inventory — 2026-11-03

This inventory precedes implementation. Source inspection is not evidence of
production adoption or executed qualification.

| Repository | Inspected revision | Relevant existing owners |
|---|---|---|
| Starter | `eec5d6bc765649a79947e30ae71b611c5d9764bb` | `dotmac_kernel.app_factory`, `middleware.tenant`, `middleware.rate_limit`, `features`, `assembly`; Dockerfile and root package metadata |
| ERP | `c3d6a20d571829958e9b512b82fbe34d1ae6f704` | `app/main.py` readiness: probe local dependencies and return HTTP 503 when unavailable; `app/dependency_health.py` separates required dependencies from external integrations; `app/middleware/rate_limit.py` owns a Redis sliding-window implementation |
| CRM | `a922decf1356f296f1816aba06cf2bcf966fc212` | `app/main.py` separates `/health` and `/health/ready`, returning 503 on failed readiness; `app/middleware/api_rate_limit.py` owns shared Redis rate limiting |
| Sub | `e388df1482dc32ad4dbf8fabc2f572849a3cc430` | `app/api/health.py` separates liveness, route readiness and database availability; `app/services/rate_limiter_adapter.py` supplies shared and memory implementations; `app/services/web_worker_readiness.py` owns the route/preflight decision |

## Scope and adaptation

- Readiness follows ERP's existing local-dependency conjunction and 200/503
  response decision, through an assembly-declared check rather than copying
  product-specific Redis, integrations or credentials into the kernel. Starter
  retains its named session boundaries. Public responses reveal no dependency
  details or exception strings. This is a source adaptation, not a product
  cutover, and neither ERP nor CRM's local owner is retired here.
- The existing Starter `RateLimitStore` protocol remains the sole middleware
  contract. Add assembly wiring for that protocol, not another limiter or a
  competing Redis implementation. A real shared backend must be extracted
  from the inventoried product owners with its own parity and cutover evidence;
  setting a URL alone must not claim distributed protection.
- Tenant-resolution scheduling and router failure handling extend their
  existing kernel owners; no new lifecycle, storage, migration or vocabulary
  owner is introduced.
- Runtime packaging is an assembly build concern. Source-development dependency
  breadth must not implicitly decide the deployed package closure.
- Process-active registries imply one composition per interpreter. Make that
  constraint explicit before supporting multiple independently configured apps
  in one process; runtime binding already rejects conflicting database owners.

## Qualification

Author regression tests before behavior changes. Run them only on Git-hosted
CI. Existing tenant-isolation canaries remain mandatory after scheduling
changes. This inventory authorizes no release, product adoption or deployment.
