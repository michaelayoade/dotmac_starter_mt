# Changelog - dotmac-device-cli

## 0.1.0a1 - UNRELEASED

First source slice (ADR-0077). Nothing is published, pinned or adopted.

- Layered capability profiles (vendor, family, model, firmware) as TOML data,
  merged in order and validated in full; unknown keys fail. Shipped: Huawei
  vendor, MA56xx and MA58xx families, MA5608T and MA5800-X2 models, and the
  V800R013 and V100R019 firmware layers. Paging-off is `unsupported` on both.
- Dialect interface (addressing, command composition, parsers) with the Huawei
  dialect and a ZTE stub that declares no capabilities and invents no command.
- Vendor-neutral `OntRef`, typed operations (`version`, `list_onts`,
  `ont_detail`, `service_ports`) and `Ok | Unreadable | Unsupported` results.
  Only `version` and `list_onts` are implemented; the others are `Unsupported`.
- Dialogue state machine over an injected channel: prompt/mode detection,
  pager answering, `<cr>` continuation, confirmation allow-list, clean logout.
- Response classifier and `display ont info <port> all` parser (spaced F/S/P,
  ANSI, pager lines); absence is a value, never a silent empty. A listed ONT
  with no row in a printed description table is `rows_unparsed`, not an
  empty description (a present-but-blank description row stays `Ok`).
- Limiter policy over an abstract `LockStore`; lock keys byte-identical to Sub's.
- Profile registry with `resolve()` by configured vendor/model/firmware and
  `detect()` that reports a mismatch and never switches profile.
- Conformance suite, test doubles, publication guard and a synthetic corpus.
- Public-surface manifest (`SUPPORTED_MODULES`/`INTERNAL_MODULES`), shaped like
  `dotmac-auth-oidc`'s, with an `__all__` on every supported submodule. The
  adapter release smoke imports each supported module from the installed wheel.
