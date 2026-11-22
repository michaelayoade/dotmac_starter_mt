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
  `ff0a25b8284f7c22c9e5d930274ae4001ef3510529f75c74144544baa4158970`, Control a18
  #76 at 4819d705) and replays every vector through the agent's own functions;
  secret-shape rules are Control's, ported rule for rule.
- Not published and not enrolled in any release lane.
