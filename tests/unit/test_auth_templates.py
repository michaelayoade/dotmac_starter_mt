"""The kernel auth lifecycle pages render standalone.

Products own the routes and token lifecycles behind these pages; the
kernel contract is only that each template renders its form with the
documented context variables and both CSRF transports (hidden field +
hx-post), mirroring `auth/login.html`.
"""

from __future__ import annotations

from types import SimpleNamespace

from dotmac_kernel.templating import templates


def _render(name: str, **context: object) -> str:
    # `surface` is injected by `dotmac_kernel.templating.render()` for real
    # requests; a raw env render supplies the same shape explicitly.
    surface = SimpleNamespace(login_path="/login", landing_path="/")
    return templates.env.get_template(name).render(
        form_action="/do-it", csrf_token="tok", surface=surface, **context
    )


def test_forgot_template_renders_email_form():
    html = _render("auth/forgot.html")
    assert 'name="email"' in html
    assert 'name="csrf_token"' in html
    assert 'hx-post="/do-it"' in html
    assert "Send reset link" in html


def test_reset_template_renders_token_and_password():
    html = _render("auth/reset.html", token="abc", min_password_length=12)
    assert 'name="token" value="abc"' in html
    assert 'name="password"' in html
    assert 'minlength="12"' in html
    assert "Update password" in html


def test_accept_template_renders_token_and_password():
    html = _render("auth/accept.html", token="xyz")
    assert 'name="token" value="xyz"' in html
    assert 'name="password"' in html
    assert "Activate account" in html
