"""The dialogue state machine, run over an injected channel.

The state is DERIVED from the prompt line actually received, never assumed from
what was sent. Pager prompts are answered with a space, a ``{ <cr>|... }:``
continuation with Enter only when ``<cr>`` is offered, and a confirmation only
when the profile's write template declares that exact question. A session that
cannot prove it is idle at a prompt is ``DIRTY``: it is closed, never reused,
and never has anything typed into it.

No socket, no credential, no database: the host authenticates and passes the
channel in.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from enum import Enum

from dotmac_device_cli.core.channel import Clock, MonotonicClock, ShellChannel
from dotmac_device_cli.core.classifier import ANSI
from dotmac_device_cli.core.profile import (
    CommandRejected,
    Confirmation,
    Profile,
    check_line,
    compile_regex,
)
from dotmac_device_cli.core.results import (
    Evidence,
    Ok,
    OutcomeUnknown,
    Reason,
    Unreadable,
)

EventSink = Callable[[str, Mapping[str, str | int]], None]
_CLEAN_PROMPT = re.compile(r"(?:^|\r?\n)\S*[>#]\s*$")
_TAIL = 400
_MAX_EVENT_TAIL = 120


class State(str, Enum):
    UNPRIMED = "unprimed"
    USER = "user"
    PRIVILEGED = "privileged"
    CONFIG = "config"
    INTERFACE = "interface"
    AWAIT_PAGER = "await_pager"
    AWAIT_CONTINUATION = "await_continuation"
    AWAIT_CONFIRM = "await_confirm"
    DIRTY = "dirty"
    CLOSED = "closed"


class LogoutResult(str, Enum):
    CLEAN = "clean"
    FORCED = "forced"
    SKIPPED_DIRTY = "skipped_dirty"


def _noop(_event: str, _fields: Mapping[str, str | int]) -> None:
    return None


def state_for_prompt_line(profile: Profile, line: str) -> State | None:
    """The mode a received prompt line proves, or None when it is not a prompt.

    Order matters: ``host(config)#`` also looks like a privileged prompt.
    """
    p = profile.prompts
    if compile_regex(p.config).search(line):
        return State.CONFIG
    if any(compile_regex(rx).search(line) for rx in p.interface.values()):
        return State.INTERFACE
    if compile_regex(p.privileged).search("\n" + line):
        return State.PRIVILEGED
    if compile_regex(p.user).search("\n" + line):
        return State.USER
    return None


class CliSession:
    """One authenticated interactive shell and its dialogue state."""

    def __init__(
        self,
        channel: ShellChannel,
        profile: Profile,
        clock: Clock,
        events: EventSink,
    ) -> None:
        self.channel = channel
        self.profile = profile
        self.clock = clock
        self._events = events
        self.state = State.UNPRIMED
        self._tail = ""
        self._clean = False
        self._eof = False
        self._prompt = profile.prompts.privileged

    @classmethod
    def attach(
        cls,
        channel: ShellChannel,
        profile: Profile,
        *,
        clock: Clock | None = None,
        events: EventSink | None = None,
    ) -> CliSession:
        return cls(channel, profile, clock or MonotonicClock(), events or _noop)

    # ── low level ────────────────────────────────────────────────────────────

    def _emit(self, event: str, **fields: str | int) -> None:
        self._events(event, fields)

    def _evidence(self, template_id: str, command: str, started: float) -> Evidence:
        return Evidence(
            profile_id=self.profile.id,
            profile_digest=self.profile.digest,
            template_id=template_id,
            rendered_command_redacted=command[: self.profile.send.max_line_length],
            duration_ms=int((self.clock.monotonic() - started) * 1000),
        )

    def _send(self, data: str) -> None:
        self._clean = False
        self.channel.send(data)

    def _recv(self) -> str | None:
        """Text, ``""`` when idle, ``None`` at EOF."""
        try:
            raw = self.channel.recv(4096)
        except TimeoutError:
            return ""
        if not raw:
            return None
        text = raw.decode("utf-8", errors="replace")
        self._tail = ANSI.sub("", self._tail + text)[-_TAIL:]
        self._clean = bool(_CLEAN_PROMPT.search(self._tail))
        return text

    def _read_until(self, pattern: str, timeout: float) -> str:
        compiled = compile_regex(pattern)
        buffer = ""
        deadline = self.clock.monotonic() + timeout
        self.channel.settimeout(0.8)
        while self.clock.monotonic() <= deadline:
            chunk = self._recv()
            if chunk is None:
                self.state = State.CLOSED
                self._eof = True
                return buffer
            if not chunk:
                if compiled.search(ANSI.sub("", buffer)):
                    return buffer
                self.clock.sleep(0.0)
                continue
            buffer += chunk
            if compiled.search(ANSI.sub("", buffer)):
                return buffer
        return buffer

    def _settle(self) -> None:
        self.clock.sleep(self.profile.send.settle_seconds)
        for _ in range(20):
            try:
                if not self.channel.recv_ready():
                    return
                self._recv()
            except Exception:
                return

    def _state_from_tail(self) -> State | None:
        lines = [ln for ln in self._tail.splitlines() if ln.strip()]
        return state_for_prompt_line(self.profile, lines[-1]) if lines else None

    def _derive_prompt(self, text: str) -> None:
        if not self.profile.prompts.derive_from_prompt_line:
            return
        for raw in reversed(ANSI.sub("", text).splitlines()):
            line = raw.strip()
            if not line or line == "#":  # bare '#' lines separate config sections
                continue
            if re.search(r"[>#]\s*$", line):
                self._prompt = rf"(?:^|\r?\n){re.escape(line)}\s*$"
                return

    # ── priming ──────────────────────────────────────────────────────────────

    def prepare(self) -> Ok[State] | Unreadable:
        """Reach the privileged prompt and learn its exact text."""
        started = self.clock.monotonic()
        evidence = self._evidence("prepare", "", started)
        p = self.profile.prompts
        if self.profile.send.drain_before_command:
            self._settle()
        any_prompt = f"{p.user}|{p.privileged}"
        out = ""
        for attempt in range(2):
            self._send("\n")
            out += self._read_until(any_prompt, 8.0 if attempt == 0 else 4.0)
            if self._state_from_tail() is not None:
                break
        state = self._state_from_tail()
        if state is None or self.state is State.CLOSED:
            self.state = State.DIRTY
            return Unreadable(Reason.PROMPT_TIMEOUT, "no prompt after login", evidence)
        if state is State.USER:
            check_line(
                self.profile.privilege.enter,
                max_length=self.profile.send.max_line_length,
            )
            self._send(f"{self.profile.privilege.enter}\n")
            out = self._read_until(p.privileged, 15.0)
            state = self._state_from_tail()
            if state is not State.PRIVILEGED:
                self.state = State.DIRTY
                return Unreadable(
                    Reason.INSUFFICIENT_PRIVILEGE,
                    "could not reach the privileged prompt",
                    evidence,
                )
        if state is not State.PRIVILEGED:
            self.state = State.DIRTY
            return Unreadable(
                Reason.UNRECOGNIZED_RESPONSE, f"login landed in {state.value}", evidence
            )
        self._derive_prompt(out)
        self.state = State.PRIVILEGED
        if self.profile.paging.off_supported:
            # Verified by reply, bounded; never assumed. Pager answering stays on.
            for _ in range(3):
                self._settle()
                res = self._execute(
                    "paging_off", self.profile.paging.off_command, (), write=False
                )
                if isinstance(res, Ok):
                    break
        return Ok(self.state, evidence)

    # ── commands ─────────────────────────────────────────────────────────────

    def run_read(self, template_id: str, command: str) -> Ok[str] | Unreadable:
        """Run one rendered READ command. Returns the cleaned-up device text."""
        res = self._execute(template_id, command, (), write=False)
        if isinstance(res, OutcomeUnknown):  # unreachable: only writes produce it
            return Unreadable(Reason.UNRECOGNIZED_RESPONSE, res.detail, res.evidence)
        return res

    def _execute(
        self,
        template_id: str,
        command: str,
        confirmations: tuple[Confirmation, ...],
        *,
        write: bool,
    ) -> Ok[str] | Unreadable | OutcomeUnknown:
        started = self.clock.monotonic()
        evidence = self._evidence(template_id, command, started)

        def fail(reason: Reason, detail: str) -> Unreadable | OutcomeUnknown:
            self.state = State.DIRTY
            ev = self._evidence(template_id, command, started)
            self._emit("command_failed", template=template_id, reason=reason.value)
            if write:
                return OutcomeUnknown(f"{reason.value}: {detail}", ev)
            return Unreadable(reason, detail, ev)

        if self.state not in (State.USER, State.PRIVILEGED):
            return Unreadable(
                Reason.UNRECOGNIZED_RESPONSE, f"session is {self.state.value}", evidence
            )
        try:
            check_line(command, max_length=self.profile.send.max_line_length)
        except CommandRejected as exc:
            return Unreadable(Reason.PARAMETER_ERROR, str(exc), evidence)

        p = self.profile.prompts
        tpl = self.profile.read_templates.get(
            template_id
        ) or self.profile.write_templates.get(template_id)
        timeout = tpl.max_seconds if tpl is not None else 30.0
        if self.profile.send.drain_before_command:
            self._settle()
        self._send(f"{command}\n")  # one atomic line: the editor drops spaces otherwise
        self.clock.sleep(self.profile.send.inter_command_delay)
        pending = f"{self._prompt}|{p.pager}|{p.continuation}|{p.confirm}"
        deadline = self.clock.monotonic() + timeout
        prior = self.state
        parts: list[str] = []
        cr_answers = 0
        answered: set[int] = set()
        while True:
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                return fail(Reason.PROMPT_TIMEOUT, f"no prompt within {timeout:g}s")
            chunk = self._read_until(pending, remaining)
            if self._eof:
                return fail(Reason.CONNECTION_FAILED, "channel closed mid-command")
            parts.append(chunk)
            stripped = ANSI.sub("", chunk).replace("\r", "")
            tail = stripped.rstrip()[-300:]
            if compile_regex(self._prompt).search(stripped):
                self.state = prior
                return Ok("".join(parts), self._evidence(template_id, command, started))
            if compile_regex(p.confirm).search(tail):
                self.state = State.AWAIT_CONFIRM
                rule = next(
                    (
                        (i, c)
                        for i, c in enumerate(confirmations)
                        if i not in answered and compile_regex(c.regex).search(tail)
                    ),
                    None,
                )
                if not write or rule is None:
                    return fail(
                        Reason.CONFIRMATION_REQUESTED,
                        "confirmation not on the allow-list",
                    )
                answered.add(rule[0])
                self._send(f"{rule[1].answer}\n")
                self.state = prior
                continue
            if compile_regex(p.pager).search(tail):
                self.state = State.AWAIT_PAGER
                self._send(" ")
                self.state = prior
                continue
            option = compile_regex(p.continuation).search(stripped)
            if option:
                self.state = State.AWAIT_CONTINUATION
                options = option.group(0).strip()
                cont = self.profile.continuation
                if cont.answer_cr_only_if_offered and "<cr>" not in options:
                    return fail(
                        Reason.NEEDS_ARGUMENTS, f"needs arguments: {options[:80]}"
                    )
                if cr_answers >= cont.max_cr_answers:
                    return fail(
                        Reason.NEEDS_ARGUMENTS, "still waiting after Enter answers"
                    )
                cr_answers += 1
                self._send("\n")
                self.state = prior
                continue
            return fail(Reason.PROMPT_TIMEOUT, "output ended before the prompt")

    # ── logout ───────────────────────────────────────────────────────────────

    def logout(self) -> LogoutResult:
        """Leave cleanly. Types nothing into a session that is not provably idle."""
        policy = self.profile.logout
        if self.state is State.CLOSED or self.channel.closed:
            self.state = State.CLOSED
            return LogoutResult.FORCED
        if self.state.value not in policy.only_from_states or not self._clean:
            self.state = State.CLOSED
            return LogoutResult.SKIPPED_DIRTY
        confirm = compile_regex(policy.confirm_regex)
        end = self.clock.monotonic() + policy.max_seconds
        try:
            for _ in range(policy.max_attempts):
                if self.channel.closed or self.clock.monotonic() >= end:
                    break
                self._send(f"{policy.command}\n")
                text, eof = self._read_logout(end)
                if eof:
                    self.state = State.CLOSED
                    return LogoutResult.CLEAN
                if confirm.search(text):
                    self._send(f"{policy.confirm_answer}\n")
                    self._read_logout(end)
                    self.state = State.CLOSED
                    return LogoutResult.CLEAN
                if "(y/n)" in text.lower():  # a question we may not answer
                    break
        except Exception:  # logout never raises; the session is closed either way
            self.state = State.CLOSED
        self.state = State.CLOSED
        return LogoutResult.FORCED

    def _read_logout(self, hard_end: float) -> tuple[str, bool]:
        text = ""
        limit = min(hard_end, self.clock.monotonic() + 1.5)
        self.channel.settimeout(0.3)
        while self.clock.monotonic() < limit:
            chunk = self._recv()
            if chunk is None:
                return text, True
            if not chunk:
                if _CLEAN_PROMPT.search(ANSI.sub("", text)):
                    return text, False
                self.clock.sleep(0.0)
                continue
            text += chunk
            plain = ANSI.sub("", text)
            if "(y/n)" in plain.lower() or _CLEAN_PROMPT.search(plain):
                return text, False
        return text, False
