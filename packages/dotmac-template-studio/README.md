# dotmac-template-studio

Tenant-authored **notification and document templates** for DotMac products —
and the reference for what an installable, stateful DotMac module looks like.

ADR-0006 names this the first stateful module: *"dotmac-template-studio a MODULE
— the first stateful one, not a kernel facility."* It owns a database namespace
and a migration lineage of its own, is optional, and is installed by an assembly
rather than imported by the kernel.

## What it does

- **Templates** are addressed by a stable `(kind, slug)` within a tenant, where
  `kind` is `notification` or `document`. One table typed by kind, because the
  two share every structural concern and differ only in which optional content
  fields they use.
- **Versions** are immutable revisions. Publishing points the template at one of
  them; the superseded revision is kept, which is the whole point of a studio.
  A published revision cannot be edited — "what was sent" must not change after
  the fact.
- **Rendering** substitutes `{{ variable }}` placeholders in the PUBLISHED
  revision. Deliberately **not** a Jinja environment: a tenant-authored body is
  untrusted input, and handing it to a template engine would give an operator
  arbitrary expression evaluation inside the server process.

## Installing it in an assembly

Three edits, and no more:

```python
# app/assembly.py
import dotmac_template_studio

assembly = ProductAssemblySpec(
    modules=[*load_manifests(FEATURE_MODULES), dotmac_template_studio.module],
    packaged_template_dirs=(dotmac_template_studio.template_dir(),),
    ...
)
```

```ini
; alembic.ini — append the module's lineage
version_locations = ... .../dotmac_template_studio/migrations/versions
```

plus the dependency itself. The module's namespace allocation
(`mod_tstudio`, prefix `ts`) is **not** one of the edits: it ships in the kernel's
`MIGRATION_OWNER_LEDGER`, which is what makes "one module, one immutable schema"
enforceable across repositories rather than a convention each product re-states.

## Its public surface

`module`, `service`, `template_dir()`, `migrations_dir()`, `__version__`.

Do **not** import `dotmac_template_studio.models` and query it. The tables are an
implementation detail behind `service`; reaching past it re-creates the
parallel-writer problem the source-of-truth standard exists to prevent.

## Two things worth knowing if you are writing the next module

1. **Its admin screens style themselves with `var(--dmui-*)` tokens, not utility
   classes.** A consuming assembly's Tailwind build cannot see inside an
   installed package, so any utility class used only in a module's templates is
   purged from the compiled CSS — the screen renders unstyled in production while
   looking fine in a dev build that scanned more. Tokens are a published
   contract; the host's utility classes are its private build output.
2. **It is held to the assembly's governance, not a weaker one.** The
   thin-wrapper, route-guard, web-import, template-convention, timestamp-filter
   and service-typing checks all walk this package. A module is the
   less-reviewed code of the two; exempting it would put the weaker standard
   exactly where the stronger one is needed.

## Versioning

`dotmac-template-studio`'s version is its own. It is independent of both the
kernel's release version and `KERNEL_MODULE_CONTRACT_VERSION` — the manifest
generation it is built against, which is what actually gates loading.
