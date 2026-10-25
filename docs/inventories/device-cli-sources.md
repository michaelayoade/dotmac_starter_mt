# Device CLI sources (dotmac-device-cli)

Inventory evidence for `packages/dotmac-device-cli/EXTRACTION.toml` (AGENTS rule
24, ADR-0077). Read-only inventory of Dotmac Sub at release
`7999c3019198ad2ce870e2645da65d73bd22ed6e` (the production release deployed on
2026-10-25). Nothing here is imported from Sub; device knowledge and one hash
derivation were copied once, and the repository is public, so no real data was.

## Qualifying source: Sub

| Capability | Sub source | Disposition |
|---|---|---|
| Prompt, pager, continuation and confirmation dialogue | `app/services/network/olt_ssh.py` (`_read_until_prompt`, `read_until_prompt_strict`, `_derive_prompt_regex`, `_run_readonly_display_cmd`, `_logout_session`) | Ported behind an injected channel; tested with scripted channels |
| Session modes | `app/services/network/olt_ssh_session.py` | Folded into one state machine |
| Limiter policy and lock keys | `app/services/network/olt_ssh_limiter.py` | Policy ported over an abstract store; key derivation copied byte for byte; SQL, settings and the engine stay in Sub |
| Response classifier | `app/services/network/huawei_cli_response.py` | Reduced to profile-declared markers evaluated in a fixed priority order |
| `display ont info ... all` parsing | `app/services/network/olt_ssh_ont/status.py`, `parsers/cli.py`, `parsers/loader.py`, `parsers/templates/huawei/display_ont_info.textfsm` | Ported without TextFSM; the "observed 0" mechanism (empty output parsed as success) is deliberately not ported |
| Model facts | `app/services/adapters/olt_types/huawei.py` | Capability facts moved to profile layers |

Sub tests that become parity tests at the Sub swap: `tests/test_olt_ssh_prompt_paging.py`,
`tests/test_olt_ssh_limiter.py`, `tests/test_huawei_cli_response.py`,
`tests/test_olt_parsers.py`.

## Other products

- ERP: `dotmac_erp` at `bcc32dbc179f989d44f791aaff87b3ba45a09e3e` was searched
  (`git grep`, case-insensitive: paramiko, netmiko, huawei, olt_ssh, `display
  ont`, telnetlib) in `app/` and `tests/` and returned no match, so the dossier
  records `no_writer`. That is a statement about this search only; nothing else
  in ERP was read.
- Integrator: ADR-0038 section 6 places OLT vendor protocol I/O there. It has
  no OLT dialogue code to extract and is a later host, not a source.

## What was verified and what was not

- Verified: Sub paths above exist at the pinned revision; the lock-key
  vectors in `tests/unit/test_device_cli_limiter.py` were computed with Sub's
  `_signed_key` source at that revision.
- Not verified: any behaviour against a real device. Profiles are
  `prod_observed` (MA5608T V800R013) or `unverified` (MA5800-X2 V100R019); none
  is `lab_verified`. The MA5800 per-port `all` command form and the `display
  version` field layout used by `detect()` are inferences recorded in the
  profile layers. The MA5800 paging-off evidence conflicts, so paging-off is
  `unsupported` on both models until a lab receipt exists.
- The `display ont info ... all` shape is two tables: data rows, then a
  "F/S/P ONT-ID Description" table (pager mid-table, long descriptions wrapped onto
  indented continuation lines), then the "total of ONTs are: N" trailer. The
  parser joins descriptions by ONT id and treats the trailer as a completeness
  check. How a wrapped description is joined (no separator) is an inference.
- The corpus is synthetic and hand-authored; byte layout (CRLF, cursor-back
  sequences, padded F/S/P) is reproduced from the layouts the Sub fixtures
  exercise, with none of their content.
