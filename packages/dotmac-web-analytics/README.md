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

See `COMPATIBILITY.md`, ADR-0055 and
`docs/inventories/web-analytics-sources.md` in the Starter repository.

## Native computation candidate

The unreleased `0.2.0a1` candidate includes a private Rust `_native` extension behind the existing public Python API. Authorization, tenant isolation, transactions and persistence retain their current owners. Installing a matching wheel needs no compiler. Building from source requires the exact Rust and build-tool pins in the packaged `build.py`, plus a C linker; the source archive includes locked Cargo inputs. No Python computation fallback is installed. Hosted qualification and publication remain separate from this source change.
