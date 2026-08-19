# dotmac-web-analytics

Privacy-first, first-party website measurement for Dotmac applications.

The package owns append-only observations, property-scoped pseudonymous
visitor/session evidence, filter decisions, deterministic projections,
retention/privacy deletion, rebuild and drift repair. It does not own websites,
forms, campaigns, provider analytics, customer identity, attribution, revenue,
consent policy, connector transport or dashboard presentation.

Every adopter installs its own tenant-plane copy and supplies explicit
property, origin, event-registry, privacy, classifier, sessionisation and
retention policy. There are no adopter hostnames or event vocabularies in the
package.

See `COMPATIBILITY.md`, ADR-0035 and
`docs/inventories/web-analytics-sources.md` in the Starter repository.
