# `dotmac_vendor_control_plane` @ `69a877d6…`

Verbatim bytes of three files from the consumer that composes
`dotmac-deployment-control`, captured at the commit that composed it:

> `69a877d6f0c6886e300f5433020f7f25421e111c` —
> "Compose Deployment Control, and cut target authority over to it (#71)"

`test_adoption_evidence_composition.py` parses these to prove the `composed_at`
evidence kind can establish composition, and mutates them to prove it can
refuse.

## Why `.txt` and not `.py`

These are another repository's sources. Named `.py` they would be collected by
pytest, linted by this repository's ruff configuration and type-checked by its
mypy settings — none of which Vendor's code was written against, and all of
which would fail for reasons that say nothing about the evidence contract. The
analyser takes source TEXT, so the extension is irrelevant to it.

## Why they can be trusted

`MANIFEST.json` records each file's **git blob id**, and
`test_the_fixtures_are_the_bytes_they_claim_to_be` re-derives it as
`sha1("blob <len>\0" + content)`. That is a pure function of the bytes: it needs
no network, no clone and no git binary, yet anyone holding the Vendor repository
can run `git rev-parse 69a877d6:<path>` and get the same value. Editing a
fixture to make a test pass turns that check red.

## What this does NOT establish

That Vendor still composes the module today. Every claim here is scoped to one
immutable commit and is past tense by construction — see
`UNMONITORED_BY_THIS_GATE["assertion_resolution"]`, which records that the
FETCH is still not built.
