# Compatibility

## `0.1.0a1`

- Python: `>=3.11,<3.14`
- `dotmac-kernel`: `>=0.1.0a85`
- persistence plane: tenant only
- schema: `mod_analytics`
- migration lineage: prefix `ay`, branch `analytics`

The package imports no assembly, sibling module or provider. Adopters compose
the manifest and lineage locally, declare their metric vocabulary, and provide
typed aggregate commands at the application boundary.
