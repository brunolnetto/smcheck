"""machine.py
============
``OrderProcessing`` StateChart definition.

Contains only the state machine class, its nested compound/parallel states,
transitions, guard methods, hooks, and the ``__init__`` constructor.
No SimPy, no scenarios, no smcheck imports.
"""
from __future__ import annotations

from statemachine import HistoryState, State, StateChart

PAYMENT_TIMEOUT = 30   # simulated time units


class OrderProcessing(StateChart):
    """
    Three ordering constraints enforced via shared boolean flags + cond guards:

      ① payment.process_payment  — cond: inventory_is_reserved
      ② shipping.begin_shipping  — cond: ready_to_ship (allocated ∧ authorised)
      ③ shipping.acknowledge     — explicit transition; must follow delivered

    Failure is driven externally by SimPy (no self.send() inside hooks)
    so that transitions are never re-entrant.
    """

    # ── Top-level ────────────────────────────────────────────────────────────
    idle      = State(initial=True)
    on_hold   = State()             # customer-initiated pause (recoverable)
    ops_hold  = State()             # ops-team hold (compliance / fraud review)
    cancelled = State(final=True)
    failed    = State(final=True)
    success   = State(final=True)

    # ── Validation (compound) ────────────────────────────────────────────────
    class validation(State.Compound, name="Validation"):
        reviewing = State(initial=True)
        approved  = State(final=True)
        rejected  = State(final=True)

        approve = reviewing.to(approved)
        reject  = reviewing.to(rejected)

    # ── Fulfillment (parallel) ───────────────────────────────────────────────
    class fulfillment(State.Parallel, name="Fulfillment"):

        # ─ Track 1: Inventory ───────────────────────────────────────────────
        class inventory(State.Compound, name="Inventory"):
            checking     = State(initial=True)
            reserved     = State()         # required items soft-held
            backordered  = State()         # required items out of stock; waiting
            stock_review = State()         # wait timeout reached; needs customer input
            partial_stock = State()        # only a subset of units available; awaiting customer opt-in
            allocated    = State(final=True)
            out_of_stock = State(final=True)  # order cannot be fulfilled

            reserve          = checking.to(reserved)         # all required items available
            allocate         = reserved.to(allocated)        # hard-commit stock
            mark_unavailable = reserved.to(out_of_stock)     # items lost after soft-hold
            # Backorder path: required items unavailable, wait for restock
            backorder        = checking.to(backordered)
            stock_available  = backordered.to(reserved)      # required items restocked
            request_approval = backordered.to(stock_review)  # timeout → notify customer
            approve_partial  = stock_review.to(reserved)     # customer: ship what's available
            decline_partial  = stock_review.to(out_of_stock) # customer: cancel order
            # Partial fulfillment path: offer reduced shipment immediately
            mark_partial     = checking.to(partial_stock)    # subset available; notify customer
            accept_partial   = partial_stock.to(allocated)   # customer opts in to reduced shipment

        # ─ Track 2: Payment ─────────────────────────────────────────────────
        class payment(State.Compound, name="Payment"):
            pay_hold   = State(initial=True)   # waits for inventory.reserved
            processing = State()
            authorized = State(final=True)
            declined   = State(final=True)

            process_payment = pay_hold.to(processing, cond="inventory_is_reserved")
            authorize       = processing.to(authorized)
            decline         = processing.to(declined)

        # ─ Track 3: Shipping ─────────────────────────────────────────────────
        class shipping(State.Compound, name="Shipping"):
            ship_hold    = State(initial=True)   # waits for inventory ∧ payment
            preparing    = State()
            ready        = State()
            in_transit   = State()
            delivered    = State()
            acknowledged = State()         # non-final: complete fires at top level

            h = HistoryState()   # restores sub-state after pause/resume

            begin_shipping = ship_hold.to(preparing, cond="ready_to_ship")
            mark_ready     = preparing.to(ready)
            dispatch       = ready.to(in_transit)
            deliver        = in_transit.to(delivered)
            acknowledge    = delivered.to(acknowledged)

    # ── Top-level transitions ────────────────────────────────────────────────
    submit   = idle.to(validation)
    start    = validation.to(fulfillment, cond="is_approved")
    pause    = fulfillment.to(on_hold,  cond="before_dispatch")
    suspend  = fulfillment.to(ops_hold, cond=["before_dispatch", "ops_only"])
    resume   = on_hold.to(fulfillment.shipping.h)    # restore shipping sub-state
    release  = ops_hold.to(fulfillment.shipping.h, cond="ops_only")
    fail     = fulfillment.to(failed)
    complete = fulfillment.to(success, cond="is_all_done")
    cancel   = (
        idle.to(cancelled)
        | validation.to(cancelled)
        | fulfillment.to(cancelled, cond="before_dispatch")
        | on_hold.to(cancelled)
        | ops_hold.to(cancelled, cond="ops_only")
    )

    # ── Shared coordination flags ────────────────────────────────────────────
    def __init__(self, *args, quiet: bool = False, **kwargs):
        self._quiet                = quiet
        self._validation_approved  = False
        self._inventory_reserved   = False
        self._inventory_allocated  = False
        self._payment_authorized   = False
        self._partial_fulfillment  = False   # True when customer approves partial
        self._dispatched           = False   # True after shipping.dispatch fires
        self._shipping_acknowledged = False  # True after customer acknowledges delivery
        self._ops_authorized       = False   # True when ops team authorised the action
        super().__init__(*args, **kwargs)

    # ── Guards ───────────────────────────────────────────────────────────────
    def is_approved(self):
        """Allow start only when validation has approved the order."""
        return self._validation_approved

    def inventory_is_reserved(self):
        """Constraint ①: block payment until soft-hold is confirmed."""
        return self._inventory_reserved

    def ready_to_ship(self):
        """Constraint ②: shipping blocked until stock committed AND payment done."""
        return self._inventory_allocated and self._payment_authorized

    def before_dispatch(self):
        """Cancel and pause only available before shipment is dispatched."""
        return not self._dispatched

    def ops_only(self):
        """Gate ops-team actions (suspend, release) behind an authorisation flag."""
        return self._ops_authorized

    def is_all_done(self):
        """Constraint ④: complete only when all tracks have fulfilled their obligations."""
        return (
            self._inventory_allocated
            and self._payment_authorized
            and self._shipping_acknowledged
        )

    # ── Hooks ────────────────────────────────────────────────────────────────
    def _p(self, msg: str) -> None:
        if not self._quiet:
            print(msg)

    def on_enter_on_hold(self):
        self._p("  [Order]       ⏸  Customer hold — order paused")

    def on_enter_ops_hold(self):
        self._p("  [Order]       ⚠  Operations hold — manual review required")

    def on_enter_success(self):
        self._p("  [Order]       ✅  Completed successfully")

    def on_enter_fulfillment(self):
        self._p("  [Fulfillment] All 3 tracks started")

    # validation
    def on_enter_approved(self):
        self._validation_approved = True
        self._p("  [Validation]  ✔ Approved")

    def on_enter_rejected(self):
        self._p("  [Validation]  ✗ Rejected")

    # inventory
    def on_enter_reserved(self):
        self._inventory_reserved = True
        self._p("  [Inventory]   ✔ Soft-hold placed")

    def on_enter_backordered(self):
        self._p("  [Inventory]   ⏳ Required items backordered — awaiting restock")

    def on_enter_partial_stock(self):
        self._inventory_reserved = True
        self._p("  [Inventory]   ⚠  Partial stock available — customer notified")

    def on_accept_partial(self):
        """Customer opts in to receive a reduced shipment."""
        self._partial_fulfillment = True

    def on_enter_stock_review(self):
        self._p("  [Inventory]   ⚠  Wait timeout elapsed — customer decision required")

    def on_approve_partial(self):
        """Customer accepts shipment with currently available stock."""
        self._partial_fulfillment = True

    def on_enter_allocated(self):
        self._inventory_allocated = True
        if self._partial_fulfillment:
            self._p("  [Inventory]   ✔ Partial approval accepted — reduced shipment committed")
        else:
            self._p("  [Inventory]   ✔ Stock hard-committed")

    def on_enter_out_of_stock(self):
        self._p("  [Inventory]   ✗ Out of stock")

    # payment
    def on_enter_processing(self):
        self._p("  [Payment]     → Gateway request sent")

    def on_enter_authorized(self):
        self._payment_authorized = True
        self._p("  [Payment]     ✔ Authorised")

    def on_enter_declined(self):
        self._p("  [Payment]     ✗ Declined")

    # shipping
    def on_enter_preparing(self):
        self._p("  [Shipping]    → Picking & packing")

    def on_enter_ready(self):
        self._p("  [Shipping]    ✔ Package ready, label printed")

    def on_enter_in_transit(self):
        self._dispatched = True
        self._p("  [Shipping]    → In transit")

    def on_enter_delivered(self):
        self._p("  [Shipping]    → Delivered — awaiting acknowledgement")

    def on_enter_acknowledged(self):
        self._shipping_acknowledged = True
        self._p("  [Shipping]    ✔ Acknowledged by customer")

    def on_enter_failed(self):
        self._p("  ❌ Order failed — holds released, customer notified")

    def on_enter_cancelled(self):
        self._p("  🚫 Order cancelled")
