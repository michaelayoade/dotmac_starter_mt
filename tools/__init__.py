"""Standalone, cross-repository-consumable tooling for this repository.

Distinct from `app/` (the reference assembly), `packages/` (published
distributions), and `tests/` (this repository's own test collection):
`tools/` holds stdlib-only code another repository's CI is meant to run
against its own checkout, never vendored or copied — see
`tools/composition_contract/` for the first occupant.
"""
