"""Smoke test for the Task 1 web foundation.

Builds a throwaway FastAPI app (no DB, no app.main middleware stack — this
is purely about the templating/asset pipeline) with one trivial route that
renders `layouts/admin.html` through the real `app.core.templating.render`
helper, and checks the rendered HTML carries the admin shell's load-bearing
pieces: the sidebar nav, the active_nav/page_title context contract, and
the CSRF header-bridge <script> tag.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.templating import render


def _build_test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/trivial")
    def trivial(request: Request):
        return render(
            request,
            "layouts/admin.html",
            {"active_nav": "dashboard", "page_title": "Trivial Page"},
        )

    return app


def test_admin_shell_renders_sidebar_and_csrf_script() -> None:
    client = TestClient(_build_test_app())
    response = client.get("/trivial")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    body = response.text

    # Sidebar nav (templates/components/sidebar.html) is present.
    assert 'aria-label="Admin navigation"' in body
    assert "Dashboard" in body
    assert 'href="/admin/parties"' in body

    # active_nav context contract highlights the current nav item.
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
