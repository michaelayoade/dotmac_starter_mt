"""Every shipped profile must pass the reusable conformance suite.

The suite replays the synthetic transcript corpus (a golden per case), checks
prompt/mode detection and OntRef round-trips, and requires a transcript for
EVERY capability a profile claims. A profile without one fails here.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest
from dotmac_device_cli import builtin_registry
from dotmac_device_cli.conformance import (
    REQUIRED_COVERS,
    chain_dirs,
    conformance_violations,
    load_cases,
    publication_violations,
    replay_case,
)

CORPUS = (
    Path(__file__).resolve().parents[2] / "packages/dotmac-device-cli/tests/transcripts"
)
PROFILES = builtin_registry().profiles


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.id)
def test_profile_passes_the_conformance_suite(profile) -> None:  # type: ignore[no-untyped-def]
    assert conformance_violations(profile, CORPUS) == []


def _all_cases():  # type: ignore[no-untyped-def]
    for profile in PROFILES:
        for cid, case in sorted(
            load_cases(chain_dirs(CORPUS, profile.layer_path)).items()
        ):
            yield pytest.param(profile, case, id=f"{profile.id}::{cid}")


@pytest.mark.parametrize("profile, case", list(_all_cases()))
def test_transcript_replays_to_its_hand_written_golden(profile, case) -> None:  # type: ignore[no-untyped-def]
    assert replay_case(profile, case) == case.expected


def test_every_profile_has_a_corpus_that_covers_the_required_behaviours() -> None:
    for profile in PROFILES:
        cases = load_cases(chain_dirs(CORPUS, profile.layer_path))
        covered = frozenset().union(*(c.covers for c in cases.values()))
        assert REQUIRED_COVERS <= covered, profile.id


def test_every_corpus_file_is_free_of_publishable_data() -> None:
    files = [p for p in CORPUS.rglob("*") if p.is_file() and p.name != ".gitattributes"]
    assert files, "the corpus is empty"
    for path in files:
        text = path.read_bytes().decode("utf-8")
        assert publication_violations(text) == [], path


def test_every_case_declares_a_hand_authored_or_synthesised_origin() -> None:
    import tomllib

    for meta in CORPUS.rglob("*.meta.toml"):
        assert tomllib.loads(meta.read_text())["origin"] in {
            "hand-authored",
            "synthesised",
        }, meta


# ── sensitivity proofs: the suite fires on a planted defect ──────────────────


def _planted_corpus(tmp_path: Path) -> Path:
    dest = tmp_path / "transcripts"
    shutil.copytree(CORPUS, dest)
    return dest


def test_a_claimed_capability_without_a_transcript_fails(tmp_path: Path) -> None:
    corpus = _planted_corpus(tmp_path)
    profile = PROFILES[0]
    claimed = dataclasses.replace(
        profile, operations={**profile.operations, "version": True, "list_onts": True}
    )
    for meta in corpus.rglob("*.meta.toml"):
        if 'operation = "version"' in meta.read_text():
            for suffix in (".meta.toml", ".txt", ".expected.json"):
                meta.with_name(meta.name.removesuffix(".meta.toml") + suffix).unlink()
    problems = conformance_violations(claimed, corpus)
    assert any("claims 'version' but has no transcript" in p for p in problems)


def test_a_true_capability_without_a_transcript_fails() -> None:
    profile = PROFILES[0]  # MA5608T: nothing is proven, so nothing may be true
    claim = dataclasses.replace(
        profile, capabilities={**profile.capabilities, "port_vlan": True}
    )
    problems = conformance_violations(claim, CORPUS)
    assert any("capability 'port_vlan' true but no transcript" in p for p in problems)


def test_a_transcript_tagging_an_unknown_capability_fails(tmp_path: Path) -> None:
    corpus = _planted_corpus(tmp_path)
    meta = corpus / "huawei/ont_inventory/normal.meta.toml"
    meta.write_text(meta.read_text() + 'capabilities = ["port_vlann"]\n')
    problems = conformance_violations(PROFILES[0], corpus)
    assert any("unknown capability 'port_vlann'" in p for p in problems)


def test_a_claim_the_dialect_cannot_serve_fails() -> None:
    profile = PROFILES[0]
    over = dataclasses.replace(
        profile, operations={**profile.operations, "service_ports": True}
    )
    problems = conformance_violations(over, CORPUS)
    assert any("service_ports" in p for p in problems)


def test_a_quirk_citing_a_missing_transcript_fails() -> None:
    profile = PROFILES[0]
    quirks = {
        **profile.quirks,
        "ghost": dataclasses.replace(
            next(iter(profile.quirks.values())), transcripts=("nope/missing",)
        ),
    }
    problems = conformance_violations(
        dataclasses.replace(profile, quirks=quirks), CORPUS
    )
    assert any("missing transcript nope/missing" in p for p in problems)


def test_a_wrong_golden_fails(tmp_path: Path) -> None:
    corpus = _planted_corpus(tmp_path)
    golden = corpus / "huawei/paging/answer_space.expected.json"
    golden.write_text(golden.read_text().replace('"privileged"', '"dirty"'))
    problems = conformance_violations(PROFILES[0], corpus)
    assert any("paging/answer_space differs from its golden" in p for p in problems)


def test_a_corpus_that_lost_a_required_behaviour_fails(tmp_path: Path) -> None:
    corpus = _planted_corpus(tmp_path)
    for suffix in (".meta.toml", ".txt", ".expected.json"):
        (corpus / f"huawei/paging/answer_space{suffix}").unlink()
    problems = conformance_violations(PROFILES[0], corpus)
    assert any("no transcript covers 'pager'" in p for p in problems)


def test_a_prompt_the_profile_cannot_detect_fails(tmp_path: Path) -> None:
    corpus = _planted_corpus(tmp_path)
    spec = corpus / "huawei/conformance.toml"
    spec.write_text(
        spec.read_text().replace(
            'config = "lab-olt(config)#"', 'config = "lab-olt[config]%"'
        )
    )
    problems = conformance_violations(PROFILES[0], corpus)
    assert any("not detected as config" in p for p in problems)


def test_a_dirty_publication_guard_input_is_refused_not_cleaned() -> None:
    assert publication_violations("host 10.1.2.3 up")
    assert publication_violations("serial 48575443AB12CD34")
    assert publication_violations("serial HWTC1234ABCD")
    assert publication_violations("mac 3C:DA:2A:11:22:33")
    assert publication_violations("CUSTOMER_Acme_Corp_authd_20240101")
    assert publication_violations("password hunter2")
    assert publication_violations("olt-a#")
    assert not publication_violations(
        "198.51.100.7 4857544300000001 00:00:5E:00:53:01 lab-olt# "
        "CUSTOMER_01_zone_Zone_1_authd_20240101"
    )
