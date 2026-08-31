"""Artifact identity refuses everything a publisher can move.

This is the module's load-bearing property: `docs/design/domain-foundation.md`
requires that the exact artifact a deployment runs never be "a mutable tag such
as `:latest`". A test suite that only proved the happy path would pass against
an implementation that accepted tags, which is the failure this file exists to
make impossible.
"""

from __future__ import annotations

import pytest
from dotmac_release_catalog import (
    ARTIFACT_KINDS,
    ATTESTATION_KINDS,
    ArtifactIdentityError,
    ArtifactKind,
    AttestationKind,
    Digest,
    DigestError,
    UnpinnedReferenceError,
    pinned_reference,
)

_HEX = "a" * 64
_OTHER_HEX = "b" * 64
_DIGEST = f"sha256:{_HEX}"


class TestDigestParsing:
    def test_parses_a_well_formed_sha256_digest(self) -> None:
        digest = Digest.parse(_DIGEST)
        assert digest.algorithm == "sha256"
        assert digest.hex_digest == _HEX
        assert str(digest) == _DIGEST

    def test_surrounding_whitespace_is_stripped_not_rejected(self) -> None:
        assert Digest.parse(f"  {_DIGEST}\n") == Digest.parse(_DIGEST)

    def test_digests_compare_by_value(self) -> None:
        assert Digest.parse(_DIGEST) == Digest.parse(_DIGEST)
        assert Digest.parse(_DIGEST) != Digest.parse(f"sha256:{_OTHER_HEX}")

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("", id="empty"),
            pytest.param(_HEX, id="bare-hex-no-algorithm"),
            pytest.param("sha256:", id="algorithm-no-hex"),
            pytest.param(f":{_HEX}", id="hex-no-algorithm"),
            pytest.param("latest", id="a-tag"),
            pytest.param("app:1.4.2", id="a-versioned-tag"),
            pytest.param(f"sha256:{_HEX}:extra", id="trailing-component"),
        ],
    )
    def test_refuses_anything_that_is_not_a_digest(self, value: str) -> None:
        with pytest.raises(DigestError):
            Digest.parse(value)

    def test_refuses_an_unknown_algorithm_rather_than_trusting_the_shape(self) -> None:
        """A weaker algorithm that still parses would be compared for equality
        with the same confidence as a strong one."""
        with pytest.raises(DigestError, match="unsupported digest algorithm"):
            Digest.parse(f"md5:{'a' * 32}")

    @pytest.mark.parametrize(
        ("hex_digest", "label"),
        [("a" * 63, "one-short"), ("a" * 65, "one-long")],
    )
    def test_refuses_a_sha256_of_the_wrong_width(
        self, hex_digest: str, label: str
    ) -> None:
        with pytest.raises(DigestError, match="64 hex characters"):
            Digest.parse(f"sha256:{hex_digest}")

    def test_refuses_uppercase_rather_than_normalising_it(self) -> None:
        """Two spellings of one digest must not both be storable — the `digest`
        column is UNIQUE, and case-folding here would let the same artifact be
        inserted twice under names the database sees as different."""
        with pytest.raises(DigestError):
            Digest.parse(f"sha256:{'A' * 64}")

    def test_cannot_be_constructed_around_the_validation(self) -> None:
        """`__post_init__` runs on direct construction too, so accepting a
        `Digest` in a signature is accepting a checked value."""
        with pytest.raises(DigestError):
            Digest(algorithm="sha256", hex_digest="short")


class TestPinnedReference:
    def test_accepts_a_digest_pinned_reference_unchanged(self) -> None:
        ref = f"registry.example.com/dotmac/app@{_DIGEST}"
        assert pinned_reference(ref) == ref

    def test_proves_the_reference_pins_the_expected_digest(self) -> None:
        ref = f"registry.example.com/dotmac/app@{_DIGEST}"
        assert pinned_reference(ref, expected=Digest.parse(_DIGEST)) == ref

    def test_refuses_a_reference_pinning_a_different_artifact(self) -> None:
        """The reference and the digest live in adjacent columns, and adjacent
        columns drift. A row whose ref pins other bytes passes every syntactic
        check and deploys the wrong thing."""
        ref = f"registry.example.com/dotmac/app@sha256:{_OTHER_HEX}"
        with pytest.raises(UnpinnedReferenceError, match="same bytes"):
            pinned_reference(ref, expected=Digest.parse(_DIGEST))

    @pytest.mark.parametrize(
        "reference",
        [
            pytest.param("registry.example.com/dotmac/app:latest", id="floating-tag"),
            pytest.param("registry.example.com/dotmac/app:1.4.2", id="version-tag"),
            pytest.param("registry.example.com/dotmac/app", id="no-tag-no-digest"),
            pytest.param(f"@{_DIGEST}", id="digest-with-no-name"),
            pytest.param(
                f"registry.example.com/app@{_DIGEST}:latest",
                id="pin-with-a-tag-appended",
            ),
        ],
    )
    def test_refuses_anything_a_publisher_could_move(self, reference: str) -> None:
        with pytest.raises(UnpinnedReferenceError):
            pinned_reference(reference)

    def test_a_pinned_reference_with_a_bad_digest_raises_the_digest_error(
        self,
    ) -> None:
        """The reference IS digest-pinned — the digest itself is wrong. Reporting
        "not digest-pinned" here would send the reader to the wrong layer, so the
        two errors stay distinct and share `ArtifactIdentityError` instead."""
        ref = f"registry.example.com/app@sha256:{'a' * 63}"
        with pytest.raises(DigestError, match="64 hex characters"):
            pinned_reference(ref)
        # A caller that only wants "is this usable?" catches one type.
        with pytest.raises(ArtifactIdentityError):
            pinned_reference(ref)

    def test_both_refusals_share_one_catchable_base(self) -> None:
        assert issubclass(DigestError, ArtifactIdentityError)
        assert issubclass(UnpinnedReferenceError, ArtifactIdentityError)

    def test_the_refusal_names_the_offending_value(self) -> None:
        """This failure is always a bug in the layer above — something resolved
        a tag where it should have resolved a digest — so the message has to
        make that layer findable."""
        with pytest.raises(UnpinnedReferenceError, match="app:latest"):
            pinned_reference("registry.example.com/app:latest")


class TestVocabularies:
    def test_artifact_kinds_are_exactly_the_three_published_today(self) -> None:
        assert {k.value for k in ARTIFACT_KINDS} == {
            "container_image",
            "python_wheel",
            "offline_bundle",
        }

    def test_attestation_kinds_answer_five_distinct_questions(self) -> None:
        """Inside / built-how / vouched-by / capabilities / database structure.

        Merging any two into "provenance" is what lets an artifact look
        attested for a claim no document actually makes.
        """
        assert {k.value for k in ATTESTATION_KINDS} == {
            "sbom",
            "provenance",
            "signature",
            "product_manifest",
            "product_database_catalog",
        }

    def test_members_are_plain_strings_for_a_text_column(self) -> None:
        assert ArtifactKind.CONTAINER_IMAGE == "container_image"
        assert AttestationKind.SBOM == "sbom"
