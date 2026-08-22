# dotmac-connector-meta-social

Ingress-only Meta Social plugin for the independently deployed Dotmac
Integrator. It verifies the Meta subscription challenge and exact request
bytes, then normalizes Facebook Messenger, Instagram DM, Facebook comment and
Instagram comment events into `messaging.receive.v1` observations.

The package owns no database, product decision, retry policy, destination,
provider schedule, or outbound Graph call. Runtime configuration binds three
logical secret names declared by the manifest. Values are materialized by the
Integrator and are never persisted by this package.
