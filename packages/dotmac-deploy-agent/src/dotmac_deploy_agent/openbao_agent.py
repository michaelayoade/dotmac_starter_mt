"""OpenBao Agent config install (DESIGN-rev3 s2.1 L6).

One config per role. The OpenBao Agent (a separate systemd unit) logs in with
AppRole using the `role_id`/`secret_id` files the agent delivered and renders
each binding as a tmpfs file `/run/dotmac/<product>/<role>/secrets/<NAME>` (0400),
which the Foundation spec mounts via `SECRETS_DIR_<ROLE>`.

Assumptions recorded for the WP7/WP10 read-back (not provable here): KV v2 mounted
at `kv`, secret path `<product>/<role>`, field = binding name, namespace
`<env>/<product>`, AppRole at `auth/approle`. The config holds no secret value.
"""

from __future__ import annotations

import re
from typing import Final

from .canon import digest
from .config import AgentConfig
from .files import require_private_dir, write_private
from .materials import RoleBindings
from .refusal import Refusal

_SAFE: Final = re.compile(r"[A-Za-z0-9_./:@-]+")


def _safe(value: str) -> str:
    if not _SAFE.fullmatch(value):
        raise Refusal("openbao_config_invalid")
    return value


def render_agent_config(config: AgentConfig, role: RoleBindings) -> str:
    base = _safe(str(config.run_dir / role.role))
    namespace = _safe(f"{config.environment}/{config.product}")
    remove = "true" if role.single_use else "false"
    blocks = [
        "# Managed by dotmac-deploy-agent. Holds no secret value.",
        "vault {",
        f'  address = "{_safe(config.openbao_url)}"',
        "}",
        "auto_auth {",
        '  method "approle" {',
        f'    namespace = "{namespace}"',
        '    mount_path = "auth/approle"',
        "    config = {",
        f'      role_id_file_path = "{base}/role_id"',
        f'      secret_id_file_path = "{base}/secret_id"',
        f"      remove_secret_id_file_after_reading = {remove}",
        "    }",
        "  }",
        "}",
        "template_config {",
        "  exit_on_retry_failure = true",
        "}",
    ]
    for binding in sorted(role.names, key=lambda item: item.name):
        name = _safe(binding.name)
        blocks += [
            "template {",
            f'  contents = "{{{{ with secret \\"kv/data/{_safe(config.product)}/'
            f'{_safe(role.role)}\\" }}}}{{{{ .Data.data.{name} }}}}{{{{ end }}}}"',
            f'  destination = "{base}/secrets/{name}"',
            '  perms = "0400"',
            "  error_on_missing_key = true",
            "}",
        ]
    return "\n".join(blocks) + "\n"


def install_agent_config(config: AgentConfig, role: RoleBindings) -> str:
    """Install atomically (0600) if the bytes changed. Returns the config digest."""
    text = render_agent_config(config, role).encode("utf-8")
    require_private_dir(config.openbao_agent_config_dir)
    path = config.openbao_agent_config_dir / f"{config.product}-{role.role}.hcl"
    if not path.exists() or path.read_bytes() != text:
        write_private(path, text, 0o600)
    return digest(text)
