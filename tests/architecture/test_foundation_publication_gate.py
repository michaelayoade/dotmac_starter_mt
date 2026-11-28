"""The Foundation 0.4.0a2 publication gate: selection, evidence, workflow shape.

Michael ruled (register `dotmac-debt-register`) that signed Gate 2 evidence
replaces the Lane 3 RehearsalReceipt for exactly one version. Every test here is
a sensitivity proof: each refusal is planted on its own against a manifest that
is otherwise accepted, and the accepting case is shown first, so a gate that
refused everything could not pass.

No key is enrolled in the repository. The tests generate throwaway keys in a
temporary directory and point the module at them; the shipped, EMPTY signer
file is asserted separately.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import foundation_publication_gate as gate  # noqa: E402
import lane3_offline_result  # noqa: E402

WORKFLOWS = REPO / ".github" / "workflows"
FACILITY = "dotmac-deployment-foundation"
VERSION = "0.4.0a2"
CANDIDATE_SHA256 = "ab" * 32
LANE3_NAMESPACE = "foundation-rehearsal-verification-v1@dotmac.io"
WAIVER_REF = 2155
COND_ID = "DR3_DR5_STAGING_DRILL"
WAIVER = {"register_ref": WAIVER_REF, "condition_id": COND_ID}
#: The test clock: after every item's recorded_at and the issue time, inside
#: the 24 h window the world's manifest carries.
NOW = datetime(2026, 11, 23, 12, 0, 0)
ISSUED = "2026-11-23T11:00:00Z"
EXPIRES = "2026-11-24T11:00:00Z"
BASELINE = gate.E3_BASELINE
ISOLATION_TEST = """\
import hashlib
import json
import pathlib


def test_deploy_bytes_equal_the_pre_change_commit():
    base = json.loads(
        pathlib.Path("tests/architecture/foundation_operator_job_baseline.json")
        .read_text()
    )
    run = pathlib.Path("engine/run.py").read_bytes()
    assert hashlib.sha256(run).hexdigest() == base["run_py"]
"""


# ── a throwaway world: keys, a git repo, and a manifest that is accepted ─────


def _run(args: list[str], *, cwd=None, data: bytes | None = None) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        args, cwd=cwd, input=data, capture_output=True, check=True
    ).stdout


def _git(repo: pathlib.Path, *args: str) -> str:
    return (
        _run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=repo,
        )
        .decode()
        .strip()
    )


def _commit(repo: pathlib.Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write(repo: pathlib.Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _baseline_text(base_commit: str, run_py: str) -> str:
    return json.dumps(
        {
            "base_commit": base_commit,
            "run_py": hashlib.sha256(run_py.encode()).hexdigest(),
        }
    )


class World:
    """Throwaway signer, three candidate source commits, and a signing helper."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.key = root / "key"
        _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)])
        self.blob = (root / "key.pub").read_text().split()[1]
        self.signers_line = (
            f'{gate.PRINCIPAL} namespaces="{gate.NAMESPACE}",'
            f'valid-after="20200101000000Z",valid-before="20990101000000Z" '
            f"ssh-ed25519 {self.blob}\n"
        )
        self.signers = root / "allowed_signers"
        self.revoked = root / "revoked_keys"
        self.signers.write_text(self.signers_line)
        self.revoked.write_text("")

        repo = root / "repo"
        repo.mkdir()
        self.repo = repo
        _git(repo, "init", "-q", "-b", "main")
        _write(repo, "engine/run.py", "one\n")
        self.base = _commit(repo, "pre-change")
        _git(repo, "checkout", "-q", "-b", "side")
        _write(repo, "side.txt", "x")
        side = _commit(repo, "divergent")
        _git(repo, "checkout", "-q", "main")
        _write(repo, BASELINE, _baseline_text(self.base, "one\n"))
        _write(repo, gate.E3_TEST, ISOLATION_TEST)
        self.good = _commit(repo, "candidate source")
        _write(repo, "engine/run.py", "two\n")
        self.changed_run = _commit(repo, "engine/run.py changed")
        _git(repo, "checkout", "-q", "-b", "stale", self.good)
        _write(repo, BASELINE, _baseline_text(side, "one\n"))
        self.base_not_ancestor = _commit(repo, "baseline names a divergent base")

    def baseline_digest(self, commit: str) -> str:
        blob = _run(["git", "show", f"{commit}:{BASELINE}"], cwd=self.repo)
        return "sha256:" + hashlib.sha256(blob).hexdigest()

    def sign(self, manifest: bytes, namespace: str = gate.NAMESPACE) -> bytes:
        return _run(
            ["ssh-keygen", "-Y", "sign", "-f", str(self.key), "-n", namespace],
            data=manifest,
        )

    def manifest(self, source: str | None = None) -> dict:
        source = source or self.good
        policy = gate.load_policy().exceptions[(FACILITY, VERSION)]
        items = []
        for number, identifier in enumerate(policy.required_evidence):
            item = {
                "id": identifier,
                "sha256": "sha256:" + f"{number + 1:064x}",
                "status": "PASS",
                "reviewed_by": "michael",
                "recorded_at": "2026-11-23T10:00:00Z",
                "register_ref": "dotmac-debt-register v2155",
            }
            if identifier == gate.E3_ID:
                item["sha256"] = self.baseline_digest(source)
            if identifier == "DR3_DR5":
                item["status"] = "WAIVED"
                item["waiver"] = dict(WAIVER)
            if identifier in policy.subject_evidence:
                item["subject_sha256"] = "sha256:" + f"{number + 101:064x}"
            items.append(item)
        return {
            "schema": gate.MANIFEST_SCHEMA,
            "facility": FACILITY,
            "version": VERSION,
            "candidate": {
                "artifact_sha256": CANDIDATE_SHA256,
                "source_sha": source,
            },
            "ruling": {
                "register": policy.ruling_register,
                "version": policy.ruling_version,
            },
            "issued_at": ISSUED,
            "expires_at": EXPIRES,
            "evidence": items,
        }


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return World(tmp_path_factory.mktemp("gate2"))


@pytest.fixture
def wired(world, monkeypatch):
    monkeypatch.setattr(gate, "SIGNERS_PATH", world.signers)
    monkeypatch.setattr(gate, "REVOKED_PATH", world.revoked)
    monkeypatch.setattr(gate, "REPO_ROOT", world.repo)
    monkeypatch.setattr(gate, "_utcnow", lambda: NOW)
    world.signers.write_text(world.signers_line)
    world.revoked.write_text("")
    return world


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _verify(
    wired: World,
    monkeypatch,
    capsys,
    tmp_path,
    document,
    *,
    source: str | None = None,
    candidate: str = CANDIDATE_SHA256,
    version: str = VERSION,
    namespace: str = gate.NAMESPACE,
    signature: bytes | None = None,
):
    manifest = (
        document
        if isinstance(document, bytes)
        else gate.canonical_result_bytes(document)
    )
    signature = signature if signature is not None else wired.sign(manifest, namespace)
    monkeypatch.setenv(gate.EVIDENCE_ENV, _b64(manifest))
    monkeypatch.setenv(gate.SIGNATURE_ENV, _b64(signature))
    out = tmp_path / "gate2"
    code = gate.main(
        [
            "verify-gate2",
            FACILITY,
            "--version",
            version,
            "--candidate-sha256",
            candidate,
            "--candidate-source-sha",
            source or wired.good,
            "--out",
            str(out),
            "--e3-python",
            sys.executable,
        ]
    )
    captured = capsys.readouterr()
    return code, captured.err, captured.out, out


def _refused(result, reason: str) -> None:
    code, err, _, _ = result
    assert code == 1, (code, err)
    # Exact: a code that merely CONTAINS the expected one is a different refusal.
    assert err.strip() == f"REFUSED: {reason}", err


# ── policy ──────────────────────────────────────────────────────────────────


def test_the_shipped_policy_defaults_to_lane3_and_excepts_exactly_one_version():
    policy = gate.load_policy()
    assert policy.default == "lane3_rehearsal"
    assert list(policy.exceptions) == [(FACILITY, VERSION)]
    found = policy.exceptions[(FACILITY, VERSION)]
    assert found.gate == "gate2_evidence"
    assert found.required_evidence == (
        "R6",
        "R11",
        "B2",
        "B7",
        "B8",
        "KIT",
        "RECOVERY",
        "S3",
        "E3",
        "NATIVE_PG",
        "NATIVE_BAO",
        "CEREMONY_AUTH",
        "DSN_SHAPE",
        "DR3_DR5",
    )
    # RECOVERY is required and NOT waivable; only DR3_DR5 may be waived.
    assert found.waivable == ("DR3_DR5",)
    assert "OPEN_GATES" not in found.required_evidence


@pytest.mark.parametrize(
    ("facility", "version", "expected"),
    [
        (FACILITY, "0.4.0a2", "gate2_evidence"),
        (FACILITY, "0.4.0a3", "lane3_rehearsal"),
        (FACILITY, "0.4.0a1", "lane3_rehearsal"),
        (FACILITY, "0.4.0", "lane3_rehearsal"),
        (FACILITY, "0.4.0a2 ", "lane3_rehearsal"),
        (FACILITY, "0.4.0A2", "lane3_rehearsal"),
        (FACILITY, "0.4.0a20", "lane3_rehearsal"),
        (FACILITY, "", "lane3_rehearsal"),
        ("dotmac-kernel", "0.4.0a2", "lane3_rehearsal"),
    ],
)
def test_only_the_exact_facility_and_version_select_gate2(facility, version, expected):
    assert gate.select_gate(gate.load_policy(), facility, version) == expected


