# Compatibility

Version `0.1.0a1` publishes contract generation 1 and requires kernel
`0.1.0a83` or newer, the first release allocating `mod_referrals` / `rf`.

The stable edge is the typed command/result surface exported from
`dotmac_referrals`; ORM models are implementation detail. Before 1.0, changing
an enum value, event payload, required command field, table identity or
migration lineage requires a documented pre-release compatibility note.
