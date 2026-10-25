# dotmac-device-cli

Stateless device CLI **knowledge and dialogue logic** for network equipment
(OLTs first, CPE later). It is data, a state machine and parsers. It performs
**no I/O**: it opens no connection, holds no credential, and imports no socket,
TLS, SSH, subprocess, database or HTTP module. The host (Dotmac Sub today,
Integrator later) authenticates, hands over an interactive channel, and
supplies a lock-store adapter. Governance: ADR-0077.

```python
from dotmac_device_cli import CliSession, builtin_registry, list_onts
from dotmac_device_cli.core.dialect import dialect_for

profile = builtin_registry().resolve("huawei", "MA5608T", "V800R013C10")  # configured, never guessed
session = CliSession.attach(channel, profile)   # `channel` is already authenticated
session.prepare()
port = dialect_for(profile.dialect).parse_ref("0/2/1")
result = list_onts(session, port)               # Ok | Unreadable | Unsupported
session.logout()
```

## What a result means

- `Ok(value, evidence)`; `Unreadable(reason, detail, evidence)` with a closed
  `Reason` set; `Unsupported(operation, profile_id, detail)`.
- **Absence is a value.** A port is empty only when the device says so
  (`explicitly_empty=True`). A header with no rows is `truncated`; rows that do
  not parse are `rows_unparsed`. There is no silent empty list.
- **Fail closed.** An operation the resolved profile does not declare is
  `Unsupported`. Nothing falls back to another model's, family's or vendor's
  syntax. Paging-off is `unsupported` until a lab receipt proves a command; the
  pager is answered with a space.
- A read never answers a confirmation. A write answers only the exact question
  its template declares. A session that cannot prove it is idle at a prompt is
  `DIRTY`: closed, never reused, nothing typed.

## Layers

Profiles are layered data under `src/dotmac_device_cli/profiles/`:

```
<vendor>/vendor.toml                       dialect, prompts, pager, logout, limits, markers
<vendor>/<family>/family.toml              argument spelling, send speed, capability facts
<vendor>/<family>/<model>/model.toml       model_patterns and model-specific facts
<vendor>/<family>/<model>/<firmware>.toml  firmware_patterns, verification, operations, quirks
```

An ordered deep merge resolves them (dictionaries merge, lists and scalars are
replaced). A layer sets only what differs. Every layer is checked against the
schema (unknown keys and keys a kind may not set fail) and the merged result is
validated in full. A profile may claim `lab_verified` only with a receipt hash.

## Dialects

A dialect is the part of a vendor that is not data: how a port or ONT is
addressed (`OntRef` keeps the vendor-native string; Huawei is `F/S/P` plus ONT
id, ZTE is `gpon-onu_f/s/p:id`), how each typed operation is composed, and how
replies are parsed. The engine and the operations depend only on the `Dialect`
interface. Shipped: `huawei`, and a `zte` **stub** that declares no operations
and invents no command.

## Adding a model (data only, same vendor)

1. Add `<family>/<model>/model.toml` (`[layer]` id and kind, `model_patterns`,
   anything that differs from the family) and one `<firmware>.toml`
   (`firmware_patterns`, `verification`, the `[operations]` it claims, `[quirks]`).
   A new family adds a `family.toml`. Declare only what you have evidence for.
2. Add synthetic transcripts for **every operation you claim** (below).
3. Run `pytest tests/unit/test_device_cli_conformance.py`. The new profile is
   picked up automatically and must pass.

## Adding a vendor (data plus a dialect)

1. Add `dialects/<vendor>.py` implementing `Dialect` (`format_ref`, `parse_ref`,
   `compose`, `parse_ont_table`, `operations`) and register it in
   `dialects/__init__.py`. Declare in `operations()` only what you compose
   **and** parse; the ZTE stub declares none.
2. Add the `vendor.toml` layer (`dialect = "<vendor>"`, prompts, pager,
   continuation, confirm, logout, limits, error markers, `[detect]`).
3. Add family, model and firmware layers as above, and a
   `tests/transcripts/<vendor>/conformance.toml` with sample prompts and
   addresses (valid and invalid).

## Transcripts and the conformance run

`packages/dotmac-device-cli/tests/transcripts/<vendor>/[<family>/<model>/<firmware>/]<dir>/<case>`
with `<case>.txt` (the device's reply, byte for byte, `{command}` standing for
the echoed command so a shared case holds for every model's spelling),
`<case>.expected.json` (the golden, **written by hand** from the device's own
words, never regenerated from the parser under test) and `<case>.meta.toml`
(`operation`, `command`, `ref`, `covers`, optional `capabilities`, `origin`). A case lives at the
broadest layer where it holds for every descendant and is replayed through each
descendant profile.

`dotmac_device_cli.conformance.conformance_violations(profile, corpus_root)` is
the reusable suite. For every profile it checks prompt and mode detection,
pager and continuation handling, clean logout and refusal of foreign
confirmations, `Unreadable` versus explicitly empty, the `OntRef` round trip,
that every `quirks.*.transcripts` id exists, and that **every claimed operation
has a transcript with a golden**, and so does every `[capabilities]` flag declared
true (a case tags it with `capabilities = ["..."]`; the flag names are a fixed
set and an unknown one is refused). Unproven flags stay false. A profile that claims a capability without a
transcript fails CI.

**Starter is public.** Use synthetic data only: serials `48575443` + `0000NNNN`
(or `HWTC0000NNNN`), descriptions `CUSTOMER_NN_zone_Zone_1_authd_20240101`,
RFC 5737 addresses, MAC `00:00:5E:00:53:NN`, hostname `lab-olt`, user
`operator`. `publication_violations()` fails (it never cleans) on anything else.
Lines carrying a secret parameter are dropped at capture time by parameter name.
Real captures stay in the consuming product's private repository.

## Limiter

`acquire_slot(store_factory, ...)` implements the policy (WRITE > INTERACTIVE >
BACKGROUND, reserved slots, an intent lock so background yields, no
hold-and-wait, fail closed when the store is unreachable) over an abstract
`LockStore`. **`slot_key`/`intent_key` are byte-identical to Sub's
`olt_ssh_limiter` and must stay in sync**: `tests/unit/test_device_cli_limiter.py`
pins known vectors here (Sub pins none; mirror them when Sub adopts). The device
key MUST be `str(olt.id)`. A host may only lower a profile's limits.

## Not in 0.1.0a1

Writes and `WriteGrant`, the sticky login-failure scope, the remaining read
operations (`ont_detail`, `service_ports`, autofind, optical, ...), TextFSM
templates, the transcript synthesiser, the acceptance suite, and any ZTE,
FiberHome or Nokia command. See `CHANGELOG.md` and ADR-0077.
