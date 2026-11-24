# Maintain feature documentation

This repository owns its documentation in Git. The README is an onboarding
summary and index; detailed feature instructions belong in a guide or reference
page. Do not describe a package as composed, released or deployed without evidence.

## Check links

The CI documentation job runs [the offline checker](../../scripts/check_documentation.py)
against immutable commits. It checks inline Markdown links, image links and
reference definitions in changed tracked Markdown, including files outside docs.
Local targets must exist in the checked revision. Markdown fragments must match
an ATX heading or explicit HTML id/name. Duplicate headings use numbered suffixes.
It also checks incoming links to deleted targets in otherwise unchanged pages.
Renames are considered a removal/addition.

This is a focused parser, not full CommonMark validation: fenced code is ignored,
external URLs are not fetched, and raw HTML links, complex nested link syntax and
Setext headings are not covered. It does not claim to check remote URL health,
rendered-site routes, every historical broken link or spelling/style. A changed
page's existing broken supported links must be repaired in that change. Historical
unrelated pages are not blanket-grandfathered as valid; they are outside this
diff-based check unless a target is deleted.

## Write a feature guide

Copy [the feature template](../templates/feature.md) into the appropriate docs
folder. Keep its version marker. Fill all eight sections, replacing every TODO.
When a topic does not apply, explain why rather than leaving it empty. The checker
validates changed marked guides; it does not guess which code changes are features
or automatically write a manual. The original template is the sole intentional
placeholder document and is excluded by its exact path.

Name the authoritative owner and public contract; cover setup, usage, permissions,
failures, evidence and limitations. Link to authoritative architecture or ADRs
instead of copying rules. Reviewers verify accuracy and template applicability.

## Keep examples executable

Every Markdown file under docs/examples is an executable doctest document, found
recursively by [the example runner](../../tests/unit/test_documented_examples.py).
Every page must contain at least one test, and every expected result must pass.
The unit CI job runs these against the actual installed package APIs; nothing
duplicates package implementations in the docs checker.

Start with [tenant-aware cache keys](../examples/cache-keys.md). Add deterministic,
database-free examples here. Do not put real credentials, network calls, deployment
commands or production writes in executable examples. Database-backed examples
belong in the repository's hosted integration fixtures with their real owners.
An example proves only the exercised API behaviour, not a release, adoption,
database isolation or a complete installation tutorial.

All tests run only on Git-hosted CI under the existing repository policy. No
documentation test command or dependency installation is authorized on a local
workstation. The documentation check is a CI prerequisite of the required unit
job; failed documentation checks explicitly fail that job instead of skipping it.
