"""Product-writer ratchet for tenant-facing messages (ADR-0006 5f, ADR-0018).

Proved here:

* the Starter tree is held at its (empty) backlog, and the exempt owners are
  exactly the findings listed in ``EXPECTED_EXEMPT_FINDINGS``;
* the synthetic Academy fixture reproduces the PLAN s1 census (26 send-site
  lines over 22 numbered rows, 2 shared outbox writers and the 3 findings of the
  one shared SMTP transport = 31);
* sensitivity: a planted outbox writer, direct SMTP call and inline copy each
  fail WITH THE EXPECTED ID; a new finding fails, a vanished one fails, a lowered
  backlog passes; ``--write-backlog`` cannot add;
* false-positive guards, so the checker is not vacuous in the other direction.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import comms_writer_ratchet as ratchet  # noqa: E402

ARCH = Path(__file__).resolve().parent
STARTER_CONFIG = ARCH / "comms_writer_ratchet_config.json"
STARTER_BACKLOG = ARCH / "comms_writer_backlog_starter.json"
FIXTURE = ARCH / "fixtures" / "comms_writer_academy"

# Findings that exist in an allowed-owner tree and are suppressed ONLY by its
# `allowed` entry. Pinning them means an owner cannot quietly grow a second
# writer: a new finding there changes this set and the change is reviewed.
EXPECTED_EXEMPT_FINDINGS: frozenset[str] = frozenset(
    {
        "packages/dotmac-connector-smtp/src/dotmac_connector_smtp/provider.py::direct_smtp::import:smtplib#1",
        "packages/dotmac-connector-smtp/src/dotmac_connector_smtp/provider.py::direct_smtp::call:starttls#1",
    }
)


# The exact Starter exclusion list. Adding an exclusion (for example a broad
# `packages/**/provider.py` all-kinds glob) hides findings, so it must change this
# set and be reviewed. All-kinds exclusions may only name non-runtime trees.
EXPECTED_EXCLUDES: frozenset[tuple[str, tuple[str, ...]]] = frozenset(
    {
        ("**/tests/**", ("direct_smtp", "inline_copy", "outbox_writer")),
        ("**/migrations/**", ("outbox_writer",)),
        ("alembic/**", ("outbox_writer",)),
        (".venv/**", ("direct_smtp", "inline_copy", "outbox_writer")),
        ("node_modules/**", ("direct_smtp", "inline_copy", "outbox_writer")),
    }
)


def _scan(sources: dict[str, str]) -> list[str]:
    findings, _ = ratchet.scan_sources(sources)
    return ratchet.number(findings)


def _ids(source: str, path: str = "services/x.py") -> list[str]:
    return _scan({path: source})


def _materialise(dest: Path) -> Path:
    """Copy the fixture and turn ``*.py.fixture`` into real ``.py`` files.

    The shared SMTP transport is committed as ``.py.fixture`` so that no real
    ``smtplib`` import sits in a committed ``.py`` file (the repository-wide
    connector governance scan would count it as an outbound-transport source).
    """
    shutil.copytree(FIXTURE, dest)
    for path in dest.rglob("*.py.fixture"):
        path.rename(path.with_suffix(""))
    return dest


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return _materialise(tmp_path / "fx")


@pytest.fixture(scope="module")
def academy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _materialise(tmp_path_factory.mktemp("academy") / "fx")


def _run(fx: Path, capsys: pytest.CaptureFixture[str] | None = None) -> int:
    return ratchet.run(fx / "config.json", fx / "backlog.json", write=False)


def _backlog(fx: Path) -> dict:
    return json.loads((fx / "backlog.json").read_text())


def _save(fx: Path, data: dict) -> None:
    data["total"] = len(data["findings"])
    (fx / "backlog.json").write_text(json.dumps(data))


def _config(fx: Path) -> dict:
    return json.loads((fx / "config.json").read_text())


def _save_config(fx: Path, config: dict) -> None:
    (fx / "config.json").write_text(json.dumps(config))


def _fails_with(fx: Path, capsys: pytest.CaptureFixture[str], needle: str) -> None:
    capsys.readouterr()
    assert _run(fx) == 1
    assert needle in capsys.readouterr().err


# -- Starter ----------------------------------------------------------------


@pytest.fixture(scope="module")
def starter() -> ratchet.Result:
    config = json.loads(STARTER_CONFIG.read_text())
    return ratchet.scan_product(config, STARTER_CONFIG.parent)


@pytest.fixture(scope="module")
def starter_unallowed() -> ratchet.Result:
    config = json.loads(STARTER_CONFIG.read_text())
    config["allowed"] = []
    return ratchet.scan_product(config, STARTER_CONFIG.parent)


def test_starter_tree_is_at_its_backlog(starter: ratchet.Result) -> None:
    assert not starter.problems
    assert not ratchet.compare(starter.ids, ratchet.load_backlog(STARTER_BACKLOG))


def test_starter_backlog_is_empty_and_says_so() -> None:
    data = json.loads(STARTER_BACKLOG.read_text())
    assert data["findings"] == [] and data["total"] == 0


def test_starter_scan_actually_covers_entry_point_families(
    starter: ratchet.Result,
) -> None:
    for family in ("web_routes", "scripts", "modules_and_kernel"):
        assert starter.families.get(family, 0) > 0, family


def test_starter_exempt_adapters_carry_exactly_the_pinned_findings(
    starter_unallowed: ratchet.Result,
) -> None:
    """Scan with the allowed-owner entries removed: what they hide is pinned."""
    config = json.loads(STARTER_CONFIG.read_text())
    for entry in config["allowed"]:
        assert entry["premise"] and entry["kinds"]
        assert "*" not in entry["glob"].split("/src/")[0], "list owners one by one"
    assert not starter_unallowed.problems
    assert frozenset(starter_unallowed.ids) == EXPECTED_EXEMPT_FINDINGS


def test_starter_exclusions_are_enforceable() -> None:
    config = json.loads(STARTER_CONFIG.read_text())
    assert not ratchet._validate_entries(config["exclude"], "exclusion")
    for entry in config["exclude"]:
        if "migrations" in entry["glob"] or entry["glob"].startswith("alembic"):
            # migrations are still scanned for the other two kinds
            assert set(entry["kinds"]) == {"outbox_writer"}


# -- Academy census ----------------------------------------------------------


def test_academy_fixture_reproduces_the_census(academy: Path) -> None:
    config = json.loads((academy / "config.json").read_text())
    result = ratchet.scan_product(config, academy)
    census = json.loads((FIXTURE / "census.json").read_text())
    mapping = census["map"]
    assert not result.problems
    assert len(result.ids) == 31
    assert set(result.ids) == set(mapping)
    send_rows = {v for v in mapping.values() if v[0].isdigit()}
    assert len(send_rows) == 26
    assert len({"".join(c for c in r if c.isdigit()) for r in send_rows}) == 22
    assert sum(v == "shared SMTP transport" for v in mapping.values()) == 3
    assert sum(v.startswith("shared writer") for v in mapping.values()) == 2
    assert "app/instructor.py" in census["_comment"], "9c duplicate is documented"
    assert ratchet.load_backlog(academy / "backlog.json") == sorted(result.ids)
    assert (
        ratchet.run(academy / "config.json", academy / "backlog.json", write=False) == 0
    )


def test_academy_fixture_locations_match_the_real_census() -> None:
    mapping = json.loads((FIXTURE / "census.json").read_text())["map"]
    where = {v: k for k, v in mapping.items()}
    assert where["1"].startswith("services/email.py::")
    assert where["13"].startswith("services/reminder_resend.py::")
    assert where["15"].startswith("cli.py::")
    assert where["19"].startswith("services/audit_context.py::")


def test_academy_fixture_covers_every_family(academy: Path) -> None:
    config = json.loads((academy / "config.json").read_text())
    result = ratchet.scan_product(config, academy)
    for family in config["families"]:
        assert result.families.get(family, 0) > 0
    assert not [i for i in result.ids if i.startswith(("workers/", "scripts/"))]


def test_row_13_import_inside_a_function_and_double_star_dict_are_both_needed(
    tree: Path,
) -> None:
    """The two cross-file shapes that hid row 13 in the real tree."""
    path = tree / "tree/services/reminder_resend.py"
    source = path.read_text()
    # (a) renderer import removed -> unresolved call -> no copy -> finding gone
    path.write_text(
        source.replace("    from app.services.reminders import _email_for\n", "")
    )
    assert _run(tree) == 1
    # (b) ** mapping replaced by a mapping with no copy keyword
    path.write_text(
        source.replace("subject=subject,", "")
        .replace("html_body=html,", "")
        .replace("text_body=text,", "")
    )
    assert _run(tree) == 1


# -- planted detectors: each fails WITH ITS EXPECTED ID ---------------------------


def test_planted_outbox_writer_fails(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "tree/models/sms_outbox.py").write_text(
        "class SmsOutbox:\n    __tablename__ = 'sms_outbox'\n"
    )
    _fails_with(
        tree, capsys, "NEW finding models/sms_outbox.py::outbox_writer::SmsOutbox#1"
    )


def test_planted_direct_smtp_fails(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "tree/workers/mailer.py").write_text(
        "import smtplib\n\n\ndef go():\n"
        "    with smtplib.SMTP_SSL('mail.example.test') as s:\n"
        "        s.sendmail('a', 'b', 'c')\n"
    )
    capsys.readouterr()
    assert _run(tree) == 1
    err = capsys.readouterr().err
    for needle in (
        "workers/mailer.py::direct_smtp::import:smtplib#1",
        "workers/mailer.py::direct_smtp::call:SMTP_SSL#1",
        "workers/mailer.py::direct_smtp::call:sendmail#1",
    ):
        assert f"NEW finding {needle}" in err


def test_planted_inline_copy_fails(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "tree/scripts/oneoff.py").write_text(
        "def go(delivery, p):\n"
        "    delivery.send_email(\n"
        "        recipient=p.email, subject=f'Hi {p.name}, welcome aboard'\n"
        "    )\n"
    )
    _fails_with(tree, capsys, "NEW finding scripts/oneoff.py::inline_copy::go#1")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "class EmailOutbox:\n    pass\n",
            ["services/x.py::outbox_writer::EmailOutbox#1"],
        ),
        (
            "class Row:\n    __tablename__ = 'mail_outbox'\n",
            ["services/x.py::outbox_writer::Row#1"],
        ),
        (
            "def enqueue_email(db):\n    pass\n",
            ["services/x.py::outbox_writer::enqueue_email#1"],
        ),
        (
            "Q = 'INSERT INTO email_outbox (id) VALUES (1)'\n",
            ["services/x.py::outbox_writer::sql:<module>#1"],
        ),
        ("import smtplib\n", ["services/x.py::direct_smtp::import:smtplib#1"]),
        (
            "from aiosmtplib import send\n",
            ["services/x.py::direct_smtp::import:aiosmtplib#1"],
        ),
        ("import resend\n", ["services/x.py::direct_smtp::import:resend#1"]),
        (
            "def f(smtplib):\n    smtplib.SMTP('h')\n",
            ["services/x.py::direct_smtp::call:SMTP#1"],
        ),
        (
            "def f(b):\n    b.client('ses')\n",
            ["services/x.py::direct_smtp::call:boto3.ses#1"],
        ),
        (
            "def f(socket):\n    socket.create_connection(('h', 587))\n",
            ["services/x.py::direct_smtp::call:socket#1"],
        ),
        (
            "def f():\n    send_email(subject='Your invoice is ready now')\n",
            ["services/x.py::inline_copy::f#1"],
        ),
        (
            "def f(n):\n    send_email(body=f'hi {n}, welcome')\n",
            ["services/x.py::inline_copy::f#1"],
        ),
        (
            "def f():\n    b = '<p>x</p>'\n    send_email(html=b)\n",
            ["services/x.py::inline_copy::f#1"],
        ),
        (
            "def r():\n    return '<p>hi</p>'\n\ndef f():\n    send_email(html=r())\n",
            ["services/x.py::inline_copy::f#1"],
        ),
        (
            "def f(x):\n    d = {'subject': f'Hello {x}, news', 'to': x}\n"
            "    send_email(**d)\n",
            ["services/x.py::inline_copy::f#1"],
        ),
        (
            "def f(x):\n    d = {}\n    d['body'] = '<b>hi</b>'\n    send_email(**d)\n",
            ["services/x.py::inline_copy::f#1"],
        ),
        (
            "def f(x):\n    h = '<tr>'\n    for r in x:\n        h += f'<td>{r}</td>'\n"
            "    send_email(html_body=h)\n",
            ["services/x.py::inline_copy::f#1"],
        ),
    ],
)
def test_each_detector_finds_its_shape_with_its_id(
    source: str, expected: list[str]
) -> None:
    assert _ids(source) == expected


def test_renderer_imported_from_another_module_is_resolved() -> None:
    sources = {
        "services/render.py": (
            "def mail_for(t):\n    return ('Hello there', '<p>' + t + '</p>')\n"
        ),
        "services/a.py": (
            "def f(db):\n    from app.services.render import mail_for\n"
            "    s, h = mail_for('x')\n    enqueue_email(db, subject=s, html_body=h)\n"
        ),
        "services/b.py": (
            "from .render import mail_for\n\n"
            "def g(db):\n    s, h = mail_for('x')\n    enqueue_email(db, subject=s)\n"
        ),
        "services/c.py": (
            "from app.services import render\n\n"
            "def h(db):\n    enqueue_email(db, subject=render.mail_for('x'))\n"
        ),
    }
    assert _scan(sources) == [
        "services/a.py::inline_copy::f#1",
        "services/b.py::inline_copy::g#1",
        "services/c.py::inline_copy::h#1",
    ]


@pytest.mark.parametrize(
    "source",
    [
        # kernel event outbox is allowed: the bare word is not enough
        "class Outbox:\n    __tablename__ = 'outbox'\n",
        "def enqueue_event(db):\n    pass\n",
        # template slug + variables: no copy
        "def f(d, p):\n"
        "    d.send_email(template='welcome', recipient=p.email, variables={})\n",
        "def f(d, t):\n    d.send_email(subject=t.subject, body=t.body)\n",
        "def f(d):\n    d.send_email(kind='score_pass')\n",
        # building a message is not sending
        "from email.message import EmailMessage\n",
        "def f(socket):\n    socket.create_connection(('h', 443))\n",
        # F4: docstrings are not copy (renderer or SQL)
        "def r():\n    '''Return the Subject line: 'Your invoice is ready now'.'''\n"
        "    return load()\n\n"
        "def f(d):\n    d.send_email(subject=r())\n",
        "def f():\n    '''INSERT INTO email_outbox is forbidden.'''\n",
        # F4: log and raise messages are not copy
        "def r(log):\n    log.info('Sending the welcome email to the user')\n"
        "    raise ValueError('The message could not be built')\n\n"
        "def f(d, log):\n    d.send_email(subject=r(log))\n",
        # F4: an incidental literal in a call is not copy
        "def f(d, to):\n    d.send_email(recipient=', '.join(to))\n",
        "def f(d, p):\n    d.send_email(recipient=p.replace('\\n', ' '))\n",
        "def f(d, p):\n    d.send_email(recipient=f'{p.name} <{p.email}>')\n",
        "def f(d, p):\n    d.send_email(recipient=p.email.strip().lower())\n",
        # a literal that never reaches a sink is not copy
        "def f(d):\n"
        "    x = 'Your invoice is ready now'\n"
        "    d.send_email(template=x2)\n",
    ],
)
def test_false_positive_guards(source: str) -> None:
    assert _ids(source) == []


def test_documented_blind_spots_are_stated_and_really_not_detected() -> None:
    doc = ratchet.__doc__ or ""
    assert "does NOT detect; reviewed 2026-11-18" in doc
    for phrase in ("HTTP APIs", "not named like", "notify wrappers", "renamed"):
        assert phrase in doc
    # each stated blind spot is genuinely a miss today; if a detector improves,
    # this test fails and the list must be updated (and the review date moved).
    assert (
        _ids(
            "def f(h):\n"
            "    h.post('https://mail.example.test/v1', json={'subject': 'Hello'})\n"
        )
        == []
    )
    assert _ids("class JobQueue:\n    __tablename__ = 'job_queue'\n") == []
    assert _ids("def push_mail(db):\n    pass\n") == []
    assert (
        _ids("def f(n):\n    notify(n, body='Hello there, your update is ready')\n")
        == []
    )


# -- two-directional backlog ---------------------------------------------------


def test_clean_fixture_passes(tree: Path) -> None:
    assert _run(tree) == 0


def test_adding_a_finding_fails(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tree / "tree/services/extra.py").write_text(
        "def go(db, p):\n    enqueue_email(db, subject=f'Hello {p}, a new notice')\n"
    )
    _fails_with(tree, capsys, "NEW finding services/extra.py::inline_copy::go#1")


def test_removing_a_backlogged_finding_without_lowering_fails(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "tree/services/hr_digest.py").unlink()
    _fails_with(
        tree,
        capsys,
        "LISTED finding gone services/hr_digest.py::inline_copy::send_hr_report#1",
    )


def test_lowered_backlog_passes(tree: Path) -> None:
    (tree / "tree/services/hr_digest.py").unlink()
    data = _backlog(tree)
    data["findings"] = [i for i in data["findings"] if "hr_digest" not in i]
    _save(tree, data)
    assert _run(tree) == 0


def test_listing_without_a_finding_fails(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _backlog(tree)
    data["findings"].append("services/ghost.py::inline_copy::ghost#1")
    _save(tree, data)
    _fails_with(
        tree, capsys, "LISTED finding gone services/ghost.py::inline_copy::ghost#1"
    )


def test_a_second_finding_in_a_listed_function_is_new(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tree / "tree/services/hr_digest.py"
    path.write_text(
        path.read_text() + "\n\ndef send_hr_report(db, tenant_id, person):\n"
        "    enqueue_email(db, subject='Second copy in the same place now')\n"
    )
    _fails_with(
        tree, capsys, "NEW finding services/hr_digest.py::inline_copy::send_hr_report#2"
    )


def test_identity_has_no_line_numbers(tree: Path) -> None:
    path = tree / "tree/services/hr_digest.py"
    path.write_text("# shifted\n\n\n" + path.read_text())
    assert _run(tree) == 0


# -- --write-backlog is remove-only (F6) --------------------------------------------


def test_write_backlog_can_lower(tree: Path) -> None:
    (tree / "tree/services/hr_digest.py").unlink()
    assert ratchet.run(tree / "config.json", tree / "backlog.json", write=True) == 0
    assert _backlog(tree)["total"] == 30
    assert _run(tree) == 0


def test_write_backlog_refuses_to_add(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "tree/services/extra.py").write_text(
        "def go(db, p):\n    enqueue_email(db, subject=f'Hello {p}, a new notice')\n"
    )
    before = (tree / "backlog.json").read_text()
    capsys.readouterr()
    assert ratchet.run(tree / "config.json", tree / "backlog.json", write=True) == 1
    assert (
        "refusing to ADD finding services/extra.py::inline_copy::go#1"
        in capsys.readouterr().err
    )
    assert (tree / "backlog.json").read_text() == before


def test_write_backlog_adds_only_with_the_explicit_flag(tree: Path) -> None:
    (tree / "tree/services/extra.py").write_text(
        "def go(db, p):\n    enqueue_email(db, subject=f'Hello {p}, a new notice')\n"
    )
    assert (
        ratchet.run(
            tree / "config.json",
            tree / "backlog.json",
            write=True,
            allow_additions=True,
        )
        == 0
    )
    assert _backlog(tree)["total"] == 32


def test_write_backlog_refuses_to_seed_without_a_backlog(tree: Path) -> None:
    (tree / "backlog.json").unlink()
    assert ratchet.run(tree / "config.json", tree / "backlog.json", write=True) == 1


# -- coverage and exemption premises --


def test_a_family_root_that_vanishes_fails(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.rmtree(tree / "tree/workers")
    _fails_with(tree, capsys, "family 'workers' root 'workers' does not exist")


def test_files_outside_every_family_are_still_scanned(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "tree/misc").mkdir()
    (tree / "tree/misc/x.py").write_text("import smtplib\n")
    _fails_with(tree, capsys, "NEW finding misc/x.py::direct_smtp::import:smtplib#1")


def test_an_entry_must_match_a_file_state_a_premise_and_list_kinds(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tree)
    config["allowed"] = [
        {"glob": "nothing/**", "kinds": ["direct_smtp"], "premise": "p"}
    ]
    _save_config(tree, config)
    _fails_with(tree, capsys, "'nothing/**' matches no file")
    config["allowed"] = [
        {"glob": "services/email_outbox.py", "kinds": ["outbox_writer"], "premise": ""}
    ]
    _save_config(tree, config)
    _fails_with(tree, capsys, "states no premise")
    config["allowed"] = [{"glob": "services/email_outbox.py", "premise": "p"}]
    _save_config(tree, config)
    _fails_with(tree, capsys, "must list its `kinds` explicitly")


def test_an_optional_entry_may_match_nothing(tree: Path) -> None:
    config = _config(tree)
    config["exclude"] = [
        {"glob": ".venv/**", "kinds": ["direct_smtp"], "premise": "p", "optional": True}
    ]
    _save_config(tree, config)
    assert _run(tree) == 0


def test_an_owner_hides_only_the_kinds_it_lists(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tree)
    config["allowed"] = [
        {"glob": "services/email*.py", "kinds": ["direct_smtp"], "premise": "adapter"}
    ]
    _save_config(tree, config)
    data = _backlog(tree)
    data["findings"] = [i for i in data["findings"] if "::direct_smtp::" not in i]
    _save(tree, data)
    # the 3 smtp findings are hidden; the row-1 inline copy in email.py is NOT
    assert _run(tree) == 0
    data["findings"] = [i for i in data["findings"] if "_queue_first_pass" not in i]
    _save(tree, data)
    _fails_with(
        tree,
        capsys,
        "NEW finding services/email.py::inline_copy::_queue_first_pass_intents#1",
    )


def test_globs_are_path_segment_safe() -> None:
    g = ratchet.glob_match
    assert g(
        "packages/dotmac-delivery-smtp/src/a/b.py",
        "packages/dotmac-delivery-smtp/src/**",
    )
    assert not g(
        "packages/dotmac-delivery-smtp/src/a/b.py", "packages/dotmac-delivery-*"
    )
    assert not g(
        "packages/dotmac-delivery-x/src/a.py", "packages/dotmac-delivery-*/src"
    )
    assert g(
        "packages/dotmac-delivery-x/src/a.py", "packages/dotmac-delivery-*/src/*.py"
    )
    assert not g(
        "packages/dotmac-delivery-x/src/a/b.py", "packages/dotmac-delivery-*/src/*.py"
    )
    assert g("a/tests/b.py", "**/tests/**") and g("tests/b.py", "**/tests/**")
    assert not g("a/mytests/b.py", "**/tests/**")


def test_exclusions_are_config_globs_not_directory_names(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A directory called tests/ or migrations/ is scanned unless config says so."""
    (tree / "tree/tests").mkdir()
    (tree / "tree/tests/t.py").write_text("import smtplib\n")
    _fails_with(tree, capsys, "NEW finding tests/t.py::direct_smtp::import:smtplib#1")
    config = _config(tree)
    config["exclude"] = [
        {
            "glob": "tests/**",
            "kinds": ["direct_smtp", "outbox_writer", "inline_copy"],
            "premise": "tests",
        }
    ]
    _save_config(tree, config)
    assert _run(tree) == 0


