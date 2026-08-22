# dotmac-connector-mono compatibility

`0.1.0a1` targets `dotmac-integration` SPI `>=1.3,<2.0` and requires
`dotmac-integration >=0.1.0a11`, the first release with an executable POLL
engine. The provider surface is Mono Financial Data v2 at the exact declared
host `api.withmono.com`; there is no v1 or alternate-host fallback.
