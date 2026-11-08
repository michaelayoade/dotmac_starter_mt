"""Exact (zero-variance) closure measures for fixture manifests.

A fixture manifest with no variance closes exactly when every memo basis equals
what the import transfers. These helpers derive that from the manifest itself so
a test that does not exercise variances still passes the ST-4c closure checks
without weakening them.
"""

from __future__ import annotations

from dataclasses import replace

from dotmac_billing.opening_receivable_contracts import OpeningManifestV2
from dotmac_billing.opening_variance_contracts import OpeningPartitionMeasuresV1

DIGEST = "d" * 64
RULES = "e" * 64


def measures_for(manifest: OpeningManifestV2) -> tuple[OpeningPartitionMeasuresV1, ...]:
    rows = []
    for control in manifest.controls:
        part = control.partition
        t = sum(i.residual_minor for i in manifest.receivables if i.partition == part)
        disp = [d for d in manifest.dispositions if d.partition == part]
        eligible = sum(1 for d in disp if d.disposition == "eligible")
        blocking = sum(1 for d in disp if d.disposition == "blocking")
        excluded = len(disp) - eligible - blocking
        rows.append(
            OpeningPartitionMeasuresV1(
                part,
                t,
                t,
                t,
                None,
                len(disp),
                eligible,
                0,
                excluded,
                0,
                blocking,
            )
        )
    return tuple(rows)


def with_exact_measures(manifest: OpeningManifestV2) -> OpeningManifestV2:
    return replace(manifest, measures=measures_for(manifest))