def test_select_prints_the_gate_and_exits_zero(capsys):
    assert gate.main(["select", FACILITY, "--version", "0.4.0a3"]) == 0
    assert capsys.readouterr().out == "gate=lane3_rehearsal\n"
    assert gate.main(["select", FACILITY, "--version", VERSION]) == 0
    assert capsys.readouterr().out == "gate=gate2_evidence\n"


def _policy_doc(**edits) -> dict:
    document = json.loads(gate.POLICY_PATH.read_text())
    entry = document["exceptions"][0]
    for key, value in edits.items():
        if key in document:
            document[key] = value
        else:
            entry[key] = value
    return document


def _policy_bytes(document: dict) -> bytes:
    return json.dumps(document).encode()


def test_the_policy_parser_accepts_the_shipped_shape():
    assert gate.parse_policy(_policy_bytes(_policy_doc()))


@pytest.mark.parametrize(
    "edits",
    [
        {"default": "gate2_evidence"},
        {"default": "anything"},
        {"version": "0.4.*"},
        {"version": "0.4"},
        {"version": "0.4.0a"},
        {"version": ">=0.4.0a2"},
        {"version": "*"},
        {"facility": "dotmac-kernel"},
        {"facility": "dotmac-*"},
        {"gate": "lane3_rehearsal"},
        {"gate": "anything"},
        {"ruling": {"register": "dotmac-debt-register", "version": "2155"}},
        {"ruling": {"register": "elsewhere", "version": 2155}},
        {"required_evidence": []},
        {"required_evidence": ["R6", "R6", "E3"]},
        {"required_evidence": ["R6", "r11", "E3"]},
        {"required_evidence": ["R6"]},
        {"waivable": ["E3"]},
        {"waivable": ["NOT_REQUIRED"]},
        {"waivable": ["DR3_DR5", "DR3_DR5"]},
        {"waivable": "DR3_DR5"},
        {"schema": "other"},
    ],
)
def test_the_policy_parser_refuses_every_wider_or_malformed_shape(edits):
    with pytest.raises(gate.PolicyInvalid):
        gate.parse_policy(_policy_bytes(_policy_doc(**edits)))


def test_a_duplicate_exception_is_refused():
    document = _policy_doc()
    document["exceptions"].append(copy.deepcopy(document["exceptions"][0]))
    with pytest.raises(gate.PolicyInvalid, match="duplicate_exception"):
        gate.parse_policy(_policy_bytes(document))


def test_an_unknown_policy_key_is_refused():
    document = _policy_doc()
    document["exceptions"][0]["extra"] = 1
    with pytest.raises(gate.PolicyInvalid):
        gate.parse_policy(_policy_bytes(document))


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json",
        b'{"schema": "x",}',
        b'{"schema": "dotmac.foundation.publication-gates.v1", "schema": "x"}',
        b"[]",
        b"x" * (gate.MAX_POLICY_BYTES + 1),
    ],
)
def test_a_malformed_policy_exits_two_and_is_never_the_default(
    payload, tmp_path, monkeypatch, capsys
):
    path = tmp_path / "policy.json"
    path.write_bytes(payload)
    monkeypatch.setattr(gate, "POLICY_PATH", path)
    assert gate.main(["select", FACILITY, "--version", "0.4.0a3"]) == 2
    assert capsys.readouterr().out == ""


def test_a_missing_policy_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "POLICY_PATH", tmp_path / "absent.json")
    assert gate.main(["select", FACILITY, "--version", VERSION]) == 2
    assert capsys.readouterr().out == ""


# ── the signer file: shipped EMPTY, own namespace, never Lane 3's ───────────


def test_the_shipped_signer_file_is_empty_and_so_is_the_revocation_file():
    assert gate.SIGNERS_PATH.read_bytes() == b""
    assert gate.REVOKED_PATH.read_bytes() == b""


def test_the_shipped_empty_signer_file_refuses_as_unenrolled():
    with pytest.raises(gate.GateRefused, match="gate2_signer_unenrolled"):
        gate.verify_signature(b"{}", b"-----BEGIN SSH SIGNATURE-----")


def test_the_namespace_and_principal_are_not_lane3s():
    assert gate.NAMESPACE == "dotmac.foundation.gate2-evidence.v1"
    assert gate.NAMESPACE != lane3_offline_result.NAMESPACE
    assert gate.PRINCIPAL != lane3_offline_result.PRINCIPAL
    assert gate.SIGNERS_PATH.name != "lane3-offline-verifier.allowed_signers"


def test_a_lane3_enrolled_key_line_is_not_accepted_as_a_gate2_signer(world):
    lane3_line = (
        f'{lane3_offline_result.PRINCIPAL} namespaces="{LANE3_NAMESPACE}",'
        'valid-after="20200101000000Z",valid-before="20990101000000Z" '
        f"ssh-ed25519 {world.blob}\n"
    ).encode()
    with pytest.raises(gate.GateRefused):
        gate.parse_signer(lane3_line)


# ── the accepting case, then one planted defect at a time ───────────────────


def test_a_correct_manifest_is_accepted_and_writes_the_three_outputs(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    code, err, out, outdir = _verify(wired, monkeypatch, capsys, tmp_path, document)
    assert code == 0, err
    manifest = gate.canonical_result_bytes(document)
    assert (outdir / "manifest.json").read_bytes() == manifest
    assert (
        (outdir / "manifest.sshsig")
        .read_bytes()
        .startswith(b"-----BEGIN SSH SIGNATURE-----")
    )
    record = json.loads((outdir / "gate.json").read_text())
    assert set(record) == {
        "gate",
        "manifest_sha256",
        "signature_sha256",
        "signer_fingerprint",
        "e3_baseline_sha256",
        "issued_at",
        "expires_at",
        "evidence",
    }
    assert record["issued_at"] == ISSUED and record["expires_at"] == EXPIRES
    assert record["gate"] == "gate2_evidence"
    assert record["manifest_sha256"] == "sha256:" + hashlib.sha256(manifest).hexdigest()
    assert record["e3_baseline_sha256"] == wired.baseline_digest(wired.good)
    assert [item["id"] for item in record["evidence"]] == list(
        gate.load_policy().exceptions[(FACILITY, VERSION)].required_evidence
    )
    lines = dict(line.split("=", 1) for line in out.splitlines())
    assert lines["gate"] == "gate2_evidence"
    assert lines["gate2_manifest_sha256"] == record["manifest_sha256"]
    assert lines["gate2_signature_sha256"] == record["signature_sha256"]
    assert lines["gate2_signer_fingerprint"] == record["signer_fingerprint"]
    assert lines["gate2_e3_baseline_sha256"] == record["e3_baseline_sha256"]


def test_a_pass_for_dr3_dr5_is_also_accepted(wired, monkeypatch, capsys, tmp_path):
    document = wired.manifest()
    item = next(i for i in document["evidence"] if i["id"] == "DR3_DR5")
    item["status"] = "PASS"
    del item["waiver"]
    code, err, _, _ = _verify(wired, monkeypatch, capsys, tmp_path, document)
    assert code == 0, err


def test_an_empty_signer_file_refuses(wired, monkeypatch, capsys, tmp_path):
    document = wired.manifest()
    manifest = gate.canonical_result_bytes(document)
    signature = wired.sign(manifest)
    wired.signers.write_bytes(b"")
    result = _verify(
        wired, monkeypatch, capsys, tmp_path, manifest, signature=signature
    )
    _refused(result, "gate2_signer_unenrolled")


def test_a_bad_signature_refuses(wired, monkeypatch, capsys, tmp_path):
    document = wired.manifest()
    other = wired.sign(b"some other bytes\n")
    result = _verify(wired, monkeypatch, capsys, tmp_path, document, signature=other)
    _refused(result, "gate2_signature_invalid")


def test_a_lane3_namespace_signature_is_refused(wired, monkeypatch, capsys, tmp_path):
    result = _verify(
        wired,
        monkeypatch,
        capsys,
        tmp_path,
        wired.manifest(),
        namespace=LANE3_NAMESPACE,
    )
    _refused(result, "gate2_signature_invalid")


def test_a_revoked_key_refuses(wired, monkeypatch, capsys, tmp_path):
    wired.revoked.write_text(f"ssh-ed25519 {wired.blob}\n")
    result = _verify(wired, monkeypatch, capsys, tmp_path, wired.manifest())
    _refused(result, "gate2_signer_revoked")


def test_the_signer_must_be_enrolled_for_the_gate2_namespace_only(
    wired, monkeypatch, capsys, tmp_path
):
    wired.signers.write_text(
        wired.signers_line.replace(gate.NAMESPACE, LANE3_NAMESPACE)
    )
    result = _verify(wired, monkeypatch, capsys, tmp_path, wired.manifest())
    _refused(result, "gate2_signer.options")


def _edit(document: dict, path: str, value) -> dict:
    cursor = document
    *head, last = path.split(".")
    for part in head:
        cursor = cursor[part]
    cursor[last] = value
    return document


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        ("facility", "dotmac-kernel", "gate2_manifest.identity"),
        ("version", "0.4.0a3", "gate2_manifest.identity"),
        ("candidate.artifact_sha256", "cd" * 32, "gate2_manifest.candidate_digest"),
        ("candidate.source_sha", "1" * 40, "gate2_manifest.candidate_source"),
        ("ruling.version", 1, "gate2_manifest.ruling"),
        ("ruling.register", "elsewhere", "gate2_manifest.ruling"),
        ("schema", "other", "gate2_manifest.schema"),
    ],
)
def test_a_manifest_bound_to_something_else_refuses(
    wired, monkeypatch, capsys, tmp_path, path, value, reason
):
    document = _edit(wired.manifest(), path, value)
    _refused(_verify(wired, monkeypatch, capsys, tmp_path, document), reason)


