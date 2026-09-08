"""Download, verify against a trusted candidate digest, install, then attest.

The host-side half of ADR-0070's 2026-09-08 amendment, decision 3: "the
installer must download the specific wheel file, verify its digest against
the committed `CandidateArtifact.v1`, and install that exact file" — and only
THEN let `dotmac_deployment_foundation.host_attester` sign what was actually
installed.

## The trust root this module refuses to let its caller choose

A `CandidateReceipt` is what makes a downloaded file's digest MEAN anything: it
is the record saying "these particular bytes are the ones the release process
vouches for." If the CALLER of this module could name which receipt to trust
— a `--receipt` path, a `CANDIDATE_RECEIPT_PATH` environment variable, a
default search location this module invents — the caller would have authored
the trust decision itself, and the digest comparison downstream would still
look rigorous in review while proving nothing at all: an attacker able to
substitute the downloaded wheel can, under that design, simply substitute a
matching receipt beside it.

So every function here takes a `CandidateReceipt` VALUE
(`dotmac_deployment_foundation.host_source.CandidateReceipt`) and NONE of them
accept a path, filename, URL, or environment variable naming where to find
one. There is no ``os.environ`` read anywhere in this file, no ``argparse``
option resembling a receipt/root/trust path, and deliberately no
``if __name__ == "__main__":`` block — a CLI entry point would need exactly
the caller-facing "which receipt" input this module refuses to open. A trusted
composition (Platform's thin deployment adapter, ADR-0070 decision 6) is
expected to call `download_verify_install` and `attest_installed_host`
directly, in Python, having already obtained a `CandidateReceipt` through its
own out-of-band, already-authenticated channel — a channel Control does not
yet build (the same gap the 2026-09-07 "trusted host provenance v2" amendment
records for `AttestationTrustPolicy` roots).

## What this does NOT close, stated because a partial guard misdescribed as a
## complete one is worse than an admitted gap

`CandidateReceipt` (`host_source.py`, a module this lane does not own) is a
plain, publicly-constructible dataclass with no factory restriction — unlike
`HostSource`, which `host_source.py`'s own docstring says is "constructible
only through `require_host_source`." Refusing a caller-named PATH to a receipt
file does not, by itself, stop a caller with ordinary Python-level access on
the same host from constructing a `CandidateReceipt` object directly with
fabricated field values and passing it to `download_verify_install` in
memory. Closing that fully needs either (a) Control's authenticated delivery
of receipt bytes plus signature verification against a Control-enrolled root
BEFORE a `CandidateReceipt` is ever constructed, or (b) hardening
`CandidateReceipt` itself the way `HostSource` is hardened — both of which are
changes to shared `host_source.py`/`trusted_host_source.py` behaviour this
lane does not make unilaterally. What IS closed here: no caller-supplied path,
filename, environment variable or default search location can stand in for a
`CandidateReceipt` value in this module's own surface.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Final

from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.host_attester import (
    HostAttesterSigner,
    build_installed_attestation,
)
from dotmac_deployment_foundation.host_source import (
    CandidateReceipt,
    InstalledArtifact,
    InstalledMetadata,
    require_host_source,
)
from dotmac_deployment_foundation.trusted_host_source import AttestationEnvelopeV2

__all__ = [
    "DOWNLOAD_DIGEST_MISMATCH",
    "NOT_A_CANDIDATE_RECEIPT",
    "attest_installed_host",
    "download_exact_wheel",
    "download_verify_install",
    "install_exact_wheel",
    "sha256_of",
    "verify_downloaded_digest",
]

#: The downloaded bytes are not the recorded candidate. The repair is to
#: re-fetch, or to establish which of the download and the receipt is stale —
#: never to install anyway and check afterward.
DOWNLOAD_DIGEST_MISMATCH: Final = "host-attester-download-digest-mismatch"

#: A `CandidateReceipt` was required and something else was offered — a dict,
#: a string, a hand-shaped stand-in. Distinct from `DOWNLOAD_DIGEST_MISMATCH`
#: because the repair is different: one says "the bytes are wrong", this says
#: "the caller did not supply the type that makes a digest a trust decision".
NOT_A_CANDIDATE_RECEIPT: Final = "host-attester-not-a-candidate-receipt"


def sha256_of(path: Path) -> Digest:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return Digest.parse(digest.hexdigest(), where=str(path))


def _require_receipt(receipt: object) -> CandidateReceipt:
    """The one gate every function below runs its `receipt` argument through.

    Refuses independently of every other component's state: it does not
    consult a signer, a verifier, a trust policy, or the filesystem. A
    misconfigured or entirely absent signer elsewhere in the pipeline cannot
    make this check pass, and a correctly configured one cannot make it more
    strict — it looks at exactly one thing, the Python type in hand.
    """
    if not isinstance(receipt, CandidateReceipt):
        raise PreconditionFailed(
            f"a CandidateReceipt value is required and {type(receipt).__name__!r} "
            "was supplied. This module resolves no receipt from a path, "
            "filename, environment variable or default location — a caller "
            "must supply the value directly, from a trusted source it "
            "obtained out of band",
            code=NOT_A_CANDIDATE_RECEIPT,
        )
    return receipt


def download_exact_wheel(
    url: str,
    dest: Path,
    *,
    opener: Callable[[str], _Readable] | None = None,
    timeout_seconds: float = 60.0,
) -> Path:
    """Fetch bytes from `url` into `dest`.

    `url` names WHERE to fetch from, not WHO is trusted to vouch for the
    result — that is `verify_downloaded_digest`'s job, against a
    caller-supplied `CandidateReceipt`. An attacker who controls `url` alone
    (a compromised mirror, a redirected registry) gains nothing here: the
    fetched bytes are refused before installation unless they match the
    receipt's digest. `opener` is an injection seam for tests; production
    callers leave it as the default, which uses `urllib.request`.
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


