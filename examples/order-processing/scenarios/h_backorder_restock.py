"""Scenario H — Backorder → restock → happy path
=================================================
Inventory finds items unavailable (``checking → backordered``), waits for
a supplier restock event, then resumes the normal reserve → allocate path.

Flow
----
  Inventory  checking → backordered → (restock delay) → reserved → allocated
  Payment    normal (waits for inventory.reserved, then authorises)
  Shipping   full path once ready_to_ship [allocated ∧ authorised]
"""
from __future__ import annotations

import simpy

from machine import OrderProcessing
from sim import (
    OrderEventBus,
    Scenario,
    _log,
    pay_track_normal,
    shp_track_full,
)

_RESTOCK_DELAY = 8   # simulated time units until supplier delivers


def _gen_h(env: simpy.Environment, sm: OrderProcessing):
    sm.submit()
    yield env.timeout(1)
    sm.approve()
    sm.start()

    bus = OrderEventBus.create(env)

    def _inventory_backorder():
        yield env.timeout(2)
        _log(env, "[inv] items unavailable — placing backorder")
        sm.backorder()                      # checking → backordered

        yield env.timeout(_RESTOCK_DELAY)
        _log(env, "[inv] restock received — items now available")
        sm.stock_available()               # backordered → reserved
        bus.reserved.succeed()

        yield env.timeout(2)
        sm.allocate()                       # reserved → allocated
        bus.allocated.succeed()

    yield simpy.AllOf(env, [
        env.process(_inventory_backorder()),
        env.process(pay_track_normal(env, sm, bus, start_t=1, auth_t=3)),
        env.process(shp_track_full(env, sm, bus)),
    ])


SCENARIO = Scenario(
    name        = "Scenario H — Backorder → restock → happy path",
    description = "Items backordered; restock arrives and order completes normally.",
    gen_fn      = _gen_h,
)
