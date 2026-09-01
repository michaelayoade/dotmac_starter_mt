"""The product's application launcher — `python -m app.runtime --port 8000`.

## Why this module exists at all

The deployment descriptor used to spell the app role's command out in full::

    command = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

That reads as harmless, and it is not. `dotmac_deployment_foundation.document
.build_canonical_document` refuses an address literal ANYWHERE in a descriptor,
because the canonical document is what `dotmac-deployment-control` binds its
independently signed authorization to, and topology resolved in Git is the one
thing that half must not carry. Only the compose renderer opts out
(`refuse_resolved_material=False`, pinned to exactly one call site by
`tests/architecture/test_canonical_document_boundary_flag.py`); everything that
SENDS a document to Control takes the default. So `to_canonical_document()`
raised on this repository's own reference descriptor, `_require_grant` could
never be satisfied, and `dotmac-deploy deploy --execute` could not authorize a
deployment of the Starter at all.

`0.0.0.0` was never topology. It is the address this process binds to INSIDE
its own container — a build-time fact about the image, which the descriptor had
turned into a deployment-time one. The repair is to put it back where it
belongs rather than to weaken the refusal:

* **this module owns the bind address.** It is immutable and not configurable,
  because there is exactly one correct answer inside a container whose host
  side is published on loopback by the rendered compose file.
* **the descriptor owns the launcher and the port** — the two facts a
  deployment really does decide — and now canonicalizes with no exemption.
* **the image digest binds this implementation.** The descriptor names
  `python -m app.runtime`; which bytes that runs is fixed by the image digest
  the descriptor already pins, so nothing about the bind became unreviewable.

The port stays an explicit argument rather than an environment read: the
descriptor's `[[roles]]` command and the health probes' `port` have to agree,
and a launcher that could silently pick a different port from the environment
would let them disagree while both look right.
"""

from __future__ import annotations

import argparse
from typing import Final

#: The container-internal bind address. Every interface, deliberately, and NOT
#: configurable.
#:
#: A container gets its own network namespace, so "every interface" here is the
#: container's own set. Binding `127.0.0.1` instead would make the process
#: unreachable from outside the container, including by the compose publish and
#: by any reverse proxy. The HOST side is where reachability is decided, and
#: that is the renderer's job: `render/compose.py` publishes on loopback and
#: `render/nginx.py` refuses `0.0.0.0` in an upstream.
BIND_ADDRESS: Final = "0.0.0.0"  # noqa: S104 # nosec B104 -- container-internal

#: The ASGI application the descriptor's app role serves.
APP: Final = "app.main:app"


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(
            f"--port must be a TCP port between 1 and 65535 (got {port})"
        )
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.runtime",
        description=(
            "Serve the reference assembly. The bind address is fixed at "
            f"{BIND_ADDRESS} (container-internal); only the port is declared."
        ),
    )
    parser.add_argument(
        "--port",
        type=_port,
        required=True,
        help="TCP port to listen on inside the container.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    import uvicorn

    uvicorn.run(APP, host=BIND_ADDRESS, port=args.port)


if __name__ == "__main__":  # pragma: no cover - process entry point
    main()
