"""Scenario A — Happy path
==========================
All three tracks complete successfully:
  Inventory  → reserved → allocated
  Payment    → authorised
  Shipping   → ready → in_transit → delivered → acknowledged
"""
from __future__ import annotations

import simpy

from machine import OrderProcessing
from sim import (
    OrderEventBus,
    Scenario,
    inv_track,
    pay_track_normal,
    shp_track_full,
)


def _gen_a(env: simpy.Environment, sm: OrderProcessing):
    sm.submit()
    yield env.timeout(2)   # reviewing…
    sm.approve()           # validation ✓
    sm.start()             # enter fulfillment

    bus = OrderEventBus.create(env)
    yield simpy.AllOf(env, [
        env.process(inv_track(env, sm, bus, reserve_t=2, allocate_t=2)),
        env.process(pay_track_normal(env, sm, bus, start_t=1, auth_t=3)),
        env.process(shp_track_full(env, sm, bus)),
    ])


SCENARIO = Scenario(
    name        = "Scenario A — Happy path",
    description = "All three tracks succeed: inventory reserved, payment authorised, order delivered.",
    gen_fn      = _gen_a,
)
