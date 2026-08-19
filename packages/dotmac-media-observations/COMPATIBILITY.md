# Compatibility

The unreleased `0.1.0a1` candidate defines:

- the dataclass command/read contracts exported by `dotmac_media_observations`;
- the provider-free `NormalizedObservationProducer` conformance seam;
- normalized-observation SPI V1, identified by
  `CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION` and declared by every producer;
- kind-matched normalized analytics payloads and timestamped receipt-provenance
  read contracts;
- the `ModuleManifest` named `module`;
- the `versions_dir()` Alembic locator; and
- tenant schema `mod_mediaobs` with root revision
  `mo_0001_media_observations`.

Pre-1.0 additions may be backward compatible; any change to observation
identity, fingerprint coverage, period semantics, exact value representation,
restatement ordering, table identity, schema, prefix or branch label is a
breaking contract change and requires explicit migration evidence.

The normalized-observation SPI version is separate from Integration's connector
SPI version. A connector must satisfy both independently. Conformance refuses a
missing, malformed or incompatible media SPI version and reports the exact
observation ids, fingerprints and immutable normalized facts used as evidence.

A restatement link retains the same installation, source system and subject:
entity identity, hierarchy child, or exact metric period as appropriate. All
public recording functions enforce that rule, including commands whose
`restates_observation_id` is set directly. Period-metric read windows use aware
`[start,end)` instants and reject naive or reversed bounds.

Metric decimals must be exactly representable by `NUMERIC(38,18)` and integral
values by signed 64-bit storage. Generic normalized entity properties use a
private tagged JSON representation so public reads restore Decimal values without
conflating them with strings; that storage encoding is not a connector contract.

Opaque installation and transport receipt references remain opaque. Provider
names, raw payloads and attribution/customer identity are not compatibility
surfaces because they are outside the package.

Normalized entity properties are aggregate configuration only. Singular and
plural person/audience fields, local Lead/opportunity/Party/customer/subscriber/
Quote/Order identifiers, attribution claims and authoritative revenue labels
are rejected recursively rather than retained as an untyped escape hatch.