def test_a_manifest_for_another_dispatch_version_is_not_selected(
    wired, monkeypatch, capsys, tmp_path
):
    result = _verify(
        wired, monkeypatch, capsys, tmp_path, wired.manifest(), version="0.4.0a3"
    )
    _refused(result, "gate2_not_selected")


def test_a_missing_required_id_refuses(wired, monkeypatch, capsys, tmp_path):
    document = wired.manifest()
    document["evidence"] = [i for i in document["evidence"] if i["id"] != "S3"]
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.missing",
    )


@pytest.mark.parametrize("extra", ["OPEN_GATES", "EXTRA", "NATIVE_PG2"])
def test_an_extra_or_removed_id_refuses_as_unknown(
    wired, monkeypatch, capsys, tmp_path, extra
):
    document = wired.manifest()
    clone = copy.deepcopy(document["evidence"][0])
    clone["id"] = extra
    document["evidence"].append(clone)
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.unknown",
    )


def test_a_duplicate_id_refuses(wired, monkeypatch, capsys, tmp_path):
    document = wired.manifest()
    document["evidence"].append(copy.deepcopy(document["evidence"][0]))
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.duplicate",
    )


@pytest.mark.parametrize("status", ["OPEN", "FAIL", "pass", "", None, "NOT YET"])
def test_a_bad_status_refuses(wired, monkeypatch, capsys, tmp_path, status):
    document = wired.manifest()
    document["evidence"][0]["status"] = status
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.status",
    )


_REQUIRED = gate.load_policy().exceptions[(FACILITY, VERSION)].required_evidence


@pytest.mark.parametrize(
    "identifier", [i for i in _REQUIRED if i not in ("DR3_DR5", "E3")]
)
def test_waived_is_refused_on_every_id_but_dr3_dr5(
    wired, monkeypatch, capsys, tmp_path, identifier
):
    document = wired.manifest()
    item = next(i for i in document["evidence"] if i["id"] == identifier)
    item["status"] = "WAIVED"
    item["waiver"] = dict(WAIVER)
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.waiver_not_allowed",
    )


def test_recovery_marked_waived_is_refused_and_e3_cannot_be_waived(
    wired, monkeypatch, capsys, tmp_path
):
    for identifier in ("RECOVERY", "E3"):
        document = wired.manifest()
        item = next(i for i in document["evidence"] if i["id"] == identifier)
        item["status"] = "WAIVED"
        item["waiver"] = dict(WAIVER)
        _refused(
            _verify(wired, monkeypatch, capsys, tmp_path, document),
            "gate2_manifest.item.waiver_not_allowed",
        )


def test_the_manifest_cannot_extend_the_waivable_list(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    document["evidence"][0]["waivable"] = ["R6"]
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.schema",
    )


@pytest.mark.parametrize(
    ("waiver", "reason"),
    [
        ({"condition_id": COND_ID}, "gate2_manifest.item.waiver.schema"),
        (
            {"register_ref": "", "condition_id": COND_ID},
            "gate2_manifest.item.waiver_register_ref",
        ),
        (
            {"register_ref": "2155", "condition_id": COND_ID},
            "gate2_manifest.item.waiver_register_ref",
        ),
        (
            {"register_ref": 2154, "condition_id": COND_ID},
            "gate2_manifest.item.waiver_register_ref",
        ),
        ({"register_ref": WAIVER_REF}, "gate2_manifest.item.waiver.schema"),
        (
            {"register_ref": WAIVER_REF, "condition_id": "OTHER_CONDITION"},
            "gate2_manifest.item.waiver_condition",
        ),
        (
            {"register_ref": WAIVER_REF, "condition_id": ""},
            "gate2_manifest.item.waiver_condition",
        ),
        (
            {"register_ref": WAIVER_REF, "condition_id": COND_ID, "extra": "x"},
            "gate2_manifest.item.waiver.schema",
        ),
        (
            {"register_ref": WAIVER_REF, "condition": "seabone 3-fault staging drill"},
            "gate2_manifest.item.waiver.schema",
        ),
    ],
)
def test_a_waiver_without_the_register_line_and_exact_condition_refuses(
    wired, monkeypatch, capsys, tmp_path, waiver, reason
):
    document = wired.manifest()
    item = next(i for i in document["evidence"] if i["id"] == "DR3_DR5")
    item["waiver"] = waiver
    _refused(_verify(wired, monkeypatch, capsys, tmp_path, document), reason)


def test_a_waived_item_without_a_waiver_object_refuses(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    item = next(i for i in document["evidence"] if i["id"] == "DR3_DR5")
    del item["waiver"]
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.schema",
    )


def test_a_non_waived_item_carrying_a_waiver_refuses(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    document["evidence"][0]["waiver"] = dict(WAIVER)
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.schema",
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("sha256", "sha256:abc"),
        ("sha256", "a" * 64),
        ("reviewed_by", ""),
        ("recorded_at", "2026-11-23"),
        ("recorded_at", "2026-13-45T10:00:00Z"),
        ("register_ref", ""),
    ],
)
def test_a_malformed_item_field_refuses(
    wired, monkeypatch, capsys, tmp_path, key, value
):
    document = wired.manifest()
    document["evidence"][0][key] = value
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        f"gate2_manifest.item.{key}",
    )


def test_non_canonical_bytes_refuse_even_when_correctly_signed(
    wired, monkeypatch, capsys, tmp_path
):
    pretty = json.dumps(wired.manifest(), indent=1).encode("ascii")
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, pretty),
        "gate2_manifest.noncanonical",
    )


def test_a_duplicate_json_key_refuses(wired, monkeypatch, capsys, tmp_path):
    payload = b'{"evidence":[],"evidence":[]}\n'
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, payload),
        "gate2_manifest.duplicate_key",
    )


def test_a_float_or_nan_refuses(wired, monkeypatch, capsys, tmp_path):
    for payload in (b'{"a":1.5}\n', b'{"a":NaN}\n'):
        _refused(
            _verify(wired, monkeypatch, capsys, tmp_path, payload),
            "gate2_manifest.number",
        )


def test_more_than_64_kib_refuses(wired, monkeypatch, capsys, tmp_path):
    payload = b'{"x":"' + b"a" * gate.MAX_MANIFEST_BYTES + b'"}\n'
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, payload),
        "gate2_input.too_large",
    )


def test_missing_inputs_refuse(wired, monkeypatch, capsys, tmp_path):
    monkeypatch.delenv(gate.EVIDENCE_ENV, raising=False)
    monkeypatch.delenv(gate.SIGNATURE_ENV, raising=False)
    code = gate.main(
        [
            "verify-gate2",
            FACILITY,
            "--version",
            VERSION,
            "--candidate-sha256",
            CANDIDATE_SHA256,
            "--candidate-source-sha",
            wired.good,
            "--out",
            str(tmp_path / "o"),
        ]
    )
    assert code == 1
    assert capsys.readouterr().err.strip() == "REFUSED: gate2_input.unavailable"


# ── E3: the machine re-check at the exact candidate source commit ───────────


def test_e3_refuses_when_engine_run_differs_from_the_baseline(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest(wired.changed_run)
    result = _verify(
        wired, monkeypatch, capsys, tmp_path, document, source=wired.changed_run
    )
    _refused(result, "gate2_e3.isolation_failed")


def test_e3_refuses_a_base_commit_that_is_not_an_ancestor(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest(wired.base_not_ancestor)
    result = _verify(
        wired,
        monkeypatch,
        capsys,
        tmp_path,
        document,
        source=wired.base_not_ancestor,
    )
    _refused(result, "gate2_e3.base_not_ancestor")


def test_e3_refuses_a_digest_that_is_not_the_baseline_at_the_source(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    item = next(i for i in document["evidence"] if i["id"] == "E3")
    item["sha256"] = "sha256:" + "0" * 64
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_e3.digest_mismatch",
    )


def test_e3_refuses_an_unknown_source_commit(wired, monkeypatch, capsys, tmp_path):
    unknown = "9" * 40
    document = wired.manifest()
    document["candidate"]["source_sha"] = unknown
    result = _verify(wired, monkeypatch, capsys, tmp_path, document, source=unknown)
    _refused(result, "gate2_e3.source_unknown")


def test_e3_leaves_no_worktree_behind(wired, monkeypatch, capsys, tmp_path):
    _verify(wired, monkeypatch, capsys, tmp_path, wired.manifest())
    listing = _git(wired.repo, "worktree", "list", "--porcelain")
    assert listing.count("worktree ") == 1


# ── recheck-gate2: the publication-boundary twin ────────────────────────────


def _recheck_env(wired, monkeypatch, document=None, **override):
    manifest = gate.canonical_result_bytes(document or wired.manifest())
    signature = wired.sign(manifest)
    monkeypatch.setenv(gate.EVIDENCE_ENV, _b64(manifest))
    monkeypatch.setenv(gate.SIGNATURE_ENV, _b64(signature))
    monkeypatch.setenv(
        gate.EXPECTED_MANIFEST_ENV,
        override.get("manifest", "sha256:" + hashlib.sha256(manifest).hexdigest()),
    )
    monkeypatch.setenv(
        gate.EXPECTED_SIGNATURE_ENV,
        override.get("signature", "sha256:" + hashlib.sha256(signature).hexdigest()),
    )


def test_recheck_accepts_the_build_jobs_decision(wired, monkeypatch, capsys):
    _recheck_env(wired, monkeypatch)
    assert gate.main(["recheck-gate2", FACILITY, "--version", VERSION]) == 0
    assert "gate2_recheck=verified" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"manifest": "sha256:" + "0" * 64}, "gate2_recheck.manifest_digest"),
        ({"signature": "sha256:" + "0" * 64}, "gate2_recheck.signature_digest"),
        ({"manifest": ""}, "gate2_recheck.expected"),
        ({"signature": "nope"}, "gate2_recheck.expected"),
    ],
)
def test_recheck_refuses_a_digest_that_is_not_the_build_jobs(
    wired, monkeypatch, capsys, override, reason
):
    _recheck_env(wired, monkeypatch, **override)
    assert gate.main(["recheck-gate2", FACILITY, "--version", VERSION]) == 1
    assert capsys.readouterr().err.strip() == f"REFUSED: {reason}"


