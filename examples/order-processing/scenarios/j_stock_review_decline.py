"""Scenario J — Backorder → stock review → customer declines
============================================================
Items are backordered; the restock wait times out, escalating to stock review.
The customer decides NOT to accept a partial shipment, so the inventory track
ends in ``out_of_stock``.  The whole order is then moved to ``failed``.

Flow
----
  Inventory  checking → backordered → stock_review → out_of_stock → (failure signal)
  Payment    never starts (no reserved signal; fails on bus.failure)
  Shipping   never starts
"""
from __future__ import annotations

import simpy

from machine import OrderProcessing
from sim import (
    OrderEventBus,
    Scenario,
    _log,
)

_WAIT_TIMEOUT  = 5   # time before escalating to stock_review
_DECISION_TIME = 3   # customer decision delay


def _gen_j(env: simpy.Environment, sm: OrderProcessing):
    sm.submit()
    yield env.timeout(1)
    sm.approve()
    sm.start()

    bus = OrderEventBus.create(env)

    def _inventory_stock_review_decline():
        yield env.timeout(2)
        _log(env, "[inv] items unavailable — placing backorder")
        sm.backorder()                          # checking → backordered

        yield env.timeout(_WAIT_TIMEOUT)
        _log(env, "[inv] restock timeout — escalating to stock review")
        sm.request_approval()                   # backordered → stock_review

        yield env.timeout(_DECISION_TIME)
        _log(env, "[inv] customer DECLINED partial shipment — cancelling")
        sm.decline_partial()                    # stock_review → out_of_stock
        bus.failure.succeed()

    def _payment_or_fail():
        """Payment waits for reserved; if failure fires first, fail the order."""
        yield bus.reserved | bus.failure
        if bus.failure.processed:
            sm.fail()
            return
        # Unreachable in this scenario, but guards correctness
        sm.process_payment()
        sm.authorize()   # pragma: no branch

    yield simpy.AllOf(env, [
        env.process(_inventory_stock_review_decline()),
        env.process(_payment_or_fail()),
    ])


SCENARIO = Scenario(
    name        = "Scenario J — Backorder → stock review → customer declines",
    description = (
        "Restock timeout escalates to stock review; "
        "customer declines partial shipment, order fails."
    ),
    gen_fn      = _gen_j,
)
