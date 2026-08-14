"""Activation — the last place all three refusals are checked together.

Discovery already refused a duplicate key and an incompatible SPI, and the
manifest already knows which capabilities it declares. Activation checks all
three again, against the STORED installation rather than the live registry,
because that is the only point where the two can disagree:

* a distribution can be installed, upgraded or removed after discovery ran;
* the module itself can be upgraded, moving `CURRENT_SPI_VERSION` under a
  binding that was activated months ago;
* a binding row can name a capability the currently-installed version of the
  connector no longer declares.

Each of those is a change the operator did not make to the binding, which is
exactly why the binding cannot be trusted to still be valid.

Nothing here dispatches. Choosing among *enabled* bindings is
`dotmac_integration.selection`; this decides whether one may be enabled at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.models import CapabilityBinding, ConnectorInstallation
from dotmac_integration.spi import (
    CURRENT_SPI_VERSION,
    SpiRange,
    SpiVersion,
)

__all__ = ["ActivationRefused", "check_activation", "require_activatable"]


class ActivationRefused(RuntimeError):
    """A binding may not be enabled, with the reason stated."""


@dataclass(frozen=True, slots=True)
class ActivationCheck:
    """The verdict, with every reason rather than only the first.

    An operator fixing an activation wants the whole list: reporting one
    problem at a time turns a single edit into three round trips.
    """

    ok: bool
    reasons: tuple[str, ...]


def check_activation(
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
    registry: ConnectorRegistry,
    *,
    spi_version: SpiVersion = CURRENT_SPI_VERSION,
) -> ActivationCheck:
    reasons: list[str] = []

    try:
        manifest = registry.get(installation.connector_key)
    except Exception as exc:
        return ActivationCheck(
            False,
            (
                f"connector {installation.connector_key!r} is not installed in "
                f"this runtime: {exc}",
            ),
        )

    # 1. SPI compatibility, checked against the range STORED on the
    #    installation as well as the one the live distribution declares. They
    #    differ exactly when the distribution was upgraded underneath, which is
    #    the case worth catching.
    try:
        SpiRange.parse(installation.spi_range).require(spi_version)
    except Exception as exc:
        reasons.append(f"stored SPI range refuses this module: {exc}")
    try:
        manifest.spi_range.require(spi_version)
    except Exception as exc:
        reasons.append(f"installed connector refuses this module: {exc}")

    # 2. The capability must still be declared by the connector actually
    #    installed — not merely by whatever it declared when the row was made.
    if binding.capability_id not in manifest.capability_ids:
        reasons.append(
            f"connector {manifest.connector_key!r} v{manifest.version} does not "
            f"declare capability {binding.capability_id!r}; it declares "
            f"{sorted(manifest.capability_ids)}"
        )

    # 3. The installation itself has to be in a state that can serve anything.
    if installation.state in {"quarantined", "retired"}:
        reasons.append(
            f"installation is {installation.state} and cannot back an enabled "
            "binding"
        )

    return ActivationCheck(not reasons, tuple(reasons))


def require_activatable(
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
    registry: ConnectorRegistry,
    *,
    spi_version: SpiVersion = CURRENT_SPI_VERSION,
) -> None:
    """`check_activation`, raising. Use at the write."""
    verdict = check_activation(installation, binding, registry, spi_version=spi_version)
    if not verdict.ok:
        raise ActivationRefused(
            f"cannot enable {binding.capability_id!r} on "
            f"{installation.connector_key}/{installation.name}: "
            + "; ".join(verdict.reasons)
        )
