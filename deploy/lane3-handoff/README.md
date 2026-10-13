# Lane 3 installation inputs

This directory describes the source-only installation bundle. The installer
renders the boot unit from its reviewed Python bytes; no host is changed by
these files. See [the installation guide](../../docs/LANE3_HANDOFF_INSTALLATION.md).

Build the bundle from a clean checkout at an independently reviewed exact
commit using `scripts/lane3_handoff_install.py build`. The output contains
`bundle.json`, `runtime/` and `supplier/`. Review and retain the bundle digest
independently of the transfer path.

Supply a separate protected directory containing exactly `host.json`,
`policy.json`, `admission.json`, `supplier-installation.json`, optionally
`coordinator.json`, and canonical `installation-input.json`:

```json
{
  "schema": "dotmac.lane3.handoff-install-input.v1",
  "baseline_unit": "REPLACE_WITH_ACTUAL_FIREWALL_OWNER.service",
  "admission": {
    "repository": "REPLACE/REPLACE",
    "path": "REPLACE.json",
    "commit": "REPLACE_WITH_REVIEWED_EXACT_COMMIT",
    "sha256": "REPLACE_WITH_RAW_ADMISSION_SHA256"
  },
  "files": {
    "host.json": "REPLACE_WITH_RAW_FILE_SHA256",
    "policy.json": "REPLACE_WITH_RAW_FILE_SHA256",
    "admission.json": "REPLACE_WITH_RAW_ADMISSION_SHA256",
    "supplier-installation.json": "REPLACE_WITH_RAW_FILE_SHA256"
  }
}
```

This example is deliberately invalid until populated and independently
reviewed. Encode the manifest with sorted keys and compact JSON separators
without a trailing newline. Its raw SHA-256 is the independently supplied
configuration pin. Configuration files retain their exact reviewed bytes.
Never add credentials, JIT configuration or `approval.json` to either bundle.
