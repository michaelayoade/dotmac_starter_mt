from __future__ import annotations

import json

from dotmac_runner_transport import cli, typed_sha256
from dotmac_runner_transport_github_actions import ADAPTER


def test_cli_emits_the_exact_resolved_policy_document(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "discover_adapter", lambda key: ADAPTER)
    assert (
        cli.main(
            [
                "--adapter",
                "github-actions",
                "--capability",
                "runner.control.v1",
                "--runner",
                "starter:2001:3128",
                "--proxy-service",
                "proxy",
                "--proxy-uid",
                "2010",
                "--nft-table",
                "dotmac_egress",
                "--nft-output-chain",
                "output",
                "--nft-before",
                "ct state established,related accept",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    bundle = json.loads((tmp_path / "bundle.json").read_text())
    policy_bytes = (tmp_path / "policy.json").read_bytes()
    assert typed_sha256(policy_bytes) == bundle["policy_digest"]
    assert bundle["policy_document_sha256"] == bundle["policy_digest"]
    assert bundle["adapter"] == {
        "key": "github-actions",
        "version": ADAPTER.manifest.version,
        "declaration_digest": ADAPTER.manifest.identity.declaration_digest,
        "snapshot_digest": ADAPTER.manifest.snapshot.semantic_sha256,
    }
    assert bundle["capabilities"] == ["runner.control.v1"]
