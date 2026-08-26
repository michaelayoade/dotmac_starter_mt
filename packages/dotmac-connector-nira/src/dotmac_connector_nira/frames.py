"""EPP command frames: typed input in, registry XML out. No decisions.

Each function turns already-decided command parameters into the exact XML the
CoCCA registry expects. A builder never asks whether the command is a good
idea, never sets a price, never chooses a name — the owning application
decided all of that and passed the result down as a ``DispatchRequest``
payload. These are string templates with correct namespaces and escaping,
nothing more.

Namespaces follow the CoCCA reference module (``cocca-whmcs-v9``): domain,
host and contact objects at their RFC URIs, plus the fee-1.0 extension used by
availability checks. DNSSEC is not emitted by this version.

``clTRID`` (client transaction id) is stamped by the caller from the engine's
idempotency key, so an operator can correlate an ambiguous command with the
registry. It is evidence, not a provider idempotency guarantee; post-send
transport failures are therefore reconciled rather than blindly retried.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from xml.sax.saxutils import escape, quoteattr  # nosec B406 - output escaping only

__all__ = [
    "login",
    "logout",
    "domain_check",
    "domain_info",
    "domain_create",
    "domain_renew",
    "domain_update_ns",
    "domain_transfer",
    "host_create",
    "host_check",
    "contact_check",
    "poll_request",
    "poll_ack",
    "FEE_EXTENSION_URI",
]

_DOMAIN = "urn:ietf:params:xml:ns:domain-1.0"
_HOST = "urn:ietf:params:xml:ns:host-1.0"
_CONTACT = "urn:ietf:params:xml:ns:contact-1.0"
FEE_EXTENSION_URI = "urn:ietf:params:xml:ns:epp:fee-1.0"
_FEE = FEE_EXTENSION_URI
_EPP = "urn:ietf:params:xml:ns:epp-1.0"

_PROLOG = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'


def _e(value: str) -> str:
    """Escape a value for XML text content."""
    return escape(value)


def _a(value: str) -> str:
    """Quote and escape one complete XML attribute value."""
    return quoteattr(value)


def _envelope(inner: str, cltrid: str) -> str:
    return (
        f"{_PROLOG}\n"
        f'<epp xmlns="{_EPP}">\n'
        f"  <command>\n{inner}\n"
        f"    <clTRID>{_e(cltrid)}</clTRID>\n"
        f"  </command>\n"
        f"</epp>"
    )


def login(clid: str, pw: str, *, cltrid: str, new_pw: str | None = None) -> str:
    """RFC 5730 login. Declares the object services the session will use.

    ``pw`` is a materialized secret; it appears only in the frame this builds
    and is never logged by the caller (the plugin redacts frames before the
    engine records them).
    """
    chg = f"      <newPW>{_e(new_pw)}</newPW>\n" if new_pw else ""
    inner = (
        "    <login>\n"
        f"      <clID>{_e(clid)}</clID>\n"
        f"      <pw>{_e(pw)}</pw>\n"
        f"{chg}"
        "      <options><version>1.0</version><lang>en</lang></options>\n"
        "      <svcs>\n"
        f"        <objURI>{_DOMAIN}</objURI>\n"
        f"        <objURI>{_HOST}</objURI>\n"
        f"        <objURI>{_CONTACT}</objURI>\n"
        "        <svcExtension>\n"
        f"          <extURI>{FEE_EXTENSION_URI}</extURI>\n"
        "        </svcExtension>\n"
        "      </svcs>\n"
        "    </login>"
    )
    return _envelope(inner, cltrid)


def logout(*, cltrid: str) -> str:
    return _envelope("    <logout/>", cltrid)


def domain_check(
    names: Iterable[str], *, cltrid: str, currency: str | None = None
) -> str:
    """<domain:check> for one or more names, optionally with the fee extension.

    NiRA's notice makes this the mandated path for availability, replacing
    WHOIS/RDAP scraping. The fee extension asks the registry for the create
    price so the availability answer carries cost — the owning application
    applies any markup, never this builder.
    """
    name_xml = "\n".join(f"      <domain:name>{_e(n)}</domain:name>" for n in names)
    check = (
        "    <check>\n"
        f'      <domain:check xmlns:domain="{_DOMAIN}">\n'
        f"{name_xml}\n"
        "      </domain:check>\n"
        "    </check>"
    )
    if currency:
        fee = (
            "    <extension>\n"
            f'      <fee:check xmlns:fee="{_FEE}">\n'
            f"        <fee:currency>{_e(currency)}</fee:currency>\n"
            '        <fee:command name="create">'
            '<fee:period unit="y">1</fee:period></fee:command>\n'
            "      </fee:check>\n"
            "    </extension>"
        )
        return _envelope(f"{check}\n{fee}", cltrid)
    return _envelope(check, cltrid)


def domain_info(name: str, *, cltrid: str) -> str:
    inner = (
        "    <info>\n"
        f'      <domain:info xmlns:domain="{_DOMAIN}">\n'
        f'        <domain:name hosts="all">{_e(name)}</domain:name>\n'
        "      </domain:info>\n"
        "    </info>"
    )
    return _envelope(inner, cltrid)


def domain_create(
    name: str,
    *,
    period_years: int,
    registrant: str,
    nameservers: Iterable[str],
    auth_pw: str,
    cltrid: str,
    contacts: Mapping[str, str] | None = None,
) -> str:
    """<domain:create>. Every value is caller-decided; the builder only frames.

    ``registrant`` and ``contacts`` are registry contact ids the owning
    application already provisioned; ``auth_pw`` is the authInfo the caller
    generated. Nameservers are host objects (glue), referenced by name.
    """
    ns_xml = "\n".join(
        f"        <domain:hostObj>{_e(h)}</domain:hostObj>" for h in nameservers
    )
    ns_block = (
        f"        <domain:ns>\n{ns_xml}\n        </domain:ns>\n" if ns_xml else ""
    )
    contact_xml = "\n".join(
        f"        <domain:contact type={_a(t)}>{_e(cid)}</domain:contact>"
        for t, cid in (contacts or {}).items()
    )
    contact_block = f"{contact_xml}\n" if contact_xml else ""
    inner = (
        "    <create>\n"
        f'      <domain:create xmlns:domain="{_DOMAIN}">\n'
        f"        <domain:name>{_e(name)}</domain:name>\n"
        f'        <domain:period unit="y">{int(period_years)}</domain:period>\n'
        f"{ns_block}"
        f"        <domain:registrant>{_e(registrant)}</domain:registrant>\n"
        f"{contact_block}"
        "        <domain:authInfo>\n"
        f"          <domain:pw>{_e(auth_pw)}</domain:pw>\n"
        "        </domain:authInfo>\n"
        "      </domain:create>\n"
        "    </create>"
    )
    return _envelope(inner, cltrid)


def domain_renew(
    name: str, *, current_expiry: str, period_years: int, cltrid: str
) -> str:
    """<domain:renew>. ``current_expiry`` is the registry's own exDate (YYYY-MM-DD).

    RFC 5731 requires the caller assert the current expiry so a renew is
    idempotent against the registry's view — the owning application reads it
    from a prior ``domain:info``, never a local guess.
    """
    inner = (
        "    <renew>\n"
        f'      <domain:renew xmlns:domain="{_DOMAIN}">\n'
        f"        <domain:name>{_e(name)}</domain:name>\n"
        f"        <domain:curExpDate>{_e(current_expiry)}</domain:curExpDate>\n"
        f'        <domain:period unit="y">{int(period_years)}</domain:period>\n'
        "      </domain:renew>\n"
        "    </renew>"
    )
    return _envelope(inner, cltrid)


def domain_update_ns(
    name: str,
    *,
    add: Iterable[str] = (),
    remove: Iterable[str] = (),
    cltrid: str,
) -> str:
    """<domain:update> adding/removing nameserver host objects."""
    add_xml = "\n".join(
        f"          <domain:hostObj>{_e(h)}</domain:hostObj>" for h in add
    )
    rem_xml = "\n".join(
        f"          <domain:hostObj>{_e(h)}</domain:hostObj>" for h in remove
    )
    add_block = (
        f"        <domain:add>\n          <domain:ns>\n{add_xml}\n"
        f"          </domain:ns>\n        </domain:add>\n"
        if add_xml
        else ""
    )
    rem_block = (
        f"        <domain:rem>\n          <domain:ns>\n{rem_xml}\n"
        f"          </domain:ns>\n        </domain:rem>\n"
        if rem_xml
        else ""
    )
    inner = (
        "    <update>\n"
        f'      <domain:update xmlns:domain="{_DOMAIN}">\n'
        f"        <domain:name>{_e(name)}</domain:name>\n"
        f"{add_block}{rem_block}"
        "      </domain:update>\n"
        "    </update>"
    )
    return _envelope(inner, cltrid)


def domain_transfer(
    name: str, *, op: str, auth_pw: str, cltrid: str, period_years: int | None = None
) -> str:
    """<domain:transfer> op=request|approve|reject|cancel|query.

    ``op`` is caller-supplied and validated by the delivery handler against the
    capability's allowed set — the builder frames whatever op it is given.
    """
    period = (
        f'        <domain:period unit="y">{int(period_years)}</domain:period>\n'
        if period_years
        else ""
    )
    inner = (
        f"    <transfer op={_a(op)}>\n"
        f'      <domain:transfer xmlns:domain="{_DOMAIN}">\n'
        f"        <domain:name>{_e(name)}</domain:name>\n"
        f"{period}"
        "        <domain:authInfo>\n"
        f"          <domain:pw>{_e(auth_pw)}</domain:pw>\n"
        "        </domain:authInfo>\n"
        "      </domain:transfer>\n"
        "    </transfer>"
    )
    return _envelope(inner, cltrid)


def host_create(name: str, *, addrs: Iterable[str] = (), cltrid: str) -> str:
    """<host:create> — a nameserver host object. This is the glue path.

    An in-bailiwick host (ns1.dotmac.ng under dotmac.ng) needs address glue;
    ``addrs`` carries IPv4/IPv6 literals the caller resolved. An out-of-zone
    host is created with no address. Which applies is the owner's decision,
    surfaced as whether ``addrs`` is populated.
    """
    addr_xml = "\n".join(_host_addr(a) for a in addrs)
    addr_block = f"{addr_xml}\n" if addr_xml else ""
    inner = (
        "    <create>\n"
        f'      <host:create xmlns:host="{_HOST}">\n'
        f"        <host:name>{_e(name)}</host:name>\n"
        f"{addr_block}"
        "      </host:create>\n"
        "    </create>"
    )
    return _envelope(inner, cltrid)


def _host_addr(literal: str) -> str:
    version = "v6" if ":" in literal else "v4"
    return f'        <host:addr ip="{version}">{_e(literal)}</host:addr>'


def host_check(names: Iterable[str], *, cltrid: str) -> str:
    name_xml = "\n".join(f"      <host:name>{_e(n)}</host:name>" for n in names)
    inner = (
        "    <check>\n"
        f'      <host:check xmlns:host="{_HOST}">\n'
        f"{name_xml}\n"
        "      </host:check>\n"
        "    </check>"
    )
    return _envelope(inner, cltrid)


def contact_check(ids: Iterable[str], *, cltrid: str) -> str:
    id_xml = "\n".join(f"      <contact:id>{_e(c)}</contact:id>" for c in ids)
    inner = (
        "    <check>\n"
        f'      <contact:check xmlns:contact="{_CONTACT}">\n'
        f"{id_xml}\n"
        "      </contact:check>\n"
        "    </check>"
    )
    return _envelope(inner, cltrid)


def poll_request(*, cltrid: str) -> str:
    """<poll op="req"> — read the head of the registry message queue.

    The registry queues messages (inbound transfer requests, host/contact
    changes) for the registrar to collect. This reads one; the poll handler
    turns it into a typed observation and acks it by id.
    """
    return _envelope('    <poll op="req"/>', cltrid)


def poll_ack(message_id: str, *, cltrid: str) -> str:
    """<poll op="ack"> — dequeue one durably recorded registry message.

    The poll handler calls this only on a later invocation whose input cursor
    proves the engine committed the corresponding observation and checkpoint.
    """
    return _envelope(f'    <poll op="ack" msgID={_a(message_id)}/>', cltrid)
