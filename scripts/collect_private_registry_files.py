#!/usr/bin/env python3
"""Download an enumerated private-index file set without credential URLs or argv.

The enumerated by-name read itself lives in `registry_read.RegistryReader`, so
the facility release lane and this kernel adapter share ONE implementation
rather than two that drift. What stays here is what is specific to the kernel
verification lane: the canonical index, the read-only identity it must be, the
canonical filename derivation, and the typed observation the decision core
consumes.

`SameOriginRedirect` is re-exported because it is the redirect policy this
adapter's own tests exercise directly; it is the same class, not a copy.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from registry_read import RegistryReader, SameOriginRedirect
from release_artifact_verification import canonical_kernel_filenames

__all__ = ["SameOriginRedirect", "main"]

REGISTRY_INDEX = (
    "https://registry.dotmac.io/api/packages/dotmac/pypi/simple/dotmac-kernel/"
)
REGISTRY_IDENTITY = "https://registry.dotmac.io/api/v1/user"
REGISTRY_ORIGIN = "https://registry.dotmac.io"
REGISTRY_LOGIN = "ci-reader"


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"registry collection refused: {name} is required")
    return value


def main() -> int:
    index = REGISTRY_INDEX
    username = REGISTRY_LOGIN
    password = required("REGISTRY_PASSWORD")
    version = required("RELEASE_VERSION")
    expected = frozenset(filter(None, required("EXPECTED_FILENAMES").split("\n")))
    if expected != canonical_kernel_filenames(version):
        raise SystemExit("registry collection refused: filenames are not canonical")
    output = Path(required("REGISTRY_OUTPUT_DIR"))
    reader = RegistryReader(index, username, password)
    password = ""
    reader.collect(expected, output)
    identity = json.loads(reader.get(REGISTRY_IDENTITY))
    if identity.get("login") != username or identity.get("is_admin") is not False:
        raise SystemExit("registry collection refused: credential identity differs")
    observations = []
    for name in sorted(expected):
        path = output / name
        observations.append({"name": name, "size": path.stat().st_size})
    observation_path = Path(required("REGISTRY_OBSERVATION"))
    observation_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    observation_path.write_text(
        json.dumps(
            {
                "schema": "PrivateRegistryReadObservation.v1",
                "index_origin": REGISTRY_ORIGIN,
                "observed_identity": {
                    "login": str(identity["login"]),
                    "is_admin": False,
                },
                "facility_http_methods": ["GET"],
                "files": observations,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    observation_path.chmod(0o600)
    print(f"collected {len(expected)} registry files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
