# Changelog

## 0.1.0a1 (unreleased)

- New package. Lane A: product-neutral launcher, HTTPS signed fetch and
  observation return, canonical params.env render and post-run classification,
  D1 secret_id delivery, OpenBao Agent config install, M11 legacy copy, refusal
  matrix. Lane B: transfer, lease client, reconcile observation, packaging.
- Conforms to the agent contract ruling: unpadded base64url wire and three-segment
  paths, Control-signed dispatch plus plan snapshot, AppRole delivery with a
  wrapping-token lookup, no chown, no HCL renderer, skopeo `--preserve-digests`.
- Vendors Control's `contract/target_api_v1.json` byte-identical (sha256
  `2e02f2ca295ee531d819d8c09aa7887ed4801c6272e60dd8b2c151d21929a907`, Control a18
  #76 at 24b24416; final for v1) and replays every vector through the agent's own functions;
  secret-shape rules are Control's, ported rule for rule.
- Role jobs (REVIEW-import-jobs A3-A13, Foundation runs them): the signed
  `run_job` plan (`dispatched_job`, window, argv digest), a metadata-only job mint
  tracked from unwrap, render-once file checks, per-job params digest (never
  `params.env`), purge on every exit and at agent start, no seal/restore for job
  roles. The Foundation entry call is refused (`foundation_job_entry_unavailable`)
  until Michael approves that increment.
- Vendors Control's `contract/target_api_v2.json` beside v1 (sha256
  `2ffcccdc2e74054ef6ff1496671c951aceaecc70e41de17b75545c4768d4c2a4`, PROVISIONAL:
  Control CI on 708e726f was not green). The PoP-signed agent observation envelope
  (`target_api_version = 2`, default stays 1 until every v2 vector passes on CI), the
  `deploy_binding` split-lease, job and image-source checks, and kind `job`.
- Not published and not enrolled in any release lane.
