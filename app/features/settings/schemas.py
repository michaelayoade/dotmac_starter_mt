"""Pydantic schemas for the tenant-scoped settings admin API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SettingOut(BaseModel):
    domain: str
    key: str
    value: Any
    value_type: str
    label: str | None
    description: str | None
    is_secret: bool
    # The scope KIND that supplied the value, or "env"/"default". Not a closed
    # Literal any more: a deployment may declare its own levels (site, region),
    # and the API should report the one that actually won.
    source: str


class SettingUpdate(BaseModel):
    value: Any
