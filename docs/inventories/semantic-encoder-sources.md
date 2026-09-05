# Semantic encoder — the inventory behind `greenfield-after-inventory`

Date: 2026-09-02

This inventory is the evidence for `dotmac_kernel.semantic_encoding`'s
classification. That classification is **`greenfield-after-inventory`**, not
`product-first`, and the distinction is the whole reason this file exists:
`greenfield-after-inventory` is a claim about what an inventory FOUND, so the
inventory has to exist, be citable, and be checkable by someone who was not
here.

The finding is a negative: **no Dotmac product contains a qualifying semantic
encoder.** A negative is a stronger claim than "we did not find one", so what
was searched, where, and at which immutable revision are all recorded below —
together with every near-miss and the exact property each one fails on. A
near-miss that fails one property is more informative than silence, and there
are several: two products contain deliberate, well-argued canonical encoders
that get partway and stop.

**What this file is not.** It is repository-local implementation evidence. It
asserts no release, no publication, no pin, no adoption and no production state.
See "The pin question, refused" at the end.

## Why the classification could not be `product-first`

Hard rule 24 requires a product-first extraction to port a **qualifying
production implementation** and its parity tests. The encoder's two visible
implementations — the Orders slice and the Refund Warrants slice — are LOCAL
INCUBATION SLICES. Nothing composes them, nothing runs them, they sit on
branches whose push remote deliberately does not exist, and neither has ever
served a request. An incubation slice cannot be a qualifying product-first
source, however good its code is.

Michael's ruling, 2026-09-02, is therefore the classification recorded
throughout the dossier:

> Treat this as a **greenfield-after-inventory kernel facility informed by two
> independently convergent implementations.**

Read that precisely. The convergent implementations are **evidence that the
design is right** — two teams reaching the same encoding independently is a
strong signal about the design — but they are **not a qualifying source**. The
facility is greenfield; the inventory is what licenses building it; the
convergence is what says the shape is not invented.

## What "qualifying" means

The bar is not "has a digest function". Every Dotmac product has dozens. The
bar is the encoding contract that `AGENTS.md` rule 35 and ADR-0064 § 2 already
state fleet-wide — an encoder qualifies only if it has **all four** of:

| | Property | Rule 35 / ADR-0064 § 2 wording |
| --- | --- | --- |
| **P1** | Length-prefixed, self-describing framing | "length-prefix its fields" — so `("ab","c")` and `("a","bc")` cannot be one value |
| **P2** | A **typed** absence sentinel | "distinguish absence with a typed sentinel" — a magic string is not a type |
| **P3** | Exact money at currency scale | "encode finite money exactly at currency scale" — never a float, never `str()`, never a hardcoded scale |
| **P4** | A domain-separated digest | "namespace the algorithm" — and the namespace must be INSIDE the hash, or it separates nothing |

Two of these are routinely half-met and it matters which half:

- A **length prefix on the value but not the key**, or over one flat level with
  a delimiter-joined collection inside it, is not P1. Framing has to hold
  everywhere a variable-length thing appears.
- An **algorithm label concatenated onto the output hex** (`cv2:<hex>`,
  `sha256:<hex>`, `rv1:<hex>`) is *not* P4. It labels the result; it does not
  separate the digest space. Two encoders that produce the same bytes produce
  the same SHA-256 regardless of what either prints in front of it. P4 requires
  the domain to be hashed.

A fifth, unnumbered requirement decides rule 24 on its own: a qualifying source
must be a **facility** — a reusable encoder with type dispatch, nesting, and
container semantics. A private function over one hardcoded field list of one
row type is not an encoder that could be ported; porting it would move that
product's fact into the kernel, not an encoder.

## Audited revisions

Every finding below was read at an exact commit, never at a branch, and every
commit named here is reachable on that repository's `origin/main`. Sibling
repositories were read through `git grep` / `git show` at those commits; no
sibling working tree was touched or checked out.

