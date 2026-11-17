"""Context v2 declarations and save-time revision validation (ADR-0006 § 5h/5i).

Legacy contexts (declaring only `variables`) must behave exactly as before; the
v2 fields are additive. `validate_revision` is also the function the deploy-time
check (TS-6) will call, so it must report problems rather than raise.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel.exceptions import BadRequestError
from dotmac_kernel.models import Tenant
from dotmac_template_studio import service
from dotmac_template_studio.contexts import (
    RenderContext,
    RenderContextConflictError,
    _reset_for_tests,
    register_contexts,
)
from dotmac_template_studio.validation import check_revision, validate_revision

RESET = RenderContext(
    name="password_reset",
    variables=("customer_name",),
    required=("reset_link",),
    optional=("note",),
    url=("reset_link", "course_url"),
    lists={"courses": ("title", "course_url")},
    sample={
        "customer_name": "Ada",
        "reset_link": "https://example.com/reset?t=1",
        "courses": [{"title": "Intro", "course_url": "https://example.com/c/1"}],
    },
)
LEGACY = RenderContext(name="legacy", variables=("a", "b"))


@pytest.fixture(autouse=True)
def _registry():
    previous = _reset_for_tests([RESET, LEGACY])
    yield
    _reset_for_tests(previous.values())


def _problems(**kw) -> list[str]:
    return validate_revision(RESET, **kw)


# ── Declarations ────────────────────────────────────────────────────────────


def test_legacy_context_is_unchanged() -> None:
    assert LEGACY.variables == frozenset({"a", "b"})
    assert LEGACY.required == frozenset()
    assert dict(LEGACY.lists) == {}
    assert validate_revision(LEGACY, subject="{a}", text="{a} {b}") == []


def test_variables_are_the_union_of_scalars() -> None:
    assert RESET.variables == frozenset({"customer_name", "reset_link", "note"})
    assert RESET.sorted_variables() == ["customer_name", "note", "reset_link"]


def test_v2_only_context_needs_no_legacy_variables() -> None:
    ctx = RenderContext(name="x", required=("a",))
    assert ctx.variables == frozenset({"a"})


def test_context_with_nothing_declared_is_refused() -> None:
    with pytest.raises(ValueError, match="declares no variables"):
        RenderContext(name="empty")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"required": ("a",), "optional": ("a",)}, "both required and optional"),
        ({"variables": ("a",), "lists": {"a": ("x",)}}, "both as a list"),
        ({"variables": ("a",), "lists": {"l": ()}}, "no item variables"),
        ({"variables": ("a",), "url": ("zzz",)}, "neither a declared scalar"),
        ({"variables": ("a",), "required": ("Bad",)}, "invalid required name"),
        ({"variables": ("a",), "max_list_items": 0}, "max_list_items"),
        ({"variables": ("a",), "max_list_items": 10_000}, "max_list_items"),
        ({"variables": ("a",), "sample": {"zzz": "x"}}, "does not declare"),
        ({"variables": ("a",), "sample": {"a": 5}}, "must be a string"),
        (
            {"variables": ("a",), "url": ("a",), "sample": {"a": "javascript:x"}},
            "not allowed",
        ),
        (
            {"lists": {"l": ("x",)}, "sample": {"l": [{"y": "1"}]}},
            "must use only",
        ),
    ],
)
def test_bad_declarations_are_refused(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        RenderContext(name="bad", **kwargs)


def test_v2_fields_are_immutable_and_part_of_conflict_detection() -> None:
    assert isinstance(RESET.required, frozenset)
    with pytest.raises(TypeError):
        RESET.lists["x"] = frozenset()  # type: ignore[index]
    register_contexts(RESET)  # identical: no-op
    with pytest.raises(RenderContextConflictError):
        register_contexts(
            RenderContext(
                name="password_reset",
                variables=("customer_name",),
                required=("reset_link",),
                optional=("note",),
                url=("reset_link", "course_url"),
                lists={"courses": ("title", "course_url", "extra")},  # widened
            )
        )


# ── Revision validation ─────────────────────────────────────────────────────


def test_a_complete_revision_is_valid() -> None:
    assert (
        _problems(
            subject="Reset for {customer_name}",
            text="Link: {reset_link}\n{#note}Note: {note}{/note}"
            "{#courses}- {title} {course_url}\n{/courses}",
            html="<a href='{reset_link}'>go</a>",
        )
        == []
    )


def test_required_variable_missing_is_refused() -> None:
    problems = _problems(subject="Reset", text="Hello {customer_name}")
    assert any("required variable(s) never used: {reset_link}" in p for p in problems)


def test_required_must_be_in_every_body_part_present() -> None:
    problems = _problems(text="{reset_link}", html="<p>hi</p>")
    assert len(problems) == 1
    assert problems[0].startswith("html:")


def test_required_inside_a_section_does_not_count() -> None:
    problems = _problems(text="{#note}{reset_link}{/note}")
    assert any("required variable(s) never used" in p for p in problems)


def test_subject_is_exempt_from_required() -> None:
    assert _problems(subject="Reset", text="{reset_link}") == []


def test_unknown_variable_is_named_with_the_allowed_list() -> None:
    (problem,) = _problems(text="{reset_link} {nonsense}")
    assert "{nonsense}" in problem
    assert "{customer_name}" in problem


def test_double_braces_refused_including_section_tags() -> None:
    problems = _problems(
        text="{reset_link} {{customer_name}} {{#courses}}x{{/courses}}"
    )
    joined = " ".join(problems)
    assert "double braces" in joined
    assert "{{customer_name}}" in joined
    assert "{{#courses}}" in joined


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{#courses}x", "never closed"),
        ("x{/courses}", "no matching opening tag"),
        ("{#courses}{#note}x{/courses}{/note}", "mis-nested"),
        ("{#courses}{#courses}x{/courses}{/courses}", "inside itself"),
    ],
)
def test_unbalanced_or_misnested_sections_are_refused(text, message) -> None:
    problems = _problems(text="{reset_link}" + text)
    assert any(message in p for p in problems), problems


def test_depth_over_two_is_refused() -> None:
    ctx = RenderContext(
        name="deep",
        required=("r",),
        optional=("o1", "o2", "o3"),
    )
    ok = validate_revision(ctx, text="{r}{#o1}{#o2}x{/o2}{/o1}")
    assert ok == []
    bad = validate_revision(ctx, text="{r}{#o1}{#o2}{#o3}x{/o3}{/o2}{/o1}")
    assert any("deeper than 2" in p for p in bad)


def test_section_over_undeclared_or_scalar_name_is_refused() -> None:
    problems = _problems(
        text="{reset_link}{#ghost}x{/ghost}{#customer_name}y{/customer_name}"
    )
    assert sum("not a declared list or optional" in p for p in problems) == 2


def test_inverted_section_names_are_checked_too() -> None:
    problems = _problems(text="{reset_link}{^ghost}x{/ghost}")
    assert any("not a declared list or optional" in p for p in problems)
    assert _problems(text="{reset_link}{^courses}none{/courses}") == []


def test_item_variable_outside_its_list_is_refused() -> None:
    problems = _problems(text="{reset_link} {title}")
    assert any("list item variable" in p and "{title}" in p for p in problems)
    # ...and not visible in an inverted branch or a different list's scope.
    problems = _problems(text="{reset_link}{^courses}{title}{/courses}")
    assert any("list item variable" in p for p in problems)


def test_item_variable_inside_its_list_is_fine_and_outer_scalars_stay_visible() -> None:
    assert (
        _problems(text="{reset_link}{#courses}{title} for {customer_name}{/courses}")
        == []
    )


def test_list_used_as_flat_placeholder_is_refused() -> None:
    problems = _problems(text="{reset_link} {courses}")
    assert any("is a list" in p for p in problems)


def test_optional_outside_its_own_section_is_refused() -> None:
    problems = _problems(text="{reset_link} {note}")
    assert any("optional and may be absent" in p for p in problems)
    assert any(
        "optional" in p
        for p in _problems(text="{reset_link}{#courses}{note}{/courses}")
    )


def test_subject_takes_no_sections() -> None:
    problems = _problems(subject="{#note}x{/note}", text="{reset_link}")
    assert any("subject may not contain sections" in p for p in problems)


def test_validate_revision_reports_all_problems_and_never_raises() -> None:
    problems = _problems(subject="{#x}", text="{{a}} {zzz}", html="{#courses}")
    assert len(problems) >= 4


def test_check_revision_raises_bad_request_with_all_problems() -> None:
    with pytest.raises(BadRequestError) as excinfo:
        check_revision(RESET, text="{nonsense}")
    assert "{nonsense}" in str(excinfo.value)
    assert "{reset_link}" in str(excinfo.value)


# ── Through the service ─────────────────────────────────────────────────────


@pytest.fixture
def tenant(db) -> Tenant:
    tenant = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


def test_create_version_refuses_a_reset_template_without_its_link(db, tenant) -> None:
    template = service.create_template(
        db, tenant.id, slug="reset", channel="email", context="password_reset", name="R"
    )
    with pytest.raises(BadRequestError, match="reset_link"):
        service.create_version(db, tenant.id, template.id, body="Hello {customer_name}")
    assert service.list_versions(db, tenant.id, template.id) == []
    good = service.create_version(
        db, tenant.id, template.id, body="Go: {reset_link}", subject="Reset"
    )
    assert good.version == 1


def test_update_version_is_validated_too(db, tenant) -> None:
    template = service.create_template(
        db, tenant.id, slug="reset", channel="email", context="password_reset", name="R"
    )
    v = service.create_version(db, tenant.id, template.id, body="{reset_link}")
    with pytest.raises(BadRequestError, match="required"):
        service.update_version(db, tenant.id, template.id, v.version, body="nothing")
    with pytest.raises(BadRequestError, match="never closed"):
        service.update_version(
            db, tenant.id, template.id, v.version, body="{reset_link}{#courses}"
        )


def test_render_published_renders_sections_not_literally(db, tenant) -> None:
    template = service.create_template(
        db, tenant.id, slug="reset", channel="email", context="password_reset", name="R"
    )
    v = service.create_version(
        db,
        tenant.id,
        template.id,
        subject="Reset {customer_name}",
        body="{reset_link}\n{#courses}[{title}]{/courses}{^courses}none{/courses}",
    )
    service.publish_version(db, tenant.id, template.id, v.version)
    subject, body = service.render_published(
        db,
        tenant.id,
        "reset",
        "email",
        {
            "customer_name": "Ada",
            "reset_link": "https://example.com/r",
            "courses": [
                {"title": "A", "course_url": "https://e.com/a"},
                {"title": "B", "course_url": "https://e.com/b"},
            ],
        },
        strict=True,
    )
    assert subject == "Reset Ada"
    assert body == "https://example.com/r\n[A][B]"
    with pytest.raises(BadRequestError, match="line-break"):
        service.render_published(
            db,
            tenant.id,
            "reset",
            "email",
            {"customer_name": "A\nBcc: x", "reset_link": "https://example.com/r"},
            strict=True,
        )
