# Compatibility

The unreleased `0.1.0a1` candidate defines:

- the dataclass command/read contracts exported by `dotmac_media_observations`;
- the provider-free `NormalizedObservationProducer` conformance seam;
- the `ModuleManifest` named `module`;
- the `versions_dir()` Alembic locator; and
- tenant schema `mod_mediaobs` with root revision
  `mo_0001_media_observations`.

Pre-1.0 additions may be backward compatible; any change to observation
identity, fingerprint coverage, period semantics, exact value representation,
restatement ordering, table identity, schema, prefix or branch label is a
breaking contract change and requires explicit migration evidence.

Opaque installation and transport receipt references remain opaque. Provider
names, raw payloads and attribution/customer identity are not compatibility
surfaces because they are outside the package.
