"""The dialogue state machine over a scripted channel (no real I/O)."""

from __future__ import annotations

import pytest
from dotmac_device_cli import (
    CliSession,
    LogoutResult,
    Ok,
    OutcomeUnknown,
    Reason,
    State,
    Unreadable,
    builtin_registry,
    list_onts,
)
from dotmac_device_cli.core.profile import Confirmation
from dotmac_device_cli.testing import FakeClock, ReplayChannel

PROFILE = builtin_registry().get("huawei-ma5608t-v800r013")
assert PROFILE is not None
CMD = "display version"


def _session(**kw: object) -> tuple[CliSession, ReplayChannel]:
    clock = FakeClock()
    channel = ReplayChannel(clock, **kw)  # type: ignore[arg-type]
    return CliSession.attach(channel, PROFILE, clock=clock), channel


def test_prepare_from_the_user_prompt_enters_privileged_mode() -> None:
    clock = FakeClock()
    channel = ReplayChannel(
        clock,
        prompt="lab-olt>",
        replies={"enable\n": "enable\r\nlab-olt#"},
    )
    session = CliSession.attach(channel, PROFILE, clock=clock)
    result = session.prepare()
    assert isinstance(result, Ok) and session.state is State.PRIVILEGED
    assert channel.sent == ["\n", "enable\n"]


def test_prepare_refuses_when_the_privileged_prompt_never_comes() -> None:
    clock = FakeClock()
    channel = ReplayChannel(
        clock,
        prompt="lab-olt>",
        replies={"enable\n": "enable\r\n% Permission denied\r\nlab-olt>"},
    )
    session = CliSession.attach(channel, PROFILE, clock=clock)
    result = session.prepare()
    assert (
        isinstance(result, Unreadable)
        and result.reason is Reason.INSUFFICIENT_PRIVILEGE
    )
    assert session.state is State.DIRTY


def test_prepare_with_no_prompt_is_a_timeout() -> None:
    clock = FakeClock()
    channel = ReplayChannel(clock)
    channel._out.clear()
    channel.send = lambda data: len(data)  # type: ignore[method-assign]
    session = CliSession.attach(channel, PROFILE, clock=clock)
    result = session.prepare()
    assert isinstance(result, Unreadable) and result.reason is Reason.PROMPT_TIMEOUT


def test_the_privileged_prompt_is_derived_from_the_received_line() -> None:
    session, _ = _session(
        prompt="lab-olt#",
        replies={"display version\n": "display version\r\n  X\r\n#\r\nlab-olt#"},
    )
    assert isinstance(session.prepare(), Ok)
    # a bare '#' config separator inside the output does not end the read
    out = session.run_read("version", CMD)
    assert isinstance(out, Ok) and "X" in out.value


def test_a_session_that_is_not_primed_types_nothing() -> None:
    session, channel = _session()
    out = session.run_read("version", CMD)
    assert isinstance(out, Unreadable) and channel.sent == []


def test_an_invalid_line_is_rejected_not_sent() -> None:
    session, channel = _session()
    session.prepare()
    channel.sent.clear()
    out = session.run_read("version", "display version; reboot")
    assert isinstance(out, Unreadable) and out.reason is Reason.PARAMETER_ERROR
    assert channel.sent == []


def test_the_line_is_sent_atomically_with_its_newline() -> None:
    session, channel = _session(
        replies={"display version\n": "display version\r\nok\r\nlab-olt#"}
    )
    session.prepare()
    channel.sent.clear()
    session.run_read("version", CMD)
    assert channel.sent == ["display version\n"]


def test_read_never_answers_a_confirmation() -> None:
    session, channel = _session(
        replies={"display version\n": "display version\r\nContinue? (y/n)[n]:"}
    )
    session.prepare()
    channel.sent.clear()
    out = session.run_read("version", CMD)
    assert isinstance(out, Unreadable) and out.reason is Reason.CONFIRMATION_REQUESTED
    assert channel.sent == ["display version\n"] and session.state is State.DIRTY


def test_a_write_answers_only_the_exact_allow_listed_question() -> None:
    reply = "do thing\r\nAre you sure to apply? (y/n)[n]:" + "\r\nlab-olt#"
    session, channel = _session(replies={"do thing\n": reply})
    session.prepare()
    channel.sent.clear()
    allowed = (Confirmation(regex=r"Are you sure to apply\?", answer="y"),)
    out = session._execute("write_x", "do thing", allowed, write=True)
    assert isinstance(out, Ok)
    assert channel.sent == ["do thing\n", "y\n"] and session.state is State.PRIVILEGED