def test_migrations_are_still_scanned_for_smtp_and_copy(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "tree/migrations").mkdir()
    (tree / "tree/migrations/m1.py").write_text(
        "import smtplib\n\n\ndef upgrade(op):\n    op.create_table('email_outbox')\n"
    )
    config = _config(tree)
    config["exclude"] = [
        {"glob": "migrations/**", "kinds": ["outbox_writer"], "premise": "DDL history"}
    ]
    _save_config(tree, config)
    capsys.readouterr()
    assert _run(tree) == 1
    err = capsys.readouterr().err
    assert "migrations/m1.py::direct_smtp::import:smtplib#1" in err
    assert "outbox_writer" not in err.split("migrations/m1.py")[1]


# -- pinned exclusions, absolute imports, base-ref, blind spots -------------------


def test_starter_excludes_are_exactly_the_pinned_list() -> None:
    config = json.loads(STARTER_CONFIG.read_text())
    actual = {(e["glob"], tuple(sorted(e["kinds"]))) for e in config["exclude"]}
    assert len(actual) == len(config["exclude"]), "duplicate exclusion"
    assert actual == EXPECTED_EXCLUDES


def test_an_all_kinds_exclusion_must_name_a_non_runtime_segment(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The architect's probe: a broad provider.py exclusion hid real findings."""
    config = _config(tree)
    config["exclude"] = [
        {
            "glob": "services/**/email*.py",
            "kinds": ["outbox_writer", "direct_smtp", "inline_copy"],
            "premise": "looks innocent",
        }
    ]
    _save_config(tree, config)
    _fails_with(tree, capsys, "hides every kind but names no non-runtime segment")
    # a partial-kind exclusion of the same glob is a different (reviewable) shape
    config["exclude"][0]["kinds"] = ["direct_smtp"]
    _save_config(tree, config)
    capsys.readouterr()
    assert not any(
        "non-runtime" in p
        for p in ratchet._validate_entries(config["exclude"], "exclusion")
    )
    # and a non-runtime tree may hide everything
    config["exclude"] = [
        {
            "glob": "tests/**",
            "kinds": ["outbox_writer", "direct_smtp", "inline_copy"],
            "premise": "tests",
        }
    ]
    assert not ratchet._validate_entries(config["exclude"], "exclusion")


def test_absolute_imports_resolve_in_the_packages_src_layout() -> None:
    sources = {
        "packages/dotmac-x/src/dotmac_x/render.py": (
            "def mail_for(t):\n    return '<p>Hello there</p>'\n"
        ),
        # sibling module in the same package, absolute import
        "packages/dotmac-x/src/dotmac_x/a.py": (
            "from dotmac_x.render import mail_for\n\n"
            "def f(d):\n    d.send_email(html=mail_for('x'))\n"
        ),
        # relative import still works
        "packages/dotmac-x/src/dotmac_x/b.py": (
            "from .render import mail_for\n\n"
            "def g(d):\n    d.send_email(html=mail_for('x'))\n"
        ),
        # the assembly importing a package module by its import name
        "app/features/c.py": (
            "from dotmac_x.render import mail_for\n\n"
            "def h(d):\n    d.send_email(html=mail_for('x'))\n"
        ),
    }
    assert _scan(sources) == [
        "app/features/c.py::inline_copy::h#1",
        "packages/dotmac-x/src/dotmac_x/a.py::inline_copy::f#1",
        "packages/dotmac-x/src/dotmac_x/b.py::inline_copy::g#1",
    ]


def _git(cwd: Path, *args: str) -> None:
    import subprocess

    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "-C",
            str(cwd),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.test",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def test_check_refuses_backlog_ids_not_in_the_base_ref(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(tree, "init", "-q")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "base")
    cfg, bl = tree / "config.json", tree / "backlog.json"
    # unchanged backlog passes against HEAD
    assert ratchet.run(cfg, bl, write=False, base_ref="HEAD") == 0
    # a finding appears AND is hand-added to the backlog: the plain check passes,
    # the base-ref check refuses
    (tree / "tree/services/extra.py").write_text(
        "def go(db, p):\n    enqueue_email(db, subject=f'Hello {p}, a new notice')\n"
    )
    data = _backlog(tree)
    data["findings"].append("services/extra.py::inline_copy::go#1")
    _save(tree, data)
    assert ratchet.run(cfg, bl, write=False) == 0
    capsys.readouterr()
    assert ratchet.run(cfg, bl, write=False, base_ref="HEAD") == 1
    assert (
        "backlog id services/extra.py::inline_copy::go#1 is not in the backlog at HEAD"
        in capsys.readouterr().err
    )
    # unusable refs and missing base backlogs fail closed
    assert (
        ratchet.main(["--config", str(cfg), "--backlog", str(bl), "--base-ref", "nope"])
        == 1
    )
    assert ratchet.run(cfg, bl, write=False, base_ref="--oops") == 1


def test_the_remaining_documented_blind_spots_are_really_missed() -> None:
    # copy loaded from a file / database row / environment / template engine
    assert (
        _ids(
            "def f(d, row, env, tpl):\n"
            "    d.send_email(subject=row.subject, html_body=env['BODY'])\n"
            "    d.send_email(subject=tpl.render('welcome'))\n"
            "    d.send_email(body=open('welcome.html').read())\n"
        )
        == []
    )
    # dynamic imports and renderers reached through a registry or getattr
    assert (
        _ids(
            "import importlib\n\n"
            "REGISTRY = {'w': lambda: '<p>Hello there friend</p>'}\n\n"
            "def f(d, name):\n"
            "    d.send_email(html=REGISTRY[name]())\n"
            "    d.send_email(html=getattr(importlib.import_module('r'), 'mail')())\n"
        )
        == []
    )
    # copy placed in email.message headers, with no sink or SMTP call after it
    assert (
        _ids(
            "from email.message import EmailMessage\n\n"
            "def f():\n"
            "    msg = EmailMessage()\n"
            "    msg['Subject'] = 'Your invoice is ready now'\n"
            "    msg.set_content('Hello there, your invoice is ready.')\n"
            "    return msg\n"
        )
        == []
    )
