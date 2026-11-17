"""Where a placeholder sits in an HTML part (ADR-0006 § 5h).

HTML-escaping a value makes it safe as element text. It does NOT make it safe
inside a tag: `<a href="{name}">` with `javascript:alert(1)` survives escaping.
So the html part is checked FAIL-CLOSED rather than by emulating the HTML5
tokenizer (emulating it is how a desync between this scanner and a browser turns
into script execution). The rules:

- A placeholder is allowed in exactly two places: element text, and the WHOLE
  value of a quoted `href` on `<a>`/`<area>` or `src` on `<img>`, when the
  variable is declared `url` (its value is then scheme-checked and
  attribute-escaped). Any other tag, attribute, or attribute-value position is
  refused.
- Markup whose parsing rules are too subtle to track is refused outright, with
  or without placeholders: every `<!` (comments, CDATA, bogus comments) except a
  single leading `<!doctype html>` or the exact XHTML 1.0 Transitional doctype
  string, every `<?`, an end tag whose name does not
  start with an ASCII letter (`</ x>` is a bogus comment), and the elements in
  `FORBIDDEN_ELEMENTS` (script, style and the other raw-text, embedding,
  form and namespace elements), case-insensitively.
- A tag that is malformed in a way that could make a browser and this scanner
  disagree about where it ends (a quote, `<` or `=` where a tag or attribute
  name belongs), an unterminated tag, and tag or attribute names longer than
  `MAX_NAME_CHARS` are refused.
- A section whose body leaves the tag state different from how it found it is
  refused (it runs several times or not at all).

Pure standard library, a single linear pass (no per-character string growth),
shared by save-time validation and `render_part`.
"""

from __future__ import annotations

from collections.abc import Collection
from string import ascii_letters

from dotmac_template_studio.sections import Node, Section, Text, Var

URL_ATTRIBUTES = frozenset({("a", "href"), ("area", "href"), ("img", "src")})
FORBIDDEN_ELEMENTS = frozenset(
    {
        "script",
        "style",
        "xmp",
        "iframe",
        "noembed",
        "noframes",
        "noscript",
        "plaintext",
        "textarea",
        "title",
        "template",
        "svg",
        "math",
        "base",
        "link",
        "meta",
        "object",
        "embed",
        "form",
        "frame",
        "frameset",
        "applet",
    }
)
MAX_NAME_CHARS = 64
DOCTYPE_HTML = "<!doctype html>"
# The XHTML 1.0 Transitional doctype common in email HTML. A fixed literal: the
# scanner skips exactly these characters and nothing else, so no part of a
# declaration is ever interpreted; any other `<!doctype ...` stays refused.
DOCTYPE_XHTML_TRANSITIONAL = (
    '<!doctype html public "-//w3c//dtd xhtml 1.0 transitional//en" '
    '"http://www.w3.org/tr/xhtml1/dtd/xhtml1-transitional.dtd">'
)
ACCEPTED_DOCTYPES = (DOCTYPE_HTML, DOCTYPE_XHTML_TRANSITIONAL)
_SPACE = " \t\n\r\f"
_LETTERS = frozenset(ascii_letters)

(
    DATA,
    TAG_NAME,
    BEFORE_ATTR,
    ATTR_NAME,
    AFTER_ATTR_NAME,
    BEFORE_VALUE,
    VALUE_DQ,
    VALUE_SQ,
    VALUE_UNQ,
) = range(9)


