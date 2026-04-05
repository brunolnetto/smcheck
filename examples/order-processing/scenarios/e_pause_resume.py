"""Scenario E — Pause & resume via HistoryState (customer hold)
===============================================================
Simulates an order paused mid-shipping (package ready, not yet dispatched)
into ``on_hold``, then later resumed.  The ``HistoryState`` on the shipping
track ensures the sub-state is restored to the exact state it was in when
the pause was triggered.
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


def _gen_e(env: simpy.Environment, sm: OrderProcessing):
    sm.submit()
    yield env.timeout(1)
    sm.approve()
    sm.start()

    bus = OrderEventBus.create(env)

    # Phase 1 — complete inventory & payment; drive shipping only to 'ready'
    #           (stop before dispatch so before_dispatch guard stays True)
    yield simpy.AllOf(env, [
        env.process(inv_track(env, sm, bus, reserve_t=2, allocate_t=2)),
        env.process(pay_track_normal(env, sm, bus, start_t=1, auth_t=3)),
    ])
    # Drive shipping manually to 'ready' (without calling dispatch)
    yield simpy.AllOf(env, [bus.allocated, bus.authorized])
    sm.begin_shipping()          # ship_hold → preparing  [ready_to_ship guard passes]
    yield env.timeout(1)
    sm.mark_ready()              # preparing → ready       (package packed, label printed)

    # Phase 2 — customer pauses; order enters on_hold (before_dispatch = True)
    _log(env, "⏸  Pausing order (customer hold — package ready, not yet dispatched)")
    sm.pause()
    _log(env, f"  SM config after pause : {sm.configuration_values}")
    yield env.timeout(10)

    # Phase 3 — resume via HistoryState (on_hold → shipping.h → restores 'ready')
    _log(env, "▶  Resuming order → restoring shipping sub-state")
    sm.resume()
    _log(env, f"  SM config after resume: {sm.configuration_values}")

    # Phase 4 — complete shipping from 'ready'
    yield env.timeout(1)
    sm.dispatch()                # ready → in_transit
    yield env.timeout(5)
    sm.deliver()                 # in_transit → delivered
    yield env.timeout(2)
    sm.acknowledge()             # delivered → acknowledged
    sm.complete()                # fulfillment → success  [is_all_done guard passes]


SCENARIO = Scenario(
    name        = "Scenario E — Pause & resume (customer hold / HistoryState)",
    description = "Order paused mid-shipping into on_hold; HistoryState restores 'ready' on resume.",
    gen_fn      = _gen_e,
)
