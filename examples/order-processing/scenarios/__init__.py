"""scenarios/__init__.py
=======================
Public surface of the ``scenarios`` package.

Exports
-------
SCENARIOS   : ordered list of all simulation scenarios
run_all     : convenience function that executes every scenario in order
"""
from __future__ import annotations

from typing import Optional

from machine import OrderProcessing
from sim import Scenario

from scenarios.a_happy               import SCENARIO as _A
from scenarios.b_out_of_stock        import SCENARIO as _B
from scenarios.c_declined            import SCENARIO as _C
from scenarios.d_timeout             import SCENARIO as _D
from scenarios.e_pause_resume        import SCENARIO as _E
from scenarios.f_shared_inv          import SCENARIO as _F
from scenarios.g_partial_fulfillment import SCENARIO as _G
from scenarios.h_backorder_restock   import SCENARIO as _H
from scenarios.i_stock_review_approve import SCENARIO as _I
from scenarios.j_stock_review_decline import SCENARIO as _J
from scenarios.k_ops_hold_release    import SCENARIO as _K
from scenarios.l_ops_hold_cancel     import SCENARIO as _L

SCENARIOS: list[Scenario] = [_A, _B, _C, _D, _E, _F, _G, _H, _I, _J, _K, _L]


def run_all() -> list[Optional[OrderProcessing]]:
    """Execute every scenario in order; return the list of resulting SMs."""
    return [s.execute() for s in SCENARIOS]


__all__ = ["SCENARIOS", "run_all"]
