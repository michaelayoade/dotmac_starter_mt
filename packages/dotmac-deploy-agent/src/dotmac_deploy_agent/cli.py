"""Entry point: `dotmac-deploy-agent --config <file> [--once]`.

Runs as a dedicated non-root user (systemd `User=`), single instance, outbound only.
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .client import TargetClient
from .commands import ReadOnlyCommands, SubprocessCommands, SubprocessRegistryLauncher
from .config import AgentConfig, refuse_db_environment
from .files import ensure_private_dir
from .launcher import Launcher
from .lease import LeaseClient
from .refusal import Refusal
from .runner import SubprocessRunner
from .systemd_creds import read_credential
from .transfer import ImageTransfer
from .transport import HttpsTransport, OpenBaoTransport
from .wire import PopSigner

LOG = logging.getLogger("dotmac.deploy_agent")


def build_launcher(config: AgentConfig) -> Launcher:
    environ = os.environ
    signer = PopSigner.from_seed(
        config.credential_id,
        read_credential(environ, config.pop_key_credential, code="config_invalid"),
    )
    signer.check_admissible()
    client = TargetClient(
        config,
        signer,
        HttpsTransport(
            config.cp_url, spki_pins=config.cp_spki_sha256, ca_file=config.cp_ca_file
        ),
    )
    runner = SubprocessRunner(config.foundation_argv, config.foundation_timeout_seconds)
    commands = SubprocessCommands()
    return Launcher(
        config,
        client,
        runner,
        OpenBaoTransport(config.openbao_url),
        LeaseClient(client),
        ImageTransfer(config, commands, SubprocessRegistryLauncher()),
        ReadOnlyCommands(commands),
        commands,
        environ=environ,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dotmac-deploy-agent")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        refuse_db_environment(os.environ)
        config = AgentConfig.load(args.config)
        config.check_instance(os.environ)
        ensure_private_dir(config.state_dir)
        lock_path = config.state_dir / "agent.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        launcher = build_launcher(config)
        while True:
            try:
                result = launcher.run_once()
                LOG.info("cycle outcome=%s reason=%s", result.outcome, result.reason)
            except Refusal as refusal:
                LOG.error("cycle refused code=%s", refusal)
            if args.once:
                return 0
            time.sleep(config.poll_seconds)
    except (Refusal, OSError) as error:
        LOG.error(
            "agent stopped code=%s", error if isinstance(error, Refusal) else "os_error"
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