def test_a_write_with_any_other_question_is_outcome_unknown_and_dirty() -> None:
    reply = "do thing\r\nDelete everything? (y/n)[n]:"
    session, channel = _session(replies={"do thing\n": reply})
    session.prepare()
    channel.sent.clear()
    allowed = (Confirmation(regex=r"Are you sure to apply\?", answer="y"),)
    out = session._execute("write_x", "do thing", allowed, write=True)
    assert isinstance(out, OutcomeUnknown)
    assert channel.sent == ["do thing\n"] and session.state is State.DIRTY


def test_a_write_that_times_out_is_outcome_unknown_never_success() -> None:
    session, _ = _session(replies={"do thing\n": "do thing\r\nworking"})
    session.prepare()
    out = session._execute("write_x", "do thing", (), write=True)
    assert isinstance(out, OutcomeUnknown)


def test_continuation_without_enter_offered_is_needs_arguments() -> None:
    session, channel = _session(
        replies={CMD + "\n": CMD + "\r\n  { ontid<U><0,127> }:"}
    )
    session.prepare()
    channel.sent.clear()
    out = session.run_read("version", CMD)
    assert isinstance(out, Unreadable) and out.reason is Reason.NEEDS_ARGUMENTS
    assert channel.sent == [CMD + "\n"]  # Enter was NOT sent


def test_enter_is_answered_at_most_the_profile_limit() -> None:
    prompt = "  { <cr>||<K> }:"
    session, channel = _session(replies={CMD + "\n": CMD + "\r\n" + prompt * 5})
    session.prepare()
    channel.sent.clear()
    out = session.run_read("version", CMD)
    assert isinstance(out, Unreadable) and out.reason is Reason.NEEDS_ARGUMENTS
    assert channel.sent.count("\n") <= PROFILE.continuation.max_cr_answers


def test_logout_is_clean_and_answers_only_its_own_question() -> None:
    session, channel = _session(
        replies={"quit\n": "quit\r\n  Are you sure to log out? (y/n)[n]:"},
        eof_after_answer=True,
    )
    session.prepare()
    channel.sent.clear()
    assert session.logout() is LogoutResult.CLEAN
    assert channel.sent == ["quit\n", "y\n"]


def test_logout_refuses_a_foreign_question() -> None:
    session, channel = _session(replies={"quit\n": "quit\r\nDelete the ONT? (y/n)[n]:"})
    session.prepare()
    channel.sent.clear()
    assert session.logout() is LogoutResult.FORCED
    assert channel.sent == ["quit\n"]


def test_a_dirty_session_is_closed_without_typing_anything() -> None:
    session, channel = _session(replies={CMD + "\n": CMD + "\r\nContinue? (y/n)[n]:"})
    session.prepare()
    session.run_read("version", CMD)
    channel.sent.clear()
    assert session.logout() is LogoutResult.SKIPPED_DIRTY
    assert channel.sent == [] and session.state is State.CLOSED


def test_list_onts_over_a_prepared_session_end_to_end() -> None:
    from dotmac_device_cli.core.dialect import dialect_for

    dialect = dialect_for("huawei")
    assert dialect is not None
    session, _ = _session()
    session.prepare()
    out = list_onts(session, dialect.parse_ref("0/2/1"))
    assert isinstance(out, Unreadable)  # the scripted device has no reply for it
    assert out.reason is Reason.PROMPT_TIMEOUT


def test_a_closed_channel_mid_command_is_connection_failed() -> None:
    session, channel = _session()
    session.prepare()
    channel._closed = True
    out = session.run_read("version", CMD)
    assert isinstance(out, Unreadable) and out.reason is Reason.CONNECTION_FAILED


@pytest.mark.parametrize(
    "line, expected",
    [
        ("lab-olt>", State.USER),
        ("lab-olt#", State.PRIVILEGED),
        ("lab-olt(config)#", State.CONFIG),
        ("lab-olt(config-if-gpon-0/1)#", State.INTERFACE),
        ("---- More ( Press 'Q' to break ) ----", None),
    ],
)
def test_mode_is_derived_from_the_prompt_line(
    line: str, expected: State | None
) -> None:
    from dotmac_device_cli.core.session import state_for_prompt_line

    assert state_for_prompt_line(PROFILE, line) is expected
