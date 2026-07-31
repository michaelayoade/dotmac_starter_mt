# Vendor Control Plane — Domain Foundation Design (Lane B) — MOVED

> **➡️ MOVED (2026-07-31) — versioned pointer only.**
>
> Ruling **C5** placed the vendor control plane in its own repository. The full
> vendor-control-plane domain-foundation design that used to live here now has
> its authoritative home in that repo:
>
> - **Repo:** `michaelayoade/dotmac_vendor_control_plane`
> - **Design (source of truth):** `docs/design/domain-foundation.md`
> - **As-built + boundaries:** `docs/ARCHITECTURE.md`, `docs/adr/` (ADR-0001
>   foundation, ADR-0002 accounts platform-scoped)
>
> This stub is retained only so existing links resolve and the move is on the
> record. **Do not re-add design content here** — the vendor repo is the one
> owner (source-of-truth standard: one named owner per decision). Kernel
> contracts this design depends on remain owned by `dotmac_starter_mt`
> (`docs/superpowers/2026-07-18-kernel-boundary.md` and the workstreams); the
> vendor design references them by name across the repo boundary.

## Why this moved

A design document that governs another repository's code, kept in this
assembly, is a second authority waiting to drift from the code it describes.
Now that `dotmac_vendor_control_plane` exists and is building against the
pinned kernel, its design belongs beside its code. See
`docs/adr/0003-unified-deployment-profiles.md` for the deployment-profiles
program this was Lane B of, which remains a starter-owned kernel concern.
