/**
 * CSRF header bridge.
 *
 * app.core.middleware.csrf.CSRFMiddleware validates the `X-CSRF-Token`
 * HEADER against the `csrf_token` COOKIE (double-submit pattern) — the
 * cookie is deliberately NOT HttpOnly so this script can read it. This file
 * copies the cookie value onto the header for every mutating request the
 * app's own JS issues.
 *
 * Covered:
 *   - htmx requests (`htmx:configRequest`) — every mutating form/link in
 *     these templates uses hx-post/hx-put/hx-delete, so this is the primary
 *     path.
 *   - `fetch()` — monkey-patched so hand-written JS (Alpine components,
 *     future custom code) calling fetch() directly also gets the header.
 *
 * NOT covered (by design):
 *   - Plain `<form method="post">` submits. A native form submit has no
 *     hook point for adding a custom header without JS intercepting the
 *     submit and re-issuing the request — which is just hx-post with extra
 *     steps. Every mutating form in these templates MUST use hx-post (or
 *     hx-put/hx-delete) instead of method="post". A bare method="post" form
 *     will 403 with `csrf_failed` since no header is ever attached to it.
 */
(function () {
    var COOKIE_NAME = 'csrf_token';
    var HEADER_NAME = 'X-CSRF-Token';

    function getCsrfToken() {
        var match = document.cookie.match(
            new RegExp('(?:^|; )' + COOKIE_NAME + '=([^;]*)')
        );
        return match ? decodeURIComponent(match[1]) : null;
    }

    // htmx: inject the header on every request htmx issues.
    document.addEventListener('htmx:configRequest', function (event) {
        var token = getCsrfToken();
        if (token) {
            event.detail.headers[HEADER_NAME] = token;
        }
    });

    // fetch(): monkey-patch so hand-written fetch() calls also carry it.
    var originalFetch = window.fetch;
    window.fetch = function (resource, options) {
        var opts = options || {};
        var token = getCsrfToken();
        if (token) {
            var headers = new Headers(opts.headers || {});
            if (!headers.has(HEADER_NAME)) {
                headers.set(HEADER_NAME, token);
            }
            opts.headers = headers;
        }
        return originalFetch(resource, opts);
    };
})();
