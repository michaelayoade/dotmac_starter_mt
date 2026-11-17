"""Sandboxed html preview document (ADR-0006 § 5h: "Preview is sandboxed").

The rendered html part is NEVER placed into the admin page. It is wrapped here
into a complete document that carries its own restrictive Content-Security-Policy
and is handed to the template as a plain string for `<iframe srcdoc="...">`,
where Jinja's autoescape attribute-escapes it. The iframe itself is emitted with
an empty `sandbox` attribute (every restriction on: no scripts, no same-origin,
no forms, no popups, no top navigation).

Layers, any one of which already stops script execution:

1. the html passed the nh3 allowlist at save and again at render;
2. the iframe is sandboxed without `allow-scripts` or `allow-same-origin`;
3. this CSP: `default-src 'none'` (no script, frame, connect, font, media),
   images only over http(s)/data, inline styles only, no `<base>`, no forms.

A srcdoc document also inherits the embedding page's CSP, so the effective
policy is the intersection of the two.
"""

from __future__ import annotations

PREVIEW_CSP = (
    "default-src 'none'; img-src https: http: data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)

# The sandbox token list. Empty on purpose: the strictest setting. A test pins
# that it never contains `allow-scripts` or `allow-same-origin`.
IFRAME_SANDBOX = ""


def build_srcdoc(rendered_html: str) -> str:
    """The full document for the iframe's `srcdoc` (NOT yet attribute-escaped)."""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{PREVIEW_CSP}">'
        '<meta name="referrer" content="no-referrer">'
        "</head><body>"
        f"{rendered_html}"
        "</body></html>"
    )


__all__ = ["IFRAME_SANDBOX", "PREVIEW_CSP", "build_srcdoc"]
