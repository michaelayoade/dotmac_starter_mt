"""Is what is RUNNING what was APPROVED?

Three digests answer it, and all three are needed because each hides a
different way of being wrong:

- **image** — the bytes. Catches a host that pulled a mutable tag, a role that
  survived a previous release, and a `docker compose up -d` run outside the
  deployment path (`dotmac_erp:scripts/deploy.sh:59-69` documents that one
  downgrading production by five weeks).
- **configuration** — the rendered assets. Catches the hand-edit. This is the
  drift recorded twice against a live staging host, where `scripts/deploy.sh`
  and `scripts/db_backup.sh` were traced to a commit that was not the deployed
  release and lacked every safeguard added since
  (`seabone-staging-dotmac-sub-deploy-landmines`, 2026-07-28 and 2026-08-04).
- **product manifest** — the composed modules. Catches a deployment running
  the approved image against a module set nobody approved, which the other two
  digests cannot see at all.

## Why an unknown is not a match

Every comparison has three outcomes, not two. `UNKNOWN` — the observation could
not be made — is kept apart from `MATCH` deliberately: an inability to read the
running digest reported as agreement is a drift check that goes green when the
thing it inspects disappears, which is the worst possible failure direction for
a monitor. ADR-0032's "unobserved is UNKNOWN, never ABSENT" applied here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from .spec import ProductDeploymentSpec


class Verdict(str, Enum):
    MATCH = "match"
    DRIFT = "drift"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Comparison:
    subject: str
    expected: str
    observed: str
    verdict: Verdict
    detail: str = ""

    def __str__(self) -> str:
        if self.verdict is Verdict.MATCH:
            return f"{self.subject}: match ({self.expected})"
        if self.verdict is Verdict.UNKNOWN:
            return f"{self.subject}: UNKNOWN — {self.detail or 'not observable'}"
        return (
            f"{self.subject}: DRIFT — approved {self.expected}, "
            f"observed {self.observed}"
        )


@dataclass(frozen=True, slots=True)
class DriftReport:
    product: str
    comparisons: tuple[Comparison, ...] = field(default_factory=tuple)

    @property
    def drifted(self) -> tuple[Comparison, ...]:
        return tuple(item for item in self.comparisons if item.verdict is Verdict.DRIFT)

    @property
    def unknown(self) -> tuple[Comparison, ...]:
        return tuple(
            item for item in self.comparisons if item.verdict is Verdict.UNKNOWN
        )

    @property
    def clean(self) -> bool:
        """Clean means every comparison MATCHED.

        An UNKNOWN is not clean. A check that cannot see its subject has not
        found agreement; it has found nothing, and reporting nothing as
        agreement is how a monitor goes green by breaking.
        """
        return bool(self.comparisons) and not self.drifted and not self.unknown

    def render(self) -> str:
        lines = [f"drift report for {self.product}"]
        lines.extend(f"  {item}" for item in self.comparisons)
        if self.clean:
            lines.append("  => running state matches the approved plan")
        elif self.drifted:
            lines.append(f"  => {len(self.drifted)} drift(s)")
        else:
            lines.append(
                f"  => {len(self.unknown)} comparison(s) could not be made; this is "
                "NOT a pass"
            )
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class Observation:
    """What the host reports right now.

    Every field is optional, and an absent one becomes `UNKNOWN` rather than
    being skipped. A drift check that quietly omits the comparison it could not
    make reports a shorter, cleaner-looking report the worse the situation
    gets.
    """

    role_image_digests: Mapping[str, str] = field(default_factory=dict)
    config_digests: Mapping[str, str] = field(default_factory=dict)
    manifest_digest: str = ""


def compare(
    spec: ProductDeploymentSpec,
    observation: Observation,
    *,
    approved_config_digests: Mapping[str, str],
    approved_image_digest: str = "",
    roles: Sequence[str] = (),
) -> DriftReport:
    """Compare a running deployment against the approved plan.

    ``approved_image_digest`` defaults to the descriptor's own, which is the
    common case: the descriptor IS the approved plan on the host. A fleet
    control plane that approved a different digest passes it explicitly, and
    the disagreement between the two is itself a drift worth surfacing — so it
    is reported rather than silently preferred.
    """
    expected_image = approved_image_digest or spec.image_digest
    comparisons: list[Comparison] = []

    if approved_image_digest and approved_image_digest != spec.image_digest:
        comparisons.append(
            Comparison(
                subject="descriptor-vs-approved-plan",
                expected=approved_image_digest,
                observed=spec.image_digest,
                verdict=Verdict.DRIFT,
                detail=(
                    "the descriptor on this host names a different digest from the "
                    "approved plan. The host's descriptor is not authoritative"
                ),
            )
        )

    checked_roles = tuple(roles) or tuple(
        role.code for role in spec.roles if role.replicas > 0
    )
    for role in checked_roles:
        observed = observation.role_image_digests.get(role, "")
        if not observed:
            comparisons.append(
                Comparison(
                    subject=f"image[{role}]",
                    expected=expected_image,
                    observed="",
                    verdict=Verdict.UNKNOWN,
                    detail="the role reported no running image digest",
                )
            )
            continue
        comparisons.append(
            Comparison(
                subject=f"image[{role}]",
                expected=expected_image,
                observed=observed,
                verdict=Verdict.MATCH if observed == expected_image else Verdict.DRIFT,
            )
        )

    for asset in sorted(approved_config_digests):
        expected = approved_config_digests[asset]
        observed = observation.config_digests.get(asset, "")
        if not observed:
            comparisons.append(
                Comparison(
                    subject=f"config[{asset}]",
                    expected=expected,
                    observed="",
                    verdict=Verdict.UNKNOWN,
                    detail="the asset was not readable on the host",
                )
            )
            continue
        comparisons.append(
            Comparison(
                subject=f"config[{asset}]",
                expected=expected,
                observed=observed,
                verdict=Verdict.MATCH if observed == expected else Verdict.DRIFT,
                detail=(
                    ""
                    if observed == expected
                    else "a rendered asset was edited on the host; re-render from "
                    "deploy/product.toml rather than editing the result"
                ),
            )
        )

    # An asset present on the host that the plan does not name at all. This is
    # the untracked-override case, and it is DRIFT rather than UNKNOWN: the
    # observation succeeded, and what it found is a file carrying configuration
    # nothing approved.
    for asset in sorted(set(observation.config_digests) - set(approved_config_digests)):
        comparisons.append(
            Comparison(
                subject=f"config[{asset}]",
                expected="(not in the approved plan)",
                observed=observation.config_digests[asset],
                verdict=Verdict.DRIFT,
                detail=(
                    "an asset the plan does not name is present on the host. A "
                    "host-only override is an undocumented manual step, and the "
                    "next render reverts it"
                ),
            )
        )

    if not observation.manifest_digest:
        comparisons.append(
            Comparison(
                subject="product-manifest",
                expected=spec.manifest_digest,
                observed="",
                verdict=Verdict.UNKNOWN,
                detail="the running deployment reported no product-manifest digest",
            )
        )
    else:
        comparisons.append(
            Comparison(
                subject="product-manifest",
                expected=spec.manifest_digest,
                observed=observation.manifest_digest,
                verdict=(
                    Verdict.MATCH
                    if observation.manifest_digest == spec.manifest_digest
                    else Verdict.DRIFT
                ),
                detail=(
                    ""
                    if observation.manifest_digest == spec.manifest_digest
                    else "the approved image is running against a module set nobody "
                    "approved; neither the image nor the config digest can see this"
                ),
            )
        )

    return DriftReport(product=spec.product, comparisons=tuple(comparisons))
