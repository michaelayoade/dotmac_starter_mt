"""Download an authenticated candidate, verify it, install it, observe it, sign it.

The host-side half of ADR-0070's 2026-09-08 amendment, decision 3: "the
installer must download the specific wheel file, verify its digest against
the committed `CandidateArtifact.v1`, and install that exact file" — sharpened
by the 2026-09-09 ruling that a bare `host_source.CandidateReceipt` is not
enough to found that verification on, because it is "a plain, publicly-
constructible dataclass with no factory restriction": any caller with
ordinary Python-level access can build one with fabricated field values.
`dotmac_deployment_foundation.trusted_host_source.verify_candidate_attestation`
(merged Starter #676) is what this module verifies against instead — it turns
a SIGNED `AttestationEnvelopeV2` into an authenticated
`CandidateAttestationSubjectV2`, checked against Control-resolved candidate
trust roots, and its own signature carries no parameter for a subject,
digest, or ad hoc root a caller could hand in instead of proving.

`host_attester.py` is deliberately NOT part of `dotmac_deployment_foundation`
— see its own module docstring for why the signing-capable code must not ship
inside the Foundation wheel (Michael, 2026-09-08: "Foundation verifies; it
does not sign").

## The trust root this module refuses to let its caller choose

If the CALLER of this module could name which envelope, verifier, or trust
policy to use — a `--candidate` path, a `CANDIDATE_ATTESTATION_PATH`
environment variable, a default search location this module invents — the
caller would have authored the trust decision itself. So every function here
takes `candidate` (an `AttestationEnvelopeV2` VALUE), `verifier` (an
`AttestationVerifier`) and `trust_policy` (an `AttestationTrustPolicy`) as
Python objects a trusted composition constructs out of band; none of them
accept a path, filename, URL, or environment variable naming where to find
one. There is no ``os.environ`` read anywhere in this file, no ``argparse``
option resembling a candidate/receipt/root/trust path, and deliberately no
``if __name__ == "__main__":`` block. A trusted composition (Platform's thin
deployment adapter, ADR-0070 decision 6) is expected to call
`download_verify_install_and_attest` directly, in Python, having already
obtained the signed candidate envelope and the Control-resolved trust policy
through its own out-of-band, already-authenticated channel.

## Why install-and-observe is ONE function, not two

An earlier shape of this module split "download, verify, install" (returning
an `InstalledArtifact`) from "sign what was installed" (accepting an
`InstalledArtifact` as an optional parameter, defaulting to `None`, alongside
an optional `metadata` reader). That split was itself a defect: both
`InstalledArtifact` and the installed-metadata reader are exactly the kind of
plain, substitutable value the 2026-09-09 ruling targets — "a parameter,
attribute, or env var through which a caller supplies the installed digest or
metadata is the caller authoring both sides of the agreement". Nothing
stopped a production caller from skipping the download/install step entirely
and calling the signing step directly with a hand-built `InstalledArtifact`
naming any digest it liked.

`download_verify_install_and_attest` closes that by being ONE function: the
install and the re-observation happen inside the same call, `read_installed_
artifact` is invoked with no injectable `metadata` argument, and there is no
parameter anywhere on this function through which a caller can hand in an
installed digest, an `InstalledArtifact`, or a metadata reader in place of
step 4 (below) actually running.

## What this does NOT close, stated because a partial guard misdescribed as a
## complete one is worse than an admitted gap

Refusing a caller-named path to a candidate envelope, and refusing a
caller-supplied installed observation, does not stop a caller with ordinary
Python-level access on the same host from constructing an
`AttestationEnvelopeV2` object directly and handing it to `verify_
candidate_attestation` in memory — that function's own refusals (signature
invalid, key not in the resolved roots, audience/purpose/validity mismatch)
are what actually catch a fabricated envelope, not this module. What IS
closed here: no caller-supplied path, filename, environment variable,
default search location, or bare unverified value can stand in for what
`verify_candidate_attestation` authenticates, and no caller-supplied
`InstalledArtifact` or metadata reader can stand in for what `read_installed_
artifact` observes.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import urllib.parse
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Final

from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.host_source import (
    CandidateReceipt,
    require_host_source,
)
from dotmac_deployment_foundation.trusted_host_source import (
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationVerifier,
    CandidateAttestationSubjectV2,
    verify_candidate_attestation,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from host_attester import (
    HostAttesterSigner,
    build_installed_attestation,
)

__all__ = [
    "DOWNLOAD_DIGEST_MISMATCH",
    "INVALID_WHEEL_FILENAME",
    "download_exact_wheel",
    "download_verify_install_and_attest",
    "install_exact_wheel",
    "sha256_of",
    "verify_downloaded_digest",
]

#: The downloaded bytes are not the ones the authenticated candidate subject
#: names. The repair is to re-fetch, or to establish which of the download and
#: the candidate attestation is stale — never to install anyway and check
#: afterward.
DOWNLOAD_DIGEST_MISMATCH: Final = "host-attester-download-digest-mismatch"

#: The download location's own filename is not a syntactically valid wheel
#: filename, or it names a different package/version than the authenticated
#: candidate subject. Distinct from `DOWNLOAD_DIGEST_MISMATCH`: that code says
#: "the bytes are wrong"; this one says "pip would refuse this file by name
#: alone, before a single byte is compared".
INVALID_WHEEL_FILENAME: Final = "host-attester-invalid-wheel-filename"

#: PEP 427's wheel filename grammar:
#: ``{distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform
#: tag}.whl``. A real `pip install <file>.whl` parses the file's name this way
#: to determine compatibility BEFORE looking at its contents — a filename with
#: fewer than five hyphen-separated segments (what `{distribution}-{version}
#: .whl` alone produces) is not a wheel filename at all, and pip refuses it
#: outright with `InvalidWheelFilename`. This is exactly what let this
#: module's own prior defect (reconstructing `{facility}-{version}.whl`,
#: dropping the mandatory python/abi/platform tags) ship undetected: the
#: tests injected a fake installer, so nothing ever asked pip to parse the
#: name.
_WHEEL_FILENAME: Final = re.compile(
    r"^(?P<name>[A-Za-z0-9_.]+)-(?P<version>[A-Za-z0-9_.]+)"
    r"(?:-(?P<build>\d[A-Za-z0-9_.]*))?"
    r"-(?P<python>[A-Za-z0-9_.]+)-(?P<abi>[A-Za-z0-9_.]+)-(?P<platform>[A-Za-z0-9_.]+)\.whl$"
)


def sha256_of(path: Path) -> Digest:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return Digest.parse(digest.hexdigest(), where=str(path))


def _canonical_wheel_filename(
    download_url: str, subject: CandidateAttestationSubjectV2
) -> str:
    """The wheel filename this download names — CARRIED THROUGH, never rebuilt.

    Never regenerated from ``subject.package``/``subject.version``: doing so
    is exactly the defect this repair closes — a facility/version
    reconstruction silently drops the mandatory python/abi/platform
    compatibility tags, producing a filename real pip refuses outright before
    installation even begins. Instead this reads the name the publisher's own
    release process gave the file, from the one place that name is still
    attached to these particular bytes: the download location itself.
    ``download_url`` names WHERE to fetch from, never WHO is trusted to vouch
    for the result — `verify_downloaded_digest`, called after this, still
    refuses before install if the fetched bytes are not what the
    authenticated candidate subject's digest names, so a URL that lies about
    the filename gains an attacker nothing beyond a refused install. The
    filename/version cross-check below exists only so a real installer sees a
    file it can even parse, and so a URL naming the wrong artifact is caught
    before a byte is downloaded.
    """
    name = urllib.parse.urlsplit(download_url).path.rsplit("/", 1)[-1]
    match = _WHEEL_FILENAME.fullmatch(name)
    if match is None:
        raise PreconditionFailed(
            f"{name!r} (from {download_url}) is not a syntactically valid "
            "wheel filename. Real pip requires "
            "{distribution}-{version}-{python tag}-{abi tag}-{platform "
            "tag}.whl and refuses anything else before this module ever "
            "shells out to it — reconstructing a filename from a facility "
            "name and version alone (dropping the tags) is exactly the "
            "defect this check exists to catch",
            code=INVALID_WHEEL_FILENAME,
        )
    expected_name = subject.package.replace("-", "_")
    expected_version = subject.version
    if (
        match.group("name") != expected_name
        or match.group("version") != expected_version
    ):
        raise PreconditionFailed(
            f"{name!r} names {match.group('name')} {match.group('version')}, "
            f"which does not match the authenticated candidate subject "
            f"{expected_name} {expected_version}. The download location "
            "names a different artifact than the one the signed candidate "
            "attestation authenticated",
            code=INVALID_WHEEL_FILENAME,
        )
    return name


def download_exact_wheel(
    url: str,
    dest: Path,
    *,
    opener: Callable[[str], _Readable] | None = None,
    timeout_seconds: float = 60.0,
) -> Path:
    """Fetch bytes from `url` into `dest`.

    `url` names WHERE to fetch from, not WHO is trusted to vouch for the
    result — that is `verify_downloaded_digest`'s job, against the digest the
    authenticated candidate subject names. An attacker who controls `url`
    alone (a compromised mirror, a redirected registry) gains nothing here:
    the fetched bytes are refused before installation unless they match.
    `opener` is an injection seam for tests; production callers leave it as
    the default, which uses `urllib.request`.
    """
    open_url = opener if opener is not None else _urllib_opener
    response = open_url(url)
    try:
        with dest.open("wb") as handle:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    return dest


class _Readable:
    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


def _urllib_opener(url: str) -> _Readable:
    import urllib.request

    return urllib.request.urlopen(url, timeout=60.0)  # noqa: S310 -- exact-artifact fetch, digest-verified below


def verify_downloaded_digest(path: Path, expected: Digest) -> None:
    """Refuse BEFORE anything downstream treats `path` as trusted.

    `expected` must already be the digest an authenticated candidate subject
    named — this function makes no trust decision of its own, it only
    compares. Order matters: this is called, and must raise, strictly before
    install runs.
    """
    if not isinstance(expected, Digest):
        raise PreconditionFailed(
            f"a Digest value is required and {type(expected).__name__!r} was "
            "supplied — this function resolves no digest of its own, the "
            "caller must supply one already produced by authenticating the "
            "candidate attestation",
            code=DOWNLOAD_DIGEST_MISMATCH,
        )
    actual = sha256_of(path)
    if actual != expected:
        raise PreconditionFailed(
            f"{path} has digest {actual}, which does not match the "
            f"authenticated candidate subject's {expected}. Refusing before "
            "install: installing first and checking the digest afterward "
            "would run bytes nobody vouched for",
            code=DOWNLOAD_DIGEST_MISMATCH,
        )


def install_exact_wheel(
    wheel: Path,
    *,
    python: Path | None = None,
    runner: Callable[[list[str]], None] | None = None,
) -> None:
    """`pip install` the exact local FILE, never a name/version resolved by an
    index — this is what makes PEP 610 record a real `archive_info.hashes.
    sha256` afterward (ADR-0070 decision 3). `runner` is an injection seam for
    tests; production callers leave it as the default, which shells to `pip`.
    """
    command = [
        str(python or Path(sys.executable)),
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--force-reinstall",
        str(wheel),
    ]

    def _default_runner(argv: list[str]) -> None:
        subprocess.run(argv, check=True)

    run = runner if runner is not None else _default_runner
    run(command)


def download_verify_install_and_attest(
    *,
    candidate: AttestationEnvelopeV2,
    verifier: AttestationVerifier,
    trust_policy: AttestationTrustPolicy,
    now: datetime,
    download_url: str,
    workdir: Path,
    expected_host_identity: str,
    signer: HostAttesterSigner,
    issuer: str,
    key_id: str,
    algorithm: str,
    custody_domain: str,
    trust_root_version: str,
    observation_id: str,
    issued_at: datetime,
    expires_at: datetime,
    python: Path | None = None,
    downloader: Callable[..., Path] | None = None,
    installer: Callable[..., None] | None = None,
) -> AttestationEnvelopeV2:
    """Download, verify, install, re-observe, sign — ONE call, in that order.

    1. `verify_candidate_attestation` authenticates `candidate` against
       `trust_policy`'s Control-resolved roots and returns the subject that
       survived that check — never a subject this module or its caller
       supplied.
    2. The exact wheel is downloaded to the filename the download location
       itself names (never reconstructed — see `_canonical_wheel_filename`)
       and its digest is verified against the authenticated subject's
       `wheel_sha256` BEFORE anything installs.
    3. `pip install`s that exact local file into `python` (or the running
       interpreter).
    4. `read_installed_artifact` re-reads PEP 610 metadata from THAT SAME
       interpreter, with no injectable `metadata` reader — there is
       structurally nowhere for a caller to substitute an observation.
    5. The freshly-read `HostSource` (bound via `require_host_source` to a
       `CandidateReceipt` built ONLY from the authenticated subject's own
       fields, never from a caller-supplied receipt) is signed.

    No parameter here can carry an installed digest, an `InstalledArtifact`,
    or an installed-metadata reader in place of step 4 actually running.
    """
    subject = verify_candidate_attestation(
        candidate=candidate, verifier=verifier, trust_policy=trust_policy, now=now
    )

    filename = _canonical_wheel_filename(download_url, subject)
    dest = workdir / filename
    fetch = downloader if downloader is not None else download_exact_wheel
    install = installer if installer is not None else install_exact_wheel

    fetch(download_url, dest)
    verify_downloaded_digest(dest, subject.wheel_sha256)  # refuses BEFORE install
    install(dest, python=python)

    # Re-observe through the SAME interpreter that just installed the wheel —
    # `read_installed_artifact` is called with no `metadata` argument, so
    # there is no seam here through which a caller could hand in an answer
    # instead of letting this read the interpreter's own PEP 610 record.
    from dotmac_deployment_foundation.host_source import read_installed_artifact

    installed = read_installed_artifact(subject.package)

    receipt = CandidateReceipt(
        facility=subject.package,
        version=subject.version,
        artifact_digest=subject.wheel_sha256,
        source_revision=subject.source_revision,
        repository=subject.repository,
        run_id=subject.run_id,
        artifact_id=subject.artifact_id,
    )
    host_source = require_host_source(
        receipt=receipt,
        installed=installed,
        distribution=subject.package,
    )
    return build_installed_attestation(
        host_source=host_source,
        expected_host_identity=expected_host_identity,
        signer=signer,
        issuer=issuer,
        key_id=key_id,
        algorithm=algorithm,
        custody_domain=custody_domain,
        trust_root_version=trust_root_version,
        observation_id=observation_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
