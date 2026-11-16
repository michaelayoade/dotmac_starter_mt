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

## Composition

A consuming control-plane assembly adds the public `versions_dir()` result to
its Alembic `version_locations` and composes `module` in its assembly spec. The
locator resolves the installed wheel's lineage; consumers never derive a path
from `__file__` or import the `migrations` package directly. The module's ORM
relationships are class-bound, so its extraction source may keep same-named
legacy models loaded during the measured shadow phase without making mapper
resolution ambiguous.

## Integration checklist

- Pin `dotmac-entitlement-allocation` in the consuming assembly and register `dotmac_entitlement_allocation.module` there.
- Bind the prerequisites declared by the manifest: `IDEMPOTENCY_LEDGER_V1.name`, `PLATFORM_AUDIT_LOG_V1.name`.
- Add `dotmac_entitlement_allocation.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: the public migration locator resolves the installed lineage](../../tests/architecture/test_entitlement_allocation_module.py#L58). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/architecture/test_entitlement_allocation_module.py::test_the_public_migration_locator_resolves_the_installed_lineage -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
