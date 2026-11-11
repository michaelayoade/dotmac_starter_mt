"""Shared feedback presentation and its actual-JavaScript hosted canary."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from dotmac_kernel.templating import compose_templates, render, static_dir
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / "packages/dotmac-kernel"
TEMPLATES = KERNEL / "src/dotmac_kernel/templates"


class _Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.head = False
        self.assets = []
        self.toasts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "head":
            self.head = True
        if tag in {"script", "link"}:
            self.assets.append((tag, attrs, self.head))
        if attrs.get("id") == "toast-container":
            self.toasts.append(attrs)

    def handle_endtag(self, tag):
        if tag == "head":
            self.head = False


def _parse(response):
    markup = _Markup()
    markup.feed(response.body.decode())
    return markup


def test_kernel_base_composes_feedback_widget_and_head_assets_once():
    compose_templates()
    response = render(
        Request({"type": "http", "path": "/", "headers": []}), "base.html"
    )
    markup = _parse(response)
    assert len(markup.toasts) == 1
    assert markup.toasts[0]["x-data"] == "toastStore"
    assert markup.toasts[0]["role"] == "status"
    assert markup.toasts[0]["aria-live"] == "polite"
    expected = [
        "/static/css/toasts.css",
        "/static/js/components.js",
        "/static/js/alpine.min.js",
    ]
    feedback = [
        (urlsplit(attrs.get("src", attrs.get("href", ""))), attrs, in_head)
        for _tag, attrs, in_head in markup.assets
        if urlsplit(attrs.get("src", attrs.get("href", ""))).path in expected
    ]
    assert [url.path for url, _attrs, _head in feedback] == expected
    assert all(in_head for _url, _attrs, in_head in feedback)
    assert all(
        re.fullmatch(r"v=[a-f0-9]{12}", url.query) for url, _attrs, _head in feedback
    )
    assert "defer" not in feedback[1][1]
    assert "defer" in feedback[2][1]
    base_source = (TEMPLATES / "base.html").read_text()
    assert 'x-data="toastStore"' not in base_source
    assert base_source.count('include "components/toasts.html"') == 1
    assert base_source.count('include "components/request_feedback_assets.html"') == 1


def test_product_can_compose_feedback_without_kernel_page_stylesheet(tmp_path):
    (tmp_path / "product.html").write_text(
        "<!doctype html><html><head>"
        '{% include "components/request_feedback_assets.html" %}'
        "</head><body><main>Product shell</main>"
        '{% include "components/toasts.html" %}</body></html>'
    )
    compose_templates(assembly_dir=tmp_path)
    response = render(
        Request({"type": "http", "path": "/product", "headers": []}), "product.html"
    )
    markup = _parse(response)
    assert len(markup.toasts) == 1
    paths = [
        urlsplit(attrs.get("src", attrs.get("href", ""))).path
        for _tag, attrs, _head in markup.assets
    ]
    assert paths == [
        "/static/css/toasts.css",
        "/static/js/components.js",
        "/static/js/alpine.min.js",
    ]
    assert "/static/css/main.css" not in paths


def test_toast_markup_uses_text_messages_and_existing_store_contract():
    source = (TEMPLATES / "components/toasts.html").read_text()
    assert 'x-text="toast.message"' in source
    assert "x-html" not in source
    assert "responseText" not in source
    assert 'x-on:show-toast.window="addToast($event.detail)"' in source
    assert 'x-on:click="removeToast(toast.id)"' in source
    assert 'aria-label="Dismiss notification"' in source
    assert "<script" not in source
    assert "unsafe-inline" not in source


def test_toast_styles_are_scoped_and_use_role_tokens_without_preflight():
    css = (static_dir() / "css/toasts.css").read_text()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    assert rules
    for selectors, _declarations in rules:
        assert all(
            selector.strip().startswith("#toast-container")
            for selector in selectors.split(",")
        )
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css)
    assert not re.search(
        r"@import|@font-face|@tailwind|@layer|:root|\b(?:html|body)\b", css
    )
    for status in ("positive", "negative", "warning", "info"):
        for role in ("surface", "border", "foreground"):
            assert f"var(--dmui-status-{status}-{role})" in css
    assert "300ms" in css
    assert "200ms" in css


def test_feedback_public_paths_are_in_kernel_package_data_and_use_csp_alpine():
    config = tomllib.loads((KERNEL / "pyproject.toml").read_text())
    includes = config["tool"]["poetry"]["include"]
    assert {
        "path": "src/dotmac_kernel/templates/**/*",
        "format": ["sdist", "wheel"],
    } in includes
    assert {
        "path": "src/dotmac_kernel/static/**/*",
        "format": ["sdist", "wheel"],
    } in includes
    for relative in ("css/toasts.css", "js/components.js", "js/alpine.min.js"):
        assert (static_dir() / relative).is_file()
    for name in ("request_feedback_assets.html", "toasts.html"):
        assert (TEMPLATES / "components" / name).is_file()
    alpine = (static_dir() / "js/alpine.min.js").read_text()
    assert "CSP build" in alpine
    assert not re.search(r"\bnew\s+(?:AsyncFunction|Function)\b", alpine)


NODE_CANARY = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Target {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, handler) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(handler);
  }
  dispatchEvent(event) {
    for (const handler of this.listeners.get(event.type) || []) handler(event);
  }
}
class Element {
  constructor(attributes, form) { this.attributes = attributes; this.form = form; }
  hasAttribute(name) { return Object.hasOwn(this.attributes, name); }
}
const document = new Target();
document.body = new Target();
const window = new Target();
const factories = {};
const stores = {};
const timers = [];
const notices = [];
window.addEventListener('show-toast', (event) => notices.push(event.detail));
const context = {
  document, window, Element, Date,
  Alpine: {
    data(name, factory) { factories[name] = factory; },
    store(name, value) { stores[name] = value; }
  },
  localStorage: {getItem() { return null; }, setItem() {}},
  CustomEvent: class {
    constructor(type, options) {this.type = type; this.detail = options.detail;}
  },
  setTimeout(callback, delay) { timers.push({callback, delay}); }
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
document.dispatchEvent({type: 'alpine:init'});
document.dispatchEvent({type: 'DOMContentLoaded'});
let submissions = 0;
const form = {submit() { submissions++; }};
const select = new Element({'data-submit-on-change': ''}, form);
document.dispatchEvent({type: 'change', target: select});
assert.equal(submissions, 1, 'one shared delegated handler submits exactly once');
document.dispatchEvent({type: 'change', target: new Element({}, select.form)});
assert.equal(submissions, 1, 'unrelated controls do not submit');
for (const type of ['htmx:responseError', 'htmx:sendError']) {
  for (const payload of ['<script>secret body</script>', '{"message":"sensitive"}']) {
    document.body.dispatchEvent({type, detail: {xhr: {responseText: payload}}});
    const notice = notices.at(-1);
    assert.equal(notice.message, 'Something went wrong. Please try again.');
    assert.equal(notice.type, 'error');
    assert.equal(JSON.stringify(notice).includes(payload), false);
  }
}
assert.equal(notices.length, 4);
document.body.dispatchEvent({
  type: 'htmx:afterRequest',
  detail: {xhr: {getResponseHeader() {
    return JSON.stringify({showToast: {message: 'saved', type: 'success'}});
  }}}
});
assert.equal(notices.at(-1).message, 'saved', 'existing server feedback remains');
const toast = factories.toastStore();
toast.addToast({message: 'saved', type: 'success', duration: 25});
assert.equal(toast.toasts.length, 1);
assert.equal(toast.toasts[0].visible, true);
assert.equal(toast.isSuccess(toast.toasts[0]), true);
assert.equal(timers.at(-1).delay, 25);
const id = toast.toasts[0].id;
toast.removeToast(id);
assert.equal(toast.toasts[0].visible, false);
assert.equal(timers.at(-1).delay, 300);
timers.at(-1).callback();
assert.equal(toast.toasts.length, 0);
assert.equal(stores.dark.on, false);
"""


def test_actual_components_js_opaque_failures_single_submit_and_toast_lifecycle():
    node = shutil.which("node")
    assert node is not None, "the hosted feedback canary requires Node"
    result = subprocess.run(  # noqa: S603 -- fixed canary and package path, no shell
        [node, "-e", NODE_CANARY, str(static_dir() / "js/components.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
