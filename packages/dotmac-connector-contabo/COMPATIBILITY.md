# Compatibility

`dotmac-connector-contabo` 0.1.0a1 requires:

- `dotmac-integration >=0.1.0a6` and SPI `>=1.2,<2.0`;
- `dotmac-kernel >=0.1.0a69,<0.2.0`;
- `dotmac-managed-infrastructure-contracts >=0.1.0a1`; and
- `dotmac-domains-contracts >=0.1.0a1` for exact, named activation-gap pins.

The manifest declares `ConnectorMode.PROVISION` and exactly
`infrastructure.firewall.lifecycle.v1`. The provider subset is inbound accept
rules using `tcp`, `udp`, or `icmp`; every unsupported owner-valid rule shape
fails closed. The exact instance, network, volume and DNS contracts are not
declared and each has a stable `CapabilityActivationGate`.

`capability_instance_ref` remains in Integration's orchestration envelope. It
does not enter the owner operation schemas or the Contabo request document.

The release remains blocked until all four first-party prerequisites are
published and verified. Checkout resolution is diagnostic evidence only.
