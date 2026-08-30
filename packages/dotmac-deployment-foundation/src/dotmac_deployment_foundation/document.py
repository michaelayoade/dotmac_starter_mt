"""``DeploymentDescriptorDocument.v1`` — the canonical projection of a descriptor.

`dotmac-deployment-control` owns authorization. Its frozen plan snapshot embeds
the desired specification verbatim and hashes it, and approval requires the
approver's evidence to carry that exact digest — so anything inside the
snapshot is digest-covered with no field allow-list to drift.

The gap this closes is one hop earlier. This facility could parse a descriptor
and could render assets, but had no **canonical document** in between: its only
digests were of rendered bytes. There was nothing to put into the desired
specification, so no descriptor fact was inside any plan digest at all.

    document = spec.to_canonical_document()
    document.sha256_digest()     # what Control binds its authorization to
    document.canonical_bytes()   # what that digest is taken over

## What is in, and what is deliberately out

**In:** schema identity, the exact Foundation version, every default fully
materialized, the service roster and every role, exact image references, the
ingress and exposure policy, and the migration, backup, handoff and rollback
requirements.

**Out:** resolved endpoints, IP addresses, credential bindings, secret values.

That exclusion list is the entire point of the split. Control binds the
DESCRIPTOR digest into its independently signed authorization and resolves the
private material separately; if a resolved address could reach the descriptor
digest, the two owners would have collapsed into one. :func:`_refuse_resolved_material`
enforces it over the finished document rather than trusting each builder, so a
future field cannot smuggle an address in.

A material NAME is not a credential binding and stays in: the descriptor holds
names and approved pointers by ADR-0009, `secrets_guard` already refuses values
at parse time, and a document that omitted the names could not describe which
credentials a release needs.

## Why the descriptor half is derived generically

Every field of every descriptor dataclass is normalized by walking
`dataclasses.fields`, not by a hand-written serializer. A hand-written one is a
field allow-list wearing a different hat: the next field somebody adds stays
out of the digest, silently, and the failure is invisible until an unapproved
change ships under an approved digest. The only exclusion is `compare=False`
fields — today just `ProductDeploymentSpec.source`, which is the path of the
machine that happened to read the file.

## The five canonicalization rules

1. **String keys only; values are string, integer, boolean, or lists and
   mappings of those.** A digest must be re-derivable months later by a reader
   who has only the stored JSON.
2. **No floats.** They do not round-trip identically through every JSON
   implementation, so a digest that depends on one sometimes differs from
   itself.
3. **No nulls.** An unset value is materialized as ``{"unset": true}``;
   "absent", "null" and "defaulted" are three states in JSON and must be one
   state here.
4. **Defaults are materialized at normalization.** Digesting the raw TOML would
   let a change to a parser default alter running behaviour under an unchanged
   digest.
5. **The schema string and the exact facility version are inside the
   document.** ``exposure = "public"`` is a word; its meaning is the socket
   this version's renderer emits. Without the version, upgrading the facility
   changes a running exposure while the approved digest stays identical.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any, Final

from . import ingress
from .errors import SpecError
from .spec import SCHEMA, ProductDeploymentSpec
from .version import VERSION

__all__ = [
    "DESCRIPTOR_DOCUMENT_SCHEMA",
    "DeploymentDescriptorDocumentV1",
    "build_canonical_document",
]

DESCRIPTOR_DOCUMENT_SCHEMA: Final = "DeploymentDescriptorDocument.v1"

#: The sentinel for an unset optional. Never `null` — see rule 3.
UNSET: Final[dict[str, bool]] = {"unset": True}


# ── canonicalization ────────────────────────────────────────────────────────


def _canonical(value: Any, *, where: str) -> Any:
    """Refuse anything the digest could not be re-derived from."""
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        return [_canonical(item, where=f"{where}[]") for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SpecError(
                    f"{where}: a non-string key ({key!r}) cannot round-trip "
                    "through JSON, so the digest could not be re-derived",
                )
            out[key] = _canonical(item, where=f"{where}.{key}")
        return out
    if value is None:
        raise SpecError(
            f"{where}: null is refused. An unset value is materialized to "
            f"{UNSET!r}; 'absent', 'null' and 'defaulted' are three states in "
            "JSON and must be one state here",
        )
    if isinstance(value, float):
        raise SpecError(
            f"{where}: a float is refused. It does not round-trip identically "
            "through every JSON implementation, and a digest that depends on "
            "one sometimes differs from itself",
        )
    raise SpecError(f"{where}: {type(value).__name__} is not a canonical value")


def _normalize(value: Any) -> Any:
    """Any descriptor value, as canonical JSON data.

    Walks `dataclasses.fields` rather than naming fields, so a field added to
    the descriptor tomorrow is inside the digest tomorrow. A hand-written
    serializer here would be a field allow-list, and a field allow-list is how
    an unapproved change ships under an approved digest.
    """
    if value is None:
        return dict(UNSET)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _normalize(getattr(value, field.name))
            for field in dataclasses.fields(value)
            # `compare=False` marks a field the descriptor itself does not
            # consider part of its identity. Today that is exactly
            # `ProductDeploymentSpec.source`, the path of whichever machine
            # read the file — digesting it would make the same descriptor
            # produce two digests from two checkouts.
            if field.compare
        }
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    return value


# ── the exclusion list, enforced rather than trusted ────────────────────────


def _walk_strings(value: Any, path: str = "document") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, list):
        return [
            found
            for index, item in enumerate(value)
            for found in _walk_strings(item, f"{path}[{index}]")
        ]
    if isinstance(value, dict):
        return [
            found
            for key, item in value.items()
            for found in _walk_strings(item, f"{path}.{key}")
        ]
    return []


def _refuse_resolved_material(content: dict[str, Any]) -> None:
    """Refuse a document carrying anything the private half owns.

    Enforced over the FINISHED document rather than inside each builder,
    because the risk is a field nobody thought about. Deployment control binds
    this digest into an independently signed authorization and resolves
    addresses and credentials separately; the moment a resolved address can
    reach this digest, the two owners have collapsed into one.
    """
    for path, text in _walk_strings(content):
        # A digest is a `sha256:<hex>` string and an image reference contains
        # one; neither parses as an address, so the address check below is not
        # confused by them. Checked explicitly anyway so the reason a value is
        # allowed is written down rather than accidental.
        if text.startswith("sha256:"):
            continue
        ingress.refuse_address_literal(text, field=path, where="canonical document")


# ── the document ────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class DeploymentDescriptorDocumentV1:
    """A canonical descriptor projection, and the digest taken over it.

    The bytes and the digest belong to the DOCUMENT rather than to the spec or
    to a renderer, so there is exactly one answer to "what was signed". A
    caller that wants the digest cannot reach it without holding the bytes it
    was taken over.
    """

    content: dict[str, Any]

    def canonical_bytes(self) -> bytes:
        """The exact bytes the digest is taken over.

        Sorted keys at every depth, no insignificant whitespace, UTF-8. A
        reader with only these bytes can re-derive the digest.
        """
        return json.dumps(
            self.content,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def sha256_digest(self) -> str:
        """``sha256:<64 hex>`` — the prefixed form, deliberately.

        `dotmac_approvals.validate_digest` requires the prefix and
        `dotmac-deployment-control`'s own plan digest is bare hex today. One of
        them has to normalize; emitting the prefixed form here means the
        facility that PRODUCES the value is the one that states its shape.
        """
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def schema(self) -> str:
        return str(self.content["schema"])

    @property
    def foundation_version(self) -> str:
        return str(self.content["foundation_version"])


def build_canonical_document(
    spec: ProductDeploymentSpec,
    *,
    refuse_resolved_material: bool = True,
) -> DeploymentDescriptorDocumentV1:
    """The canonical document for `spec`.

    Same descriptor in, same bytes out. Nothing here reads a clock, an
    environment variable or a filesystem, so the digest is a property of the
    descriptor and this facility version, and of nothing else.

    ``refuse_resolved_material=False`` is for ONE caller: the compose renderer,
    which stamps this document's digest onto every service it emits as that
    release's configuration identity. The flag does not change a single byte of
    the document — :func:`_refuse_resolved_material` only ever raises — so the
    digest a rendered label carries and the digest Control authorizes are the
    same value, which is the whole reason a controller may compare them with
    ``==``.

    The flag is needed because the refusal is a BOUNDARY check about what may
    leave this facility, not a canonicalization rule. `uvicorn --host 0.0.0.0`
    in a role's command is an in-container bind, not topology in Git, and it
    trips the address check; a descriptor that cannot pass that check is still
    a configuration that has to be identifiable on a running container.
    Refusing to render it would leave the container with NO identity, which is
    strictly worse than the thing the refusal guards against.

    Anything that SENDS a document to deployment control takes the default.
    """
    from .policy import ingress_policy_document

    content: dict[str, Any] = {
        "schema": DESCRIPTOR_DOCUMENT_SCHEMA,
        "descriptor_schema": SCHEMA,
        # Rule 5. Not decoration: it is what makes the word "public" mean one
        # specific rendered socket rather than whatever the installed renderer
        # currently thinks.
        "foundation_version": VERSION,
        "descriptor": _normalize(spec),
        "ingress_policy": ingress_policy_document(spec),
    }
    canonical = _canonical(content, where="document")
    if refuse_resolved_material:
        _refuse_resolved_material(canonical)
    return DeploymentDescriptorDocumentV1(content=canonical)
