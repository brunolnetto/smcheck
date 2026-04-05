"""Scenario B — Out of stock
============================
Inventory check fails immediately; the order enters ``failed``.
Payment and shipping never start because the bus.failure event
is watched by the orchestrator.
"""
from __future__ import annotations

import simpy

from machine import OrderProcessing
from sim import (
    OrderEventBus,
    Scenario,
    inv_oos_track,
    pay_track_normal,
)


def _gen_b(env: simpy.Environment, sm: OrderProcessing):
    sm.submit()
    yield env.timeout(1)
    sm.approve()
    sm.start()

    bus = OrderEventBus.create(env)

    def _payment_or_fail():
        yield bus.reserved | bus.failure
        if bus.failure.processed:
            sm.fail()
            return
        yield from pay_track_normal(env, sm, bus)

    yield simpy.AllOf(env, [
        env.process(inv_oos_track(env, sm, bus, fail_t=2)),
        env.process(_payment_or_fail()),
    ])


SCENARIO = Scenario(
    name        = "Scenario B — Out of stock",
    description = "Inventory is unavailable; order moves to failed immediately.",
    gen_fn      = _gen_b,
)