def test_recheck_refuses_after_the_key_is_revoked(wired, monkeypatch, capsys):
    _recheck_env(wired, monkeypatch)
    wired.revoked.write_text(f"ssh-ed25519 {wired.blob}\n")
    assert gate.main(["recheck-gate2", FACILITY, "--version", VERSION]) == 1
    assert capsys.readouterr().err.strip() == "REFUSED: gate2_signer_revoked"


def test_recheck_refuses_a_version_the_policy_does_not_select(
    wired, monkeypatch, capsys
):
    _recheck_env(wired, monkeypatch)
    assert gate.main(["recheck-gate2", FACILITY, "--version", "0.4.0a3"]) == 1
    assert capsys.readouterr().err.strip() == "REFUSED: gate2_not_selected"


# ── release_facility.py: `--gate gate2_evidence` needs the same policy ──────


def test_release_facility_refuses_gate2_for_any_other_version():
    import release_facility

    release_facility._require_gate2_selected(FACILITY, VERSION)
    for version in ("0.4.0a3", "0.4.0a1", "0.3.0a2"):
        with pytest.raises(release_facility.ReleaseRefused):
            release_facility._require_gate2_selected(FACILITY, version)


def test_the_gate2_revision_binding_has_no_runner_revision_parameter():
    import inspect

    import release_facility

    parameters = inspect.signature(
        release_facility.require_gate2_revision_relationship
    ).parameters
    assert "runner_revision" not in parameters
    assert "release_revision" in parameters
    # The Lane 3 binding is untouched and still demands the runner revision.
    assert (
        "runner_revision"
        in inspect.signature(release_facility.require_revision_relationships).parameters
    )


# ── the release record ──────────────────────────────────────────────────────


def _gate_json_item(identifier: str, policy) -> dict:
    item = {"id": identifier, "sha256": "sha256:" + "4" * 64, "status": "PASS"}
    if identifier in policy.subject_evidence:
        item["subject_sha256"] = "sha256:" + "5" * 64
    if identifier == "DR3_DR5":
        item["status"] = "WAIVED"
        item["waiver"] = dict(WAIVER)
    return item


def _record_inputs(tmp_path, monkeypatch, wired):
    import write_release_record as record

    ledger = tmp_path / "records.json"
    ledger.write_text(
        json.dumps({"schema": record.PUBLICATION_RECORDS_SCHEMA, "records": []})
    )
    monkeypatch.setattr(record, "PUBLICATION_RECORDS", ledger)
    policy = gate.load_policy().exceptions[(FACILITY, VERSION)]
    gate_json = {
        "gate": "gate2_evidence",
        "manifest_sha256": "sha256:" + "1" * 64,
        "signature_sha256": "sha256:" + "2" * 64,
        "signer_fingerprint": "SHA256:" + "A" * 43,
        "e3_baseline_sha256": "sha256:" + "3" * 64,
        "issued_at": ISSUED,
        "expires_at": EXPIRES,
        "evidence": [_gate_json_item(i, policy) for i in policy.required_evidence],
    }
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(gate_json))
    return record, ledger, path, gate_json


def test_the_release_record_carries_the_gate_and_the_cited_receipts(
    tmp_path, monkeypatch, wired
):
    record, ledger, path, gate_json = _record_inputs(tmp_path, monkeypatch, wired)
    tag = f"{FACILITY}-v{VERSION}"
    changed = record.write_publication_gate_record(
        distribution=FACILITY,
        version=VERSION,
        tag=tag,
        publication_gate="gate2_evidence",
        gate_record=str(path),
    )
    assert changed
    row = json.loads(ledger.read_text())["records"][0]
    assert row["publication_gate"] == "gate2_evidence"
    assert row["gate2_manifest_sha256"] == gate_json["manifest_sha256"]
    assert row["gate2_signature_sha256"] == gate_json["signature_sha256"]
    assert row["gate2_signer_fingerprint"] == gate_json["signer_fingerprint"]
    assert row["e3_baseline_sha256"] == gate_json["e3_baseline_sha256"]
    assert row["ruling"] == {"register": "dotmac-debt-register", "version": 2155}
    assert row["evidence"] == gate_json["evidence"]
    assert row["issued_at"] == ISSUED and row["expires_at"] == EXPIRES
    # F-1: the FULL adoption condition and the waiver's register ref are kept.
    assert row["adoption_conditions"] == [
        {
            "id": COND_ID,
            "waives": "DR3_DR5",
            "blocks": "production_governed_deploy",
            "environment": "seabone-staging",
            "required_faults": [
                "db_outage_at_migrate",
                "network_loss",
                "timeout_sigterm",
            ],
            "register_ref": 2155,
        }
    ]
    waived = next(i for i in row["evidence"] if i["id"] == "DR3_DR5")
    assert waived["waiver"] == {"register_ref": 2155, "condition_id": COND_ID}
    # Idempotent; a disagreeing row refuses rather than overwriting.
    assert (
        record.write_publication_gate_record(
            distribution=FACILITY,
            version=VERSION,
            tag=tag,
            publication_gate="gate2_evidence",
            gate_record=str(path),
        )
        == []
    )
    gate_json["manifest_sha256"] = "sha256:" + "9" * 64
    path.write_text(json.dumps(gate_json))
    with pytest.raises(record.ReleaseRecordError):
        record.write_publication_gate_record(
            distribution=FACILITY,
            version=VERSION,
            tag=tag,
            publication_gate="gate2_evidence",
            gate_record=str(path),
        )


def test_the_lane3_record_row_keeps_its_fields_and_takes_no_gate2_record(
    tmp_path, monkeypatch, wired
):
    record, ledger, path, _ = _record_inputs(tmp_path, monkeypatch, wired)
    record.write_publication_gate_record(
        distribution=FACILITY,
        version="0.4.0a3",
        tag=f"{FACILITY}-v0.4.0a3",
        publication_gate="lane3_rehearsal",
        gate_record=None,
    )
    row = json.loads(ledger.read_text())["records"][0]
    assert set(row) == {"distribution", "version", "tag", "publication_gate"}
    with pytest.raises(record.ReleaseRecordError):
        record.write_publication_gate_record(
            distribution=FACILITY,
            version="0.4.0a4",
            tag=f"{FACILITY}-v0.4.0a4",
            publication_gate="lane3_rehearsal",
            gate_record=str(path),
        )


def test_the_gate2_record_refuses_a_version_the_policy_does_not_except(
    tmp_path, monkeypatch, wired
):
    record, _, path, _ = _record_inputs(tmp_path, monkeypatch, wired)
    with pytest.raises(record.ReleaseRecordError):
        record.write_publication_gate_record(
            distribution=FACILITY,
            version="0.4.0a3",
            tag=f"{FACILITY}-v0.4.0a3",
            publication_gate="gate2_evidence",
            gate_record=str(path),
        )


def test_the_gate2_record_refuses_evidence_that_differs_from_the_policy(
    tmp_path, monkeypatch, wired
):
    record, _, path, gate_json = _record_inputs(tmp_path, monkeypatch, wired)
    gate_json["evidence"] = gate_json["evidence"][:-1]
    path.write_text(json.dumps(gate_json))
    with pytest.raises(record.ReleaseRecordError):
        record.write_publication_gate_record(
            distribution=FACILITY,
            version=VERSION,
            tag=f"{FACILITY}-v{VERSION}",
            publication_gate="gate2_evidence",
            gate_record=str(path),
        )


# ── workflow structure ──────────────────────────────────────────────────────

#: sha256 of the canonical JSON of the `rehearsal` step as it stood BEFORE this
#: change, with the `if` key removed. The step may gain `if` and nothing else.
REHEARSAL_STEP_SHA256 = (
    "a76400cf8282520a83c3f3054d5274c205ecb80059c69827e3c7c8e80d01dbcd"
)


def _workflow() -> dict:
    return yaml.safe_load((WORKFLOWS / "release-facility.yml").read_text())


def _steps(job: str) -> list[dict]:
    return _workflow()["jobs"][job]["steps"]