| Repository | Immutable revision | Reachability |
| --- | --- | --- |
| `dotmac_starter_mt` | `a203b6e6dc6cecb1821b3a7654db81d71a337943` | `origin/main`, 2026-09-02 |
| `dotmac_sub` | `10a46162c85d9730f22529b34e65bd0626b9c737` | `origin/main`, 2026-09-01 |
| `dotmac_erp` | `4af9ba14ee2d750f63d86ac50f190f47c21f0ca2` | `origin/main`, 2026-09-01 |
| `dotmac_vendor_control_plane` | `88b8d5b1b6324a50275e35acb037eb0f5202448a` | `origin/main` |
| `dotmac_crm` | `a922decf1356f296f1816aba06cf2bcf966fc212` | `origin/main` |
| `dotmac_integrator` | `84c0f020ca63333ab56c39fb96a0acc4630b2e01` | `origin/main` |
| `dotmac_workspace` | `18e86765097191251dc88405b80cdffd27d9cc14` | branch head, read-only |

`dotmac_starter_mt` was audited at `a203b6e6`, which is AHEAD of this branch's
base `a498f9e9`. That is deliberate: the inventory should answer "does a
qualifying encoder exist in the fleet today", and the later revision is the
stronger negative. The starter audit covers `dotmac_kernel` in full —
`fingerprints`, `idempotency`, `capability_contract`, `money` — and **every**
`packages/*` distribution, not a sample.

## What was searched

Run at each revision above, over `*.py`, with the hits reviewed rather than
counted:

- `canonical`, `fingerprint`, `digest`, `checksum` — as names, as `def`s, and
  as filenames via `git ls-tree`
- `hashlib.sha256` / `blake2` / `md5` / `sha1` — **every** call site outside
  tests, so the search is over hashing rather than over naming
- `json.dumps` filtered to `sort_keys`, `separators`, `default=str`
- length-prefix framing: `{len(`, `str(len(`, `struct.pack`, `.to_bytes(`,
  `int.from_bytes`, `}:{`
- typed absence: `ABSENT`, `SENTINEL`, `_MISSING`, `UNSET`, `= object()`,
  `class _Absent`
- exact money: `minor_units`, `quantize(`, `scaleb`, `Decimal`, `to_minor`
- domain separation: `sha256(` immediately followed by a literal, an f-string,
  a `domain` or a `namespace` argument
- the facility's own vocabulary: `cv1`, `cv2`, `encode_fields`,
  `encode_ordered`, `encode_unordered`, `digest_of`, `semantic_encoding`,
  `CANONICAL_ALGORITHM`

## The finding, by candidate

| Candidate (immutable revision) | Searched for | Found | Qualifies? |
| --- | --- | --- | --- |
| `dotmac_starter_mt` @ `a203b6e6` — `dotmac_kernel` incl. `fingerprints`, `idempotency`, `capability_contract`, `money`, and all 80+ `packages/*` | P1–P4 across every hashing call site | Three serious near-misses (`dotmac-tax`, `dotmac-sales`, `dotmac-subscriptions`), plus two length-prefixed LOCK KEYS that compute no digest | **No** |
| `dotmac_sub` @ `10a46162` | P1–P4 across `app/**` and `scripts/**` | A deliberate hand-written canonicaliser that **rejects P2 by design**; the only P1 in the repo is a file-tree digest | **No** |
| `dotmac_erp` @ `4af9ba14` | P1–P4 across 2,908 Python files | The fleet's closest near-miss (`_content_source_version`, production, merged) and a separate P4-only digest — complementary, never combined | **No** |
| `dotmac_vendor_control_plane` @ `88b8d5b1` | P1–P4 | No length-prefixed framing anywhere; digests are sorted-JSON over manifests | **No** |
| `dotmac_crm` @ `a922decf` | P1–P4 | `_canonical_json` only; no framing, no sentinel, no money encoding | **No** |
| `dotmac_integrator` @ `84c0f020` | P1–P4 | `canonical_fingerprint` — a `default=str` JSON dump, deliberately duplicated from its destination | **No** |
| `dotmac_workspace` @ `18e86765` | P1–P4 | Nothing; no canonical encoder or fingerprint helper at all | **No** |

## The near-misses, and the property each one fails

These are the informative results. Each is a real attempt at the same problem
by someone who understood it.

### `dotmac_erp` — `app/services/finance/tax/adoption/inbound.py::_content_source_version`

The closest thing in the fleet, and the only near-miss running in production.
Reachable public entry point `source_fact_content_version`; algorithm constant
`_SOURCE_VERSION_ALGORITHM = "cv2"`; helpers `_exact_money_text`,
`_digest_optional`, `_ABSENT`.

