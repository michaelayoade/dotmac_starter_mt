# Compatibility — dotmac-config-layers

## 0.1 contract

- Python: `>=3.11,<3.14`.
- Kernel: `>=0.1.0a88` (manifest registration only). Sub's pinned kernel is
  `0.1.0a98`, so adopting this package does not raise Sub's kernel pin.
- Runtime dependencies: the kernel (manifest) and `pydantic >=2.7.4,<3.0`.
- `Effective.schema_version` is 1. Hashes are SHA-256 over canonical JSON of the
  composed state and are stable for identical inputs in any order.
- Dated exceptions lapse the day after `until` and are reported for 7 days
  (`LAPSE_REPORT_DAYS`); the maximum exception life is 180 days and the minimum
  reason length is 10 characters. These are engine constants, not settings.

The package has no persistence plane and composes unchanged in tenant and
platform contexts.

A plug-in version must change when its field specs or module semantics change;
a layer block records the module version it was written against and an
incompatible block is invalid for that module only.

## Notes

- `Effective.router_hash` of the source engine is renamed `subject_hash` before
  the first release: it is a hash over the per-module effective hashes and has no
  router meaning. This is a reviewed delta from the Sub draft (see
  `PORT_MAP.json`); a Sub consumer reads `subject_hash`.
- `compose()` and `exception_state()` take a keyword-only `lapse_report_days`
  defaulting to `LAPSE_REPORT_DAYS` (7), so a consumer may align the lapse
  reporting window with its own posture rules. The default keeps the source
  behaviour.
- Kernel floor `a88` is the floor of the sibling stateless module
  `dotmac-document-rendering` 0.1.0a1 and the first kernel the manifest-only
  probe in `scripts/module_floor_check.sh` is run against here. The package uses
  only `ModuleManifest(code, version, core=False)`.