def _index(steps: list[dict], key: str, value: str) -> int:
    return next(i for i, step in enumerate(steps) if step.get(key) == value)


def test_the_rehearsal_step_is_unchanged_apart_from_its_if():
    step = dict(_steps("build")[_index(_steps("build"), "id", "rehearsal")])
    assert step.pop("if") == "steps.gate.outputs.gate == 'lane3_rehearsal'"
    digest = hashlib.sha256(
        json.dumps(step, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert digest == REHEARSAL_STEP_SHA256


def test_the_gate_is_selected_before_either_oracle_runs():
    steps = _steps("build")
    gate_at = _index(steps, "id", "gate")
    assert gate_at < _index(steps, "id", "rehearsal")
    assert gate_at < _index(steps, "id", "gate2")
    run = steps[gate_at]["run"]
    assert "foundation_publication_gate.py select" in run
    assert "inputs.gate2" not in run


def test_gate2_runs_under_the_runner_interpreter_and_only_via_env_inputs():
    steps = _steps("build")
    step = steps[_index(steps, "id", "gate2")]
    assert step["if"] == "steps.gate.outputs.gate == 'gate2_evidence'"
    assert (
        "python3 -I scripts/foundation_publication_gate.py verify-gate2" in step["run"]
    )
    assert ".foundation-candidate" not in step["run"]
    assert "verify-gate2" in step["run"]
    assert "steps.candidate.outputs.candidate_sha256" in step["run"]
    assert "steps.candidate.outputs.candidate_source_sha" in step["run"]
    assert step["env"][gate.EVIDENCE_ENV] == "${{ inputs.gate2_evidence_b64 }}"
    assert step["env"][gate.SIGNATURE_ENV] == "${{ inputs.gate2_signature_b64 }}"
    for job in _workflow()["jobs"].values():
        for each in job["steps"]:
            assert "inputs.gate2" not in str(each.get("run", ""))


def test_exactly_one_gate_guard_always_runs_and_requires_a_skip_and_a_success():
    steps = _steps("build")
    at = _index(steps, "name", "Exactly one publication gate passed")
    guard = steps[at]
    assert guard["if"] == "always()"
    assert at > _index(steps, "id", "rehearsal")
    assert at > _index(steps, "id", "gate2")
    run = guard["run"]
    assert "lane3_rehearsal:success:skipped" in run
    assert "gate2_evidence:skipped:success" in run
    assert "exit 1" in run
    assert guard["env"]["REHEARSAL_OUTCOME"] == "${{ steps.rehearsal.outcome }}"
    assert guard["env"]["GATE2_OUTCOME"] == "${{ steps.gate2.outcome }}"


def test_every_step_after_the_guard_depends_on_it():
    """No later build step may override the default success() condition."""
    steps = _steps("build")
    at = _index(steps, "name", "Exactly one publication gate passed")
    assert at < len(steps) - 1
    for step in steps[at + 1 :]:
        condition = str(step.get("if", ""))
        for override in ("always()", "failure()", "cancelled()"):
            assert override not in condition, step.get("name")
        assert step.get("continue-on-error") is None


def test_the_guard_bites_when_a_later_step_overrides_success():
    steps = copy.deepcopy(_steps("build"))
    at = _index(steps, "name", "Exactly one publication gate passed")
    steps[at + 1]["if"] = "always()"
    with pytest.raises(AssertionError):
        for step in steps[at + 1 :]:
            assert "always()" not in str(step.get("if", ""))


def test_lane3_binding_is_first_and_gated_and_gate2_binding_has_no_runner():
    steps = _steps("build")
    lane3 = _index(steps, "name", "Bind the three revisions")
    gate2 = _index(steps, "name", "Bind the candidate and release revisions (Gate 2)")
    assert lane3 < gate2
    assert steps[lane3]["if"] == "steps.gate.outputs.gate == 'lane3_rehearsal'"
    assert "--runner-revision" in steps[lane3]["run"]
    assert "steps.rehearsal.outputs.rehearsal_runner_revision" in steps[lane3]["run"]
    assert steps[gate2]["if"] == "steps.gate.outputs.gate == 'gate2_evidence'"
    assert "--gate gate2_evidence" in steps[gate2]["run"]
    assert "--runner-revision" not in steps[gate2]["run"]
    assert "${GITHUB_SHA}" in steps[gate2]["run"]


def test_each_retained_artifact_belongs_to_exactly_one_gate():
    steps = _steps("build")
    offline = steps[
        _index(
            steps,
            "name",
            "Retain authenticated public offline verification evidence",
        )
    ]
    gate2 = steps[_index(steps, "name", "Retain the Gate 2 evidence")]
    assert offline["if"] == "steps.gate.outputs.gate == 'lane3_rehearsal'"
    assert gate2["if"] == "steps.gate.outputs.gate == 'gate2_evidence'"
    assert gate2["with"]["name"] == (
        "foundation-gate2-evidence-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert gate2["with"]["if-no-files-found"] == "error"
    assert gate2["with"]["retention-days"] == 90


def test_the_job_outputs_add_the_gate_and_keep_the_offline_ones():
    outputs = _workflow()["jobs"]["build"]["outputs"]
    assert {
        "gate",
        "gate2_manifest_sha256",
        "gate2_signature_sha256",
        "gate2_signer_fingerprint",
        "gate2_e3_baseline_sha256",
    } <= set(outputs)
    assert {
        "offline_repository_id",
        "offline_run_id",
        "offline_receipt_digest",
        "offline_release_revision",
    } <= set(outputs)


def test_the_dispatch_gains_two_optional_inputs_and_no_gate_selector():
    document = _workflow()
    inputs = document.get("on", document.get(True))["workflow_dispatch"]["inputs"]
    for name in ("gate2_evidence_b64", "gate2_signature_b64"):
        assert inputs[name]["required"] is False
        assert inputs[name]["default"] == ""
    assert not [name for name in inputs if "gate" in name and "gate2" not in name]


def test_the_publish_step_branches_only_on_the_gate_and_exits_one_otherwise():
    steps = _steps("publish")
    step = steps[_index(steps, "name", "Publish to the Forgejo private index")]
    assert step["env"]["PUBLICATION_GATE"] == "${{ needs.build.outputs.gate }}"
    run = step["run"]
    assert 'case "${PUBLICATION_GATE}" in' in run
    lane3 = run.index("lane3_rehearsal)")
    gate2 = run.index("gate2_evidence)")
    other = run.index("*)")
    assert lane3 < gate2 < other < run.index("twine upload")
    assert "recheck_offline_result.py" in run[lane3:gate2]
    assert "recheck-gate2" in run[gate2:other]
    assert (
        "python3 -I scripts/foundation_publication_gate.py recheck-gate2"
        in run[gate2:other]
    )
    # The unpinned twine install runs only after the gate (after `esac`).
    assert run.count("pip install") == 1
    assert run.index("pip install") > run.index("esac")
    assert run.index("pip install") < run.index("twine upload")
    assert "exit 1" in run[other : run.index("esac")]
    assert (
        step["env"]["FOUNDATION_EXPECTED_GATE2_MANIFEST_SHA256"]
        == "${{ needs.build.outputs.gate2_manifest_sha256 }}"
    )
    assert (
        step["env"]["FOUNDATION_EXPECTED_GATE2_SIGNATURE_SHA256"]
        == "${{ needs.build.outputs.gate2_signature_sha256 }}"
    )


def test_the_release_record_step_forwards_the_gate_and_never_the_retained_files():
    steps = _steps("verify")
    step = steps[_index(steps, "name", "Open the post-release record")]
    assert "--publication-gate" in step["run"]
    assert "--gate2-record" in step["run"]
    download = steps[_index(steps, "name", "Download the retained Gate 2 evidence")]
    # Outside the checkout: the record writer commits with `git add -A`.
    assert download["with"]["path"].startswith("${{ runner.temp }}")


#: Byte hashes of everything this change must NOT touch: the other release
#: lanes, the candidate lane, the Lane 3 oracle and its trust files.
UNCHANGED = {
    ".github/workflows/release-adapter.yml": (
        "26e8fad69706be4cdb1761e2857f3dcee2d6f4794e0d44233f02c63d578648dd"
    ),
    ".github/workflows/release-connector.yml": (
        "6dc981e4fe6d794bed6ae79a3f2e89a2780ad443a3271c069ee917839d9479f1"
    ),
    ".github/workflows/release-contract.yml": (
        "0b53cebe9289ac9f75be8c38704175271cdd3a0ae71224d3f3494f8e8929efac"
    ),
    ".github/workflows/release-kernel.yml": (
        "8f94a69f8f44540c12881f020036f32ca3fd328d88ae4d49cfeac43f98af9e1b"
    ),
    ".github/workflows/release-ui.yml": (
        "02882ce5e449984c2b3ed4be2edda5a851c3daa6ac0360b0560f1a43e24bf58b"
    ),
    ".github/workflows/foundation-candidate.yml": (
        "c0568925e545195ba72ed5bc4671fc7b31c39d9334dc86bfe9a603a2a28598ba"
    ),
    "scripts/require_rehearsal.py": (
        "40ee000c03eec6d0b71fc54e91af80f8cd6fc07c327feec5501d91cb695b774b"
    ),
    "scripts/recheck_offline_result.py": (
        "94ca69f1612474f2fde056f2917646cc66f107aa6348fe83b5b5969902b99e0e"
    ),
    "scripts/lane3_offline_result.py": (
        "9764bb68e5c1c3102750e393293fc0e45afba75fff133d99e1e9a23c8457e23d"
    ),
    "scripts/lane3_offline_signature.py": (
        "79ed94af12ed5165f7ebee6fd82e64572c36786473c2e2d8ab0485500fae60d5"
    ),
    "docs/inventories/lane3-offline-verifier-policy.json": (
        "bd77961db5d6e9ba39b8ff75e5e51d70367e8f8a42758597131e9e6fef519e8f"
    ),
    "docs/inventories/lane3-offline-verifier.allowed_signers": (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    ),
    "docs/inventories/lane3-offline-verifier.revoked_keys": (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    ),
}


@pytest.mark.parametrize("path", sorted(UNCHANGED))
def test_the_other_lanes_and_the_lane3_oracle_are_byte_unchanged(path):
    digest = hashlib.sha256((REPO / path).read_bytes()).hexdigest()
    assert digest == UNCHANGED[path], (
        f"{path} changed. The a2 publication gate touches release-facility.yml "
        "and the new gate files only; if this edit is deliberate, re-pin it in "
        "its own reviewed change."
    )


# `release-module.yml` is checked STRUCTURALLY, not by byte hash, so a harmless
# edit does not break the suite. What the a2 publication gate (SPEC-a2 section 3,
# "release-module.yml and the other lanes are unchanged") needs is isolation:
# the module lane never reaches Gate 2 or Lane 3, never publishes the
# deployment foundation, and has exactly ONE way to publish. Credential
# separation does not back this up (release-facility.yml uses the same
# environment and token), so the workflow text is the whole control and the
# property set below closes the publish routes instead of only naming them.
# Residual (architect review of #1179): an obfuscated edit that names no tool or
# marker (for example `bash -c "$(... | base64 -d)"`) is not caught; edits to this
# workflow stay under architect review.
MODULE_LANE = ".github/workflows/release-module.yml"
MODULE_REGISTRY = ".github/release-modules.json"
PUBLISH_STEP = ("publish", "Publish to the Forgejo private index")
FOUNDATION_RE = re.compile(r"dotmac[-_.]+deployment[-_.]+foundation", re.IGNORECASE)
GATE_MARKER_RE = re.compile(
    r"foundation[-_]publication[-_]gate|require_rehearsal|recheck_offline_result"
    r"|lane3_offline|gate2|publication_gate|release[-_]facility|foundation[-_]candidate",
    re.IGNORECASE,
)
UPLOAD_RE = re.compile(
    r"twine\s+upload"
    r"|\b(uv|poetry|hatch|flit|pdm)\s+publish"
    r"|\bgh\s+(release|api)\b"
    r"|curl\b.*(\s-T\b|--upload-file|\s-F\b|--form|-X\s*(PUT|POST)|--data|\s-d\b)"
    r"|wget\b.*--post",
    re.IGNORECASE,
)
ALLOWED_USES = re.compile(
    r"actions/(checkout|setup-python|upload-artifact|download-artifact)@[0-9a-f]{40}"
)
LOCAL_USES = {
    "./.github/actions/setup-poetry",
    "./.github/actions/release-recorder-token",
}
MODULE_SCRIPTS = {
    "scripts/assert_current_main.sh",
    "scripts/open_release_record_pr.sh",
    "scripts/release_module.py",
    "scripts/tag_module_release.py",
    "scripts/write_release_record.py",
}
MODULE_SECRETS = {
    "FORGEJO_PUBLISH_TOKEN",
    "GITHUB_TOKEN",
    "RELEASE_RECORDER_PRIVATE_KEY",
}
GUARD_STEPS = {
    "Assert run is on the current protected main SHA",
    "Resolve the allowlisted module",
    "Re-assert the run SHA is still the current protected main",
    "Re-assert the target is allowlisted",
    "Recheck build-once bytes and current main before publication",
    PUBLISH_STEP[1],
}
RECORDER_STEPS = {
    "Mint the narrow release-recorder App token",
    "Open the post-release record",
}
TERMINAL_RE = re.compile(r"\b(always|failure|cancelled)\s*\(")
SHELL = "bash --noprofile --norc -eo pipefail {0}"


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _env_name(job: dict):
    environment = job.get("environment")
    return environment.get("name") if isinstance(environment, dict) else environment


def _module_lane_violations(document: dict, modules: dict) -> list[str]:
    bad: list[str] = []
    triggers = document.get(True, document.get("on"))
    inputs: dict = {}
    if not isinstance(triggers, dict) or set(triggers) != {"workflow_dispatch"}:
        bad.append("triggers must be exactly workflow_dispatch")
    else:
        inputs = triggers["workflow_dispatch"].get("inputs", {})
        if set(inputs) != {"module", "version", "compose_kernel", "compose_with"}:
            bad.append("dispatch inputs changed (a gate selector must not be added)")
    module_input = inputs.get("module", {})
    if module_input.get("type") != "choice":
        bad.append("inputs.module must stay type: choice")
    if sorted(module_input.get("options", [])) != sorted(modules.get("modules", {})):
        bad.append("module options differ from .github/release-modules.json")
    if any(FOUNDATION_RE.search(name) for name in modules.get("modules", {})):
        bad.append("the foundation is in .github/release-modules.json")
    if document.get("permissions") != {"contents": "read"}:
        bad.append("top-level permissions must be contents: read")
    if document.get("defaults", {}).get("run", {}).get("shell") != SHELL:
        bad.append("defaults.run.shell must keep -eo pipefail")
    jobs = document.get("jobs", {})
    if set(jobs) != {"build", "publish", "verify"}:
        bad.append("jobs must be build, publish, verify")
        return bad
    expected_perms = {
        "build": None,
        "publish": {"contents": "read"},
        "verify": {"contents": "write"},
    }
    for name, perms in expected_perms.items():
        if jobs[name].get("permissions") != perms:
            bad.append(f"{name}: permissions changed")
    if _as_list(jobs["publish"].get("needs")) != ["build"]:
        bad.append("publish: must need build")
    if _env_name(jobs["publish"]) != "registry-release":
        bad.append("publish: must run in the registry-release environment")
    if _env_name(jobs["verify"]) != "registry-release":
        bad.append("verify: must run in the registry-release environment")
    if _env_name(jobs["build"]) is not None:
        bad.append("build: must not enter an environment")
    for name, job in jobs.items():
        for key in ("uses", "secrets", "continue-on-error", "container", "services"):
            if key in job:
                bad.append(f"{name}: job-level {key} is refused")
        if not re.match(r"^ubuntu-", str(job.get("runs-on", ""))):
            bad.append(f"{name}: runs-on must be an ubuntu runner")
    for name in ("publish", "verify"):
        if "if" in jobs[name]:
            bad.append(f"{name}: job-level if is refused")
    hits: list[tuple[str, str]] = []
    scripts: set[str] = set()
    for jname, job in jobs.items():
        for step in job.get("steps", []):
            sname = step.get("name")
            if "continue-on-error" in step:
                bad.append(f"{jname}/{sname}: continue-on-error is refused")
            if "shell" in step:
                bad.append(f"{jname}/{sname}: step-level shell is refused")
            condition = str(step.get("if", ""))
            if sname in GUARD_STEPS and "if" in step:
                bad.append(f"{jname}/{sname}: a guard step must be unconditional")
            if TERMINAL_RE.search(condition) and sname not in RECORDER_STEPS:
                bad.append(f"{jname}/{sname}: always/failure/cancelled is refused")
            uses = step.get("uses")
            if uses is not None and not (
                ALLOWED_USES.fullmatch(uses) or uses in LOCAL_USES
            ):
                bad.append(f"{jname}/{sname}: uses {uses} is not allow-listed")
            run = re.sub(r"\s+", " ", step.get("run", ""))
            if UPLOAD_RE.search(run):
                hits.append((jname, sname))
            scripts |= set(re.findall(r"scripts/[\w./-]+", run))
    if hits != [PUBLISH_STEP]:
        bad.append(f"exactly one publish step expected, found {hits}")
    if scripts != MODULE_SCRIPTS:
        bad.append(f"the scripts invoked changed: {sorted(scripts ^ MODULE_SCRIPTS)}")
    dumped = yaml.safe_dump(document)
    if set(re.findall(r"secrets\.(\w+)", dumped)) != MODULE_SECRETS:
        bad.append("the set of secrets referenced changed")
    if "FORGEJO_PUBLISH_TOKEN" in yaml.safe_dump(jobs["build"]):
        bad.append("build: must not hold the publish token")
    if FOUNDATION_RE.search(dumped):
        bad.append("names the deployment foundation")
    marker = GATE_MARKER_RE.search(dumped)
    if marker:
        bad.append(f"references the gate machinery: {marker.group(0)}")
    return bad


def _module_lane() -> tuple[dict, dict]:
    return (
        yaml.safe_load((REPO / MODULE_LANE).read_text()),
        json.loads((REPO / MODULE_REGISTRY).read_text()),
    )


def test_the_module_lane_cannot_reach_the_gate_or_publish_the_foundation():
    document, modules = _module_lane()
    assert _module_lane_violations(document, modules) == []


def _step(document: dict, job: str, name: str) -> dict:
    return next(s for s in document["jobs"][job]["steps"] if s.get("name") == name)


def _add_step(document: dict, job: str, **step) -> None:
    document["jobs"][job]["steps"].append({"name": "added", **step})


_PIN = "a" * 40
MODULE_LANE_MUTATIONS = [
    # triggers, inputs, permissions
    lambda d: d[True].update(push={"branches": ["main"]}),
    lambda d: d[True]["workflow_dispatch"]["inputs"].update(gate={}),
    lambda d: d[True]["workflow_dispatch"]["inputs"]["module"].update(type="string"),
    lambda d: d[True]["workflow_dispatch"]["inputs"]["module"]["options"].append(
        "dotmac-deployment-foundation"
    ),
    lambda d: d[True]["workflow_dispatch"]["inputs"]["module"]["options"].pop(),
    lambda d: d.update(permissions={"contents": "write"}),
    lambda d: d["jobs"]["publish"].update(permissions={"contents": "write"}),
    lambda d: d["jobs"]["publish"].update(
        permissions={"contents": "read", "id-token": "write"}
    ),
    # structure of publish / verify
    lambda d: d["jobs"]["publish"].update(environment="other"),
    lambda d: d["jobs"]["publish"].pop("needs"),
    lambda d: d["jobs"].pop("verify"),
    lambda d: d["jobs"]["build"].update(environment="registry-release"),
    lambda d: d["jobs"]["verify"].update(environment="other"),
    lambda d: d["jobs"]["publish"].update(**{"runs-on": "self-hosted"}),
    # H1: other publish routes
    lambda d: _add_step(d, "verify", run="python -m twine upload dist/*"),
    lambda d: _add_step(d, "verify", run="python -m twine   upload dist/*"),
    lambda d: _add_step(d, "verify", run="python -m TWINE\tupload dist/*"),
    lambda d: _add_step(
        d,
        "verify",
        uses=f"pypa/gh-action-pypi-publish@{_PIN}",
        **{"with": {"repository-url": "$INDEX"}},
    ),
    lambda d: _add_step(d, "publish", run="uv publish dist/*"),
    lambda d: _add_step(d, "publish", run="poetry publish --build"),
    lambda d: _add_step(d, "verify", run='curl -F content=@w.whl "$INDEX"'),
    lambda d: _add_step(d, "verify", run="curl --upload-file w.whl $INDEX"),
    lambda d: _add_step(d, "verify", run="gh release create v1 dist/*"),
    lambda d: _add_step(d, "verify", run="gh api repos/o/r/releases -f x=1"),
    lambda d: d["jobs"].update(
        verify={"uses": "./.github/workflows/x.yml", "secrets": "inherit"}
    ),
    lambda d: d["jobs"]["publish"].update(secrets="inherit"),
    lambda d: _add_step(d, "build", uses="actions/checkout@v4"),
    lambda d: _add_step(d, "build", uses=f"someone/else@{_PIN}"),
    lambda d: _add_step(d, "build", run="bash scripts/new_helper.sh"),
    # M1: guard integrity
    lambda d: _step(
        d, "build", "Assert run is on the current protected main SHA"
    ).update(**{"if": "false"}),
    lambda d: _step(
        d, "publish", "Re-assert the run SHA is still the current protected main"
    ).update(**{"if": "false"}),
    lambda d: _step(d, "publish", "Re-assert the target is allowlisted").update(
        **{"continue-on-error": True}
    ),
    lambda d: _step(d, "publish", PUBLISH_STEP[1]).update(**{"if": "true"}),
    lambda d: d["jobs"]["publish"].update(**{"if": "always()"}),
    lambda d: d["jobs"]["verify"].update(**{"if": "success()"}),
    lambda d: _step(d, "build", "Upload built artifacts").update(**{"if": "failure()"}),
    lambda d: d["defaults"]["run"].update(**{"shell": "bash"}),
    lambda d: _step(d, "build", "Inspect artifacts").update(**{"shell": "bash {0}"}),
    # M2: secrets
    lambda d: _add_step(
        d, "build", run="true", env={"T": "${{ secrets.CANDIDATE_SOURCE_TOKEN }}"}
    ),
    lambda d: _add_step(
        d, "build", run="true", env={"T": "${{ secrets.FORGEJO_PUBLISH_TOKEN }}"}
    ),
    # M3: every spelling of the foundation
    lambda d: _add_step(d, "build", run="echo dotmac-deployment-foundation"),
    lambda d: _add_step(d, "build", run="echo dotmac_deployment_foundation"),
    lambda d: _add_step(
        d, "build", run="ls Dotmac.Deployment.Foundation-0.4.0a2-py3-none-any.whl"
    ),
    # gate machinery in any value
    lambda d: _add_step(d, "build", run="python scripts/require_rehearsal.py"),
    lambda d: _add_step(
        d, "build", run="python scripts/foundation_publication_gate.py"
    ),
    lambda d: _add_step(d, "build", run="python scripts/release_facility.py"),
    lambda d: _add_step(d, "build", run="gh workflow run release-facility.yml"),
    lambda d: _add_step(d, "build", run="gh workflow run foundation-candidate.yml"),
    lambda d: _add_step(d, "build", run="recheck-gate2"),
]


@pytest.mark.parametrize("edit", MODULE_LANE_MUTATIONS)
def test_the_module_lane_check_refuses_structural_mutations(edit):
    document, modules = _module_lane()
    document = copy.deepcopy(document)
    edit(document)
    assert _module_lane_violations(document, modules)


def test_the_module_lane_check_refuses_a_foundation_in_the_module_registry():
    document, modules = _module_lane()
    modules = copy.deepcopy(modules)
    modules["modules"]["dotmac-deployment-foundation"] = {}
    document = copy.deepcopy(document)
    document[True]["workflow_dispatch"]["inputs"]["module"]["options"].append(
        "dotmac-deployment-foundation"
    )
    assert _module_lane_violations(document, modules)
    modules["modules"].pop("dotmac-deployment-foundation")
    assert any(
        "options differ" in v for v in _module_lane_violations(document, modules)
    )


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["jobs"]["publish"].update(needs=["build"]),
        lambda d: d["jobs"]["publish"].update(environment={"name": "registry-release"}),
        lambda d: d.update(jobs=dict(reversed(list(d["jobs"].items())))),
    ],
)
def test_the_module_lane_check_allows_harmless_edits(edit):
    document, modules = _module_lane()
    document = copy.deepcopy(document)
    edit(document)
    assert _module_lane_violations(document, modules) == []


def test_comments_in_the_module_lane_are_free_to_mention_the_gate():
    text = (REPO / MODULE_LANE).read_text() + "\n# see gate2 and publication_gate\n"
    _, modules = _module_lane()
    assert _module_lane_violations(yaml.safe_load(text), modules) == []


def test_no_foundation_package_source_is_part_of_this_change():
    """A static pin: the gate module never reads or writes package source."""
    source = (REPO / "scripts" / "foundation_publication_gate.py").read_text()
    assert "packages/dotmac-deployment-foundation/src" not in source


# ── F-1: the DR3_DR5 adoption condition is structured policy data ───────────


def _conditions(document: dict) -> list:
    return document["exceptions"][0]["adoption_conditions"]


def test_the_shipped_policy_carries_the_structured_adoption_condition():
    found = gate.load_policy().exceptions[(FACILITY, VERSION)]
    assert [c.as_record() for c in found.adoption_conditions] == [
        {
            "id": COND_ID,
            "waives": "DR3_DR5",
            "blocks": "production_governed_deploy",
            "environment": "seabone-staging",
            "required_faults": [
                "db_outage_at_migrate",
                "network_loss",
                "timeout_sigterm",
            ],
            "register_ref": 2155,
        }
    ]
    assert found.max_age_hours == 72
    assert found.subject_evidence == ("RECOVERY", "S3")
    assert not hasattr(gate, "WAIVER_CONDITION")


def test_the_shipped_ruling_version_is_2155():
    found = gate.load_policy().exceptions[(FACILITY, VERSION)]
    assert found.ruling_register == "dotmac-debt-register"
    assert found.ruling_version == 2155
    assert type(found.ruling_version) is int


def _mutate(path_edit):
    document = _policy_doc()
    path_edit(document)
    return document


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: _conditions(d).clear(),
        lambda d: _conditions(d).append(copy.deepcopy(_conditions(d)[0])),
        lambda d: _conditions(d)[0].update(id="lower_case"),
        lambda d: _conditions(d)[0].update(waives="R6"),
        lambda d: _conditions(d)[0].update(blocks="anything"),
        lambda d: _conditions(d)[0].update(environment="production"),
        lambda d: _conditions(d)[0].update(required_faults=["network_loss"]),
        lambda d: _conditions(d)[0].update(required_faults=[]),
        lambda d: _conditions(d)[0].update(
            required_faults=["db_outage_at_migrate", "network_loss", "other"]
        ),
        lambda d: _conditions(d)[0].update(
            required_faults=["network_loss", "network_loss", "timeout_sigterm"]
        ),
        lambda d: _conditions(d)[0].update(register_ref="2155"),
        lambda d: _conditions(d)[0].update(register_ref=2154),
        lambda d: _conditions(d)[0].update(extra=1),
        lambda d: _conditions(d)[0].pop("environment"),
        lambda d: d["exceptions"][0].pop("adoption_conditions"),
        lambda d: d["exceptions"][0].update(adoption_conditions={}),
        lambda d: d["exceptions"][0].update(max_age_hours=0),
        lambda d: d["exceptions"][0].update(max_age_hours="72"),
        lambda d: d["exceptions"][0].update(max_age_hours=True),
        lambda d: d["exceptions"][0].update(max_age_hours=100000),
        lambda d: d["exceptions"][0].pop("max_age_hours"),
        lambda d: d["exceptions"][0].update(subject_evidence=["NOT_REQUIRED"]),
        lambda d: d["exceptions"][0].update(subject_evidence=["S3", "S3"]),
        lambda d: d["exceptions"][0].pop("subject_evidence"),
    ],
)
def test_the_policy_parser_refuses_a_malformed_condition_or_freshness_entry(edit):
    with pytest.raises(gate.PolicyInvalid):
        gate.parse_policy(_policy_bytes(_mutate(edit)))


