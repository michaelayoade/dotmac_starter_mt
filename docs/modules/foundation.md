# Shared foundations

- Short explanations of package responsibilities, not a claim of production readiness.
- Check the [catalog](../MODULE_CATALOG.md) for installation, release and adoption evidence.
- A **tenant** is a customer workspace; the **platform** is the operator control plane.
- [All module groups](index.md).

## Platform kernel (`dotmac-kernel`)

- **What it does:** Provides shared tenancy, identity, settings, security and application contracts.
- **Use it when:** Building a Dotmac product on the common foundation.
- **Keep in mind:** Business modules still own their domain decisions.
- **Details:** [Package guide](../../packages/dotmac-kernel/README.md) · [Source and contract](../../packages/dotmac-kernel/EXTRACTION.toml).

## Shared UI design system (`dotmac-ui`)

- **What it does:** Provides shared design tokens, styles and published UI components.
- **Use it when:** Giving products a consistent appearance.
- **Keep in mind:** It is not an application or a business workflow engine.
- **Details:** [Package guide](../../packages/dotmac-ui/README.md) · [Source and contract](../../packages/dotmac-ui/EXTRACTION.toml).

## Application synchronization contracts (`dotmac-app-sync`)

- **What it does:** Defines the messages independent Dotmac applications exchange.
- **Use it when:** Synchronizing data between separately deployed products.
- **Keep in mind:** It does not own transport, authentication or the receiving product's decisions.
- **Details:** [Package guide](../../packages/dotmac-app-sync/README.md) · [Source and contract](../../packages/dotmac-app-sync/EXTRACTION.toml).

## External sign-in (OpenID Connect) (`dotmac-auth-oidc`)

- **What it does:** Verifies external identities through OpenID Connect.
- **Use it when:** Connecting an application to a compatible identity provider.
- **Keep in mind:** The product still decides account linking and access rights.
- **Details:** [Package guide](../../packages/dotmac-auth-oidc/README.md) · [Source and contract](../../packages/dotmac-auth-oidc/EXTRACTION.toml).

## Connected application directory (`dotmac-application-directory`)

- **What it does:** Records a tenant's connected applications and how to reach them.
- **Use it when:** Building a workspace that lists a customer's applications.
- **Keep in mind:** A directory entry is not permission to use an application.
- **Details:** [Package guide](../../packages/dotmac-application-directory/README.md) · [Source and contract](../../packages/dotmac-application-directory/EXTRACTION.toml).

## Layered configuration (`dotmac-config-layers`)

- **What it does:** Combines configuration layers and explains where each result came from.
- **Use it when:** Resolving layered configuration with overrides and dated exceptions.
- **Keep in mind:** It does not replace each domain's configuration rules or persist business decisions.
- **Details:** [Package guide](../../packages/dotmac-config-layers/README.md) · [Source and contract](../../packages/dotmac-config-layers/EXTRACTION.toml).

## Device command-line support (`dotmac-device-cli`)

- **What it does:** Provides device command profiles, dialogue rules and response parsers.
- **Use it when:** Building an adapter that interacts safely with supported device command lines.
- **Keep in mind:** It provides no network connection, credentials or device transport.
- **Details:** [Package guide](../../packages/dotmac-device-cli/README.md) · [Source and contract](../../packages/dotmac-device-cli/EXTRACTION.toml).