- **P1 — partial.** `f"{key}:{len(value)}:{value}"`, `\n`-joined. The value is
  length-prefixed; the key is not. `len()` counts code points and the result is
  UTF-8-encoded afterwards, so it is self-describing over text, not over bytes.
  Decisively, its one variable-length collection —
  `"\x1f".join(sorted(observed_tax_code_refs))` — is a delimiter join with no
  length prefix at all, so a ref containing `\x1f` aliases a different set.
- **P2 — no.** `_ABSENT = "\x00absent"` is a magic STRING, not a typed
  sentinel. A `str` value that spells those bytes is absence. The rule says
  *typed* precisely because "no real value can spell it" is a convention about
  today's callers, not a property of the encoding. It also does not separate
  "missing" from "null" — the field set is closed, so it cannot.
- **P3 — yes.** `_exact_money_text` refuses `bool`, non-`Decimal` and
  non-finite outright, quantizes to `Decimal(1).scaleb(-currency.minor_units)`,
  and emits `format(canonical, "f")` with `currency_code` and `minor_units` as
  separate digested fields. This is the one property done properly. (Exact
  decimal TEXT at currency scale rather than integer minor units, but that is a
  representation choice within the property, not a failure of it.)
- **P4 — no.** `f"{_SOURCE_VERSION_ALGORITHM}:{digest}"` concatenates `cv2:`
  onto the OUTPUT. The hashed bytes begin `jurisdiction_id:36:…` and carry no
  domain at all. Any other encoder producing the same field payload produces a
  byte-identical SHA-256.
- **Not a facility.** It is one private function taking fifteen named keyword
  arguments of one fact shape, non-recursive, with no type dispatch, no
  container semantics and no public encoder surface. Porting it would give the
  kernel ERP's tax fact, not an encoder.

**This is also the fleet's third independently convergent implementation**, and
it is the strongest evidence in this file that the design is right rather than
invented: written 2026-08-25 in a different repository by a different lane, it
reaches the same six requirements — length prefixing, sorted unordered
collections, exact money at minor units, an absence sentinel, an algorithm
namespace, and a cutover discipline (`cv1` → `cv2`) — from its own problem.
That convergence is why the classification is *greenfield after inventory*
rather than *greenfield*.

### `dotmac_erp` — `app/services/finance/gl/accounting_shadow.py::digest_facts`

Holds the property the one above is missing, and misses the ones it has.

- **P4 — yes, genuinely.** `hasher.update(f"{DIGEST_VERSION}\n{scope.label()}\n".encode())`
  with `DIGEST_VERSION = "erp-gl-posted-ledger.v1"` feeds the domain INTO the
  hash before any fact. This is the fleet's only true domain-separated digest.
- **P3 — partial.** `normalise_amount` quantizes to a fixed `MONEY_SCALE = 6`
  and raises rather than round lossily — never a float — but six places is an
  ERP posting scale, not the currency's minor units, and `currency_code` is
  just another pipe-joined field.
- **P1 — no.** `"|".join(...)`; a `|` inside an account code or journal number
  shifts the field boundary.
- **P2 — no.** No optional fields, no sentinel.

The two ERP near-misses are **complementary**: one has P1+P3, the other has P4.
Neither has P2, and no symbol in the repository has all four. That two separate
lanes each solved half is itself the argument for one owner.

### `dotmac_sub` — `app/migration_source/canonical.py`

The most deliberate canonicaliser in the fleet, and the most informative
near-miss, because it **rejects P2 explicitly** rather than overlooking it. Its
own docstring:

> Null — renders as `null` and is a value. A field that is absent cannot be
> distinguished from one that is null by a consumer, so absence is not allowed:
> every declared field appears in every record.

It refuses `json.dumps` on the ground that a library default changing between
versions would silently change every digest ever computed — exactly the right
instinct — and then:

- **P1 — no**, by a different mechanism. `_quote()` backslash-escapes and
  wraps, so collisions are genuinely prevented, but by escaping rather than by
  framing. A reader cannot find a field's extent without scanning for an
  unescaped delimiter, and there is no `<tag><len>:<body>` anywhere.
- **P2 — no, deliberately.** Absence is forbidden rather than encoded, which
  moves the burden to every caller and out of the encoder. `_render(None)`
  returns `"null"`, so a null is a value.
