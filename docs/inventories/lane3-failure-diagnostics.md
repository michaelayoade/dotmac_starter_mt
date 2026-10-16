# Lane 3 bounded failure diagnostics

The campaign management helper, bridge, operator and invoker preserve fixed
failure metadata without changing admission, timer predicates, deadlines,
retry policy, process ownership or cleanup authority. This is diagnostic
coverage, not a resolution of Window7's unknown failing phase or Gate0 proof.

The bridge's failure-only frame is exactly `{"status":"REFUSED","diagnostic":D}`.
`D` contains exactly `phase`, `stage`, `category` and `cleanup`, each a string
from explicit finite whitelists. Phase names the attempted operation rather
than the last completed state. Retain stages distinguish arming, binding
checks, both retained readbacks, original stop and original readback. Cleanup
is `not_attempted`, `completed` or `unproven`; it never replaces the primary
failure category. Cleanup is conservative across attempts: an earlier unproven
outcome cannot be overwritten by later successful finalization. The original
failure phase/stage/category remains intact.

The bridge recognizes exceptions by the class identity of its measured helper,
not by arbitrary exception attributes. The operator accepts only the exact
refusal frame with whitelisted values and the currently attempted phase. A
legacy bridge frame, malformed frame or unrecognized value remains a generic
refusal. All failures retain failing exits; diagnostics never advance a phase.

Only the measured coordinator launch enables parsing of nonzero child output.
That output must be exactly `{"status":"REFUSED","category":C}` with `C` in the
existing handoff protocol's `CATEGORIES` (the operator's explicit wire snapshot
is checked against its canonical owner). Duplicate keys, additional keys,
nonfinite values, malformed/private output and arbitrary child roles remain
generic failures. Stderr is still suppressed; no raw child output or exception
message is retained in a diagnostic.

For a future measured private owner wrapper, replace exception-class-only
reporting with `operator.failure_diagnostic(exc)` from that same measured,
configured operator instance. This API revalidates metadata, returns a fresh
fixed-label dictionary, and maps unknown exception types to generic unproven.
Never serialize `str(exc)`, `repr(exc)`, exception attributes, tracebacks,
stdout or stderr. Keep the wrapper's existing refusal/nonzero behavior and
cleanup unchanged. Refresh and review the wrapper, source acceptance and all
transitive packet pins together; do not edit an ended or frozen packet in place.

Hosted CI owns executable evidence. Unit coverage targets transition stages,
all retained-launch phases, strict nonzero coordinator refusals and privacy
canaries. The existing real-systemd hosted integration matrix additionally
injects failure after an actual original stop/absence readback. Local static
checks are not test evidence.
