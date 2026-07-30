"""FastAPI app entrypoint for the reference assembly.

Composition moved to the kernel in Task 3A: this module now just builds the
reference `ProductAssemblySpec` (`app/assembly.py`) and hands it to
`dotmac_kernel.create_app`, which does everything that used to live here —
logging, surface globals, lifespan (config validation + feature seeds), the
middleware stack (security headers → observability → tenant resolver →
rate limit → CSRF), error handlers, the `/health` liveness route, the platform
auth surface, the static mount (assembly-over-kernel override), and feature
mounting. See `dotmac_kernel.app_factory.create_app`.
"""

from __future__ import annotations

from dotmac_kernel import create_app

from app.assembly import assembly

app = create_app(assembly)
