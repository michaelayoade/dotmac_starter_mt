# Compatibility — dotmac-settings

## 0.1 contract

- Python: `>=3.11,<3.14`.
- Kernel: `>=0.1.0a106` (manifest registration only). Academy pins
  `0.1.0a107`, so adopting this package does not raise Academy's
  kernel pin.
- Runtime dependencies: the kernel (manifest) and `sqlalchemy ^2.0`.
- Storage contract: kernel `domain_settings`, platform scope
  (`tenant_id IS NULL`), one declared domain per store instance.
  Value encoding matches the kernel table: dict/list/tuple values in
  `value_json`, everything else as text (`true`/`false` for booleans).
- Whole-object CAS serializes on a transaction-scoped advisory lock
  keyed `dotmac-settings:<domain>:<key>`; the key format is part of
  the 0.1 contract because mixed-version writers must contend on the
  same lock.

The package has no persistence plane of its own and composes unchanged
in tenant and platform contexts (it operates on the caller's session).

## Notes

- Tenant-scoped domain settings are out of scope for 0.1; the Academy
  source only ever used platform scope. A scoped variant must be a
  reviewed contract change, not a flag.
- The kernel floor is the Learning 0.1.0a6 floor, chosen so the first
  adopter (Academy, kernel a107) never raises its pin. It is not
  floor-probe proven minimal.
