"""The reference assembly's ProductAssemblySpec (kernel-boundary Task 3A).

This is the one place the reference application declares what it IS: its feature
modules (`FEATURE_MODULES`, assembly-owned), the presentation packages it
composes, plus the deployment-driven surface switches read from the environment.
`app/main.py` builds `app` from this spec via `dotmac_kernel.create_app`.

**`dotmac-ui` adoption (ADR-0006 U1, and the D5 consumer boundary).** The
assembly — not the kernel — composes the shared design system, because the
dependency direction is `assembly → module → dotmac-ui → dotmac-kernel` and the
kernel may never reach forward to a presentation package. Two lines do it, and
they are the whole integration:

- `packaged_static_dirs` layers `dotmac_ui`'s packaged assets into the existing
  `/static` mount (under any assembly-owned file, over the kernel's), so
  `/static/dotmac-ui/dotmac-ui-1.css` resolves from the installed package
  rather than from a vendored copy;
- `stylesheets` puts one `<link>` in every page's `<head>`, at the URL
  `dotmac_ui.stylesheet_url()` builds, complete with its own content-derived
  cache-busting token.

Note what is NOT here: no Tailwind step, no build, no toolchain agreement
(ADR-0006 D3). This assembly compiles its own CSS with Tailwind v4; the design
system arrives already compiled, and a product on Tailwind v3 — or on no
Tailwind at all — writes these same two lines.

The spec deliberately leaves `assembly_template_dir` / `assembly_static_dir`
unset — the reference app renders the kernel's packaged templates and serves the
kernel's static assets directly, with no override. A downstream product sets
those to layer its own look over the kernel's.
"""

from __future__ import annotations

import dotmac_template_studio
import dotmac_ui
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.config import settings
from dotmac_kernel.features import load_manifests

from app.features import FEATURE_MODULES

assembly = ProductAssemblySpec(
    name="dotmac_starter_mt",
    # The assembly's own features, plus every INSTALLED MODULE it composes.
    #
    # `dotmac_template_studio.module` is a `ModuleManifest`, not a
    # `FeatureManifest`, so it cannot go through `load_manifests` (which
    # isinstance-checks the latter) and must NOT go in `FEATURE_MODULES` —
    # that list is the assembly's own packages, and an architecture test holds
    # it byte-for-byte equal to the features independence contract. The
    # assembly importing a module directly is the legal direction: `assembly →
    # module → dotmac-ui → dotmac-kernel`.
    modules=[*load_manifests(FEATURE_MODULES), dotmac_template_studio.module],
    web_enabled=settings.web_enabled,
    disabled_modules=frozenset(settings.disabled_feature_set),
    packaged_static_dirs=(dotmac_ui.static_dir(),),
    # An installed module's admin screens are package data outside this
    # assembly's template root — see `ProductAssemblySpec.packaged_template_dirs`.
    packaged_template_dirs=(dotmac_template_studio.template_dir(),),
    stylesheets=(dotmac_ui.stylesheet_url(),),
)
