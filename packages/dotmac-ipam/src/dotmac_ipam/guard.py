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

from sqlalchemy import Delete, Insert, Update, event
from sqlalchemy.engine import Engine
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
def _command_scope() -> Iterator[None]:
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
        with _command_scope():
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


def _guarded_tables() -> frozenset[Any]:
    return frozenset(model.__table__ for model in _GUARDED)


def _refuse_dml_outside_command(clauseelement: Any) -> None:
    """Refuse INSERT/UPDATE/DELETE on a module table outside a command scope."""
    if in_command_scope():
        return
    if isinstance(clauseelement, Insert | Update | Delete):
        table = getattr(clauseelement, "table", None)
        if table in _guarded_tables():
            name = getattr(table, "name", table)
            raise IpamWriteOutsideCommand(
                "dotmac_ipam rows may only be written by module commands; "
                f"refused {type(clauseelement).__name__} on {name}"
            )


def _orm_execute_guard(execute_state: Any) -> None:
    if execute_state.is_insert or execute_state.is_update or execute_state.is_delete:
        _refuse_dml_outside_command(execute_state.statement)


def _engine_execute_guard(
    conn: Any, clauseelement: Any, multiparams: Any, params: Any, execution_options: Any
) -> None:
    _refuse_dml_outside_command(clauseelement)


def _guard_legacy_bulk(name: str) -> None:
    original = getattr(Session, name)

    @wraps(original)
    def guarded(self: Session, objects_or_mapper: Any, *args: Any, **kw: Any) -> Any:
        mapper = objects_or_mapper if name.endswith("_mappings") else None
        targets = [mapper] if mapper is not None else list(objects_or_mapper)
        for target in targets:
            cls = getattr(target, "class_", type(target))
            if (
                isinstance(cls, type)
                and issubclass(cls, _GUARDED)
                and not in_command_scope()
            ):
                raise IpamWriteOutsideCommand(
                    f"dotmac_ipam rows may only be written by module commands; "
                    f"refused Session.{name} on {cls.__name__}"
                )
        return original(self, objects_or_mapper, *args, **kw)

    setattr(Session, name, guarded)


def install_flush_guard() -> bool:
    """Install the guard on ``Session`` (idempotent). Returns True when added."""
    global _installed
    if _installed:
        return False
    event.listen(Session, "before_flush", _refuse_foreign_writes)
    # ORM bulk DML (db.execute(update/delete/insert(...))) bypasses the flush.
    event.listen(Session, "do_orm_execute", _orm_execute_guard)
    # Core DML on a module table (connection.execute(insert(table))).
    event.listen(Engine, "before_execute", _engine_execute_guard)
    # Legacy bulk_* Session APIs bypass both.
    for legacy in ("bulk_insert_mappings", "bulk_update_mappings", "bulk_save_objects"):
        _guard_legacy_bulk(legacy)
    _installed = True
    return True


def flush_guard_installed() -> bool:
    return _installed


install_flush_guard()

__all__ = [
    "IpamWriteOutsideCommand",
    "command",
    "flush_guard_installed",
    "install_flush_guard",
]
