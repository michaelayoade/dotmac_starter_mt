The historical oracle must be captured on Git-hosted CI from clean immutable
prechange commit `1750e2776d5f2374892761763067723ff27595f5`. Do not generate it
from the changed implementation, change expected hashes to match a failure,
or run this generator on the workstation.

The coordinator prepares that detached checkout on a Git-hosted runner using
the repository's existing hosted dependency environment, then runs:

```sh
python tests/fixtures/foundation_live_instance/generate_legacy_goldens.py \
  --baseline-checkout "$RUNNER_TEMP/foundation-legacy-baseline" \
  --output-dir "$RUNNER_TEMP/foundation-legacy-goldens"
```

The generator verifies the exact clean HEAD, isolates Python imports to that
checkout, and refuses to overwrite a destination. It emits all four original
TOML inputs and their canonical descriptor, Compose rendering, V1 execution
plan, and V1/V2 deployment evidence bytes, plus a baseline-bound SHA256 manifest.
It does no execution-provider work, host inspection, network access or mutation
of the baseline. Capture and retain the hosted run/artifact coordinates. Review
and copy the resulting directory as `goldens/` beside this README; the focused
compatibility test then compares current behavior to those immutable bytes.
The source change remains on HOLD until that fixture capture and hosted tests
are complete. No placeholder expected bytes are supplied.
