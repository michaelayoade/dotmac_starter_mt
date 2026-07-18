"""Single write-owner for the `Party` identity invariants shared by its two
writers — `app.features.auth.service.register` (self-service signup) and
`app.features.parties.service` (the tenant-admin parties API's create AND
update paths). See `docs/ARCHITECTURE.md`'s "Known dual-writer: Parties"
section: the writers themselves stay two (that split is deliberate — one
flow is "a person signs themselves up," the other is "an admin manages a
contact record") but the INVARIANTS both must preserve identically now have
exactly one implementation, here, instead of being hand-duplicated at every
call site.

Two invariants live here:

- `normalize_email` — the email-lowercasing rule (the `parties` table's
  uniqueness index is `lower(email)`-based; a mixed-case write that skipped
  normalization would silently mismatch a lowercase login lookup later).
  `Party.email` is the ONLY email column as of Phase 2b.1 Task 3 (finding
  F2) — the credential table used to carry its own duplicate email column,
  now dropped entirely, so there is no second writer left to keep in sync.
- `person_display_name` — the `Party.display_name` projection for
  `party_type == person` (`f"{first_name} {last_name}"`). Both the parties
  service (`create_person_party`/`update_person_party`) and the auth
  service (`register`) call this so the projection can never drift between
  writers. Organizations have no equivalent helper: `legal_name` IS the
  display name already, nothing to derive.
"""

from __future__ import annotations


def normalize_email(email: str) -> str:
    """Lowercase `email` — the one place this rule is implemented.

    Every writer of `Party.email` (the single email authority, Task 3) must
    call this (or already-normalized input) before persisting, so a
    case-insensitive read later (`login()`'s Party lookup, the parties
    uniqueness index) never silently mismatches a write that skipped
    normalization.
    """
    return email.lower()


def person_display_name(first_name: str, last_name: str) -> str:
    """`Party.display_name` projection for `party_type == person`.

    Called by both writers of a person `Party` — `app.features.parties.
    service.create_person_party`/`update_person_party` and `app.features.
    auth.service.register` — so the two never compute this independently
    (and potentially inconsistently) again.
    """
    return f"{first_name} {last_name}"


__all__ = ["normalize_email", "person_display_name"]
