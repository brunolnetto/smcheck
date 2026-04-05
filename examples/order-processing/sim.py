"""sim.py
========
SimPy simulation primitives for the OrderProcessing example.

Contains:
- ``OrderEventBus``          — named SimPy event container per order
- ``ScheduledEvent``         — declarative timestamped step
- ``TrackConfig``            — ordered sequence of scheduled events
- ``run_track()``            — generic coroutine driven by a TrackConfig
- Track-config factory fns   — inv_config, pay_config_normal, shp_config_full, …
- Backward-compat wrappers   — inv_track, pay_track_normal, … (yield-from)
- ``InventorySystem``        — shared SimPy Container + supplier-delay refill
- ``make_order()``           — full pipeline coroutine for multi-order sims
- ``Scenario``               — self-contained named simulation scenario
- ``_banner``, ``_log``, ``_outcome`` — print utilities
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import simpy

from machine import OrderProcessing, PAYMENT_TIMEOUT


# ==============================================================================
# Print utilities
# ==============================================================================

def _banner(title: str) -> None:
    width = 64
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def _log(env: simpy.Environment, msg: str) -> None:
    print(f"  t={env.now:>4.0f}  {msg}")


def _outcome(sm: OrderProcessing) -> str:
    cfg = sm.configuration_values
    if "success" in cfg:
        return "✅  DONE"
    if "failed" in cfg:
        return "❌  FAILED"
    if "cancelled" in cfg:
        return "🚫  CANCELLED"
    if "on_hold" in cfg:
        return "⏸  ON HOLD"
    if "ops_hold" in cfg:
        return "⚠️  OPS HOLD"
    if sm.is_terminated:
        return "✅  DONE"
    return "⚠️   INCOMPLETE"


# ==============================================================================
# OrderEventBus
# ==============================================================================

@dataclass
class OrderEventBus:
    """
    Named SimPy synchronisation events for one order's fulfillment lifecycle.

    Fields
    ------
    reserved   : inventory soft-hold placed       (enables payment)
    allocated  : inventory hard-committed          (enables shipping)
    authorized : payment gateway approved          (enables shipping)
    failure    : any terminal failure              (oos / declined / timeout)
    dispatched : shipping handed to carrier        (used by pause/resume only)
    """
    reserved:   simpy.Event
    allocated:  simpy.Event
    authorized: simpy.Event
    failure:    simpy.Event
    dispatched: simpy.Event

    @classmethod
    def create(cls, env: simpy.Environment) -> "OrderEventBus":
        return cls(
            reserved   = env.event(),
            allocated  = env.event(),
            authorized = env.event(),
            failure    = env.event(),
            dispatched = env.event(),
        )


# ==============================================================================
# ScheduledEvent + TrackConfig
# ==============================================================================

@dataclass
class ScheduledEvent:
    """
    A single declarative step in a simulation track.

    Attributes
    ----------
    at      : absolute simulation time at which the action fires
    action  : SM transition name (e.g. ``"reserve"``) or bus signal
              (``"bus.reserved"``, ``"bus.failure"``, …)
    log     : optional message printed when the event fires
    kwargs  : extra keyword arguments forwarded to the SM transition call
    """
    at:     int
    action: str
    log:    str = ""
    kwargs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.at < 0:
            raise ValueError(f"ScheduledEvent.at must be ≥ 0, got {self.at}")


@dataclass
class TrackConfig:
    """
    An ordered sequence of ScheduledEvents describing one simulation track.

    Events are sorted by ``at`` on creation.  A non-empty ``label`` is
    prepended to every log message.
    """
    events: list[ScheduledEvent]
    label:  str = ""

    def __post_init__(self) -> None:
        self.events = sorted(self.events, key=lambda e: e.at)

    def prefixed(self, msg: str) -> str:
        return f"[{self.label}] {msg}" if self.label else msg


# ── Bus signal name constants ─────────────────────────────────────────────────
_BUS_RESERVED   = "bus.reserved"
_BUS_ALLOCATED  = "bus.allocated"
_BUS_AUTHORIZED = "bus.authorized"
_BUS_FAILURE    = "bus.failure"
_BUS_DISPATCHED = "bus.dispatched"
_BUS_WAIT_ALL   = "bus.wait_allocated_and_authorized"
_BUS_WAIT_RES   = "bus.wait_reserved"


def _resolve_bus_signal(action: str, bus: OrderEventBus) -> "simpy.Event | None":
    return {
        _BUS_RESERVED:   bus.reserved,
        _BUS_ALLOCATED:  bus.allocated,
        _BUS_AUTHORIZED: bus.authorized,
        _BUS_FAILURE:    bus.failure,
        _BUS_DISPATCHED: bus.dispatched,
    }.get(action)


# ==============================================================================
# Generic track runner
# ==============================================================================

def run_track(
    env:    simpy.Environment,
    sm:     OrderProcessing,
    bus:    OrderEventBus,
    config: TrackConfig,
):
    """
    Generic coroutine driven by a ``TrackConfig``.

    * ``"bus.<name>"``                  → succeed the named bus event
    * ``"bus.wait_reserved"``           → ``yield bus.reserved``
    * ``"bus.wait_allocated_and_authorized"`` → ``yield AllOf(allocated, authorized)``
    * Any other string                  → ``getattr(sm, action)(**kwargs)``
    """
    now = 0
    for ev in config.events:
        delta = ev.at - now
        if delta > 0:
            yield env.timeout(delta)
        now = ev.at

        if ev.log:
            _log(env, config.prefixed(ev.log))

        if ev.action == _BUS_WAIT_RES:
            yield bus.reserved
            now = env.now
        elif ev.action == _BUS_WAIT_ALL:
            yield simpy.AllOf(env, [bus.allocated, bus.authorized])
            now = env.now
        else:
            bus_event = _resolve_bus_signal(ev.action, bus)
            if bus_event is not None:
                bus_event.succeed()
            else:
                getattr(sm, ev.action)(**ev.kwargs)


# ==============================================================================
# TrackConfig factories
# ==============================================================================

def inv_config(reserve_t: int = 2, allocate_t: int = 2) -> TrackConfig:
    """Normal inventory: warehouse query → soft-hold → hard-commit."""
    return TrackConfig(label="inv", events=[
        ScheduledEvent(at=reserve_t,             action="reserve",   log="reserve"),
        ScheduledEvent(at=reserve_t,             action=_BUS_RESERVED),
        ScheduledEvent(at=reserve_t+allocate_t,  action="allocate",  log="allocate"),
        ScheduledEvent(at=reserve_t+allocate_t,  action=_BUS_ALLOCATED),
    ])


def inv_oos_config(fail_t: int = 2) -> TrackConfig:
    """Inventory failure: item unavailable → signals bus.failure."""
    return TrackConfig(label="inv", events=[
        ScheduledEvent(at=fail_t, action="mark_unavailable", log="out of stock!"),
        ScheduledEvent(at=fail_t, action=_BUS_FAILURE),
    ])


def pay_config_normal(
    reserve_wait: bool = True,
    start_t: int = 1,
    auth_t:  int = 3,
    base_t:  int = 0,
) -> TrackConfig:
    """Payment that authorises."""
    events: list[ScheduledEvent] = []
    if reserve_wait:
        events.append(ScheduledEvent(at=0, action=_BUS_WAIT_RES))
    t = base_t + start_t
    events += [
        ScheduledEvent(at=t,        action="process_payment",
                       log="process_payment  (inventory reserved ✓)"),
        ScheduledEvent(at=t+auth_t, action="authorize",       log="authorize"),
        ScheduledEvent(at=t+auth_t, action=_BUS_AUTHORIZED),
    ]
    return TrackConfig(label="pay", events=events)


def pay_config_decline(
    reserve_wait: bool = True,
    start_t:   int = 1,
    decline_t: int = 3,
    base_t:    int = 0,
) -> TrackConfig:
    """Payment that is hard-declined by the gateway."""
    events: list[ScheduledEvent] = []
    if reserve_wait:
        events.append(ScheduledEvent(at=0, action=_BUS_WAIT_RES))
    t = base_t + start_t
    events += [
        ScheduledEvent(at=t,           action="process_payment", log="process_payment"),
        ScheduledEvent(at=t+decline_t, action="decline",         log="DECLINED!"),
        ScheduledEvent(at=t+decline_t, action=_BUS_FAILURE),
    ]
    return TrackConfig(label="pay", events=events)


def pay_config_timeout(
    reserve_wait: bool = True,
    start_t:   int = 1,
    timeout_t: int = PAYMENT_TIMEOUT,
    base_t:    int = 0,
) -> TrackConfig:
    """Payment that times out → auto-decline."""
    events: list[ScheduledEvent] = []
    if reserve_wait:
        events.append(ScheduledEvent(at=0, action=_BUS_WAIT_RES))
    t = base_t + start_t
    events += [
        ScheduledEvent(at=t,           action="process_payment",
                       log="process_payment (started, waiting for gateway…)"),
        ScheduledEvent(at=t+timeout_t, action="decline",
                       log=f"TIMEOUT after {timeout_t} s → auto-decline"),
        ScheduledEvent(at=t+timeout_t, action=_BUS_FAILURE),
    ]
    return TrackConfig(label="pay", events=events)


def shp_config_full(
    prepare_t: int = 1,
    pack_t:    int = 2,
    transit_t: int = 1,
    deliver_t: int = 5,
    ack_t:     int = 2,
) -> TrackConfig:
    """Full shipping flow through to acknowledged."""
    t0 = prepare_t
    t1 = t0 + pack_t
    t2 = t1 + transit_t
    t3 = t2 + deliver_t
    t4 = t3 + ack_t
    return TrackConfig(label="shp", events=[
        ScheduledEvent(at=0,  action=_BUS_WAIT_ALL),
        ScheduledEvent(at=t0, action="begin_shipping",
                       log="begin_shipping  (ready_to_ship ✓)"),
        ScheduledEvent(at=t1, action="mark_ready"),
        ScheduledEvent(at=t2, action="dispatch"),
        ScheduledEvent(at=t3, action="deliver"),
        ScheduledEvent(at=t4, action="acknowledge", log="acknowledge  (Constraint ③)"),
        ScheduledEvent(at=t4, action="complete"),
    ])


def shp_config_until_dispatch(
    prepare_t: int = 1,
    pack_t:    int = 2,
    transit_t: int = 1,
) -> TrackConfig:
    """Shipping that stops at dispatch, firing bus.dispatched."""
    t0 = prepare_t
    t1 = t0 + pack_t
    t2 = t1 + transit_t
    return TrackConfig(label="shp", events=[
        ScheduledEvent(at=0,  action=_BUS_WAIT_ALL),
        ScheduledEvent(at=t0, action="begin_shipping", log="begin_shipping"),
        ScheduledEvent(at=t1, action="mark_ready"),
        ScheduledEvent(at=t2, action="dispatch",       log="dispatch → in_transit"),
        ScheduledEvent(at=t2, action=_BUS_DISPATCHED),
    ])


# ==============================================================================
# Backward-compatible track wrappers
# ==============================================================================

def inv_track(env, sm, bus, *, reserve_t=2, allocate_t=2):
    yield from run_track(env, sm, bus, inv_config(reserve_t, allocate_t))

def inv_oos_track(env, sm, bus, *, fail_t=2):
    yield from run_track(env, sm, bus, inv_oos_config(fail_t))

def pay_track_normal(env, sm, bus, *, start_t=1, auth_t=3):
    yield from run_track(env, sm, bus, pay_config_normal(start_t=start_t, auth_t=auth_t))

def pay_track_decline(env, sm, bus, *, start_t=1, decline_t=3):
    yield from run_track(env, sm, bus, pay_config_decline(start_t=start_t, decline_t=decline_t))

def pay_track_timeout(env, sm, bus, *, start_t=1, timeout_t=PAYMENT_TIMEOUT):
    yield from run_track(env, sm, bus, pay_config_timeout(start_t=start_t, timeout_t=timeout_t))

def shp_track_full(env, sm, bus, *, prepare_t=1, pack_t=2, transit_t=1, deliver_t=5, ack_t=2):
    yield from run_track(env, sm, bus,
                         shp_config_full(prepare_t, pack_t, transit_t, deliver_t, ack_t))

def shp_track_until_dispatch(env, sm, bus, *, prepare_t=1, pack_t=2, transit_t=1):
    yield from run_track(env, sm, bus,
                         shp_config_until_dispatch(prepare_t, pack_t, transit_t))


# ==============================================================================
# InventorySystem — shared SimPy Container with supplier-delay refill
# ==============================================================================

class InventorySystem:
    """
    Physical product storage modelled as a SimPy Container.

    When any order requests a unit and the current level ≤ low_threshold,
    a single supplier order is placed after supplier_delay time units.
    Only one refill may be in-flight at a time.
    """

    def __init__(
        self,
        env:            simpy.Environment,
        initial:        int,
        capacity:       int,
        low_threshold:  int,
        refill_qty:     int,
        supplier_delay: int,
    ) -> None:
        self.env            = env
        self.capacity       = capacity
        self.low_threshold  = low_threshold
        self.refill_qty     = refill_qty
        self.supplier_delay = supplier_delay
        self.store          = simpy.Container(env, capacity=capacity, init=initial)
        self._refilling     = False

    def request_unit(self) -> simpy.resources.container.ContainerGet:
        if self.store.level <= self.low_threshold and not self._refilling:
            self.env.process(self._refill())
        return self.store.get(1)

    def _refill(self):
        self._refilling = True
        _log(
            self.env,
            f"[Stock] ⚠  low ({self.store.level}/{self.capacity}) "
            f"— supplier notified, ETA +{self.supplier_delay}",
        )
        yield self.env.timeout(self.supplier_delay)
        qty = min(self.refill_qty, self.capacity - self.store.level)
        if qty > 0:
            yield self.store.put(qty)
        _log(
            self.env,
            f"[Stock] ✔  {qty} units delivered "
            f"(level now {self.store.level}/{self.capacity})",
        )
        self._refilling = False


def inv_shared_track(
    env:     simpy.Environment,
    sm:      OrderProcessing,
    bus:     OrderEventBus,
    inv_sys: InventorySystem,
    *,
    allocate_t: int = 1,
    label:      str = "",
):
    """Inventory backed by a shared InventorySystem container."""
    pfx = f"[{label}] " if label else ""
    _log(env, f"{pfx}requesting stock unit  (level={inv_sys.store.level})")
    yield inv_sys.request_unit()
    _log(env, f"{pfx}unit acquired           (level={inv_sys.store.level})")
    sm.reserve()
    bus.reserved.succeed()
    yield env.timeout(allocate_t)
    sm.allocate()
    bus.allocated.succeed()


# ==============================================================================
# make_order — multi-order helper
# ==============================================================================

def make_order(env: simpy.Environment, inv_sys: InventorySystem, oid: int):
    """Full OrderProcessing pipeline for one customer (quiet=True)."""
    tag = f"Ord{oid:02d}"
    sm  = OrderProcessing(quiet=True)
    sm.submit()
    yield env.timeout(1)
    sm.approve()
    sm.start()
    bus = OrderEventBus.create(env)
    yield simpy.AllOf(env, [
        env.process(inv_shared_track(env, sm, bus, inv_sys, label=tag)),
        env.process(pay_track_normal(env, sm, bus)),
        env.process(shp_track_full(env, sm, bus)),
    ])
    _log(env, f"[{tag}] ✅ complete  ({sm.configuration_values})")


# ==============================================================================
# Scenario — self-contained named simulation scenario
# ==============================================================================

@dataclass
class Scenario:
    """
    A self-contained, named simulation scenario.

    Attributes
    ----------
    name        : display title shown in the banner
    description : one-line human description
    gen_fn      : ``(env, sm) -> Generator`` coroutine that drives the run
    time_limit  : SimPy simulation ceiling (default 300)
    report_sm   : print the SM config / outcome when True
    """
    name:        str
    description: str
    gen_fn:      Callable
    time_limit:  int  = 300
    report_sm:   bool = True

    def execute(self) -> Optional[OrderProcessing]:
        """Run the scenario, print summary, and return the SM (or None)."""
        _banner(self.name)
        env = simpy.Environment()
        sm  = OrderProcessing()
        env.process(self.gen_fn(env, sm))
        env.run(until=self.time_limit)
        if self.report_sm:
            print(f"\n  config : {sm.configuration_values}")
            print(f"  result : {_outcome(sm)}")
            return sm
        return None
