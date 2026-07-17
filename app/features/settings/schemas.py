"""Pydantic schemas for the tenant-scoped settings admin API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class SettingOut(BaseModel):
    domain: str
    key: str
    value: Any
    value_type: str
    label: str | None
    is_secret: bool
    source: Literal["tenant", "platform", "default"]


class SettingUpdate(BaseModel):
    value: Any
