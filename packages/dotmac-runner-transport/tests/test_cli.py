from __future__ import annotations

import json
from dataclasses import dataclass

from dotmac_runner_transport import (
    ExactHost,
    ProviderDomainSnapshotV1,
    RunnerTransportAdapterManifest,
    RunnerTransportCapability,
    TransportEndpointV1,
    canonical_bytes,
    cli,
    typed_sha256,
)


@dataclass(frozen=True)
class _Adapter:
    manifest: RunnerTransportAdapterManifest


def _adapter() -> _Adapter:
    host = ExactHost("transport.invalid")
    return _Adapter(
        RunnerTransportAdapterManifest(
            key="fake-provider",
            version="1",
            capabilities=(RunnerTransportCapability.CONTROL,),
            endpoints=(TransportEndpointV1(RunnerTransportCapability.CONTROL, host),),
            snapshot=ProviderDomainSnapshotV1(
                "https://provider.invalid/meta",
                "2026-08-31T00:00:00Z",
                typed_sha256(canonical_bytes((host.value,))),
                "domains.exact",
                (host,),
            ),
        )
    )


def test_cli_emits_the_canonical_host_binding_document(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "discover_adapter", lambda _: _adapter())
    result = cli.main(
        [
            "--adapter",
            "fake-provider",
            "--capability",
            "runner.control.v1",
            "--runner",
            "starter:2001:3128",
            "--proxy-service",
            "squid",
            "--proxy-uid",
            "2010",
            "--nft-table",
            "dotmac_egress",
            "--nft-output-chain",
            "output",
            "--nft-before",
            'oifname "lo" accept',
            "--output",
            str(tmp_path),
        ]
    )
    assert result == 0
    binding_bytes = (tmp_path / "binding.json").read_bytes()
    binding = json.loads(binding_bytes)
    bundle = json.loads((tmp_path / "bundle.json").read_text(encoding="utf-8"))
    assert binding["schema"] == "HostRunnerTransportSpec.v1"
    assert binding["identities"] == [
        {
            "runner_name": "starter",
            "transport_port": 3128,
            "uid": 2001,
            "workload_port": None,
        }
    ]
    assert binding["proxy_identity"]["uid"] == 2010
    assert binding["nftables_binding"]["table"] == "dotmac_egress"
    assert bundle["binding_digest"] == typed_sha256(binding_bytes)
    assert bundle["binding_document_sha256"] == bundle["binding_digest"]
