# Public surface and stability — dotmac-deployment-foundation

## What is public

- `ProductDeploymentSpec` and every type it exposes, plus the `SCHEMA` string.
- The `dotmac-deploy` CLI: its subcommands, its flags, and its **exit codes**
  (`0` ok, `1` refused, `2` usage). CI wires the exit codes, so they are part
  of the contract rather than an implementation detail.
- The rendered output of every renderer, treated as bytes: `render --check` is
  a byte comparison, so a whitespace change is a breaking change for every
  consumer that has committed the previous output.
- `Effects`, `Executor`, `DeploymentPlan`, `Step`, `StepKind`, `Strategy`.
- `conformance.*` — the functions a product calls in its own CI.
- `RESOURCE_ATTRIBUTES` and the alert `code` of every entry in `COMMON_ALERTS`.

## What is not

Anything underscore-prefixed, the internal layout of the renderers, and the
`Finding`/`Comparison` message TEXT (the `rule` and `verdict` values are
stable; the prose explaining them is not).

## The version rule

**A change to rendered bytes is a MINOR bump at least**, even when the change
is cosmetic, because every consumer has committed the previous bytes and
`render --check` will fail for all of them at once. That is the intended
behaviour — the alternative is a renderer that can change what a host runs
without anyone reviewing a diff — but it means a whitespace tidy-up is a
release, not a patch.

**A new REFUSAL in `spec.py` is a MAJOR bump.** A descriptor that parsed
yesterday and does not parse today breaks a consumer's build, and calling that
a patch is how a facility loses the trust it needs to be adopted. The right
shape for a new rule is: add it warning-only in a minor release, name the
version it becomes fatal in, then make it fatal in the next major.

**A new schema version is a new string.** `ProductDeploymentSpec.v2` is a
different `schema` value, and a v1 reader REFUSES a v2 document rather than
reading the subset it understands — a field an older reader cannot see may be
the one that disables a control.

## Consuming this package

Exact-pin it. A conformance gate resolving to "whatever is newest" cannot
distinguish a product that drifted from a foundation that changed, and the
reusable workflow refuses a range for that reason.
