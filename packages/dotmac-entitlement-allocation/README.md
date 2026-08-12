# dotmac-entitlement-allocation

**What a contract entitles — frozen, and proven against the product that
declares it.**

An `Allocation` is an immutable projection of an activated contract version,
staged once per `(contract_ref, content_hash)`. Ruling C4 keeps it distinct from
a grant: the control plane **allocates**, and the product data plane is the only
writer of its own `tenant_entitlement_grants`.

## Usage

```python
from dotmac_entitlement_allocation import (
    ContractEntitlement, ContractSnapshot, stage_allocation,
)

view = stage_allocation(
    db,
    ContractSnapshot(
        contract_ref=contract_id,
        product_code="dotmac-sub",
        customer_ref="acme-isp",
        content_hash=activation.content_hash,
        source_event_id=event.id,
        entries=(
            ContractEntitlement("billing.invoicing"),
            ContractEntitlement("network.provisioning", quantity=25),
        ),
    ),
    catalogues=my_catalogue_reader,
)
```

## The module validates; you supply the authority

`CapabilityCatalogueReader` is a one-method port you implement over whatever
holds the truth for the named product:

```python
class VendorCatalogue:
    def require_declared(self, *, product_code: str, capability_code: str) -> None:
        catalogue_for(product_code).require(capability_code)   # raises
```

The module then performs the check. That preserves the invariant across every
adapter — HTTP route, outbox consumer, CLI backfill — instead of once per
adapter, where the newest one is always the one that forgot. There is no
`validated=True`: an optional invariant is a comment.

**Do not wrap `active_capabilities()`.** It describes the modules installed in
*your* process, not the ones declared by the target application. Validating
against it checks the wrong product's manifest, and it will look correct in
every test where the two sets happen to overlap.

## Rules worth knowing

- **Atomic rejection.** One undeclared code refuses the whole snapshot. There is
  no state in which some entries were allocated.
- **Fail closed on an unknown product.** That is not an empty catalogue; it is a
  caller who cannot prove anything.
- **Replay does not re-validate.** An allocation already staged is history. A
  capability retired afterwards must not make a delivered entitlement
  unreplayable.
- **`product_code` is persisted**, and licence issuance must read it via
  `allocation_product()` rather than accepting a fresh value — otherwise an
  allocation validated against product A can be issued as a licence for B.

An upstream offer or contract service enforcing the same rule at its own
boundary is not a second authority, provided both consult the same
manifest-derived catalogue rather than implementing separate legality rules.

## Not here

Delivery and acknowledgement state — that is licence issuance, a different
owner. An allocation tracking its own delivery would become a second delivery
authority.

## Where it may be installed

Vendor and OEM control-plane assemblies only.
