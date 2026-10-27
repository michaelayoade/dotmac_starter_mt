"""POLL: radpostauth and radacct rows as access observations.

One handler, two streams, one cursor: canonical JSON
`{"acct_id":N,"postauth_id":N}`, both fields or neither (neither on first
poll). The cursor advances over every row READ — including rows skipped as
unusable (no username, Accounting-On/Off) — because the engine persists only
the cursor this handler returns; stalling on a junk row would stall forever.
Skipped rows emit nothing.

`subject_ref` carries the RADIUS login: it is the only subject identity the
wire has. Translating login -> service-instance subject is the receiving
composition's job (the credential owner knows the binding); inventing a
service ref here would be exactly the scope conflation this fleet forbids.

Fingerprints are canonical digests over the source row's identity fields,
stable across re-polls, so the receiver's (source_ref, fingerprint) dedupe
holds. No secret is in any fingerprint material.
"""

from __future__ import annotations

import json

from dotmac_integration.spi import InboundEvent, canonical_digest

from dotmac_connector_freeradius.config import target_config
from dotmac_connector_freeradius.ports import RadiusDbPort
from dotmac_connector_freeradius.provisioning import DbFactory

_STATUS_KIND = {
    "start": "start",
    "interim-update": "interim",
    "stop": "stop",
}


class FreeRadiusPollError(RuntimeError):
    """The poll could not be served honestly (bad cursor, bad row shape)."""


def _decode_cursor(cursor: str | None) -> tuple[int, int]:
    if cursor is None:
        return 0, 0
    try:
        decoded = json.loads(cursor)
    except (TypeError, ValueError) as exc:
        raise FreeRadiusPollError("poll cursor is not canonical JSON") from exc
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"acct_id", "postauth_id"}
        or not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in decoded.values()
        )
    ):
        raise FreeRadiusPollError(
            "poll cursor must carry exactly postauth_id and acct_id"
        )
    return int(decoded["postauth_id"]), int(decoded["acct_id"])


def _encode_cursor(postauth_id: int, acct_id: int) -> str:
    return json.dumps(
        {"acct_id": acct_id, "postauth_id": postauth_id},
        sort_keys=True,
        separators=(",", ":"),
    )


class FreeRadiusPollHandler:
    def __init__(self, *, db_factory: DbFactory) -> None:
        self._db_factory = db_factory

    def poll(
        self,
        cursor: str | None,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> tuple[tuple[InboundEvent, ...], str | None]:
        settings = target_config(config)
        postauth_after, acct_after = _decode_cursor(cursor)
        db: RadiusDbPort = self._db_factory(config, secrets)

        events: list[InboundEvent] = []
        postauth_rows = db.poll_postauth(
            after_id=postauth_after, limit=settings.poll_limit
        )
        for row in postauth_rows:
            postauth_after = max(postauth_after, row.row_id)
            if not row.username:
                continue
            outcome = "accepted" if "accept" in row.reply.lower() else "rejected"
            payload: dict[str, object] = {
                "kind": "authentication",
                "subject_ref": row.username,
                "nas_ref": row.nas_address or "unknown",
                "session_ref": None,
                "outcome": outcome,
                "event_kind": None,
                "reason_code": None if outcome == "accepted" else "access_reject",
                "input_octets": None,
                "output_octets": None,
                "session_seconds": None,
                "source_ref": f"radpostauth:{row.row_id}",
                "observed_at": row.authdate.isoformat(),
                "fingerprint": canonical_digest(
                    {
                        "source": "radpostauth",
                        "row_id": row.row_id,
                        "username": row.username,
                        "reply": row.reply,
                        "authdate": row.authdate.isoformat(),
                    }
                ),
            }
            events.append(
                InboundEvent(
                    provider_event_id=f"radpostauth:{row.row_id}",
                    event_type="authentication",
                    payload=payload,
                )
            )

        acct_rows = db.poll_accounting(after_id=acct_after, limit=settings.poll_limit)
        for acct_row in acct_rows:
            acct_after = max(acct_after, acct_row.row_id)
            kind = _STATUS_KIND.get(acct_row.status_type.strip().lower())
            if kind is None or not acct_row.username or not acct_row.session_id:
                continue
            payload = {
                "kind": "accounting",
                "subject_ref": acct_row.username,
                "nas_ref": acct_row.nas_address or "unknown",
                "session_ref": acct_row.session_id,
                "outcome": None,
                "event_kind": kind,
                "reason_code": None,
                "input_octets": acct_row.input_octets,
                "output_octets": acct_row.output_octets,
                "session_seconds": acct_row.session_seconds,
                "source_ref": f"radacct:{acct_row.row_id}",
                "observed_at": acct_row.event_at.isoformat(),
                "fingerprint": canonical_digest(
                    {
                        "source": "radacct",
                        "row_id": acct_row.row_id,
                        "username": acct_row.username,
                        "session_id": acct_row.session_id,
                        "status_type": acct_row.status_type,
                        "event_at": acct_row.event_at.isoformat(),
                    }
                ),
            }
            events.append(
                InboundEvent(
                    provider_event_id=f"radacct:{acct_row.row_id}",
                    event_type="accounting",
                    payload=payload,
                )
            )
        return tuple(events), _encode_cursor(postauth_after, acct_after)


__all__ = ["FreeRadiusPollError", "FreeRadiusPollHandler"]