def verify_downloaded_digest(path: Path, receipt: object) -> None:
    """Refuse BEFORE anything downstream treats `path` as trusted.

    Order matters: this is called, and must raise, strictly before
    `install_exact_wheel` runs. `download_verify_install` enforces that
    ordering; this function only makes the comparison, so it stays true
    however a future caller sequences the two.
    """
    bound = _require_receipt(receipt)
    actual = sha256_of(path)
    if actual != bound.artifact_digest:
        raise PreconditionFailed(
            f"{path} has digest {actual}, which does not match the "
            f"CandidateArtifact.v1 receipt's {bound.artifact_digest} for "
            f"{bound.facility} {bound.version}. Refusing before install: "
            "installing first and checking the digest afterward would run "
            "bytes nobody vouched for",
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


def download_verify_install(
    *,
    receipt: object,
    download_url: str,
    workdir: Path,
    python: Path | None = None,
    downloader: Callable[..., Path] | None = None,
    installer: Callable[..., None] | None = None,
    metadata: InstalledMetadata | None = None,
) -> InstalledArtifact:
    """Download, verify against `receipt` BEFORE installing, install, re-read.

    Returns the freshly re-read `InstalledArtifact` — never a value computed
    before installation, and never one the caller could substitute: it comes
    from `dotmac_deployment_foundation.host_source.read_installed_artifact`,
    imported lazily below so tests can supply `metadata` without needing a
    real interpreter's site-packages.
    """
    bound = _require_receipt(receipt)
    fetch = downloader if downloader is not None else download_exact_wheel
    install = installer if installer is not None else install_exact_wheel

    dest = workdir / f"{bound.facility.replace('-', '_')}-{bound.version}.whl"
    fetch(download_url, dest)
    verify_downloaded_digest(dest, bound)  # refuses BEFORE install, see above
    install(dest, python=python)

    from dotmac_deployment_foundation.host_source import read_installed_artifact

    return read_installed_artifact(bound.facility, metadata=metadata)


def attest_installed_host(
    *,
    receipt: object,
    expected_host_identity: str,
    signer: HostAttesterSigner,
    issuer: str,
    key_id: str,
    algorithm: str,
    public_key_fingerprint: str,
    custody_domain: str,
    trust_root_version: str,
    observation_id: str,
    issued_at: datetime,
    expires_at: datetime,
    installed: InstalledArtifact | None = None,
    metadata: InstalledMetadata | None = None,
) -> AttestationEnvelopeV2:
    """Bind the receipt to what is ACTUALLY installed, then sign that binding.

    `require_host_source` does the binding (and refuses `ABSENT`, `WRONG_KIND`,
    `DISAGREES` or `NO_RECEIPT` — the four refusals `host_source.py` already
    hardens); `build_installed_attestation` refuses to accept anything but the
    `HostSource` that binding produces. Neither step here accepts an installed
    digest as its own parameter.
    """
    bound = _require_receipt(receipt)
    host_source = require_host_source(
        receipt=bound,
        installed=installed,
        distribution=bound.facility,
        metadata=metadata,
    )
    return build_installed_attestation(
        host_source=host_source,
        expected_host_identity=expected_host_identity,
        signer=signer,
        issuer=issuer,
        key_id=key_id,
        algorithm=algorithm,
        public_key_fingerprint=public_key_fingerprint,
        custody_domain=custody_domain,
        trust_root_version=trust_root_version,
        observation_id=observation_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
