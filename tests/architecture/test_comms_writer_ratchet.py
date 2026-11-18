"""Product-writer ratchet for tenant-facing messages (ADR-0006 5f, ADR-0018).

Three things are proved here:

* the Starter tree is held at its (empty) backlog;
* the synthetic Academy fixture reproduces the PLAN s1 census (26 send-site
  lines over 22 numbered rows, plus the 2 shared outbox writers = 28);
* sensitivity: a planted outbox writer, direct SMTP call and inline copy each
  fail, a new finding fails, a vanished listed finding fails, and a lowered
  backlog passes. A checker that finds nothing passes for the wrong reason, so
  every detector is also asserted to FIND something.
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


def _kinds(source: str, path: str = "services/x.py") -> list[str]:
    return [f.kind for f in ratchet.scan_source(path, source)]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A writable copy of the Academy fixture, plus its config and backlog."""
    dest = tmp_path / "fx"
    shutil.copytree(FIXTURE, dest)
    return dest


def _run(fx: Path) -> int:
    return ratchet.run(fx / "config.json", fx / "backlog.json", write=False)


def _backlog(fx: Path) -> dict:
    return json.loads((fx / "backlog.json").read_text())


def _save(fx: Path, data: dict) -> None:
    data["total"] = len(data["findings"])
    (fx / "backlog.json").write_text(json.dumps(data))


# -- Starter ----------------------------------------------------------------


def test_starter_tree_is_at_its_backlog() -> None:
    assert ratchet.run(STARTER_CONFIG, STARTER_BACKLOG, write=False) == 0


def test_starter_backlog_is_empty_and_says_so() -> None:
    data = json.loads(STARTER_BACKLOG.read_text())
    assert data["findings"] == [] and data["total"] == 0


def test_starter_scan_actually_covers_entry_point_families() -> None:
    config = json.loads(STARTER_CONFIG.read_text())
    result = ratchet.scan_product(config, STARTER_CONFIG.parent)
    assert not result.problems
    for family in ("web_routes", "scripts", "modules_and_kernel"):
        assert result.families.get(family, 0) > 0, family


def test_starter_allowed_owners_state_premises_and_are_currently_unused() -> None:
    """Each owner glob matches a file and states a premise (the scan refuses
    otherwise). Today the owners carry no finding even when scanned, so the
    allowance is headroom, not a hiding place: this proves it by scanning
    with the allowance removed."""
    config = json.loads(STARTER_CONFIG.read_text())
    for entry in config["allowed"]:
        assert entry["premise"]
    config["allowed"] = []
    result = ratchet.scan_product(config, STARTER_CONFIG.parent)
    assert not result.problems
    assert result.ids == []


# -- Academy census ----------------------------------------------------------


def test_academy_fixture_reproduces_the_census() -> None:
    config = json.loads((FIXTURE / "config.json").read_text())
    result = ratchet.scan_product(config, FIXTURE)
    census = json.loads((FIXTURE / "census.json").read_text())["map"]
    assert not result.problems
    assert len(result.ids) == 28
    assert set(result.ids) == set(census)
    rows = {v for v in census.values() if not v.startswith("shared writer")}
    assert len({"".join(c for c in r if c.isdigit()) for r in rows}) == 22
    assert len(rows) == 26
    assert ratchet.load_backlog(FIXTURE / "backlog.json") == sorted(result.ids)
    assert _run_in_place() == 0


def _run_in_place() -> int:
    return ratchet.run(FIXTURE / "config.json", FIXTURE / "backlog.json", write=False)


def test_academy_fixture_covers_every_family() -> None:
    config = json.loads((FIXTURE / "config.json").read_text())
    result = ratchet.scan_product(config, FIXTURE)
    for family in config["families"]:
        assert result.families.get(family, 0) > 0
    # The clean worker and script files are scanned and produce nothing.
    assert not [i for i in result.ids if i.startswith(("workers/", "scripts/"))]


# -- planted detectors (each must fail the ratchet) ---------------------------


def test_planted_outbox_writer_fails(tree: Path) -> None:
    (tree / "tree/models/sms_outbox.py").write_text(
        "class SmsOutbox:\n    __tablename__ = 'sms_outbox'\n"
    )
    assert _run(tree) == 1


def test_planted_direct_smtp_fails(tree: Path) -> None:
    (tree / "tree/workers/mailer.py").write_text(
        "import smtplib\n\n\ndef go():\n"
        "    with smtplib.SMTP_SSL('mail.example.test') as s:\n"
        "        s.sendmail('a', 'b', 'c')\n"
    )
    assert _run(tree) == 1


def test_planted_inline_copy_fails(tree: Path) -> None:
    (tree / "tree/scripts/oneoff.py").write_text(
        "def go(delivery, p):\n"
        "    delivery.send_email(\n"
        "        recipient=p.email, subject=f'Hi {p.name}, welcome aboard'\n"
        "    )\n"
    )
    assert _run(tree) == 1


