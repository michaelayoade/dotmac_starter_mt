# Features in the reference app

- These are the nine application-owned features declared in `FEATURE_MODULES`.
- They live under `app/features/`; they are not nine separately published distributions.
- Their routes depend on the assembly selection and web/feature switches.
- See [feature selection](../guides/build-a-product.md) before changing startup options.
- [Back to all modules](index.md).

## tenants

- **What it does:** Creates and looks up tenant workspaces; provisioning also creates an owner account.
- **Use it when:** A platform administrator needs to create a customer workspace.
- **Keep in mind:** Platform administrators and tenant owners are separate identities.
- **Details:** [Feature source](../../app/features/tenants/) · [Manifest](../../app/features/tenants/feature.py).

## auth

- **What it does:** Provides tenant sign-in, bearer-token authentication and browser login/logout.
- **Use it when:** Users need to authenticate to their tenant's API or admin portal.
- **Keep in mind:** Signing in does not automatically grant every permission or capability.
- **Details:** [Feature source](../../app/features/auth/) · [Manifest](../../app/features/auth/feature.py).

## parties

- **What it does:** Creates and manages people and organization identities.
- **Use it when:** The app needs a shared identity record for a person or organization.
- **Keep in mind:** A Party is not automatically an employee, commercial customer or authenticated user.
- **Details:** [Feature source](../../app/features/parties/) · [Manifest](../../app/features/parties/feature.py).

## rbac

- **What it does:** Exposes role, role-grant and audit-log administration.
- **Use it when:** Administrators need to manage who may perform declared actions.
- **Keep in mind:** A role is distinct from a feature flag or a tenant's commercial entitlement.
- **Details:** [Feature source](../../app/features/rbac/) · [Manifest](../../app/features/rbac/feature.py).

## settings

- **What it does:** Exposes tenant setting administration and declares the reference app's settings.
- **Use it when:** Administrators need to view or change supported business settings.
- **Keep in mind:** The shared resolver lives in the kernel; editing environment variables does not overwrite existing setting rows.
- **Details:** [Feature source](../../app/features/settings/) · [Manifest](../../app/features/settings/feature.py).

## presentation

- **What it does:** Serves generated tenant branding styles for browser surfaces.
- **Use it when:** The login and authenticated portal need consistent tenant colors.
- **Keep in mind:** It projects resolved brand data; it does not author templates or accept arbitrary tenant CSS.
- **Details:** [Feature source](../../app/features/presentation/) · [Manifest](../../app/features/presentation/feature.py).

## custom_fields

- **What it does:** Defines extra fields and validates values on registered entity types.
- **Use it when:** A tenant needs an additional field without changing the database schema.
- **Keep in mind:** Entities must be registered; supported field rules still apply to every write.
- **Details:** [Feature source](../../app/features/custom_fields/) · [Manifest](../../app/features/custom_fields/feature.py).

## licensing

- **What it does:** Receives and verifies signed license and revocation information for this application.
- **Use it when:** A deployment needs to apply supported commercial software-use grants.
- **Keep in mind:** This is the receiver, not the separate vendor license-issuing package or a repository copyright license.
- **Details:** [Feature source](../../app/features/licensing/) · [Manifest](../../app/features/licensing/feature.py).

## web

- **What it does:** Provides the tenant admin dashboard landing page.
- **Use it when:** Staff need a browser entry point to the reference app.
- **Keep in mind:** Disabling this feature removes its dashboard, not every other feature's admin page or login.
- **Details:** [Feature source](../../app/features/web/) · [Manifest](../../app/features/web/feature.py).
