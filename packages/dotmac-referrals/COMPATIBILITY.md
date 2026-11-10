# Compatibility

Version `0.1.0a1` publishes contract generation 1 and requires kernel
`0.1.0a85` or newer, the first INSTALLABLE release carrying the allocation of `mod_referrals` / `rf`.

The stable edge is the typed command/result surface exported from
`dotmac_referrals`; ORM models are implementation detail. Before 1.0, changing
an enum value, event payload, required command field, table identity or
migration lineage requires a documented pre-release compatibility note.

## Version `0.1.0a2`

Adds `rf_0002_service_location_ref` (nullable `mod_referrals.referrals.service_location_ref`)
and `capture_referral_v2` / `CaptureReferralV2`. Additive: released `rf_0001`
bytes and every V1 signature are unchanged. The location is an opaque token with
a closed character set; coordinate pairs and address text are not admissible
values.
