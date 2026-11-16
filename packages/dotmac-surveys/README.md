# dotmac-surveys

`dotmac-surveys` owns reusable tenant feedback mechanics: typed survey
definitions, lifecycle, invitations, response evidence, rating/NPS validation,
and rebuildable aggregates.

It deliberately does not own the ticket, work order, service, conversation or
employee state that makes feedback eligible. Adopters pass opaque recipient,
source-event and subject references after their local owner decides eligibility.
Delivery remains in the adopter's outbox/Integrator path, and business
consequences remain with the subject owner.

The first package version is tenant-only and owns three tables in
`mod_surveys`. Services mutate and flush inside the caller's transaction; they
never commit, roll back or perform network I/O. See
[`ADR-0053`](../../docs/adr/0053-surveys-own-feedback-evidence-not-subject-consequences.md)
and the [product-first inventory](../../docs/inventories/surveys-sources.md).

## Native computation candidate

The unreleased `0.2.0a1` candidate batches answer validation and rating/NPS
cohort arithmetic in a private Rust extension. Python retains Unicode and value
conversion/comparison semantics, ordered Decimal signal replay, and persistence
and lifecycle decisions. Source builds require Rust 1.90.0, a C compiler and
make. The source archive embeds the canonical arithmetic crate, checksum-pinned
libmpdec source and BSD license. Hosted qualification and publication remain
separate.

## Integration checklist

- Pin `dotmac-surveys` in the consuming assembly and register `dotmac_surveys.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_surveys.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: numeric tokens must be canonical ascii integers](../../tests/unit/test_surveys_native.py#L24). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_surveys_native.py::test_numeric_tokens_must_be_canonical_ascii_integers -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