def test_a_waiver_naming_an_unknown_condition_is_refused(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    item = next(i for i in document["evidence"] if i["id"] == "DR3_DR5")
    item["waiver"]["condition_id"] = "SOMETHING_ELSE"
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.waiver_condition",
    )


def test_a_waiver_without_a_condition_reference_is_refused(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    item = next(i for i in document["evidence"] if i["id"] == "DR3_DR5")
    item["waiver"] = {"register_ref": WAIVER_REF}
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.waiver.schema",
    )


def test_a_waiver_register_ref_that_differs_from_the_condition_is_refused(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    item = next(i for i in document["evidence"] if i["id"] == "DR3_DR5")
    item["waiver"]["register_ref"] = 2154
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.waiver_register_ref",
    )


def test_the_verify_record_keeps_the_waiver_reference(
    wired, monkeypatch, capsys, tmp_path
):
    code, err, _, outdir = _verify(
        wired, monkeypatch, capsys, tmp_path, wired.manifest()
    )
    assert code == 0, err
    items = {
        i["id"]: i for i in json.loads((outdir / "gate.json").read_text())["evidence"]
    }
    assert items["DR3_DR5"]["waiver"] == WAIVER
    assert "waiver" not in items["R6"]
    assert items["RECOVERY"]["subject_sha256"].startswith("sha256:")


