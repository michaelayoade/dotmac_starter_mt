# Compatibility

`dotmac-connector-freeradius 0.1.0a1` implements dotmac-integration SPI
`>=1.7,<2.0` under connector key `freeradius`, in PROVISION and POLL modes.

| capability | modes | claimed contract digest |
|---|---|---|
| `network.access_projection.v1` | PROVISION | `8dce9d5c…a17f` |
| `network.access_observation.v1` | POLL | `4b1ef5ff…c6ed` |
| `network.session_disconnect.v1` | PROVISION | `e683ccdc…c6ed` |

(Full digests in `plugin.py`; Sub's
`app/services/integrations/network_capability_contracts.py` is their source.)

It declares two secret bindings (`freeradius_dsn` required,
`secret_fetch_token` optional), exact egress hosts `freeradius-db` and
`sub` (deployment service aliases, not an activation), and an SPI 1.7 fleet
ceiling of UDP 3799 and UDP 1700. Disconnect endpoints outside the
installation's bound network targets are refused before any packet exists.

The public Python surface is `MANIFEST`, `PLUGIN`, `FreeRadiusConnector`,
`FreeRadiusProvisioningHandler`, `FreeRadiusPollHandler`,
`FreeRadiusPollError`, `render_projection`, `rows_match`, `RenderConfig`,
`RenderRefused`, capability/connector constants and `__version__`.
