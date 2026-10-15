# Lane 3 admission evidence (§ 7 of LANE3_EXECUTION_TOPOLOGY.md)

`.github/lane3-execution.json` `admission_evidence` may only be set to
`docs/inventories/lane3-admission-evidence.md#<anchor>`, where `<anchor>` is a
heading below holding one row for each of the eight § 7 requirements.
`scripts/lane3_execution.py` (`require_admission_evidence`) refuses the topology
unless every row has a typed coordinate (`run:<id>`, `job:<id>`, or
`audit:`/`readback:`/`scan:` plus a token) and a lowercase sha256 digest of the
recorded evidence, with nothing pending, unknown or not applicable.

The guard checks that the evidence is recorded and well formed. It does not
fetch anything: the reviewer of the change that fills a table checks the
coordinates and digests against the runs and the OpenBao audit side.

## What each digest covers, and where the bytes live

The digest is sha256 over the exact bytes of ONE retained record, so a reviewer
can recompute it. API captures are not digestible as they come (`updated_at`
and similar fields move); the retained record is a canonical file, not the live
response.

| Kind | Digest preimage | Where the bytes are retained |
| --- | --- | --- |
| `run:<id>` / `job:<id>` | the saved run (or job) JSON with volatile fields removed, serialised as sorted-key compact UTF-8 JSON | private evidence directory, `.cutover-private/` |
| `readback:<token>` | the saved read-back documents for the control, concatenated in the order the record lists them | private evidence directory |
| `audit:<token>` | the OpenBao audit-side export for the probe window, as exported | private evidence directory, never in Git |
| `scan:<token>` | the private-side scan report | private evidence directory, never in Git |

A row covering several probes (PR exclusion, Ref and workflow failure,
Credential failure) names ONE aggregate record: its coordinate identifies the
combined record and its digest covers that record's bytes. Per-row rules the
guard enforces: No-grant refusal and Credential failure use `audit:` only;
Isolation and disclosure uses `scan:`; Authorized scheduling uses `run:` or
`job:`; Public controls uses `readback:`. All eight coordinates and all eight
digests must be distinct.

The Credential failure row names the filled B7 matrix of PR #841 (an `audit:`
coordinate with the matrix record id; the digest is sha256 of the filled matrix
file).

## Staleness

Any change to the Lane 3 JWT role, or to Gate-0 or CP policies (including
adding the proposed `gate0-exchange/` mount, W1/W2), makes the Credential
failure and No-grant refusal rows stale. Re-run those probes and record a new
section before pointing `admission_evidence` at it.

## Template (unfilled; cannot satisfy the guard)

| Requirement | Evidence coordinate | Evidence sha256 |
| --- | --- | --- |
| Public controls | TBD | TBD |
| Authorized scheduling | TBD | TBD |
| PR exclusion | TBD | TBD |
| Ref and workflow failure | TBD | TBD |
| Credential failure | TBD | TBD |
| No-grant refusal | TBD | TBD |
| Isolation and disclosure | TBD | TBD |
| Oracle reachability | TBD | TBD |
