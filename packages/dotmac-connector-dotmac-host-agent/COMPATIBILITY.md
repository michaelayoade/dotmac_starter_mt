# Compatibility

`dotmac-connector-dotmac-host-agent` 0.1.0a1 requires:

- `dotmac-integration >=0.1.0a6` and SPI `>=1.2,<2.0`;
- `dotmac-kernel >=0.1.0a69,<0.2.0`; and
- `dotmac-managed-host-contracts >=0.1.0a1`.

It declares only `ConnectorMode.PROVISION` and exactly the managed-host
deployment-bundle, backup/restore and health-probe `.v1` capabilities. Every
declaration carries its exact contract snapshot and eight operation schemas.

Protocol version 1 is closed by `TARGET_AGENT_API.md`. Adding a route, envelope
field, generic execution shape, evidence field or weaker activation proof is a
new reviewed protocol/contract version, never a runtime setting.

`capability_instance_ref`, command/approval identity and plan hash remain in
Integration's orchestration envelope. Agent endpoint, identity, held secret,
bundle catalogue and backup storage remain installation config; none appears in
an owner operation document.

The connector cannot release until the three first-party dependency floors are
published and verified and the package canaries pass on Observer CI. Target
agent deployment and Seabone acceptance are later adoption gates, not evidence
earned by publishing this client.