@pytest.mark.parametrize(
    ("source", "kinds"),
    [
        ("class EmailOutbox:\n    pass\n", ["outbox_writer"]),
        ("class Row:\n    __tablename__ = 'mail_outbox'\n", ["outbox_writer"]),
        ("def enqueue_email(db):\n    pass\n", ["outbox_writer"]),
        ("Q = 'INSERT INTO email_outbox (id) VALUES (1)'\n", ["outbox_writer"]),
        ("import smtplib\n", ["direct_smtp"]),
        ("from aiosmtplib import send\n", ["direct_smtp"]),
        ("import resend\n", ["direct_smtp"]),
        ("def f(smtplib):\n    smtplib.SMTP('h')\n", ["direct_smtp"]),
        ("def f(b):\n    b.client('ses')\n", ["direct_smtp"]),
        ("def f(socket):\n    socket.create_connection(('h', 587))\n", ["direct_smtp"]),
        (
            "def f():\n    send_email(subject='Your invoice is ready now')\n",
            ["inline_copy"],
        ),
        ("def f(n):\n    send_email(body=f'hi {n}')\n", ["inline_copy"]),
        ("def f():\n    b = '<p>x</p>'\n    send_email(html=b)\n", ["inline_copy"]),
        (
            "def r():\n    return '<p>hi</p>'\n\ndef f():\n    send_email(html=r())\n",
            ["inline_copy"],
        ),
    ],
)
def test_each_detector_finds_its_shape(source: str, kinds: list[str]) -> None:
    assert _kinds(source) == kinds


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
        # one-word literal that is not a copy keyword
        "def f(d):\n    d.send_email(kind='score_pass')\n",
        # building a message is not sending
        "from email.message import EmailMessage\n",
        # a non-mail port
        "def f(socket):\n    socket.create_connection(('h', 443))\n",
    ],
)
def test_false_positive_guards(source: str) -> None:
    assert _kinds(source) == []


# -- two-directional backlog ---------------------------------------------------


def test_clean_fixture_passes(tree: Path) -> None:
    assert _run(tree) == 0


def test_adding_a_finding_fails(tree: Path) -> None:
    (tree / "tree/services/extra.py").write_text(
        "def go(db, p):\n    enqueue_email(db, subject=f'Hello {p}, a new notice')\n"
    )
    assert _run(tree) == 1


def test_removing_a_backlogged_finding_without_lowering_fails(tree: Path) -> None:
    (tree / "tree/services/hr_digest.py").unlink()
    assert _run(tree) == 1


def test_lowered_backlog_passes(tree: Path) -> None:
    (tree / "tree/services/hr_digest.py").unlink()
    data = _backlog(tree)
    data["findings"] = [i for i in data["findings"] if "hr_digest" not in i]
    _save(tree, data)
    assert _run(tree) == 0


def test_listing_without_a_finding_fails_even_if_lowered_elsewhere(tree: Path) -> None:
    data = _backlog(tree)
    data["findings"].append("services/ghost.py::inline_copy::ghost#1")
    _save(tree, data)
    assert _run(tree) == 1


def test_a_second_finding_in_a_listed_function_is_new(tree: Path) -> None:
    path = tree / "tree/services/hr_digest.py"
    path.write_text(
        path.read_text() + "\n\ndef send_hr_report(db, tenant_id, person):\n"
        "    enqueue_email(db, subject='Second copy in the same place now')\n"
    )
    assert _run(tree) == 1


def test_identity_has_no_line_numbers(tree: Path) -> None:
    path = tree / "tree/services/hr_digest.py"
    path.write_text("# shifted\n\n\n" + path.read_text())
    assert _run(tree) == 0


def test_write_backlog_round_trips(tree: Path) -> None:
    (tree / "tree/services/hr_digest.py").unlink()
    assert ratchet.run(tree / "config.json", tree / "backlog.json", write=True) == 0
    assert _backlog(tree)["total"] == 27
    assert _run(tree) == 0


# -- coverage premises ----------------------------------------------------------


def test_a_family_root_that_vanishes_fails(tree: Path) -> None:
    shutil.rmtree(tree / "tree/workers")
    assert _run(tree) == 1


def test_a_files_outside_every_family_are_still_scanned(tree: Path) -> None:
    (tree / "tree/misc").mkdir()
    (tree / "tree/misc/x.py").write_text("import smtplib\n")
    assert _run(tree) == 1


def test_allowed_owner_must_match_a_file_and_state_a_premise(tree: Path) -> None:
    config = json.loads((tree / "config.json").read_text())
    config["allowed"] = [{"glob": "nothing/**", "premise": "p"}]
    (tree / "config.json").write_text(json.dumps(config))
    assert _run(tree) == 1
    config["allowed"] = [{"glob": "services/email_outbox.py", "premise": ""}]
    (tree / "config.json").write_text(json.dumps(config))
    assert _run(tree) == 1


def test_allowed_owner_suppresses_its_own_findings(tree: Path) -> None:
    config = json.loads((tree / "config.json").read_text())
    config["allowed"] = [
        {"glob": "services/email_outbox.py", "premise": "stands in for kernel delivery"}
    ]
    (tree / "config.json").write_text(json.dumps(config))
    data = _backlog(tree)
    data["findings"] = [
        i for i in data["findings"] if "services/email_outbox.py" not in i
    ]
    _save(tree, data)
    assert _run(tree) == 0
