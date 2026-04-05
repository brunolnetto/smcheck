"""Scenario D — Payment timeout
================================
The payment gateway stops responding.  After ``PAYMENT_TIMEOUT`` simulated
seconds, the payment track auto-declines and the order fails.
"""
from __future__ import annotations

import simpy

from machine import OrderProcessing, PAYMENT_TIMEOUT
from sim import (
    OrderEventBus,
    Scenario,
    inv_track,
    pay_track_timeout,
)


def _gen_d(env: simpy.Environment, sm: OrderProcessing):
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
        env.process(pay_track_timeout(env, sm, bus, start_t=1, timeout_t=PAYMENT_TIMEOUT)),
        env.process(_watch_failure()),
    ])


SCENARIO = Scenario(
    name        = f"Scenario D — Payment timeout (PAYMENT_TIMEOUT={PAYMENT_TIMEOUT})",
    description = f"Gateway goes silent; auto-decline fires after {PAYMENT_TIMEOUT} s.",
    gen_fn      = _gen_d,
    time_limit  = PAYMENT_TIMEOUT + 20,
)