def test_the_release_record_refuses_a_waiver_that_does_not_match_the_policy(
    tmp_path, monkeypatch, wired
):
    record, _, path, gate_json = _record_inputs(tmp_path, monkeypatch, wired)
    item = next(i for i in gate_json["evidence"] if i["id"] == "DR3_DR5")
    item["waiver"] = {"register_ref": 2155, "condition_id": "SOMETHING_ELSE"}
    path.write_text(json.dumps(gate_json))
    with pytest.raises(record.ReleaseRecordError):
        record.write_publication_gate_record(
            distribution=FACILITY,
            version=VERSION,
            tag=f"{FACILITY}-v{VERSION}",
            publication_gate="gate2_evidence",
            gate_record=str(path),
        )


# ── F-2: signed evidence expires ────────────────────────────────────────────


def _recheck(wired, monkeypatch, capsys, document):
    _recheck_env(wired, monkeypatch, document)
    code = gate.main(["recheck-gate2", FACILITY, "--version", VERSION])
    return code, capsys.readouterr().err


@pytest.mark.parametrize(
    ("issued", "expires", "now", "reason"),
    [
        # expired: now is at or after expires_at
        ("2026-11-23T11:00:00Z", "2026-11-23T11:30:00Z", NOW, "gate2_manifest.expired"),
        ("2026-11-23T10:00:00Z", "2026-11-23T12:00:00Z", NOW, "gate2_manifest.expired"),
        # not yet valid: issued in the future
        (
            "2026-11-23T13:00:00Z",
            "2026-11-23T14:00:00Z",
            NOW,
            "gate2_manifest.not_yet_valid",
        ),
        # window longer than max_age_hours (72): 72 h + 1 s
        (
            "2026-11-23T11:00:00Z",
            "2026-11-26T11:00:01Z",
            NOW,
            "gate2_manifest.over_max_age",
        ),
        # inverted or empty window
        (
            "2026-11-23T11:00:00Z",
            "2026-11-23T11:00:00Z",
            NOW,
            "gate2_manifest.validity_window",
        ),
        (
            "2026-11-23T11:00:00Z",
            "2026-11-23T10:00:00Z",
            NOW,
            "gate2_manifest.validity_window",
        ),
    ],
)
def test_stale_future_or_overlong_evidence_is_refused_by_both_commands(
    wired, monkeypatch, capsys, tmp_path, issued, expires, now, reason
):
    document = wired.manifest()
    document["issued_at"], document["expires_at"] = issued, expires
    monkeypatch.setattr(gate, "_utcnow", lambda: now)
    _refused(_verify(wired, monkeypatch, capsys, tmp_path, document), reason)
    code, err = _recheck(wired, monkeypatch, capsys, document)
    assert code == 1 and err.strip() == f"REFUSED: {reason}", err


