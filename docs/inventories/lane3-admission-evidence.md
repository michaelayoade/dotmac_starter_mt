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