class _Scanner:
    def __init__(self) -> None:
        self.state = DATA
        self.tag = ""
        self.attr = ""
        self.value_chars = 0
        self.var_in_value = False
        self.problems: list[str] = []
        # `id()` of each Var node accepted as the whole value of an `<img src>`.
        # Only http(s) may be rendered there; see `html_scan`.
        self.src_nodes: set[int] = set()
        self.started = False  # any non-space content or placeholder seen yet
        self.doctype_seen = False

    def signature(self) -> tuple[int, str, str]:
        if self.state == DATA:
            return (DATA, "", "")
        return (self.state, self.tag, self.attr)

    def _problem(self, message: str) -> None:
        self.problems.append(message)

    def feed(self, text: str) -> None:
        i, n = 0, len(text)
        while i < n:
            if self.state == DATA:
                j = text.find("<", i)
                segment = text[i:] if j < 0 else text[i:j]
                if not self.started and segment.strip():
                    self.started = True
                if j < 0:
                    return
                i = j
                i = self._open_angle(text, i, n)
                continue
            ch = text[i]
            state = self.state
            if state == TAG_NAME:
                if ch in _SPACE or ch in "/>":
                    self._tag_name_done()
                    if ch == ">":
                        self._end_tag()
                    else:
                        self.state = BEFORE_ATTR
                elif ch in "\"'<=":
                    self._problem("malformed HTML tag name")
                    self.state = BEFORE_ATTR
                elif len(self.tag) <= MAX_NAME_CHARS:
                    self.tag += ch.lower()
            elif state in (BEFORE_ATTR, AFTER_ATTR_NAME):
                if ch == ">":
                    self._end_tag()
                elif ch == "=" and state == AFTER_ATTR_NAME:
                    self.state = BEFORE_VALUE
                elif ch in "\"'<=":
                    self._problem(
                        "malformed HTML tag: a quote, '<' or '=' where an "
                        "attribute name belongs"
                    )
                    self.state = BEFORE_ATTR
                elif ch in _SPACE or ch == "/":
                    pass
                else:
                    self.state, self.attr = ATTR_NAME, ch.lower()
            elif state == ATTR_NAME:
                if ch == "=":
                    self.state = BEFORE_VALUE
                elif ch in _SPACE or ch == "/":
                    self.state = AFTER_ATTR_NAME
                elif ch == ">":
                    self._end_tag()
                elif ch in "\"'<":
                    self._problem("malformed HTML attribute name")
                    self.state = BEFORE_ATTR
                elif len(self.attr) <= MAX_NAME_CHARS:
                    self.attr += ch.lower()
                if len(self.attr) > MAX_NAME_CHARS:
                    self._problem(f"attribute name longer than {MAX_NAME_CHARS}")
                    self.attr = self.attr[:MAX_NAME_CHARS]
            elif state == BEFORE_VALUE:
                if ch in _SPACE:
                    pass
                elif ch == '"':
                    self._start_value(VALUE_DQ)
                elif ch == "'":
                    self._start_value(VALUE_SQ)
                elif ch == ">":
                    self._end_tag()
                else:
                    self._start_value(VALUE_UNQ)
                    self.value_chars = 1
            elif state in (VALUE_DQ, VALUE_SQ):
                quote = '"' if state == VALUE_DQ else "'"
                if ch == quote:
                    self.state = BEFORE_ATTR
                    self.attr = ""
                elif self.var_in_value:
                    self._problem(
                        f"text follows a url placeholder inside the {self.attr!r} "
                        "attribute value; the placeholder must be the whole value"
                    )
                    self.var_in_value = False
                else:
                    self.value_chars += 1
            elif state == VALUE_UNQ:
                if ch in _SPACE:
                    self.state = BEFORE_ATTR
                    self.attr = ""
                elif ch == ">":
                    self._end_tag()
            i += 1

    def _open_angle(self, text: str, i: int, n: int) -> int:
        """Handle a '<' met in element text; return the next index to scan."""
        was_started, self.started = self.started, True
        if i + 1 >= n:
            # '<' ends this chunk: a placeholder may follow. Fail closed: it is
            # the start of a tag whose name a placeholder would be inside.
            self.state, self.tag = TAG_NAME, ""
            return i + 1
        nxt = text[i + 1]
        if nxt == "!":
            if not self.doctype_seen and not was_started:
                for literal in ACCEPTED_DOCTYPES:
                    if text[i : i + len(literal)].lower() == literal:
                        self.doctype_seen = True
                        return i + len(literal)
            self._problem(
                "HTML comments, CDATA and declarations (`<!`) are not allowed "
                "(only a single leading <!doctype html>)"
            )
            return i + 2
        if nxt == "?":
            self._problem("processing instructions (`<?`) are not allowed")
            return i + 2
        if nxt == "/":
            if i + 2 < n and text[i + 2] not in _LETTERS:
                self._problem(
                    "an end tag must start with a letter (`</` followed by "
                    "anything else is a bogus comment)"
                )
                return i + 2
            self.state, self.tag = TAG_NAME, ""
            return i + 2
        if nxt in _LETTERS:
            self.state, self.tag = TAG_NAME, ""
            return i + 1
        return i + 1  # a literal '<' in text

    def _tag_name_done(self) -> None:
        if len(self.tag) > MAX_NAME_CHARS:
            self._problem(f"tag name longer than {MAX_NAME_CHARS}")
        if self.tag in FORBIDDEN_ELEMENTS:
            self._problem(f"the <{self.tag}> element is not allowed in an html part")

    def _start_value(self, state: int) -> None:
        self.state = state
        self.value_chars = 0
        self.var_in_value = False

    def _end_tag(self) -> None:
        if self.state == TAG_NAME:
            self._tag_name_done()
        self.state = DATA
        self.attr = ""

    def place(self, name: str, is_url: bool, node_id: int = 0) -> None:
        self.started = True
        state = self.state
        if state == DATA:
            return
        if (
            state in (VALUE_DQ, VALUE_SQ)
            and is_url
            and (self.tag, self.attr) in URL_ATTRIBUTES
        ):
            if self.value_chars == 0 and not self.var_in_value:
                self.var_in_value = True
                if (self.tag, self.attr) == ("img", "src"):
                    self.src_nodes.add(node_id)
                return
            self._problem(
                f"{{{name}}} must be the whole value of the {self.attr!r} attribute"
            )
            return
        if state in (VALUE_DQ, VALUE_SQ, VALUE_UNQ) and self.attr:
            where = f"the {self.attr!r} attribute of <{self.tag}>"
        else:
            where = f"an HTML tag (<{self.tag}>)"
        kind = "a url placeholder" if is_url else "a placeholder"
        self._problem(
            f"{kind} {{{name}}} is inside {where}; placeholders are allowed only "
            "in element text, or as the whole value of a quoted href on <a>/<area> "
            "or src on <img> when the variable is declared url"
        )


