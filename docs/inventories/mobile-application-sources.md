# Native mobile application sources (three apps)

**As of:** 2026-08-26
**Audited revisions** (all three worktrees clean, on `main`, at audit time):

| Repository | Revision audited | Paths audited |
|---|---|---|
| `dotmac_sub` | `1a3edf0eb567fe02665606d368f8f342536f548c` | `mobile/`, `field_mobile/`, `.github/workflows/mobile.yml`, `.github/workflows/mobile-release.yml`, `brand.json` |
| `dotmac_crm` | `a922decf1356f296f1816aba06cf2bcf966fc212` | `mobile/`, `.github/workflows/field-app-ci.yml`, `.github/workflows/field-app-release.yml` |
| `dotmac_starter_mt` | `531f7f8c` | this repository (the inventory's home; it contains no mobile code) |

**This document is NEW.** `dotmac_starter_mt` has never carried a mobile
inventory. Nothing here replaces or corrects an earlier characterization — the
three Flutter applications below have existed for two and a half months with no
cross-repository record of what they are, what they assert, or how far they have
drifted apart. Read it under the same two cautions as every other file in this
directory: facts go stale (re-run the measurements rather than trusting them),
and **an inventory is not a mandate** — ADR-0006 § "The extraction rule" governs
whether anything measured here may be extracted, as amended by § "Decision
amendment — 2026-08-12", which rules that a second consumer is **evidence, not
permission** and that all three dossier states permit a shared module.

Its companion decision is
[`ADR-0065 — Native mobile clients are composed applications`](../adr/0065-mobile-clients-are-composed-applications.md),
which states the contracts. This file states only the facts they rest on.

---

## 1. The three applications, at a glance

| | `dotmac_sub/mobile` | `dotmac_sub/field_mobile` | `dotmac_crm/mobile` |
|---|---|---|---|
| Human name | DotMac self-care | DotMac Field | DotMac Field (fork of record) |
| Dart package | `dotmac_portal` | `dotmac_field` | `dotmac_field` |
| Audience | ISP subscribers and resellers | in-house technicians, vendor crews, field managers | same as centre column |
| Android `applicationId` | `io.dotmac.selfcare` | `io.dotmac.field` | `io.dotmac.field` |
| Android `namespace` | `com.example.dotmac_portal` | `io.dotmac.dotmac_field` | `io.dotmac.dotmac_field` |
| iOS bundle | `io.dotmac.selfcare` | `io.dotmac.field` | `io.dotmac.field` |
| `pubspec` version | `8.14.0+235` (tracks the Sub repo version) | `1.0.1+2` | `1.0.1+2` |
| Flutter pin (`.metadata`) | `924134a44c189315be2148659913dda1671cbe99` (3.44.1) | same revision | same revision |
| Dart SDK constraint | `>=3.4.0 <4.0.0` | `^3.12.1` | `^3.12.1` |
| `lib/` Dart files | 128 | 66 | 69 |
| `lib/` lines | 26,554 | 22,584 | 22,078 |
| — of which generated | 0 | 5,641 (`database.g.dart`) | 4,917 (`database.g.dart`) |
| — hand-written | 26,554 | 16,943 | 17,161 |
| Test files under `test/` | 23 | 23 (22 + 1 helper) | 23 (22 + 1 helper) |
| Test lines | 3,360 | 6,655 | 5,964 |
| **Executable test cases** | **159** | **216** | **192** |
| Other harness files | `test_live/live_backend_test.dart` | `integration_test/screenshots_test.dart`, `test_driver/screenshot_driver.dart` | same two |
| Local persistence | on-disk JSON response cache | **Drift/SQLite, unencrypted** | **Drift/SQLite, unencrypted** |
| Crash telemetry | `sentry` (pure Dart, GlitchTip DSN) | `sentry_flutter` (native) | `sentry_flutter` (native) |
| Owner repository | Sub | Sub | CRM — **retirement target** |

`applicationId` and bundle id are the two identifiers that matter for store
identity: **`io.dotmac.field` is claimed by two source trees in two
repositories.** Only one of them can ever be the artifact a store install
updates.

### Dependency sets

`field_mobile/pubspec.yaml` and `crm/mobile/pubspec.yaml` are **byte-identical**,
as are their `pubspec.lock` files — including the package `name: dotmac_field`
and the version `1.0.1+2`. That is the cleanest single proof that one is a copy
of the other and that neither has been re-declared as an independent artifact.

| Concern | self-care | field (both copies) |
|---|---|---|
| State / DI | `flutter_riverpod ^2.5.1` | `flutter_riverpod ^2.6.1` |
| HTTP | `dio ^5.5.0` | `dio ^5.7.0` |
| Routing | `go_router ^14.2.0` | `go_router ^16.0.0` |
| Secure storage | `flutter_secure_storage ^9.2.2` | `flutter_secure_storage ^9.2.2` |
| Local database | — (none) | `drift ^2.28.0`, `sqlite3 ^2.4.0`, `sqlite3_flutter_libs ^0.5.27` |
| Connectivity | — | `connectivity_plus ^6.1.0` |
| Push | `firebase_core ^3.6.0`, `firebase_messaging ^15.1.3`, `flutter_local_notifications ^17.2.3` | `firebase_core ^3.8.0`, `firebase_messaging ^15.1.5` |
| Telemetry | `sentry ^8.9.0` (pure Dart) | `sentry_flutter ^9.22.0` (native) |
| Maps | `flutter_map ^8.3.0`, `latlong2`, `geolocator ^14.0.2` | `flutter_map ^8.1.0`, `latlong2`, `geolocator ^13.0.1` |
| Media | `image_picker ^1.1.2` | `image_picker ^1.1.2`, `image ^4.2.0` |
| Payments / webview | `webview_flutter ^4.8.0`, `app_links ^6.3.2` | — |
| Biometrics | `local_auth ^2.3.0` | — |
| Charts | `fl_chart ^0.69.0` | — |
| Realtime | `web_socket_channel ^3.0.1` | — |
| Voice | — | `speech_to_text ^7.0.0` |
| Codegen | none (manual JSON) | `drift_dev`, `build_runner` |
| Fonts | system | bundled Outfit + Plus Jakarta Sans |

Eight of the shared dependencies sit at **different major or minor constraints
between the two Sub apps** (`go_router` 14 vs 16, `dio` 5.5 vs 5.7, `geolocator`
13 vs 14, `sentry` 8 pure-Dart vs `sentry_flutter` 9 native, two Firebase
constraints). Any future shared package has to satisfy both ranges
simultaneously; today nothing forces them to agree.

---

## 2. `dotmac_sub/mobile` — customer self-care

**Provenance.** Introduced by `365d99662` (2026-06-08), *"feat(mobile): Flutter
customer self-care app + self-scoped /me API"*. It has been developed
continuously in Sub ever since; the three most recent commits touching it are
automated version bumps (`2370b2b25`, 2026-08-25, "chore: bump version to
8.14.0"). It has no fork anywhere in the fleet.

**Structure** — `lib/main.dart` + `lib/src/` in nine directories:
`config` (1 file), `core` (14), `features` (53, across `auth`, `billing`,
`home`, `profile`, `reseller`, `service`, `settings`, `support`, `usage`),
`models` (29), `providers` (6), `repositories` (16), `router` (1),
`widgets` (6).

**Persistence and identity.**

- Tokens live in `flutter_secure_storage`
  (`lib/src/core/token_storage.dart`), written as **two separate,
  non-atomic writes** (`_kAccess` then `_kRefresh`) with no version marker.
  `clear()` deletes access token, refresh token and the cached profile;
  biometric opt-in, the "prompt seen" flag, theme and the per-install
  `device_id` are deliberately excluded and survive logout.
- `lib/src/core/response_cache.dart` writes **plaintext JSON** for every
  successful authenticated `GET` into the app-support directory, keyed
  `"GET <path>?<sorted query>"` — **the key carries no principal, account or
  tenant component.** The file's own docstring defends this on the grounds that
  "tokens live in the secure store and are never part of a response body"; that
  reasoning does not hold, because the bodies cached are subscription, billing,
  usage and profile responses. Isolation between accounts rests entirely on
  `ResponseCache.clear()` being called on logout, which is a best-effort
  directory walk whose failure is explicitly swallowed
  ("a failed clear is not worth surfacing").
- In-memory Riverpod caches use `cacheFor(ref, ttl)`
  (`lib/src/providers/data_providers.dart`, 20 call sites). The single test
  `cache_for_identity_test.dart` asserts one property — "cacheFor refetches when
  the account id changes" — for the in-memory layer only. Nothing makes the
  same assertion about the on-disk cache.

**Session handling** (`lib/src/core/api_client.dart`). `validateStatus` lets
every status below 500 through to an interceptor, which on a 401 performs a
**single-flight** refresh (`Future<bool>? _refreshing`) and replays the request
once. Impersonation ("view as", reseller) is a separate token override whose 401
clears impersonation instead of refreshing — deliberately, so a reseller's token
is never refreshed into a customer request. A replay that then fails at the
transport layer is rejected with the transport error, not the stale 401. A
stable per-install `X-Device-Id` is attached to every request.

**Push** (`lib/src/core/push_service.dart`). `routeForNotificationData` reads
six payload keys in order — `route`, `path`, `deep_link`, `deeplink`, `link`,
`url` — and `_internalRoute` accepts **any string beginning with `/` verbatim as
an in-app route**. When no such key is present it falls back to substring
matching over the notification title, body, keys and values against hardcoded
word lists (`'chat'`, `'crm'`, `'ticket'`, `'invoice'`, `'usage'`, …) and
returns `/support/chat`, `/support`, `/billing`, `/usage` or
`/dashboard/notifications`. The server therefore supplies raw client navigation
targets, and where it does not, the client guesses from prose.

**What its 159 test cases actually assert.** Predominantly *parsing and
presentation*, not lifecycle:

- `models_test.dart` (51 cases) — JSON parsing for every DTO, and a consistent
  theme of **server-owned presentation**: "parses the server-owned status
  presentation", "uses a neutral compatibility fallback for older servers",
  "authoritative total preserves zero instead of treating it as missing",
  "keeps unavailable values distinct from authoritative zero", plus a cluster of
  prepaid/postpaid expiry rules ("postpaid never expires on
  `next_billing_at` (no false expiry)").
- `biometric_lock_test.dart` (15) — the most behaviourally dense file in the
  app: lock/unlock state machine, "survives a token clear (session-expiry) but
  disable removes it", "bootstrap does not resurrect a session signed out
  mid-flight", "lockOnResume rolls back when biometrics became unavailable".
- `uiux_audit_test.dart` (14) — invoice filters and **push deep-link routing**:
  "honours explicit internal routes from FCM data", "routes generic push
  payloads to the notifications inbox".
- `response_cache_test.dart` (6) — "writes through on a successful GET, then
  serves it on a timeout", "serves stale on a 5xx", "does NOT serve stale on a
  4xx (real answer must surface)". **No case asserts anything about which
  account a cached body belongs to.**
- `api_client_test.dart` (4) — "successful refresh + replay returns the replayed
  response", "refresh failure delivers the 401 and signals session expiry".
- `formatters_test.dart`, `semantic_colors_test.dart`, `theme_controller_test.dart`,
  `connection_status_test.dart`, `fup_card_test.dart`, `widgets_test.dart` —
  formatting, theming and widget rendering.
- `notification_repository_test.dart` (5) — "markRead posts selected IDs to the
  self-scoped owner", "legacy migration clears device IDs only after server
  acceptance".
- `device_command_repository_test.dart` (3) — "Wi-Fi update posts desired fields
  with idempotency evidence".

`test_live/live_backend_test.dart` needs a running backend and credentials and
is deliberately excluded from CI.

**CI.** `.github/workflows/mobile.yml`, job `flutter`, gated by a
`dorny/paths-filter` on `mobile/**`: `flutter pub get`, `dart format` on the
**changed files only**, `flutter analyze`, `flutter test`. A macOS `ios-build`
job compiles the iOS project without code signing and is explicitly advisory —
excluded from the required `Mobile CI` gate.

**Release.** `.github/workflows/mobile-release.yml` builds a signed Android
`appbundle`/`apk` from `ANDROID_KEYSTORE_BASE64` and friends, injecting
`--dart-define-from-file=../brand.json` (which exists at `dotmac_sub/brand.json`)
and a `MOBILE_GLITCHTIP_DSN`. Its `ios-release` job is a deliberate always-fail
gate documenting that Apple signing is not wired. It triggers on `mobile-v*`
tags — **`dotmac_sub` carries no `mobile-v*` tag**, so this workflow has only
ever been reachable by `workflow_dispatch`.

---

## 3. `dotmac_sub/field_mobile` — field operations

**Provenance.** `f07406229` (2026-07-09), *"Import field mobile app into sub"* —
a wholesale copy of `dotmac_crm/mobile` into a new `field_mobile/` directory.
25 commits have touched it since; the most recent is `f4baa8c4a` (2026-08-18).

**Structure** — `lib/main.dart`, `lib/app/`, `lib/core/`
(`api`, `location`, `offline`, `photos`, `push`, `voice`) and `lib/features/`
in twelve directories: `auth`, `execution`, `expenses`, `jobs`, `location`,
`manager`, `materials`, `profile`, `schedule`, `today`, `vendor`, `voice`.

**Persistence.**

- `lib/main.dart:54` — `final db = AppDatabase(NativeDatabase(dbFile));`.
  The Drift database at `<documents>/dotmac_field.sqlite` is opened with the
  plain native factory: **the entire field database is unencrypted at rest**,
  and so are `<documents>/field_photos/` and
  `<documents>/pending_location_pings.json`.
- Schema version 5 (`lib/core/offline/database.dart`). Tables:
  `CachedJobs`, `CachedJobDetails`, `CachedScheduleEntries`, `CachedMapAssets`,
  `CachedMapAssetSyncCursors`, `CachedWorkOrderEvidenceMaps`, `OutboxEntries`,
  `PendingPhotos`, `DraftEntries`.
- `OutboxEntries.clientRef` is unique and doubles as the server-side idempotency
  key (`client_event_id` / `client_ref`); `seq` is an autoincrement that defines
  FIFO flush order; `status` is `pending|sent|conflict`.
- **Exactly one table is partitioned by principal.**
  `CachedWorkOrderEvidenceMaps` has primary key
  `(principalScope, workOrderPublicId, reportSha256)`, and its docstring says
  callers "must never cross principals/jobs or overwrite a hash". The
  `principalScope` value comes from `jwtSubject()` in
  `lib/core/api/api_client.dart`, added alongside it. **Every other cached
  table, every queued mutation, every photo and the location-ping file carry no
  principal component at all.**

**Logout.** `AuthRepository.logout()` is `_store.clear()` — it deletes the token
store entries and nothing else. The Drift database, the photo directory and the
pending-ping file all survive a logout untouched. The only `db.delete` calls in
the app are three domain-scoped ones (schedule refresh, map-asset pruning,
evidence-hash invalidation); none is a logout participant.

**Session handling** (`lib/core/api/api_client.dart`). Different in shape from
self-care's: it decodes the JWT `exp` locally and refreshes **proactively**
before expiry (`_refreshSkew`), retries once on a 401 after refreshing, and
routes refresh through a second `Dio` instance so it cannot recurse through its
own interceptor. It supports two login modes (`staff` / `vendor`) on different
paths. Both transports send `X-Auth-Refresh-In-Body: true`.

**Push** (`lib/core/push/push_source.dart`). `routeForMessage` is a **typed
intent**: it requires `data['work_order_id']` and accepts exactly two `type`
codes, `work_order_assigned` and `work_order_comment`, both resolving to
`/jobs/<id>`. Anything else returns null. This is the shape the self-care app
does not have.

**What its 216 test cases actually assert.** Materially more lifecycle
behaviour than self-care, concentrated in four files:

- `jobs_screens_test.dart` (26) and `map_test.dart` (18) — screen rendering,
  pin construction, coordinate validation ("skips out-of-range cached
  coordinates", "job pin editor falls back when initial coordinates are
  invalid").
- `location_ping_test.dart` (23) — the densest contract file: "shift states use
  the backend presence status contract", "restores the server-owned sharing
  state", "accepts a fully accounted mixed response", "retains the queue when
  response accounting is incomplete", "corrupt queue is discarded with
  payload-free evidence", "file store survives service recreation and clears
  after sync", "restored buffer retains only the newest configured fixes".
- `expenses_test.dart` (20) and `materials_test.dart` (13) — request payloads,
  envelope tolerance ("skips malformed rows instead of crashing"), drafts.
- `execution_test.dart` (18) — the completion gate, and it asserts the gate is
  **server-owned**: "blocks only on server-required photo and sign-off",
  "advisory checklist never creates a client-only completion gate", "disabled
  server evidence policy permits completion without evidence", "fallback does
  not satisfy a contract that disallows it".
- `sync_service_test.dart` (15) — the outbox contract: FIFO, "duplicate enqueue
  with same clientRef is a no-op", "409 conflict parks the entry without
  dropping it", "server error stops the flush and preserves order", "429 honors
  Retry-After", "5xx poison entry parks as conflict after the attempt cap, queue
  drains", "permanently-4xx photo is marked failed and not retried",
  "flushAll uploads photos before outbox mutations".
- `auth_flow_test.dart` (14) — "concurrent refreshes share one in-flight request
  and all get the new token", "proactive refresh fires before expiry without a
  401", **"transient refresh failure preserves the session for retry"**,
  "config gate blocks below min version and merges flags".
- `photo_queue_test.dart` (11) — "native camera capture is bounded to the stored
  photo limit", "lost Android camera result is recovered once for its work
  order", "4xx keeps the file and records the error".
- `work_order_evidence_map_test.dart` (10) — the only principal-isolation
  assertions in the fleet's mobile code: **"offline evidence never crosses
  authenticated principals"**, "a new report hash invalidates the older snapshot
  for that job", "authoritative 4xx conflict never falls back to stale
  evidence", "mismatched work-order identity fails closed and is not cached".
- `app_shell_test.dart` (7), `push_test.dart` (6), `voice_extraction_test.dart`
  (6), `map_assets_repository_test.dart` (6), `schedule_test.dart` (4),
  `profile_test.dart` (4), `status_presentation_test.dart` (4),
  `offline_fallback_test.dart` (4), `vendor_trace_recorder_test.dart` (3),
  `vendor_map_test.dart` (2), `device_location_test.dart` (1),
  `job_location_update_test.dart` (1).

**CI.** The `field-flutter` job in `.github/workflows/mobile.yml`, filtered on
`field_mobile/**` — same four steps as the self-care job. Note that it does
**not** run `dart run build_runner build`, so `database.g.dart` is trusted as
committed; CRM's own workflow does run it (see § 4). No iOS build job covers
`field_mobile` at all: `ios-build` is gated on `needs.changes.outputs.mobile`.

**Release: there is none.** Four independent facts:

1. `mobile-release.yml` contains zero references to `field`; its
   `working-directory` is `mobile`.
2. `field_mobile/docs/RELEASE.md` documents
   `.github/workflows/field-app-release.yml` — **that file does not exist in
   `dotmac_sub`.** It exists in `dotmac_crm`, which is where the doc was copied
   from.
3. `field_mobile/ios/ci_scripts/ci_post_clone.sh` sets
   `MOBILE="$REPO/mobile"` and reads `"$MOBILE/.metadata"`. In CRM that path
   was the field app. In Sub it is the **customer self-care app** — the field
   app's Xcode Cloud post-clone hook installs Flutter for, and builds, the wrong
   application. Its own comment still says "Lives at
   `mobile/ios/ci_scripts/ci_post_clone.sh`".
4. `field_mobile/brand.json` does not exist (only `dotmac_sub/brand.json` does)
   and the post-clone hook passes no `--dart-define-from-file`, so a field build
   would take default theming.

No `field-mobile-v*` or `mobile-v*` tag exists in `dotmac_sub`.

---

## 4. `dotmac_crm/mobile` — the field app's fork of record

**Provenance.** `244f1021` (2026-06-10), *"Scaffold the Flutter field app
(mobile/)"* — the original. Sub's copy was taken from it on 2026-07-09. Its last
commit is `50beb0cb` (2026-08-18), *"fix(mobile): await token handling in auth
flows"*.

**Structure.** Identical layout to `field_mobile`, plus two feature directories
Sub's copy does not have (`customers`, `sales`) and two extra files under
`vendor/`.

**Status: retirement target.** Michael's approved goal
(`crm-web-layer-import-not-adaptable-to-sub`, status `approved`) is to complete
every CRM web capability in Sub and retire `dotmac_crm` entirely. On 2026-08-26
he ruled that CRM mobile follows the repository: it is a retirement target, not
a continuing product. Two consequences are load-bearing for this dossier:

- CRM mobile receives **no Wave-2 security or offline-sync work.** Its
  unencrypted Drift database is not scheduled for SQLCipher.
- CRM mobile **cannot supply adopter evidence** for any shared package. A
  consumer being deleted cannot corroborate a contract or constrain a
  generalisation; evidence must come from another live Dotmac product (see § 7).
  This limits what may be *claimed*, not what may be *built* — see § 5.5.

**CI.** `.github/workflows/field-app-ci.yml` runs on pull requests to `main`
only. Its `mobile` job additionally runs `dart run build_runner build
--delete-conflicting-outputs` before `flutter analyze` / `flutter test` — CRM
regenerates `database.g.dart`, Sub does not. Its `backend` job runs a
`-k "field or push_service or vendor_auth or ..."` slice of the Python suite,
coupling the Dart client's CI to CRM's backend. The Flutter action pins no
version (`channel: stable`), so CRM's Flutter floats while Sub's is pinned to
`3.44.1` in both jobs.

**Release.** `.github/workflows/field-app-release.yml` exists here — signed
Android build on `field-mobile-v*` tags or `workflow_dispatch`, with iOS
delegated to Xcode Cloud. `dotmac_crm` carries no `field-mobile-v*` tag either;
its most recent tags are `v0.27.7`, `v0.22.1`, `v0.12.0`. **Neither field
application has ever been released from a tag.**

---

## 5. The CRM → Sub field migration, measured

### 5.1 Method

```
diff -rq dotmac_crm/mobile/lib dotmac_sub/field_mobile/lib
```
at the audited revisions. Result: **51 files differ, 9 paths exist on only one
side.** Per-file line counts and add/delete counts come from `diff` between the
same pair; classification is by reading each diff.

### 5.2 The 9 one-sided paths

**Only in `dotmac_crm/mobile` — 8 files, 2,968 lines.** All of them are CRM
domain surface that Sub deliberately did not import:

| Path | Files | Lines |
|---|---|---|
| `features/customers/` (`customer_providers`, `customer_lookup_screen`, `customer_models`) | 3 | 587 |
| `features/sales/` (`sales_models`, `sales_screen`, `sales_providers`) | 3 | 1,054 |
| `features/vendor/vendor_providers.dart` | 1 | 390 |
| `features/vendor/vendor_screens.dart` | 1 | 937 |

**Only in `dotmac_sub/field_mobile` — 5 files, 1,400 lines.** All of them are
work done in Sub after the import:

| Path | Lines | What it is |
|---|---|---|
| `app/status_presentation.dart` | 66 | consumes the server-owned status label/icon/tone contract |
| `core/location/location_ping_store.dart` | 118 | durable file-backed queue for unsent location fixes |
| `features/jobs/work_order_evidence_map_models.dart` | 501 | fibre evidence snapshot model |
| `features/jobs/work_order_evidence_map_repository.dart` | 137 | the only principal-scoped cache reader/writer in the app |
| `features/jobs/work_order_evidence_map_screen.dart` | 578 | its screen |

### 5.3 The 51 diverged files, classified

Classification key:

- **contract** — the difference changes a wire contract, a session or
  persistence-identity rule, an idempotency vocabulary, or which side owns a
  decision. Porting a fix across requires understanding both servers.
- **behaviour** — the difference changes what the app does, but within the same
  contract (new screen state, new query, new fallback).
- **cosmetic** — formatter output (the two trees were formatted by different
  `dart format` versions), design-token substitution, comment rewording,
  `const` promotion/demotion. No behavioural consequence.

| # | File | CRM / Sub lines | +/- | Class | What differs |
|---|---|---|---|---|---|
| 1 | `core/api/api_client.dart` | 149 / 192 | +60 −17 | **contract** | Sub adds `jwtSubject()` (principal derivation for cache partitioning) and the `X-Auth-Refresh-In-Body` header on both transports, and **narrows session termination to HTTP 401/403** — CRM calls `onSessionExpired` on *any* `DioException`, so a timeout or 502 signs a technician out |
| 2 | `core/offline/database.dart` | 135 / 157 | +26 −4 | **contract** | schema 4→5; Sub adds `CachedWorkOrderEvidenceMaps` keyed on `(principalScope, workOrderPublicId, reportSha256)`; outbox `kind` vocabulary loses `as_built`; draft `type` vocabulary loses `sales_order` |
| 3 | `core/offline/database.g.dart` | 4,917 / 5,641 | +724 −0 | **contract** | generated consequence of row 2 |
| 4 | `core/offline/sync_service.dart` | 423 / 497 | +85 −11 | **contract** | outbox routing table diverges (`as_built`, `quote_line_item` removed; material/expense repointed to `/submit`); Sub injects `client_ref` into material/expense payloads at flush time; multipart gains an explicit `DioMediaType`; new `offlineRequestHistory`, `pendingPhotosForJob`, `removePendingPhoto`, `isOnline`; error-detail mapping rewritten |
| 5 | `core/offline/draft_store.dart` | 57 / 97 | +41 −1 | **contract** | Sub adds `SavedDraft` + `list(type)` + two providers; `salesOrderDraftId` removed |
| 6 | `core/photos/photo_queue.dart` | 133 / 210 | +93 −16 | **contract** | `ImageSourceAdapter` gains `recoverLost()` (Android process-death camera recovery), native capture bounds, and a `.pending_completion_capture` marker file |
| 7 | `features/execution/completion_state.dart` | 62 / 78 | +42 −26 | **contract** | the completion gate moves from a client-hardcoded rule (`checklistDone && hasPhoto && hasSignOff`) to a server-supplied `JobCompletionRequirements`; CRM still gates on its own advisory checklist |
| 8 | `features/execution/completion_wizard.dart` | 340 / 618 | +370 −92 | **contract** | same shift, plus the `CompletionPhotoGateway` seam |
| 9 | `features/jobs/job_models.dart` | 412 / 518 | +148 −42 | **contract** | `JobCompletionRequirements` + `safeFallback`, migrated nullable classification fields, server status presentation |
| 10 | `features/auth/auth_state.dart` | 161 / 161 | +1 −1 | **contract** | `defaultBaseUrl`: `https://crm.dotmac.io` vs `https://selfcare.dotmac.io` — the two builds talk to different backends |
| 11 | `features/auth/auth_repository.dart` | 141 / 151 | +25 −15 | **contract** | **drift runs both ways here** — see § 5.4 |
| 12 | `features/location/location_ping_service.dart` | 148 / 245 | +123 −26 | **contract** | Sub adds the durable ping store, server-owned sharing restore, and response accounting (partial acceptance retains the queue) |
| 13 | `features/location/location_cadence.dart` | 33 / 42 | +14 −5 | **contract** | shift states move onto the backend presence-status contract |
| 14 | `core/location/device_location.dart` | 107 / 139 | +47 −15 | **contract** | background permission and position-stream semantics |
| 15 | `features/materials/materials_providers.dart` | 193 / 237 | +56 −12 | **contract** | `/api/v1/field/material-requests/submit` vs `/material-requests` |
| 16 | `features/expenses/expenses_providers.dart` | 210 / 247 | +50 −13 | **contract** | `/api/v1/field/expense-requests/submit` vs `/expense-requests` |
| 17 | `features/manager/manager_providers.dart` | 313 / 329 | +18 −2 | **contract** | consumes `status_presentation` from the server |
| 18 | `features/today/map_models.dart` | 150 / 165 | +15 −0 | behaviour | extra pin/search fields |
| 19 | `features/jobs/jobs_providers.dart` | 218 / 243 | +27 −2 | behaviour | detail/notes query shape |
| 20 | `features/jobs/job_detail_screen.dart` | 1,321 / 1,417 | +118 −22 | behaviour | lifecycle context, linked requests, combined history |
| 21 | `features/jobs/job_chat_screen.dart` | 212 / 227 | +17 −2 | behaviour | thread rendering |
| 22 | `features/jobs/widgets/job_card.dart` | 167 / 170 | +6 −3 | behaviour | status stripe from the presentation contract |
| 23 | `features/expenses/expenses_screen.dart` | 1,254 / 1,336 | +136 −54 | behaviour | queued-request labels, offline history |
| 24 | `features/expenses/expense_models.dart` | 212 / 216 | +7 −3 | behaviour | extra ERP fields |
| 25 | `features/materials/materials_screen.dart` | 1,105 / 1,167 | +68 −6 | behaviour | queued-request labels, draft list |
| 26 | `features/materials/material_models.dart` | 299 / 308 | +9 −0 | behaviour | issue-progress fields |
| 27 | `features/today/today_screen.dart` | 787 / 759 | +54 −82 | behaviour | overdue/unscheduled inclusion, Done view restricted to same-day |
| 28 | `features/today/map_screen.dart` | 720 / 739 | +39 −20 | behaviour | network-asset layers replace CRM-asset layers |
| 29 | `features/schedule/schedule_providers.dart` | 76 / 83 | +21 −14 | behaviour | mostly reformatting around one query |
| 30 | `features/manager/manager_screen.dart` | 906 / 902 | +6 −10 | behaviour | dispatch list |
| 31 | `features/location/location_tracking_controller.dart` | 190 / 200 | +10 −0 | behaviour | background-tracking start/stop |
| 32 | `features/vendor/vendor_map_screen.dart` | 132 / 147 | +39 −24 | behaviour | plant search around the crew |
| 33 | `features/vendor/trace_recorder.dart` | 53 / 56 | +10 −7 | behaviour | jitter filter constants |
| 34 | `features/voice/voice_extraction.dart` | 119 / 129 | +18 −8 | behaviour | ASR confidence forwarding |
| 35 | `features/auth/mfa_screen.dart` | 106 / 115 | +13 −4 | behaviour | error copy |
| 36 | `features/execution/signature_pad.dart` | 90 / 97 | +9 −2 | behaviour | clear/redraw |
| 37 | `app/router.dart` | 315 / 333 | +70 −52 | behaviour | CRM routes (`/customers`, `/sales`) absent; evidence-map route added |
| 38 | `app/theme.dart` | 289 / 337 | +97 −49 | behaviour | token set extended (`primaryDeep` and friends) |
| 39 | `main.dart` | 109 / 125 | +22 −6 | behaviour | wires `FileLocationPingStore` and `_QueuedCompletionPhotoGateway` |
| 40 | `app/widgets/status_pill.dart` | 48 / 50 | +16 −14 | behaviour | renders server label/icon/tone |
| 41 | `app/widgets/section_header.dart` | 53 / 57 | +7 −3 | cosmetic | spacing |
| 42 | `app/app.dart` | 46 / 47 | +1 −0 | cosmetic | `restorationScopeId: 'dotmac-field-app'` |
| 43 | `app/widgets/stat_tile.dart` | 89 / 86 | +2 −5 | cosmetic | `const` demotion + formatter |
| 44 | `app/widgets/primary_action_button.dart` | 67 / 67 | +2 −2 | cosmetic | hardcoded `Color(0xFF0891B2)` → `AppColors.primaryDeep` |
| 45 | `features/jobs/location_pin_screen.dart` | 150 / 150 | +1 −1 | cosmetic | `const Icon` → `Icon` |
| 46 | `features/today/asset_pin_screen.dart` | 124 / 124 | +1 −1 | cosmetic | same |
| 47 | `features/auth/login_screen.dart` | 291 / 291 | +3 −3 | cosmetic | formatter |
| 48 | `core/api/token_store.dart` | 99 / 99 | +1 −1 | cosmetic | formatter (constructor initializer indent) |
| 49 | `core/offline/connectivity.dart` | 42 / 43 | +2 −1 | cosmetic | formatter |
| 50 | `core/location/location_source.dart` | 43 / 44 | +3 −2 | cosmetic | formatter + one comment reworded |
| 51 | `core/voice/device_transcription_source.dart` | 83 / 85 | +3 −1 | cosmetic | formatter |

**Totals: 17 contract-affecting, 23 behavioural, 11 cosmetic.**

Read the shape, not just the counts. The cosmetic tail is real — the two trees
were run through different `dart format` versions, which inflates the raw file
count — but it sits almost entirely in `app/widgets/` and leaf adapters. **Every
file in `core/` that carries state or talks to a server is in the contract
column**, and so is every file implementing the completion gate. The divergence
is concentrated exactly where a shared package would have to live.

### 5.4 Drift runs in both directions — the decisive finding

`features/auth/auth_repository.dart` is not a case of Sub moving ahead. CRM's
**most recent mobile commit**, `50beb0cb` (2026-08-18), is a real correctness
fix:

```dart
-      return _handleTokens(response.data as Map, mode);
+      return await _handleTokens(response.data as Map, mode);
```

applied to both `login` and `verifyMfa`. Without the `await`, the returned
future escapes the enclosing `try { … } on DioException` frame, so a failure
raised inside `_handleTokens` — which writes the token pair to secure storage —
is not converted into a `LoginFailure` and surfaces as an unhandled error
instead. **`dotmac_sub/field_mobile` still has the unfixed form** at both call
sites (lines 99 and 118).

A fix landed on the copy that is being retired and never reached the copy that
is being kept, eight days before this audit, in the authentication path. No
process detected it. That is the concrete cost of duplication in this fleet, and
it is the single strongest argument in this dossier for a contract with a named
owner rather than a second copy.

### 5.5 Why this is duplication evidence, not reuse evidence

**First, what this section does not claim.** ADR-0006 § 5 point 1, read as
written, made two independent consumers a prerequisite for sharing at all. **That
reading was amended on 2026-08-12** — the amendment is headed "a second consumer
is evidence, not permission" and rules that "a second consumer proves reuse and
constrains generalisation; it does **not** determine whether a coherent
capability belongs in a module." All three of its dossier states —
`audit-complete`, `adopted`, `reuse-proven` — permit a shared module; the state
records the evidence level, never the placement permission.

So nothing below is an argument that a package is *forbidden* because only one
adopter exists. The consumer count neither grants nor withholds permission. What
the count does determine is **what may be claimed**, and the finding here is
narrower and firmer: these two trees supply *one implementation's* worth of
evidence, not two.

1. **They are not independent, so they cannot corroborate each other.**
   `field_mobile` is a directory copy of `crm/mobile` taken on 2026-07-09. Their
   `pubspec.yaml` and `pubspec.lock` are byte-identical, down to the package name
   and the version string. Michael ruled on 2026-08-26 that Sub's two apps count
   as **one product** for adoption purposes; the CRM/Sub pair is not two products
   either — it is one implementation stored twice. Two copies of one design
   cannot constrain a generalisation, which is the specific work the amendment
   says a second consumer does.
2. **They are not consumers of the same contract.** They point at different
   hosts (row 10), route four outbox kinds differently (rows 2 and 4), disagree
   about who owns the completion gate (rows 7–9), and disagree about whether a
   transport failure ends a session (row 1). Seventeen contract-affecting
   differences is the opposite of one contract with two consumers.
3. **One of them is being deleted.** A consumer scheduled for retirement cannot
   carry an adoption record, because the record stops describing the fleet the
   day the repository goes.

The correct reading of the 51 files is therefore: *this is what happens without a
contract.* It is the input to ADR-0065. Whether a package is built is decided in
ADR-0065 § 9 on grounds of readiness — proposed contracts, unproven boundaries,
missing enforcement, defect remediation first — and **not** on this consumer
count.

---

## 6. Cross-application defects this audit confirmed

Each was verified against the audited revisions. They are recorded here as
facts; ADR-0065 decides what shape the fix takes, and none of them is fixed by
this document.

| # | Application | Defect | Evidence |
|---|---|---|---|
| D1 | field (both copies) | The offline database is **entirely unencrypted**, as are queued photos, signatures and the pending-ping file. A copied `dotmac_field.sqlite` reveals customer names, addresses, coordinates and job history in plaintext. | `field_mobile/lib/main.dart:54` — `AppDatabase(NativeDatabase(dbFile))` |
| D2 | self-care | The on-disk response cache is plaintext JSON with **no principal component in the key**; account isolation depends on a best-effort `clear()` whose failure is swallowed. | `mobile/lib/src/core/response_cache.dart` (`_key`, `clear`) |
| D3 | self-care | Access and refresh tokens are two **separate, non-atomic** secure-storage writes with no version marker; an interrupted save can leave a new access token beside an old refresh token. | `mobile/lib/src/core/token_storage.dart` (`save`) |
| D4 | field (Sub) | **Logout wipes only the token store.** The Drift database, the photo directory and `pending_location_pings.json` survive a sign-out and an account switch. | `field_mobile/lib/features/auth/auth_repository.dart:124` |
| D5 | field (Sub) | Only **one** of nine cached tables is partitioned by principal; the outbox, drafts, cached jobs, schedule, map assets and photos are not. | `field_mobile/lib/core/offline/database.dart` |
| D6 | self-care | Push routing accepts **any raw internal path** supplied in the payload, and otherwise guesses the destination by substring-matching the notification title and body. | `mobile/lib/src/core/push_service.dart` (`routeForNotificationData`, `_internalRoute`) |
| D7 | field (Sub) | The Xcode Cloud post-clone hook builds the **wrong application** — `MOBILE="$REPO/mobile"` is the self-care app in this repository. | `field_mobile/ios/ci_scripts/ci_post_clone.sh` |
| D8 | field (Sub) | `field_mobile/docs/RELEASE.md` documents `.github/workflows/field-app-release.yml`, which exists only in `dotmac_crm`. Sub's field app has analyze/test CI and **no release pipeline at all**. | `field_mobile/docs/RELEASE.md:6`; `dotmac_sub/.github/workflows/` |
| D9 | field (Sub) | `field_mobile/brand.json` does not exist and no `--dart-define-from-file` is passed, so a field build takes default theming. | `dotmac_sub/brand.json` only |
| D10 | field (Sub) | Sub's CI does not run `build_runner`; `database.g.dart` is trusted as committed. CRM's CI regenerates it. A stale generated file passes Sub's gate. | `mobile.yml` `field-flutter` job vs `field-app-ci.yml` `mobile` job |
| D11 | both fields | **Neither field app has ever been released from a tag.** No `field-mobile-v*` tag exists in either repository, and no `mobile-v*` tag exists in `dotmac_sub`. | `git tag --list` in both repositories |
| D12 | field (Sub) | An authentication fix on the retiring copy never reached the surviving copy — see § 5.4. | `dotmac_crm` `50beb0cb` vs `field_mobile/lib/features/auth/auth_repository.dart:99,118` |

---

## 7. Candidate future adopters — and why none is an adopter today

Michael's 2026-08-26 rulings put CRM out of the pool and count Sub's two apps as
one product. **Michael has since ruled on the outcome: stay at "no package"** —
justified in ADR-0065 § 9 on grounds of readiness, not on the count below.
Nothing in this section is an argument that a package is forbidden; ADR-0006's
2026-08-12 amendment settled that a consumer count neither grants nor withholds
permission. This section establishes only **what could eventually be evidenced,
and by whom.**

The pool, measured across the fleet beside this checkout:

| Repository | Dart files | Mobile artefact | Assessment |
|---|---|---|---|
| `dotmac_erp` | 0 | none in-tree, but a checked-in spec: `dotmac_erp/docs/mobile/dotmac-frontline-spec.md` v1.0 (2026-06-10) | **the only candidate with a stated product** |
| `dotmac_vendor_control_plane` | 0 | none | vendor operators are a desktop/control-plane audience; no mobile intent recorded |
| `dotmac_workspace` | 0 | none | an assembly, not an audience |
| `dotmac_cloud` | 0 | none | — |
| `dotmac_academy_app` / `dotmac-academy` | 0 | none | — |
| `dotmac_backoffice` | 0 | none | ADR-0006's README correction records this is not an independently deployed application |
| `dotmac_integrator` | 0 | none | a transport |

### 7.1 ERP "DotMac Frontline" — a credible future candidate, not an adopter

**Michael's ruling, recorded as given:** *a credible future candidate, but zero
Dart means it is not yet an adopter. Its online-only MVP could eventually prove
the session and data-scope slices, but not queued mutation or push.*

Measured against ADR-0006's own definition of a concrete candidate — **"an
assembly that exists and will consume it — the smallest claim that still carries
evidence"** — Frontline fails on the first half:

| Half of the test | Frontline today | Verdict |
|---|---|---|
| *an assembly that **exists*** | `dotmac_erp` contains **zero Dart files** and no `pubspec.yaml`; the consuming assembly for a Flutter package is a Flutter application, and none exists | **fails** |
| *and **will consume it*** | the spec locks scope, personas and backend posture, and its `/me/*` backend shipped with 28 tests | plausible |

So Frontline is **not an adopter, and not yet a concrete candidate either**. A
spec is intent; an assembly is evidence.

What remains true and worth recording, for when it does start:

1. **It is a real product with a written scope, not a hypothetical.** The spec
   names three personas (employee self-service, manager approvals,
   field/warehouse), locks the MVP scope, and records resolved decisions.
2. **It would be genuinely independent.** Different repository, database, domain,
   backend and audience from both Sub apps. It cannot be a copy of either,
   because no Dart exists yet — which is exactly what would let it *constrain a
   generalisation* rather than confirm one.
3. **Greenfield adoption checks a contract rather than retrofitting it.** An
   application not yet written cannot inherit the first implementation's
   accidents by accident.
4. **ERP is the fleet's outlier on every other adoption axis** — no kernel
   adoption, no import boundary, its own licence scheme (ADR-0006's F0 reading
   2). Adopting a *new* contract is easier than retrofitting an old one, but it
   is still ERP.

### 7.2 Evidence is tracked PER CONTRACT SLICE, not per application

This is the mechanism that stops a partial adopter being counted as a whole one,
and Frontline is the case that forces it. Its locked posture is *"lean —
online-only, polling (no push in MVP)"*. Counted per application it would look
like one future adopter of everything. Counted per slice:

| Contract slice (ADR-0065) | Consumers exercising it today | Future candidate | Could Frontline ever evidence it? |
|---|---|---|---|
| `MobileSessionContextV1` | 0 | ERP Frontline | **yes** — its personas need authenticated sessions from day one |
| `MobileDataScopeV1` | 0 (`field_mobile` partitions 1 of 9 tables; the self-care disk cache partitions nothing) | ERP Frontline | **yes** — a role-aware app on shared staff devices |
| `QueuedMutationV1` | 0 on the contract; `field_mobile` has the closest implementation | **none** | **no** — online-only is a locked decision, not a schedule |
| `PushIntentV1` | 0 on the contract; `field_mobile`'s typed `routeForMessage` is the closest shape | **none** | **no** — push is a post-MVP fast-follow, not a commitment |
| Wipe participation | 0 (`field_mobile` logout clears only the token store) | ERP Frontline **only if** it persists anything | undetermined while online-only |
| Auth state machine | 0 on the contract; two Sub apps independently implement single-flight refresh | ERP Frontline | **yes** |

**A slice's evidence level is the number of consumers that exercise that slice** —
never the number of applications that adopted a neighbouring slice, and never a
whole-application claim applied downward. The two slices with the most expensive
semantics, `QueuedMutationV1` and `PushIntentV1`, have **no candidate at all**,
now or in the locked MVP. Any future dossier must carry one row per slice; a
single state for a multi-slice package reports a number that does not exist.

## 8. What a reviewer can decide from this file alone

The package/no-package decision needs four facts, all of which are above and
none of which requires session context, a plan document, or a Knowledge entry:

1. **How much code is actually shared?** 51 files differ and 9 are one-sided
   between the only two trees that could be called duplicates (§ 5.2, § 5.3).
   Seventeen of the 51 differ on contract, not appearance (§ 5.3).
2. **What evidence do the existing trees supply?** One implementation's worth,
   not two. One tree is a July copy of the other, with byte-identical
   `pubspec.yaml` and `pubspec.lock`, and the original is scheduled for deletion
   (§ 4, § 5.5). Two copies of one design cannot constrain a generalisation.
3. **Is duplication actually costing anything?** Yes, measurably: an
   authentication fix committed to the retiring copy on 2026-08-18 has still not
   reached the surviving copy (§ 5.4), and twelve further defects are recorded
   in § 6.
4. **Is there an adopter, per slice?** No. Frontline fails the concrete-candidate
   test today (zero Dart), and even in its locked MVP it could never evidence
   `QueuedMutationV1` or `PushIntentV1` (§ 7.1, § 7.2).

**Note what question is NOT on that list: "may a package be built?"** The
consumer count does not answer it. ADR-0006's 2026-08-12 amendment ruled that a
second consumer is evidence, not permission, and that all three dossier states
permit a shared module. The build/no-build decision is taken in ADR-0065 § 9 on
four readiness grounds — proposed contracts, unproven boundaries, missing
enforcement, defect remediation first — and the answer today is **no package
yet**. This file supplies facts (1)–(4); it does not supply that decision, and a
reviewer should not derive it from the adopter count.

---

## 9. Measurements, reproducible

```sh
# file and line counts
find <app>/lib -name '*.dart' | wc -l
find <app>/lib -name '*.dart' -exec cat {} + | wc -l

# executable test cases (matches ^\s*(test|testWidgets)\( with a quoted name)
grep -cE "^ *(test|testWidgets)\(" <app>/test/*.dart

# divergence
diff -rq dotmac_crm/mobile/lib dotmac_sub/field_mobile/lib

# per-file add/delete used in the § 5.3 table
diff dotmac_crm/mobile/lib/<f> dotmac_sub/field_mobile/lib/<f> | grep -c '^>'
diff dotmac_crm/mobile/lib/<f> dotmac_sub/field_mobile/lib/<f> | grep -c '^<'
```

Case counts include cases inside `group(...)` blocks and exclude `group` names
themselves. `main.dart` is counted in `lib/` totals. Generated files are counted
and then broken out separately, because a `.g.dart` diff is a consequence of a
schema change rather than an independent divergence.
