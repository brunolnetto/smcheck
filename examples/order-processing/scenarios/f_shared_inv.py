"""Scenario F — Shared inventory pool (multi-order)
===================================================
Six concurrent orders compete for a shared ``InventorySystem`` container.
The supplier replenishes stock after a delay whenever the level drops low,
demonstrating resource-contention behaviour in a realistic warehouse.
"""
from __future__ import annotations

import simpy

from machine import OrderProcessing
from sim import (
    InventorySystem,
    Scenario,
    _log,
    make_order,
)


def _gen_f(env: simpy.Environment, sm: OrderProcessing):
    """
    Runs 6 concurrent orders against one shared InventorySystem.

    The ``sm`` argument is unused here — scenario F creates its own
    OrderProcessing instances inside ``make_order()``.  It is accepted
    for API consistency with the other generators.
    """
    inv_sys = InventorySystem(
        env            = env,
        initial        = 3,
        capacity       = 10,
        low_threshold  = 2,
        refill_qty     = 5,
        supplier_delay = 5,
    )
    _log(env, f"[Stock] Warehouse opened  (level={inv_sys.store.level}/{inv_sys.capacity})")

    procs = [env.process(make_order(env, inv_sys, oid=i)) for i in range(1, 7)]
    yield simpy.AllOf(env, procs)

    _log(env, f"[Stock] All orders done   (level={inv_sys.store.level}/{inv_sys.capacity})")


SCENARIO = Scenario(
    name        = "Scenario F — Shared inventory (6 concurrent orders)",
    description = "Warehouse with 3 units; supplier restocks after a delay under contention.",
    gen_fn      = _gen_f,
    time_limit  = 300,
    report_sm   = False,   # multi-order: no single SM to report
)
