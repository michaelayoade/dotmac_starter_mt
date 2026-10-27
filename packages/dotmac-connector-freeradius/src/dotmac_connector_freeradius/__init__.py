"""Public surface for the FreeRADIUS projection/observation/CoA connector."""

from dotmac_connector_freeradius.plugin import (
    CONNECTOR_KEY,
    FREERADIUS_DSN,
    MANIFEST,
    OBSERVATION_CAPABILITY,
    PLUGIN,
    SECRET_FETCH_TOKEN,
    FreeRadiusConnector,
)
from dotmac_connector_freeradius.polling import (
    FreeRadiusPollError,
    FreeRadiusPollHandler,
)
from dotmac_connector_freeradius.provisioning import (
    DISCONNECT_CAPABILITY,
    PROJECTION_CAPABILITY,
    FreeRadiusProvisioningHandler,
)
from dotmac_connector_freeradius.render import (
    RenderConfig,
    RenderRefused,
    render_projection,
    rows_match,
)

__version__ = "0.1.0a1"

__all__ = [
    "CONNECTOR_KEY",
    "DISCONNECT_CAPABILITY",
    "FREERADIUS_DSN",
    "MANIFEST",
    "OBSERVATION_CAPABILITY",
    "PLUGIN",
    "PROJECTION_CAPABILITY",
    "SECRET_FETCH_TOKEN",
    "FreeRadiusConnector",
    "FreeRadiusPollError",
    "FreeRadiusPollHandler",
    "FreeRadiusProvisioningHandler",
    "RenderConfig",
    "RenderRefused",
    "__version__",
    "render_projection",
    "rows_match",
]