def html_scan(
    nodes: list[Node], url_names: Collection[str]
) -> tuple[list[str], frozenset[int]]:
    """`(problems, src_node_ids)` for a parsed html part.

    `src_node_ids` are the `id()`s of the `Var` nodes that are the whole value of
    an `<img src>`: a renderer must allow only http(s) for those (a mailto image
    is meaningless), matching what a literal `src` may carry.
    """
    scanner = _Scanner()

    def walk(items: list[Node]) -> None:
        for node in items:
            if isinstance(node, Text):
                scanner.feed(node.value)
            elif isinstance(node, Var):
                scanner.place(node.name, node.name in url_names, id(node))
            elif isinstance(node, Section):
                before = scanner.signature()
                if scanner.state != DATA:
                    scanner._problem(
                        f"section {{{'^' if node.inverted else '#'}{node.name}}} "
                        "starts inside an HTML tag"
                    )
                walk(node.children)
                if scanner.signature() != before:
                    scanner._problem(
                        f"section {{{'^' if node.inverted else '#'}{node.name}}} "
                        "changes the HTML tag state; open and close tags within "
                        "the same section"
                    )
                scanner.var_in_value = False

    walk(nodes)
    if scanner.state != DATA:
        scanner._problem("the html part ends inside an unterminated tag")
    return list(dict.fromkeys(scanner.problems)), frozenset(scanner.src_nodes)


def html_placeholder_problems(
    nodes: list[Node], url_names: Collection[str]
) -> list[str]:
    """Every unsafe construct in a parsed html part; empty means safe."""
    return html_scan(nodes, url_names)[0]


__all__ = [
    "ACCEPTED_DOCTYPES",
    "DOCTYPE_HTML",
    "DOCTYPE_XHTML_TRANSITIONAL",
    "FORBIDDEN_ELEMENTS",
    "MAX_NAME_CHARS",
    "URL_ATTRIBUTES",
    "html_placeholder_problems",
    "html_scan",
]
