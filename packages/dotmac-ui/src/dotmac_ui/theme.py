"""Choosing a theme before the first paint.

The stylesheet already carries both themes — `DARK_THEME_SELECTORS` decides
which set of token values applies. Something still has to put the host in the
right state, and it has to happen *before* the browser paints, or the user sees
a flash of the wrong theme and then a jump.

That is a handful of lines of JavaScript, and every consumer writing their own
copy is how the fleet ended up with three design systems. `dotmac_academy_app`
had four copies of it in one pull request, under an attribute name that did not
match this package's.

## Why this returns source, not a `<script>` tag

The programme requires CSP-safe loading. An inline script needs a nonce, and
only the host knows its nonce — so the host builds the tag:

    <script nonce="{request.state.csp_nonce}">{bootstrap_script()}</script>

A host with a strict CSP that forbids inline script entirely can instead serve
`bootstrap_script()` as a static file and load it with `defer` in `<head>`.

## What it does

1. Reads a stored preference, if the user has expressed one.
2. Falls back to the operating system's `prefers-color-scheme`.
3. Writes the result to `THEME_ATTRIBUTE` on `<html>`.

Storage is wrapped: `localStorage` throws in Safari's private mode and when a
site is embedded with third-party storage blocked. A theme script must not be
able to break a page, so every failure lands on the light default.
"""

from __future__ import annotations

from typing import Final

from dotmac_ui.contract import THEME_ATTRIBUTE

#: Where a user's explicit choice is remembered. Namespaced to this package so
#: a host storing its own unrelated `theme` key cannot collide with it.
THEME_STORAGE_KEY: Final[str] = "dmui:theme"

#: The two values `THEME_ATTRIBUTE` may hold. Anything else in storage — a
#: stale value from an older scheme, or a hand-edited one — is ignored rather
#: than trusted, which is why the script tests membership instead of truthiness.
THEME_VALUES: Final[tuple[str, str]] = ("light", "dark")

#: Used when no preference is stored and the OS expresses none, and whenever
#: anything at all goes wrong.
DEFAULT_THEME: Final[str] = "light"

_TEMPLATE = """\
(function () {{
  var d = document.documentElement;
  try {{
    var stored = window.localStorage.getItem({key!r});
    var theme = ({values!r}).indexOf(stored) === -1 ? null : stored;
    if (theme === null) {{
      theme = window.matchMedia
        && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : {default!r};
    }}
    d.setAttribute({attribute!r}, theme);
  }} catch (e) {{
    d.setAttribute({attribute!r}, {default!r});
  }}
}})();"""


def bootstrap_script() -> str:
    """JavaScript that sets the theme attribute before first paint.

    Returns the source only. See the module docstring for why there is no tag.
    """
    return _TEMPLATE.format(
        key=THEME_STORAGE_KEY,
        values=list(THEME_VALUES),
        default=DEFAULT_THEME,
        attribute=THEME_ATTRIBUTE,
    )


def set_theme_script(theme: str) -> str:
    """JavaScript that records an explicit choice and applies it immediately.

    For a theme switcher. Kept here rather than left to each host so the storage
    key and the attribute have exactly one definition — the pair is what a copy
    gets wrong, and a mismatch is invisible until a user reloads and their
    choice is silently forgotten.
    """
    if theme not in THEME_VALUES:
        raise ValueError(f"theme must be one of {THEME_VALUES}, not {theme!r}")
    return (
        f"(function(){{try{{window.localStorage.setItem({THEME_STORAGE_KEY!r},{theme!r});}}"
        f"catch(e){{}}"
        f"document.documentElement.setAttribute({THEME_ATTRIBUTE!r},{theme!r});}})();"
    )


__all__ = [
    "DEFAULT_THEME",
    "THEME_STORAGE_KEY",
    "THEME_VALUES",
    "bootstrap_script",
    "set_theme_script",
]
