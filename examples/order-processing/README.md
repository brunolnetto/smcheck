# Order-Processing Example

A fully working **SimPy + python-statemachine** application that models an
e-commerce order lifecycle.  It is the canonical demonstration target for the
[smcheck](../../README.md) static-analysis library.

---

## Business Rules

The machine enforces four ordering constraints via shared boolean flags and
guard methods.  Guards are wired through `cond=` / `unless=` parameters and
are inspected automatically by smcheck to derive test-setup maps.

| # | Constraint | Guard method | What it checks |
|---|-----------|-------------|----------------|
| ① | Payment cannot begin until inventory is soft-held | `inventory_is_reserved` | `self._inventory_reserved` |
| ② | Shipping cannot begin until stock is committed **and** payment is authorised | `ready_to_ship` | `self._inventory_allocated and self._payment_authorized` |
| ③ | Delivery acknowledgement must follow actual delivery | `acknowledge` event | wired by state topology (`delivered → acknowledged`) |
| ④ | Order is only `complete` when all three tracks have fulfilled their obligations | `is_all_done` | `self._inventory_allocated and self._payment_authorized and self._shipping_acknowledged` |

Additional constraint guards:

| Guard | Purpose |
|-------|---------|
| `before_dispatch` | `cancel`, `pause`, and `suspend` are only available before the shipment is dispatched to the carrier. |
| `is_approved` | The `start` transition into fulfillment only fires after validation approves the order. |
| `ops_only` | `suspend`, `release`, and the ops-hold `cancel` require an explicit authorisation flag (`self._ops_authorized`). |

---

## State-Machine Topology

```
idle
 ├─ submit ──► validation (compound)
 │               reviewing ──[approve]──► approved
 │               reviewing ──[reject]───► rejected
 │
 └─ start [is_approved] ──► fulfillment (parallel)
         ┌─────────────┬─────────────────────┐
         │ inventory   │ payment             │ shipping
         │ checking    │ pay_hold            │ ship_hold
         │   ├─reserve─► reserved   ①─────► │   └─begin_shipping [ready_to_ship]─► preparing
         │   │            └─allocate─► allocated ②─────────────────────────────►      └─mark_ready─► ready
         │   │            └─mark_unavailable─► out_of_stock                                └─dispatch─► in_transit
         │   ├─backorder─► backordered                                                          └─deliver─► delivered
         │   │   └─stock_available─► reserved                                                       └─acknowledge─► acknowledged ③
         │   │   └─request_approval─► stock_review
         │   │           ├─approve_partial─► reserved
         │   │           └─decline_partial─► out_of_stock
         │   └─mark_partial─► partial_stock
         │           └─accept_partial─► allocated

fulfillment ──[pause, before_dispatch]──► on_hold ──[resume]──► shipping.h
fulfillment ──[suspend, before_dispatch + ops_only]──► ops_hold ──[release, ops_only]──► shipping.h
fulfillment ──[complete, is_all_done] ④──► success (final)
fulfillment ──[fail]──► failed (final)
fulfillment ──[cancel, before_dispatch]──► cancelled (final)
on_hold     ──[cancel]──────────────────────────────────────────► cancelled
ops_hold    ──[cancel, ops_only]────────────────────────────────► cancelled
```

The Mermaid diagram (`diagram.mmd`) is generated automatically by smcheck and
includes constraint notes on the waiting states:

- **Pay hold** — _Constraint ①: block payment until soft-hold is confirmed_
- **Ship hold** — _Constraint ②: shipping blocked until stock committed AND payment done_

---

## Scenarios

| ID | Name | Outcome | Key path |
|----|------|---------|----------|
| A | Happy path | `success` | All three tracks succeed |
| B | Out of stock | `failed` | `checking → out_of_stock → fail` |
| C | Payment declined | `failed` | `processing → declined → fail` |
| D | Payment timeout | `failed` | Simpy timeout → `fail` |
| E | Customer pause / resume | `success` | `pause → on_hold → resume → success` |
| F | Shared inventory (concurrent orders) | both `success` | SimPy Container shared between two orders |
| G | Partial fulfilment | `success` | `checking → partial_stock → accept_partial → allocated` |
| H | Backorder restock | `success` | `backordered → stock_available → reserved → allocated` |
| I | Stock-review approve | `success` | `backordered → stock_review → approve_partial → reserved` |
| J | Stock-review decline | `failed` | `backordered → stock_review → decline_partial → out_of_stock` |
| K | Ops hold → release | `success` | `suspend → ops_hold → release → success` |
| L | Ops hold → cancel | `cancelled` | `suspend → ops_hold → cancel [ops_only]` |

---

## File Layout

```
order-processing/
├── machine.py          State machine definition (StateChart subclass)
├── sim.py              SimPy primitives (OrderEventBus, TrackConfig, Scenario)
├── scenarios/          One module per named scenario
│   ├── __init__.py     SCENARIOS list + run_all()
│   ├── a_happy.py  … l_ops_hold_cancel.py
├── analysis.py         smcheck pipeline wrappers (called from main.py)
├── main.py             Entry point — runs all scenarios then smcheck pipeline
└── diagram.mmd         Auto-generated Mermaid diagram (overwritten by run)
```

---

## How to Run

```bash
# from the repo root
cd examples/order-processing
python main.py
```

This will:
1. Execute all 12 SimPy scenarios (A–L) and print their outcomes.
2. Run the full smcheck pipeline: graph analysis → validation → path
   enumeration → test generation → Mermaid diagram export → optional LLM
   path explanations → **business rules coherence check**.

Generated artefacts are written alongside the source:
- `diagram.mmd` — Mermaid state diagram with constraint notes
- `generated_tests/` — pytest test files for every transition and path
- `RULES_CHECK.md` — Business rules coherence report (when LLM is available)

To run only the generated tests:

```bash
cd examples/order-processing
pytest generated_tests/ -v
```

---

## Key Design Decisions

### Why `acknowledged` is **not** a final state

In python-statemachine v3, a `State.Parallel` auto-terminates when **all**
of its regions reach a final sub-state.  If `acknowledged` were final, the
entire `fulfillment` parallel block would terminate the moment the customer
acknowledges delivery — before the top-level `complete` transition could
fire.  Making `acknowledged = State()` (non-final) keeps `fulfillment` alive
so that `complete [is_all_done]` can fire and move the machine to the
terminal `success` state.

### HistoryState and pause/resume

`shipping.h = HistoryState()` records the last active sub-state within the
`shipping` compound.  When the machine returns from `on_hold` (via `resume`)
or `ops_hold` (via `release`), it re-enters shipping at exactly the
sub-state it left — preserving in-flight picking, packing, or transit state.

### SimPy coordination

The three parallel tracks coordinate via `OrderEventBus` (a dataclass of
named `simpy.Event` objects).  Track coroutines signal readiness by calling
`.succeed()` on the bus events; downstream tracks `yield` on them.  This
mirrors the machine's guard semantics at the simulation layer without
coupling the track coroutines to each other directly.
