"""Scenario C — Payment declined
================================
Inventory succeeds but the payment gateway hard-declines the charge.
The order enters ``failed`` once the factory detects ``bus.failure``.
"""
from __future__ import annotations

import simpy

from machine import OrderProcessing
from sim import (
    OrderEventBus,
    Scenario,
    inv_track,
    pay_track_decline,
)


def _gen_c(env: simpy.Environment, sm: OrderProcessing):
    sm.submit()
    yield env.timeout(1)
    sm.approve()
    sm.start()

    bus = OrderEventBus.create(env)

    def _watch_failure():
        yield bus.failure
        sm.fail()

    yield simpy.AllOf(env, [
        env.process(inv_track(env, sm, bus, reserve_t=2, allocate_t=2)),
        env.process(pay_track_decline(env, sm, bus, start_t=1, decline_t=3)),
        env.process(_watch_failure()),
    ])


SCENARIO = Scenario(
    name        = "Scenario C — Payment declined",
    description = "Inventory reserved but payment gateway rejects the charge.",
    gen_fn      = _gen_c,
)
