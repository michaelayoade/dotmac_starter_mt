"""``before_flush`` write guard keyed on a command-scope contextvar.

Every module command runs inside :func:`command_scope`. A flush that would
insert, update or delete a module-mapped instance outside that scope is
refused, so "writers call module commands" is a runtime check, not a
convention. Installed once, process-wide, when ``dotmac_ipam`` is imported.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from sqlalchemy import event
from sqlalchemy.orm import Session

from dotmac_ipam.models import ALL_MODELS

_P = ParamSpec("_P")
_R = TypeVar("_R")

_depth: ContextVar[int] = ContextVar("dotmac_ipam_command_depth", default=0)
_GUARDED = tuple(ALL_MODELS)
_installed = False


class IpamWriteOutsideCommand(RuntimeError):
    """A module-mapped row was written outside a ``dotmac_ipam`` command."""


@contextmanager
def command_scope() -> Iterator[None]:
    token = _depth.set(_depth.get() + 1)
    try:
        yield
    finally:
        _depth.reset(token)


def in_command_scope() -> bool:
    return _depth.get() > 0


def command(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Mark a service function as a module command (opens the write scope).

    A command always ends flushed: a pending module row must never survive its
    scope, or the guard would refuse it at some later, unrelated autoflush."""

    @wraps(function)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with command_scope():
            result = function(*args, **kwargs)
            session = kwargs.get("db", args[0] if args else None)
            if isinstance(session, Session):
                session.flush()
            return result

    return wrapper


def _refuse_foreign_writes(
    session: Session, flush_context: Any, instances: Any
) -> None:
    if in_command_scope():
        return
    offenders = sorted(
        {
            type(obj).__name__
            for obj in (*session.new, *session.dirty, *session.deleted)
            if isinstance(obj, _GUARDED)
            and (
                obj in session.new or obj in session.deleted or session.is_modified(obj)
            )
        }
    )
    if offenders:
        raise IpamWriteOutsideCommand(
            "dotmac_ipam rows may only be written by module commands; "
            f"refused writes to: {', '.join(offenders)}"
        )


def install_flush_guard() -> bool:
    """Install the guard on ``Session`` (idempotent). Returns True when added."""
    global _installed
    if _installed:
        return False
    event.listen(Session, "before_flush", _refuse_foreign_writes)
    _installed = True
    return True


def flush_guard_installed() -> bool:
    return _installed


install_flush_guard()

__all__ = [
    "IpamWriteOutsideCommand",
    "command",
    "command_scope",
    "flush_guard_installed",
    "in_command_scope",
    "install_flush_guard",
]
