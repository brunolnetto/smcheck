"""Scenario L — Ops hold → ops-only cancel
==========================================
An operations team member places the order under an ops-hold (e.g. fraud
flag) and then decides to cancel it outright.  Only an authorised ops user
can cancel from ``ops_hold`` (the ``ops_only`` guard is required).

Flow
----
  Phases 1–2 : inventory + payment complete; shipping starts (before dispatch)
  Phase 3    : ops team suspends the order (fulfillment → ops_hold)
  Phase 4    : ops team cancels the order  (ops_hold → cancelled)
"""
from __future__ import annotations

import simpy

from machine import OrderProcessing
from sim import (
    OrderEventBus,
    Scenario,
    _log,
    inv_track,
    pay_track_normal,
)

_REVIEW_DELAY = 5   # time before ops team makes the cancel decision


def _gen_l(env: simpy.Environment, sm: OrderProcessing):
    sm.submit()
    yield env.timeout(1)
    sm.approve()
    sm.start()

    bus = OrderEventBus.create(env)

    # Phase 1 — complete inventory and payment
    yield simpy.AllOf(env, [
        env.process(inv_track(env, sm, bus, reserve_t=2, allocate_t=2)),
        env.process(pay_track_normal(env, sm, bus, start_t=1, auth_t=3)),
    ])

    # Phase 2 — begin shipping (before first dispatch)
    yield simpy.AllOf(env, [bus.allocated, bus.authorized])
    sm.begin_shipping()                     # ship_hold → preparing (before_dispatch = True)

    # Phase 3 — ops team suspends for fraud review
    _log(env, "🔒 Ops team suspending order — fraud flag raised")
    sm._ops_authorized = True
    sm.suspend()                            # fulfillment → ops_hold
    _log(env, f"  SM config after suspend: {sm.configuration_values}")
    yield env.timeout(_REVIEW_DELAY)

    # Phase 4 — ops team confirms fraud; cancels the order
    _log(env, "❌ Ops team cancelling order after fraud confirmation")
    sm.cancel()                             # ops_hold → cancelled  [ops_only guard]
    sm._ops_authorized = False
    _log(env, f"  SM config after cancel : {sm.configuration_values}")


SCENARIO = Scenario(
    name        = "Scenario L — Ops hold → ops-only cancel",
    description = (
        "Operations team places fraud-flag hold and cancels the order; "
        "only authorised ops users may cancel from ops_hold."
    ),
    gen_fn      = _gen_l,
)
