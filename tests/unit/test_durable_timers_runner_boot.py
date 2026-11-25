"""Boot refusals of the job runner that need no database."""

from __future__ import annotations

from dotmac_durable_timers.manifest import module as timers_module
from dotmac_durable_timers.run import foreign_outbox_event_types
from dotmac_kernel.modules import ModuleManifest


def _other(code: str, *types: str) -> ModuleManifest:
    return ModuleManifest(code=code, version="1.0.0", outbox_event_types=types)


def test_the_runner_owns_only_its_own_outbox_event_type() -> None:
    assert foreign_outbox_event_types([timers_module]) == {}
    assert foreign_outbox_event_types([timers_module, _other("plain")]) == {}


def test_a_foreign_outbox_event_type_is_named_so_the_runner_refuses_to_boot() -> None:
    foreign = foreign_outbox_event_types(
        [
            timers_module,
            _other("billing_like", "billing.invoice_due.v1"),
            _other("plain"),
            _other("fulfilment_like", "fulfilment.reobserve_due.v1"),
        ]
    )
    assert foreign == {
        "billing_like": ("billing.invoice_due.v1",),
        "fulfilment_like": ("fulfilment.reobserve_due.v1",),
    }
