"""Scenario G — Partial fulfillment (customer opt-in)
======================================================
Some items are unavailable but the warehouse has a reduced quantity.
Instead of failing outright, the customer is offered a partial shipment
at a lower price.

Flow
----
  Inventory  checking → partial_stock → (user waits 5 s) → accept_partial → allocated
  Payment    waits for soft-hold (set on entering partial_stock)
             → process_payment → authorize (with reduced amount)
  Shipping   normal full path once both constraints are met

Key design points
-----------------
* ``mark_partial``   fires when only a subset of ordered units is available.
* The bus.reserved event is still signalled immediately (partial_stock hook
  sets _inventory_reserved = True), so payment can start while the customer
  decides.
* ``accept_partial`` sets sm._partial_fulfillment = True, then the allocated
  hook prints the "reduced shipment" message.
* The scenario prints the final reduced charge so the discount is visible.
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

# Simulated customer decision delay (seconds after partial-stock notification)
_DECISION_DELAY = 5


def _gen_g(env: simpy.Environment, sm: OrderProcessing):
    sm.submit()
    yield env.timeout(2)
    sm.approve()
    sm.start()

    bus = OrderEventBus.create(env)

    def _inventory_partial():
        """
        Inventory finds partial stock at t=2, notifies customer,
        and waits _DECISION_DELAY before the customer opts in.
        """
        yield env.timeout(2)
        sm.mark_partial()                # checking → partial_stock (sets _inventory_reserved)
        bus.reserved.succeed()           # unblock payment track
        _log(env, "[inv] partial stock detected — customer notified")

        yield env.timeout(_DECISION_DELAY)
        _log(env, "[inv] customer ACCEPTED reduced shipment")
        sm.accept_partial()              # partial_stock → allocated (sets _partial_fulfillment)
        bus.allocated.succeed()

    def _watch_and_report():
        """Wait for the whole order to finish and report the reduced charge."""
        yield simpy.AllOf(env, [bus.allocated, bus.authorized])
        discount = 0.25 if sm._partial_fulfillment else 0.0
        final_pct = int((1 - discount) * 100)
        _log(
            env,
            f"[pay] reduced charge applied: {final_pct}% of original amount "
            f"(partial_fulfillment={sm._partial_fulfillment})",
        )

    yield simpy.AllOf(env, [
        env.process(_inventory_partial()),
        env.process(pay_track_normal(env, sm, bus, start_t=1, auth_t=3)),
        env.process(shp_track_full(env, sm, bus)),
        env.process(_watch_and_report()),
    ])


SCENARIO = Scenario(
    name        = "Scenario G — Partial fulfillment (customer opt-in)",
    description = (
        "Warehouse has fewer units than ordered. "
        "Customer accepts a 25% reduced shipment; order completes at lower price."
    ),
    gen_fn     = _gen_g,
)
