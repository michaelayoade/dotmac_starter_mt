"""Smoke test for the Task 1 web foundation.

Builds a throwaway FastAPI app (no DB, no app.main middleware stack — this
is purely about the templating/asset pipeline) with one trivial route that
renders `layouts/admin.html` through the real `app.core.templating.render`
helper, and checks the rendered HTML carries the admin shell's load-bearing
pieces: the sidebar nav, the page_title context contract, and the CSRF
header-bridge <script> tag.

Phase 2b.1 Task 1: the sidebar nav (and its active-item highlighting) is no
longer driven by an `active_nav` context var — it derives entirely from the
`nav_items` Jinja global (`app.core.templating.install_surface_globals`,
set for every unit test by `tests/unit/conftest.py`'s autouse
`_default_surface_globals` fixture) and path-matches against
`request.url.path`. The trivial route below is mounted AT `/admin` itself
(the dashboard's own nav path) so it exercises real active-item
highlighting, not a synthetic one.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.templating import render


def _build_test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/admin")
    def trivial(request: Request):
        return render(
            request,
            "layouts/admin.html",
            {"page_title": "Trivial Page"},
        )

    return app


def test_admin_shell_renders_sidebar_and_csrf_script() -> None:
    client = TestClient(_build_test_app())
    response = client.get("/admin")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    body = response.text

    # Sidebar nav (templates/components/sidebar.html) is present.
    assert 'aria-label="Admin navigation"' in body
    assert "Dashboard" in body
    assert 'href="/admin/parties"' in body

    # Path-based highlighting (request.url.path == "/admin", the Dashboard
    # nav item's own path) marks the current nav item active.
    assert 'aria-current="page"' in body

    # page_title context contract reaches the topbar / <title>.
    assert "Trivial Page" in body

    # CSRF header-bridge script tag (static/js/csrf.js) is present — this is
    # what reads the csrf_token cookie and injects X-CSRF-Token on htmx
    # requests and fetch().
    assert "/static/js/csrf.js" in body

    # htmx + Alpine vendor assets and the compiled Tailwind stylesheet are
    # wired into base.html.
    assert "/static/js/htmx.min.js" in body
    assert "/static/js/alpine.min.js" in body
    assert "/static/css/main.css" in body
