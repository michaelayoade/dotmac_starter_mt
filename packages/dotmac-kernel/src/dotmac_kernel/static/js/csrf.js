/**
 * CSRF header bridge.
 *
 * app.core.middleware.csrf.CSRFMiddleware validates the `X-CSRF-Token`
 * HEADER against a signed, session-bound CSRF COOKIE (double-submit pattern) — the
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
 * Native forms are covered independently by a hidden `csrf_token` field.
 */
(function () {
    var COOKIE_NAMES = ['__Host-csrf_token', 'csrf_token'];
    var HEADER_NAME = 'X-CSRF-Token';

    function getCsrfToken() {
        for (var i = 0; i < COOKIE_NAMES.length; i += 1) {
            var match = document.cookie.match(
                new RegExp('(?:^|; )' + COOKIE_NAMES[i] + '=([^;]*)')
            );
            if (match) {
                return decodeURIComponent(match[1]);
            }
        }
        return null;
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
