"""Scenario K — Ops hold → release → order completes
======================================================
An operations team member (``ops_only``) places the order under an ops-hold
for compliance review while shipping is being prepared.  After review the
order is released; shipping resumes via ``HistoryState`` and completes
successfully.

Flow
----
  Phases 1–2 : inventory + payment complete; shipping driven to 'preparing'
  Phase 3    : ops team suspends the order (fulfillment → ops_hold)
  Phase 4    : ops team releases the order (ops_hold → shipping.h → 'preparing')
  Phase 5    : shipping completes → success
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

_REVIEW_DELAY = 8   # time the ops team spends reviewing


def _gen_k(env: simpy.Environment, sm: OrderProcessing):
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

    # Phase 2 — start shipping (ready_to_ship now True); drive to 'preparing'
    yield simpy.AllOf(env, [bus.allocated, bus.authorized])
    sm.begin_shipping()                     # ship_hold → preparing (before_dispatch = True)

    # Phase 3 — ops team places compliance hold (suspend)
    _log(env, "🔒 Ops team suspending order for compliance review")
    sm._ops_authorized = True              # elevate to ops role
    sm.suspend()                            # fulfillment → ops_hold
    _log(env, f"  SM config after suspend: {sm.configuration_values}")
    yield env.timeout(_REVIEW_DELAY)

    # Phase 4 — ops team releases the hold (restore shipping.h = 'preparing')
    _log(env, "🔓 Ops team releasing order after review")
    sm.release()                            # ops_hold → shipping.h → preparing
    sm._ops_authorized = False             # drop elevated privileges
    _log(env, f"  SM config after release: {sm.configuration_values}")

    # Phase 5 — complete the shipping flow from 'preparing'
    yield env.timeout(1)
    sm.mark_ready()
    yield env.timeout(1)
    sm.dispatch()
    yield env.timeout(5)
    sm.deliver()
    yield env.timeout(2)
    sm.acknowledge()
    sm.complete()


SCENARIO = Scenario(
    name        = "Scenario K — Ops hold → release → success",
    description = (
        "Operations team places compliance hold mid-shipping; "
        "order released after review and completes."
    ),
    gen_fn      = _gen_k,
)
