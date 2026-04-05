"""Scenario I — Backorder → stock review → operator approves partial
====================================================================
Items are backordered; the restock wait times out and the case is escalated
to stock review.  The operations team approves a partial shipment of what is
currently available.  The order then resumes the normal payment + shipping path.

Flow
----
  Inventory  checking → backordered → stock_review → reserved → allocated
             (approve_partial sets _partial_fulfillment)
  Payment    waits for reserved after approve_partial, then authorises
  Shipping   full path once ready_to_ship
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

_WAIT_TIMEOUT  = 5   # time before escalating to stock_review
_DECISION_TIME = 3   # ops team decision delay


def _gen_i(env: simpy.Environment, sm: OrderProcessing):
    sm.submit()
    yield env.timeout(1)
    sm.approve()
    sm.start()

    bus = OrderEventBus.create(env)

    def _inventory_stock_review_approve():
        yield env.timeout(2)
        _log(env, "[inv] items unavailable — placing backorder")
        sm.backorder()                          # checking → backordered

        yield env.timeout(_WAIT_TIMEOUT)
        _log(env, "[inv] restock timeout — escalating to stock review")
        sm.request_approval()                   # backordered → stock_review

        yield env.timeout(_DECISION_TIME)
        _log(env, "[inv] ops approved partial shipment")
        sm.approve_partial()                    # stock_review → reserved (sets _partial_fulfillment)
        bus.reserved.succeed()

        yield env.timeout(2)
        sm.allocate()                           # reserved → allocated
        bus.allocated.succeed()

    yield simpy.AllOf(env, [
        env.process(_inventory_stock_review_approve()),
        env.process(pay_track_normal(env, sm, bus, start_t=1, auth_t=3)),
        env.process(shp_track_full(env, sm, bus)),
    ])


SCENARIO = Scenario(
    name        = "Scenario I — Backorder → stock review → approve partial",
    description = (
        "Restock timeout escalates to stock review; "
        "ops approves partial shipment, order completes."
    ),
    gen_fn      = _gen_i,
)
