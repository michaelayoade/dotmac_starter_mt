# Lane 3 source capability owner and review inventory

Status: proposed source contract, no newly classified live packet or window.
Owner: `scripts/lane3_handoff_coordinator.py::parse_config` and
`readback_binding`; controller independently verifies the qualification's
approval before effects. The manifest is trusted deployment input reviewed with
the exact source closure. It is never a runner assertion or a source analyzer's
claim that arbitrary code is safe.

The only implemented capability is `dotmac.lane3.source-capability.v1` with
purpose `qualification`. The exact-key record contains repository, workflow path,
workflow commit and Git blob, Starter supplier repository/commit, and the exact
path-to-SHA256 supplier module map. Every field must equal the independently
pinned coordinator configuration. Its operations must be exactly the ordered
list `github-oidc`, `bounded-openbao-kv-read`, `proof-record`, `cleanup`; its
terminal classification must be `execution-refused`. The topology evidence
reference and evidence hash remain null for that capability. Unknown fields,
purpose, operation, terminal classification or source mismatches refuse.

Review must inspect the complete executable workflow and all transitive supplier
imports at those pins, including dynamic/fetched code and external actions.
Changes to any workflow blob, supplier hash/path, import closure or operation
invalidate the previously reviewed capability digest and configuration digest.
The operator's measured deployment and controller's independent expiring approval
bind that complete tuple; neither a self-computed new digest nor a newly named
manifest is acceptance. A new source classification requires human review of its
actual closure, not an auto-generated permissive manifest.

The known refusal-only launcher inspected in the adopted assessment was commit
`0f3db810bca29a9b0a6f82319a7ecef39cf53a3e`, workflow blob
`4edb7bfee47230a3b55c1d3b8ec61cafc1651abb`. That assessment observed a bounded
OIDC/KV proof, `gate0_accepted:false`, and final unconditional refusal, with no
candidate execution path. These are evidence coordinates for the source review,
not a hardcoded allowlist or approval of another run. The actual v2 deployment
owner must supply the entire reviewed supplier map and refresh the packet once;
this inventory supplies no operational config or authorization.

The installed runtime's validator is independently measured through
`lane3_handoff_install.RUNTIME_MODULES` and the operator runtime roles. Topology
and fixed evidence file are DATA owned by one admission commit. The canonical
validator checks recorded table completeness/shape; human acceptance still owns
the truth of the evidence. Complete evidence does not install an execution owner:
`admitted_execution` remains `execution.unsupported` before any privileged effect.

Hosted canaries cover exact qualification with null admission, phase mutation,
unknown purpose, source/capability tampering, regular-file/symlink and same-commit
evidence binding, complete real evidence parsing followed by unsupported
execution, approval expiry/substitution before lease effects, and owned legacy
cleanup after invalidation. They establish mechanical scope only; no fixture
claims a real OpenBao audit, ExecutionGrant, Gate 0 or candidate readiness.
