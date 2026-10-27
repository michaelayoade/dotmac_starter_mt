"""Profile registry and detection.

Resolution is by CONFIGURED vendor/model/firmware, never by guessing. Detection
reads the device's own version text and REPORTS agreement or mismatch; it never
switches the profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from importlib import resources

from dotmac_device_cli.core.layers import discover
from dotmac_device_cli.core.profile import (
    NoProfile,
    Profile,
    ProfileError,
    compile_regex,
)


class DetectOutcome(str, Enum):
    AGREES = "agrees"
    MISMATCH = "mismatch"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True, slots=True)
class Detection:
    outcome: DetectOutcome
    detected_model: str | None
    detected_firmware: str | None
    detail: str


class ProfileRegistry:
    def __init__(self, profiles: tuple[Profile, ...]) -> None:
        self._profiles = profiles

    @property
    def profiles(self) -> tuple[Profile, ...]:
        return self._profiles

    def get(self, profile_id: str) -> Profile | None:
        return next((p for p in self._profiles if p.id == profile_id), None)

    def resolve(self, vendor: str, model: str, firmware: str) -> Profile | NoProfile:
        """The single profile for a configured device, else ``NoProfile``."""
        hits = [p for p in self._profiles if p.matches(vendor, model, firmware)]
        if len(hits) > 1:
            raise ProfileError(f"ambiguous profiles: {sorted(p.id for p in hits)}")
        return hits[0] if hits else NoProfile(vendor, model, firmware)

    def detect(self, profile: Profile, version_text: str) -> Detection:
        """Compare the device's own version text with the profile it was given."""
        m = compile_regex(profile.detect.model).search(version_text)
        f = compile_regex(profile.detect.firmware).search(version_text)
        model = m.group(1) if m else None
        firmware = f.group(1) if f else None
        if model is None and firmware is None:
            return Detection(
                DetectOutcome.UNDETERMINED, None, None, "no model or firmware found"
            )
        problems: list[str] = []
        if model is not None and not any(
            re.search(p, model, re.IGNORECASE) for p in profile.model_patterns
        ):
            problems.append(
                f"model {model!r} does not match {list(profile.model_patterns)}"
            )
        if firmware is not None and not any(
            re.search(p, firmware) for p in profile.firmware_patterns
        ):
            problems.append(
                f"firmware {firmware!r} does not match "
                f"{list(profile.firmware_patterns)}"
            )
        if problems:
            return Detection(
                DetectOutcome.MISMATCH, model, firmware, "; ".join(problems)
            )
        outcome = (
            DetectOutcome.AGREES
            if model is not None and firmware is not None
            else DetectOutcome.UNDETERMINED
        )
        return Detection(outcome, model, firmware, "")


def builtin_registry() -> ProfileRegistry:
    """The profiles shipped inside the package."""
    return ProfileRegistry(discover(resources.files("dotmac_device_cli") / "profiles"))


__all__ = [
    "DetectOutcome",
    "Detection",
    "ProfileRegistry",
    "builtin_registry",
]
