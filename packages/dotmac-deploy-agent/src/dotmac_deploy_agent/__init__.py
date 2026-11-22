"""Product-neutral deploy launcher (DESIGN-rev3 L5 Agent).

The agent only LAUNCHES: it fetches signed materials, takes the host lease,
moves the image, renders `params.env`, delivers `secret_id`s, runs Foundation
and returns an observation. Desired state and approvals belong to Control; the
fence/backup/migrate/start/health sequence belongs to Foundation.
"""

__version__ = "0.1.0a1"
