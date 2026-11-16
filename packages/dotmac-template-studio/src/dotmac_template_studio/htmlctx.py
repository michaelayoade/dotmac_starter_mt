"""Where a placeholder sits in an HTML part (ADR-0006 § 5h).

HTML-escaping a value makes it safe as element text. It does NOT make it safe
inside a tag: `<a href="{name}">` with `javascript:alert(1)` survives escaping,
and `style`, `on*`, `srcset` and an unquoted value have the same problem. So the
html part allows a placeholder in exactly two places:

- element text (outside any tag, comment, `<script>` or `<style>`), and
- the WHOLE value of a quoted `href` or `src` attribute, when the variable is
  declared `url` (its value is then scheme-checked and attribute-escaped).

Everything else is refused: a non-url placeholder in any tag or attribute, a url
placeholder in any other attribute or with text around it in the value, any
placeholder in a comment or in script/style content, and a section whose body
leaves the tag state different from how it found it (it would run several times
or not at all, so the markup around it could not be reasoned about).

Pure standard library, a single linear pass, shared by save-time validation and
`render_part` so the public renderer is safe without the validator.
"""

from __future__ import annotations

from collections.abc import Collection

from dotmac_template_studio.sections import Node, Section, Text, Var

URL_ATTRIBUTES = frozenset({"href", "src"})
_RAW_TEXT_ELEMENTS = frozenset({"script", "style"})
_SPACE = " \t\n\r\f"

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
    COMMENT,
    RAW_TEXT,
) = range(11)


class _Scanner:
    def __init__(self) -> None:
        self.state = DATA
        self.tag = ""
        self.closing = False
        self.attr = ""
        self.value_chars = 0
        self.var_in_value = False
        self.problems: list[str] = []
        self.raw_element = ""
        self.tail = ""  # last chars, for comment / raw-text terminators

    def signature(self) -> tuple[int, str, str]:
        if self.state == DATA:
            return (DATA, "", "")
        return (self.state, self.tag, self.attr)

    def feed(self, text: str) -> None:
        i, n = 0, len(text)
        while i < n:
            if self.state == DATA:
                # Fast path: element text needs no per-character work.
                j = text.find("<", i)
                if j < 0:
                    return
                i = j
            ch = text[i]
            state = self.state
            if state == DATA:
                if ch == "<":
                    rest = text[i + 1 : i + 4]
                    if i + 1 >= n:
                        # '<' ends this chunk: a placeholder may follow, so be
                        # conservative and treat it as the start of a tag.
                        self.state, self.tag, self.closing = TAG_NAME, "", False
                    elif rest.startswith("!--"):
                        self.state, self.tail = COMMENT, ""
                        i += 3
                    elif rest[0].isalpha() or rest[0] in "/!?":
                        self.closing = rest[0] == "/"
                        self.state, self.tag = TAG_NAME, ""
                        if self.closing:
                            i += 1
            elif state == TAG_NAME:
                if ch in _SPACE:
                    self.state = BEFORE_ATTR
                elif ch == ">":
                    self._end_tag()
                elif ch == "/":
                    self.state = BEFORE_ATTR
                else:
                    self.tag += ch.lower()
            elif state in (BEFORE_ATTR, AFTER_ATTR_NAME):
                if ch == ">":
                    self._end_tag()
                elif ch == "=":
                    self.state = BEFORE_VALUE
                elif ch in _SPACE or ch == "/":
                    if state == ATTR_NAME:
                        self.state = AFTER_ATTR_NAME
                else:
                    self.state, self.attr = ATTR_NAME, ch.lower()
            elif state == ATTR_NAME:
                if ch == "=":
                    self.state = BEFORE_VALUE
                elif ch in _SPACE or ch == "/":
                    self.state = AFTER_ATTR_NAME
                elif ch == ">":
                    self._end_tag()
                else:
                    self.attr += ch.lower()
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
                elif self.var_in_value:
                    self.problems.append(
                        f"text follows a url placeholder inside the {self.attr!r} "
                        "attribute value; the placeholder must be the whole value"
                    )
                    self.var_in_value = False
                    self.value_chars += 1
                else:
                    self.value_chars += 1
            elif state == VALUE_UNQ:
                if ch in _SPACE:
                    self.state = BEFORE_ATTR
                elif ch == ">":
                    self._end_tag()
            elif state == COMMENT:
                self.tail = (self.tail + ch)[-3:]
                if self.tail == "-->":
                    self.state = DATA
            elif state == RAW_TEXT:
                self.tail = (self.tail + ch.lower())[-(len(self.raw_element) + 2) :]
                if self.tail == "</" + self.raw_element:
                    self.state, self.tag = TAG_NAME, self.raw_element
                    self.closing = True
            i += 1

    def _start_value(self, state: int) -> None:
        self.state = state
        self.value_chars = 0
        self.var_in_value = False

    def _end_tag(self) -> None:
        if self.tag in _RAW_TEXT_ELEMENTS and not self.closing:
            self.state, self.raw_element, self.tail = RAW_TEXT, self.tag, ""
        else:
            self.state = DATA
        self.attr = ""

    def place(self, name: str, is_url: bool) -> None:
        state = self.state
        if state == DATA:
            return
        if state in (VALUE_DQ, VALUE_SQ) and is_url and self.attr in URL_ATTRIBUTES:
            if self.value_chars == 0 and not self.var_in_value:
                self.var_in_value = True
                return
            self.problems.append(
                f"{{{name}}} must be the whole value of the {self.attr!r} attribute"
            )
            return
        where = {
            COMMENT: "an HTML comment",
            RAW_TEXT: f"<{self.raw_element}> content",
        }.get(state)
        if where is None:
            where = (
                f"the {self.attr!r} attribute of <{self.tag}>"
                if state in (VALUE_DQ, VALUE_SQ, VALUE_UNQ) and self.attr
                else f"an HTML tag (<{self.tag}>)"
            )
        kind = "a url placeholder" if is_url else "a placeholder"
        self.problems.append(
            f"{kind} {{{name}}} is inside {where}; placeholders are allowed only "
            "in element text, or as the whole value of a quoted href/src when the "
            "variable is declared url"
        )


def html_placeholder_problems(
    nodes: list[Node], url_names: Collection[str]
) -> list[str]:
    """Every unsafe placeholder position in a parsed html part; empty means safe."""
    scanner = _Scanner()

    def walk(items: list[Node]) -> None:
        for node in items:
            if isinstance(node, Text):
                scanner.feed(node.value)
            elif isinstance(node, Var):
                scanner.place(node.name, node.name in url_names)
            elif isinstance(node, Section):
                before = scanner.signature()
                walk(node.children)
                if scanner.signature() != before:
                    scanner.problems.append(
                        f"section {{{'^' if node.inverted else '#'}{node.name}}} "
                        "changes the HTML tag state; open and close tags within "
                        "the same section"
                    )
                scanner.var_in_value = False

    walk(nodes)
    # Deduplicate, keep order.
    return list(dict.fromkeys(scanner.problems))


__all__ = ["URL_ATTRIBUTES", "html_placeholder_problems"]