- **P3 — no.** `canonical_decimal` calls `.normalize()` and formats, which
  **destroys scale**: `Decimal("1.10")` and `Decimal("1.1")` are one value. No
  currency parameter, no minor units.
- **P4 — no.** `canonical_digest` is a bare `sha256` over the form. Domain-ish
  separation exists only by caller convention (`schema_version` /
  `entity_type` placed in the payload by `digest.py` and `snapshot.py`), which
  the encoder does not enforce.

Its sibling `snapshot.py` demonstrates the failure P2 exists to prevent:
`_blob()` returns `(None, None)` for an absent blob, which renders identically
to a present blob with null keys.

### `dotmac_sub` — `scripts/release_backup_policy.py::describe_migration_tree`

The **only** genuine length-prefixed framing in the repository —
`digest.update(len(relative).to_bytes(8, "big"))` before every variable-length
field, key and value both. Correct P1, and nothing else: it walks a file tree,
so there are no fields, no optionality, no money, and the hash is seeded with
nothing.

### `dotmac_sub` — `app/services/billing/rating.py::rating_input_fingerprint`

The money-carrying candidate. Never a float and never a bare `str()`, but
`_decimal_text` calls `.normalize()`, so `Decimal("10.00")` and `Decimal("10")`
fingerprint identically, and the `currency` field sitting beside the amount is
never used to select a scale. Fails P3 on the load-bearing half, plus P1, P2
and P4.

### `dotmac_starter_mt` — `packages/dotmac-tax/src/dotmac_tax/service.py::_result_content_fingerprint`

The starter's closest, and the fleet's only correct P1.

- **P1 — yes, fully.** Both key and value are BYTE-length-prefixed:
  `str(len(key_bytes))` + `b":"` + key bytes, then the same for the value.
  Injective at every field boundary.
- **P2 — no.** Absence is `b"\x00"`, a single NUL byte. A value whose UTF-8 is
  `b"\x00"` is absence, and missing and null are one state.
- **P3 — no.** `_fixed_decimal(value, 6, key)` — a hardcoded six places (eight
  for rates), not the currency's minor units. `currency_code` and `minor_units`
  are digested as separate fields rather than selecting the scale.
- **P4 — no.** `f"rv1:{...hexdigest()}"` labels the output; the hash input
  carries no domain.
- **Not a facility.** A private function over one ORM row type with a hardcoded
  field list; non-recursive, no type dispatch, no container semantics.

### `dotmac_starter_mt` — `packages/dotmac-sales/src/dotmac_sales/contracts.py::canonical_digest`

The starter's only domain-separated digest:
`hashlib.sha256(domain.encode() + b"\x00" + encoded).hexdigest()` — **P4, and
correctly inside the hash.** Everything else is the anti-pattern: `_json_value`
maps `UUID | Decimal | datetime` through `str`, so `Decimal("1.0")` and the
string `"1.0"` are one value (fails P3 in the exact way the facility exists to
fix); it is a sorted-JSON dump (fails P1); and `None` becomes JSON `null` with
no sentinel (fails P2).

### `dotmac_starter_mt` — `packages/dotmac-subscriptions/src/dotmac_subscriptions/values.py`

Has the best money vocabulary in the fleet — `canonical_decimal` refusing
floats and non-finite values, `ExactAmount` enforcing a declared scale, a
NUMERIC(20,6) precision bound — and then digests through `_digest`, a plain
sorted-JSON dump. So: P3 in the value types, none of P1, P2, or P4 in the
encoding. `occurrence_idempotency_key` returns
`f"subscriptions:occurrence:{_digest(payload)}"` — the namespace is outside the
hash, which is the P4 failure named above. This is the near-miss both
incubation slices already cite as the reason they encoded rather than dumped.

### `dotmac_starter_mt` — the two length-prefixed lock keys

