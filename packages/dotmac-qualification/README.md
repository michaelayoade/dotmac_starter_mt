# dotmac-qualification

Owns time-bounded eligibility evidence and the resulting service-qualification
decision. Positioning and network systems supply observations only; this module
does not own coordinates, topology, reachability, provisioning, or service
lifecycle. The tenant-only `qu` lineage owns `mod_qual`.

## Warrant verification

A product that promotes a pin, address or any other consequence on the strength
of a qualification asks `verify_warrant(db, scope=..., decision_id=...,
subject_reference=..., at=...)`. It returns the decision facts only if the
decision is `ELIGIBLE`, unexpired at the caller's instant and about exactly that
subject reference; otherwise it raises `WarrantRefused` with a closed reason.
The read carries no rationale or evidence fact, so nothing about location is
exposed. The module still stores no coordinates or address.