def test_a_window_of_exactly_max_age_is_accepted_and_one_second_past_expiry_is_not(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    document["issued_at"], document["expires_at"] = (
        "2026-11-23T11:00:00Z",
        "2026-11-26T11:00:00Z",
    )
    code, err, _, _ = _verify(wired, monkeypatch, capsys, tmp_path, document)
    assert code == 0, err
    monkeypatch.setattr(gate, "_utcnow", lambda: datetime(2026, 11, 26, 11, 0, 0))
    code, err = _recheck(wired, monkeypatch, capsys, document)
    assert code == 1 and err.strip() == "REFUSED: gate2_manifest.expired", err


def test_recheck_refuses_evidence_that_expired_after_the_build_job(
    wired, monkeypatch, capsys
):
    _recheck_env(wired, monkeypatch)
    assert gate.main(["recheck-gate2", FACILITY, "--version", VERSION]) == 0
    capsys.readouterr()
    monkeypatch.setattr(gate, "_utcnow", lambda: datetime(2026, 11, 24, 11, 0, 0))
    assert gate.main(["recheck-gate2", FACILITY, "--version", VERSION]) == 1
    assert capsys.readouterr().err.strip() == "REFUSED: gate2_manifest.expired"


def test_an_item_recorded_after_the_issue_time_is_refused(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    document["evidence"][0]["recorded_at"] = "2026-11-23T11:00:01Z"
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.recorded_after_issue",
    )
    document["evidence"][0]["recorded_at"] = ISSUED
    code, err, _, _ = _verify(wired, monkeypatch, capsys, tmp_path, document)
    assert code == 0, err


@pytest.mark.parametrize("field", ["issued_at", "expires_at"])
def test_a_manifest_missing_or_malforming_a_freshness_field_is_refused(
    wired, monkeypatch, capsys, tmp_path, field
):
    document = wired.manifest()
    del document[field]
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.schema",
    )
    document = wired.manifest()
    document[field] = "2026-11-23"
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        f"gate2_manifest.{field}",
    )


# ── F-3: subject digest for the review items ────────────────────────────────


@pytest.mark.parametrize("identifier", ["RECOVERY", "S3"])
def test_a_review_item_needs_its_own_subject_digest(
    wired, monkeypatch, capsys, tmp_path, identifier
):
    document = wired.manifest()
    item = next(i for i in document["evidence"] if i["id"] == identifier)
    del item["subject_sha256"]
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.schema",
    )
    item["subject_sha256"] = "sha256:abc"
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.subject_sha256",
    )


def test_a_subject_digest_on_any_other_item_is_refused(
    wired, monkeypatch, capsys, tmp_path
):
    document = wired.manifest()
    document["evidence"][0]["subject_sha256"] = "sha256:" + "7" * 64
    _refused(
        _verify(wired, monkeypatch, capsys, tmp_path, document),
        "gate2_manifest.item.schema",
    )


# ── F-5: signer-file and signature refusals ─────────────────────────────────


def test_a_valid_signature_by_a_key_that_is_not_enrolled_is_refused(
    wired, monkeypatch, capsys, tmp_path
):
    other = tmp_path / "other"
    _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)])
    manifest = gate.canonical_result_bytes(wired.manifest())
    foreign = _run(
        ["ssh-keygen", "-Y", "sign", "-f", str(other), "-n", gate.NAMESPACE],
        data=manifest,
    )
    result = _verify(wired, monkeypatch, capsys, tmp_path, manifest, signature=foreign)
    _refused(result, "gate2_signature_invalid")


@pytest.mark.parametrize(
    ("after", "before"),
    [
        ("20200101000000Z", "20210101000000Z"),  # expired key line
        ("20900101000000Z", "20990101000000Z"),  # future-dated key line
    ],
)
def test_an_expired_or_future_dated_key_line_is_refused(
    wired, monkeypatch, capsys, tmp_path, after, before
):
    wired.signers.write_text(
        wired.signers_line.replace("20200101000000Z", after).replace(
            "20990101000000Z", before
        )
    )
    result = _verify(wired, monkeypatch, capsys, tmp_path, wired.manifest())
    _refused(result, "gate2_signature_invalid")


@pytest.mark.parametrize(
    "key_type", ["ecdsa-sha2-nistp256", "sk-ssh-ed25519@openssh.com", "ssh-rsa"]
)
def test_non_ed25519_key_types_are_refused(world, key_type):
    line = world.signers_line.replace("ssh-ed25519", key_type)
    with pytest.raises(gate.GateRefused, match="gate2_signer.schema"):
        gate.parse_signer(line.encode())


def test_a_multi_line_signer_file_is_refused(wired, monkeypatch, capsys, tmp_path):
    wired.signers.write_text(wired.signers_line * 2)
    result = _verify(wired, monkeypatch, capsys, tmp_path, wired.manifest())
    _refused(result, "gate2_signer.schema")