`dotmac-durable-timers`' `_identity_key` and `dotmac-collections`' `_lock_key`
both length-prefix, and the timers one reasons about it explicitly ("A
delimiter alone would let a value containing that delimiter alias a different
tuple"). Neither is an encoder: both build a string for
`pg_advisory_xact_lock(hashtextextended(...))`, over open strings only, with no
types, no money, no absence and no cryptographic digest. Recorded because a
future audit will find them under a P1 search and should not have to
re-investigate.

### `dotmac_starter_mt` — `packages/dotmac-deployment-foundation/.../document.py`

The fleet's one **typed-adjacent** absence handling: `UNSET = {"unset": True}`,
with `null` REFUSED at canonicalization because "'absent', 'null' and
'defaulted' are three states in JSON and must be one state here". Correct
instinct, and still not P2 — the sentinel is a dict literal a payload could
contain, not a type. Floats are refused; `Decimal` is refused too, so there is
no P3 at all; the encoding is sorted JSON (no P1); and `sha256_digest` prefixes
`sha256:` onto the output (no P4).

### `dotmac_starter_mt` — `dotmac_kernel.fingerprints.fingerprint_of`

The kernel's existing digest, and the one the facility sits BESIDE rather than
replaces. `json.dumps(sort_keys=True, separators=(",",":"), default=str)`:
none of P1–P4, by design. Its docstring says so — "anything else
non-serializable falls back to `str`" — because it answers "is this the same
payload?" for `execute_once`, whose stored fingerprints in
`idempotency_records` make its bytes a compatibility contract in their own
right. It is listed here as the inventory's baseline, not as a near-miss: it is
not trying to be this.

### Other products

- `dotmac_integrator` — `product_port.py::canonical_fingerprint` is a
  `default=str` sorted-JSON dump, deliberately duplicated byte-for-byte from
  its destination under ADR-0024. None of P1–P4.
- `dotmac_crm` — `meta_webhooks.py::_canonical_json` only. None of P1–P4.
- `dotmac_vendor_control_plane` — manifest and plan digests over sorted JSON;
  no length-prefixed framing anywhere in the repository.
- `dotmac_workspace` — no canonical encoder or fingerprint helper at all.

## What the inventory establishes

1. **No qualifying encoder exists in any Dotmac product.** No symbol in any
   audited repository has all four properties. In `dotmac_sub` no symbol has
   more than two, and never P1+P2 or P3+P4 together. In `dotmac_erp` the two
   strongest candidates are complementary and neither has P2. In
   `dotmac_starter_mt` the only correct P1 and the only correct P4 live in
   different packages.
2. **P2 is the property nothing has.** Not one audited implementation has a
   typed absence sentinel. Two use a magic string, one uses a NUL byte, one
   uses a dict literal, and `dotmac_sub`'s most careful canonicaliser rejects
   the idea outright and forbids absence instead. That is the clearest single
   statement that this facility was missing rather than duplicated.
3. **The requirement was already fleet law, and nothing implemented it.**
   ADR-0064 (accepted 2026-08-25, fleet-wide) and `AGENTS.md` rule 35 state all
   six encoding requirements as a standard. The inventory found the standard
   written down and no reusable implementation of it anywhere — only bespoke
   per-fact digests that each satisfy a subset. A missing owner for a rule the
   fleet already declared is the textbook case for a kernel facility.
4. **The design is convergent, not invented.** Three implementations reached
   the same encoding independently of the kernel: ERP's tax adapter (2026-08-25,
   production, merged), and the Orders incubation slice (whose copy the Refund
   Warrants slice then carried — byte-identical AST
   `c56642b3163de6941f6e707896e7e232a7b09a7768b727c99c75d4c724772aa6`, so the
   two slices are one implementation and one copy, not two independent ones).
   Convergence is evidence the design is right. It is **not** a qualifying
   source, and none of the three is recorded as one.

## The pin question, refused

Nothing in this file is a pin, and no pin is recorded anywhere in the dossier
for this facility.

- **No `source_revisions` entry names an incubation head.** Both consumer
  branches have a push remote that deliberately does not exist, so neither head
  is a coordinate any oracle can confirm — the claim `AGENTS.md` rule 26 and
  Governance ADR 0013 forbid.
- **No `pinned_at`, and no invented version floor.** The kernel version a
  consumer would need does not exist on any index. Naming one would assert a
  release no oracle can confirm.
- **The inventory evidence above IS citable**, and that is the distinction to
  keep visible: every revision in the audited-revisions table is reachable on
  its repository's `origin/main` and re-readable by anyone. The incubation
  branches are not. An inventory can be pinned; an unpushed branch cannot.
