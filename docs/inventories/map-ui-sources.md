# Reusable map UI source audit

**As of:** 2026-08-18

**Starter:** `4b285cb3b0da82b9a2a3d5f39f4aca7da19105ff`

**Sub:** `510b80ca7fab4f54a57f261872f94b5e972c8eb6`

**CRM:** `60daaa2dd305696636632f48505ab784110a55d2`

**ERP:** `dd6416cd981ffdf48564e2770b87d3cd7201186c`

This audit answers one narrow question: what map presentation can enter
`dotmac-ui` without moving product data, decisions or provider integration into
the design system? It is characterization evidence for ADR-0006, not evidence
that any product has adopted the resulting contract.

## Sources examined

| Product | Production surface | Relevant behavior |
|---|---|---|
| Sub | `templates/admin/dispatch/live_map.html` | responsive map card, live technician markers, search, plant layers, product popups and 30-second refresh |
| Sub | `templates/admin/dispatch/movement_playback.html` | route polyline, start/end/head markers, scrubber and distance calculation |
| CRM | `templates/admin/operations/field-live-map.html` | live markers and trails plus an explicit map-unavailable notice and list fallback |
| CRM | `templates/admin/operations/field-movement-playback.html` | movement playback with loading, empty and failed-load copy |
| ERP | `templates/people/hr/geofence_editor.html` | draggable centre, radius, polygon drawing/editing and point-in-boundary testing |

Sub is the primary product-first source. Its two dispatch surfaces use the same
outer shape: a bounded, rounded map canvas in product-owned page chrome. CRM is
a related fork and therefore requirement evidence, not an independent reuse
consumer; its useful delta is explicit degraded/list presentation when tiles or
the runtime are unavailable. ERP is an independent, materially different map
use case. Its geofence editor proves that the portable unit cannot assume a
tracker, read-only markers, polling or a sidebar.

The focused source proofs are limited. Sub's
`tests/test_admin_maps_web.py` compiles the map templates and protects route and
typed-query seams; `tests/architecture/test_field_live_map_boundary.py` protects
the live-map projection owner. CRM's `tests/test_field_live_map_feed.py` and
`tests/test_field_recent_tracks.py` protect the feed and playback data paths.
ERP has no focused browser or template test for the geofence editor at this
revision. That absence is why no Leaflet/drawing behavior is claimed as a
portable tested contract.

## Boundary decision

The shared unit is `map_frame`, an accessible presentation frame with:

- a host-named canvas region;
- generic `ready`, `loading`, `empty` and `error` presentation states;
- caller-supplied accessible label and state copy;
- a polite live region; and
- one role-named minimum-size token that the composing surface can override.

`dotmac-ui` owns that markup, state presentation, token-native CSS and its WCAG
contrast checks. The host owns the transition between states; changing the
modifier class, `aria-busy`, visible copy and live-region copy is one host
operation. The package intentionally ships no controller because no common
provider-independent controller exists in the audited products.

## Explicitly excluded seams

The following stay in each product or its integration adapter:

- map provider/runtime and optional drawing plugin;
- tile source, attribution, credentials, CSP and offline-cache policy;
- initial centre, bounds, zoom and all geographic defaults;
- endpoints, request payloads, polling cadence, retry and freshness policy;
- marker, trail, polygon, circle, popup and search-result rendering;
- entity identities and vocabularies such as technician, vehicle, subscriber,
  work order, shift, fleet, plant and geofence;
- authorization, privacy/consent decisions and location retention; and
- non-map fallback lists, filters, legends, playback controls and editors.

This is the hardcoding boundary. The shared template contains no coordinates,
URLs, provider name, endpoint, timer, location schema, product noun, inline
palette or fixed product height. The default minimum block size is the
role-named `--dmui-map-frame-min-block-size` token (`24rem`), so a product can
override it in its own scoped surface without copying the component CSS.

## Evidence state and adoption gate

The contract is **audit-complete, not adopted**. Candidate consumers are Sub's
dispatch live map/playback and ERP's geofence editor. CRM remains useful fork
evidence but does not count as an independent consumer. Per the owner's
direction, this change does not modify or pin any product.

The first adoption must replace one product's outer map frame and local sizing,
while leaving its provider runtime and domain behavior local. It must add a
product-owned test that resolves the package template through the real loader,
asserts the declared `MAP_FRAME` signature, and rejects a vendored copy. A
second independent adoption must do the same before this contract can be called
`reuse-proven`. Only then may the superseded local frame CSS be retired in both
products.
