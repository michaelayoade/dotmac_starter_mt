"""WHO executed a recovery and WHICH data it was — as comparable identities.

A LEAF module: `errors` and nothing else. `spec.py` parses these out of a
descriptor and `external_recovery.py` compares them against a receipt, so they
cannot live in either without a cycle — and putting them in `spec.py` would have
made the *contract* a detail of the *parser*.

The two types answer the two questions a receipt from somebody else has to
survive:

* **Who.** Never a free-text owner. `owner = "the DBA team"` cannot be compared
  with anything, so a receipt from the wrong party reads identically to the
  right one. Kind is a closed set, identifier is machine-shaped, and the
  executor's own VERSION is part of its identity — a receipt from v1 and one
  from v2 of the same platform are facts about different procedures.

* **Which data.** Independently of host and executor, because those are exactly
  the two things that change while the data does not. If identity were the host,
  a failover would orphan every proof; if it were the executor, changing
  supplier would. Both are the moments the old proofs matter most.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Final

from .errors import SpecError

__all__ = [
    "EXECUTOR_KINDS",
    "PRIVILEGE_VERIFICATIONS",
    "DatasetIdentityV1",
    "ExternalExecutorV1",
]

#: What KIND of party executed the recovery. Closed, because an open string is a
#: free-text owner with extra steps: nothing can compare `"our provider"` with
#: anything, and a receipt from the wrong party would read identically to the
#: right one.
EXECUTOR_KINDS: Final[tuple[str, ...]] = (
    "managed_database_service",
    "backup_platform",
    "operator_team",
    "sibling_product",
)

#: Machine identity, not prose. Rejects spaces and capitals so that
#: `"DBA Team (Lagos)"` cannot be smuggled in as an identifier — that string is
#: an owner, and an owner is what this type exists to refuse.
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")

#: A dataset lineage token. Opaque on purpose: it names the DATA, and anything
#: legible enough to be a hostname or a supplier name is something that changes
#: when the data does not.
_LINEAGE = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")

_SEMVER_ISH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")

#: The four privilege verifications the descriptor could not previously reach.
#: `recovery.py` has modelled every one of them since the bundle contract
#: landed; `BackupDataset.VERIFICATIONS` simply did not list them, so a
#: descriptor asking for the checks that decide whether a restore is USABLE was
#: refused at parse.
PRIVILEGE_VERIFICATIONS: Final[tuple[str, ...]] = (
    "roles",
    "ownership",
    "memberships",
    "effective_privileges",
)


@dataclasses.dataclass(frozen=True, slots=True)
class ExternalExecutorV1:
    """WHO executed the recovery, as a comparable identity.

    ``version`` is required and has no default. A managed service that changed
    its restore procedure between releases produces receipts that mean different
    things, and a receipt whose executor version is unknown cannot be compared
    with the one the descriptor accepted.

    ``key_id`` binds the identity to the key its receipts must carry, so a valid
    signature from a stranger is still a stranger — `evidence.TrustPolicy` draws
    the same line for release evidence.
    """

    kind: str
    identifier: str
    version: str
    key_id: str

    def __post_init__(self) -> None:
        if self.kind not in EXECUTOR_KINDS:
            raise SpecError(
                f"executor kind {self.kind!r} is not one of {list(EXECUTOR_KINDS)}. "
                "An open kind is a free-text owner with extra steps: nothing can "
                "compare it, so a receipt from the wrong party reads exactly like "
                "one from the right party"
            )
        if not _IDENTIFIER.match(self.identifier):
            raise SpecError(
                f"executor identifier {self.identifier!r} is not machine-shaped "
                "(lowercase, 3-64 of [a-z0-9._-]). A human-readable name here is "
                "the free-text owner this type exists to refuse"
            )
        if not _SEMVER_ISH.match(self.version):
            raise SpecError(
                f"executor version {self.version!r} is not a version token. It "
                "has no default because a receipt from v1 and a receipt from v2 "
                "of the same platform are facts about different procedures"
            )
        if not _IDENTIFIER.match(self.key_id):
            raise SpecError(f"executor key_id {self.key_id!r} is not machine-shaped")

    def as_document(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "identifier": self.identifier,
            "version": self.version,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class DatasetIdentityV1:
    """WHICH data, independently of where it lives and who holds it.

    Host and executor are precisely the two things that change while the data
    does not. If identity were the host, a failover would orphan every proof; if
    it were the executor, changing supplier would. Both are the moments the old
    proofs matter most.

    So ``lineage`` is an opaque token minted once for the dataset, and it is
    checked for the two things it must not be.
    """

    product: str
    dataset: str
    lineage: str

    def __post_init__(self) -> None:
        if not _LINEAGE.match(self.lineage):
            raise SpecError(
                f"dataset lineage {self.lineage!r} is not an opaque token "
                "(lowercase, 8-64 of [a-z0-9-]). It names the DATA; anything "
                "legible enough to be a hostname is something that changes when "
                "the data does not"
            )
        if "." in self.lineage:
            raise SpecError(
                f"dataset lineage {self.lineage!r} contains a dot and is "
                "host-shaped. A lineage derived from where the data currently "
                "lives is orphaned by the first failover"
            )

    def refuse_executor_derived(self, executor: ExternalExecutorV1) -> None:
        """A lineage that repeats the executor is the executor, renamed."""
        if self.lineage == executor.identifier or executor.identifier in self.lineage:
            raise SpecError(
                f"dataset lineage {self.lineage!r} contains the executor "
                f"identifier {executor.identifier!r}. Changing supplier would "
                "then change the dataset's identity, so every proof about this "
                "data would stop being about it at the exact moment it mattered"
            )

    def as_document(self) -> dict[str, str]:
        return {
            "product": self.product,
            "dataset": self.dataset,
            "lineage": self.lineage,
        }
