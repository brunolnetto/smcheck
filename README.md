# smcheck

[![PyPI - Version](https://img.shields.io/pypi/v/smcheck.svg)](https://pypi.org/project/smcheck/)
[![codecov](https://codecov.io/gh/brunolnetto/smcheck/graph/badge.svg?token=0VL2Z0X4R4)](https://codecov.io/gh/brunolnetto/smcheck)

**Automatic analysis, testing, and visualization for [python-statemachine](https://python-statemachine.readthedocs.io/) state machines.**

smcheck gives you confidence in your state machines by:
- 🔍 **Validating** 9 structural properties (reachability, liveness, determinism, completeness, etc.)
- 📊 **Analyzing** all possible paths through your state machine
- 🧪 **Generating** pytest tests for every transition and path
- 📈 **Exporting** interactive Mermaid diagrams with guard labels

---

## Installation

```bash
pip install smcheck
```

For LLM-powered path explanations (optional):
```bash
pip install smcheck[llm]
```

Requires **Python ≥3.11** and **python-statemachine ≥3.0**.

---

## Quick Start — 5 minutes

Define your state machine normally:

```python
# myapp/machine.py
from statemachine import State, StateChart

class OrderFlow(StateChart):
    waiting   = State(initial=True)
    processing = State()
    shipped   = State(final=True)
    cancelled = State(final=True)

    submit = waiting.to(processing)
    ship   = processing.to(shipped)
    cancel = processing.to(cancelled)  # can cancel during processing
```

Now run **smcheck**:

```python
from smcheck import SMCheck

sm = SMCheck(OrderFlow)

# Validation report (9 checks)
sm.report_validation()

# Path analysis
analysis = sm.analyze_paths()
print(f"Total paths: {analysis.combined_count}")

# Auto-generate tests
sm.write_tests(
    sm_import="myapp.machine",
    output_dir="tests/generated/"
)

# Export diagram
sm.write_mermaid("diagram.mmd")
```

That's it! You now have:
- ✅ A validation report
- ✅ All states are reachable
- ✅ No deadlocks or infinite loops
- ✅ Pytest test files for every transition and path
- ✅ A diagram you can share with stakeholders

---

## The 9 Validation Checks

smcheck validates that your state machine is:

| Check | Detects | Level |
|-------|---------|-------|
| **Reachability** | States you can't reach from the start | ⚠️ WARN |
| **Liveness** | Deadlocks (states with no way out) | 🚨 ERROR |
| **Determinism** | Ambiguous transitions (multiple targets for one event) | ⚠️ WARN |
| **Completeness** | Unfinished states (no outgoing transitions) | ⚠️ WARN |
| **Trap cycles** | Infinite loops with no exit | 🚨 ERROR |
| **Class flags** | Risky behavioral settings | ⚠️ WARN |
| **Invoke states** | States that fire events spontaneously | ℹ️ INFO |
| **Self-transitions** | Loops that trigger entry/exit callbacks | ℹ️ INFO |
| **Hook names** | Typos in convention method names | ⚠️ WARN |

Example:
```python
sm = SMCheck(OrderFlow)
v = sm.validate()
for finding in v.run_all():
    print(f"[{finding.level}] {finding.category}")
    if finding.detail:
        print(f"  {finding.detail}")
```

---

## Common Tasks

### Task: Add guards with conditions

```python
class OrderFlow(StateChart):
    # ... states ...
    
    def can_proceed(self) -> bool:
        """Payment verified."""
        return self._payment_approved
    
    submit = waiting.to(processing, cond="can_proceed")
    
    def __init__(self):
        self._payment_approved = False
        super().__init__()
```

smcheck automatically detects the guard and generates setup code in tests.

### Task: Add sub-machines (compound states)

```python
class OrderFlow(StateChart):
    waiting   = State(initial=True)
    
    class processing(StateChart):
        checking  = State(initial=True)
        shipping  = State()
        done      = shipping  # shorthand for final
        
        check_inv    = checking.to(shipping)
        finish_ship  = shipping.to(done)
    
    cancelled = State(final=True)
    
    submit = waiting.to(processing)
    cancel = waiting.to(cancelled) | processing.to(cancelled)
```

### Task: See the diagram before generating tests

```python
sm = SMCheck(OrderFlow)
diagram_text = sm.to_mermaid()
print(diagram_text)
# → copy/paste into https://mermaid.live
```

---

## Understanding the Output

### Validation Report Example

```
╔════════════════════════════════════════════════════════════════════════╗
║                    VALIDATION FINDINGS                                 ║
╠════════════════════════════════════════════════════════════════════════╣
║  [PASS]     reachability     All 8 states reachable                    ║
║  [PASS]     liveness         No deadlocks (all non-final states exit)  ║
║  [INFO]     invoke_states     processing [auto-timeout]                ║
║  [WARN]     hook_names       on_exit_old_state: state 'old_state' not ║
║                              found (typo?) — remove or fix             ║
╚════════════════════════════════════════════════════════════════════════╝
```

### Generated Tests

```
generated_tests/
├── test_transitions.py    # One test per (state, event) → target
└── test_paths.py          # One test per enumerated path
```

Tests are self-contained and can run offline (no real dependencies).

### Mermaid Diagram

The exported diagram includes:
- Guard conditions in `[square brackets]`
- Constraint notes (e.g., "Only if payment verified")
- Compound states grouped together
- Auto-generated from your state machine — always in sync

---

## Advanced: Custom Guard Setup

If your machine uses **dynamic** guards that can't be inspected statically:

```python
sm = SMCheck(OrderFlow)

# Tell smcheck how to set up each guarded event
guard_setup = {
    "submit": {"_payment_approved": True},
    "ship":   {"_inventory_reserved": True},
}

tests = sm.generate_tests(guard_setup_map=guard_setup)
sm.write_tests(...)
```

---

## Detailed Analysis with `PathAnalysis`

```python
analysis = sm.analyze_paths()

print(f"Top-level paths: {len(analysis.top_level_paths)}")
print(f"Parallel track paths: {analysis.track_paths}")
print(f"Total combinations: {analysis.combined_count}")

# Each path is an SMPath object
for path in analysis.top_level_paths:
    for edge in path.edges:
        print(f"  {edge.source} --{edge.event}--> {edge.target}")
```

---

## Why smcheck?

**For teams developing state machines:**
- Catch structural bugs early (deadlocks, unreachable states)
- Auto-generate test boilerplate (no manual path enumeration)
- Share diagrams with non-technical stakeholders
- Refactor with confidence (re-run validation in CI)

**For library authors:**
- Document state machine behavior with generated tests
- Ensure compatibility with new python-statemachine versions
- Give users example state machines + their tests

**For teaching:**
- Teach students to think about paths and edge cases
- Let students focus on business logic, not test scaffolding
- Visualize complex state machines

---

## Further Reading

See [Feature Catalogue](smcheck/statemachine_feature_catalogue.md) for details on:
- Graph extraction (adjacency maps, path algorithms)
- Detailed validation rules
- Code generation internals
- Mermaid export options

For **python-statemachine** docs, see [python-statemachine.readthedocs.io](https://python-statemachine.readthedocs.io/).

---

## Contributing

Issues and PRs welcome at [github.com/brunolnetto/smcheck](https://github.com/brunolnetto/smcheck).

## License

MIT

```python
from smcheck.graph import derive_guard_setup_map

guard_setup = derive_guard_setup_map(MyMachine)
# e.g. {"process_payment": {"_inventory_reserved": True},
#        "begin_shipping":   {"_inventory_allocated": True, "_payment_authorized": True}}
```

### `derive_compound_traversal(sm_class)` → `dict[str, dict[str, list[str]]]`

For non-parallel compound states with a guarded exit:

1. Identifies the guard method and its flag references.
2. Finds which child's `on_enter_*` hook sets the flag to `True`.
3. BFS-computes the shortest event sequence from the compound's initial
   child to that flag-setting child.

Returns `{compound_id: {exit_target_id: [event1, event2, ...]}}`, used by
`generate_all` as `compound_traversal` to drive the machine through the
compound before firing the guarded exit.

### `extract_transition_actions(sm_class)` → `dict[tuple[str,str], str]`

Returns `{(src_id, event_name): "action1, action2"}` for all transitions
that declare non-convention `before=`, `on=`, or `after=` callbacks.

---

## Mermaid Export

```python
from smcheck.mermaid import to_mermaid, write_mermaid

# Return as a string
diagram = to_mermaid(MyMachine)            # default direction="LR"
diagram = to_mermaid(MyMachine, direction="TB")

# Write to file
write_mermaid(MyMachine, "docs/diagram.mmd")
```

The generated diagram:
- Nests compound and parallel states correctly using Mermaid compound blocks
  and `--` parallel separators.
- Renders `HistoryState` pseudo-states as `[H]` nodes inside their compound.
- Annotates guarded transitions with `[guard_name]` labels.
- Adds `note right of state` annotations for **atomic states that have
  guarded outgoing transitions**, derived from the guard method's docstring —
  making ordering constraints visible directly in the diagram.

---

## Test Generation

```python
from smcheck.testgen import generate_all, write_tests

tests = generate_all(
    MyMachine,
    analysis          = analysis,          # from analyze_paths()
    guard_setup_map   = guard_setup,       # from derive_guard_setup_map() or hand-crafted
    compound_traversal= traversal,         # from derive_compound_traversal() or hand-crafted
)

written = write_tests(
    tests,
    sm_import  = "my_machine",      # import path for the SM class
    sm_class   = "MyMachine",
    output_dir = "generated_tests/",
)
```

Three test levels are generated:

| Level | What is tested |
|-------|---------------|
| `transition` | One test per named edge: reach the source state, fire the event, assert the outcome. |
| `path_top` | One test per enumerated top-level path: drive the complete event sequence and assert the final top-level state. |
| `path_track` | One test per parallel-track path: assert the track's terminal sub-state. |

Guard setup flags are set directly on the SM instance (`sm._flag = True`)
before firing each event; this mirrors the machine's own guard contracts
without tying the tests to internal implementation details.

---

## CLI

```bash
# Validate and print a report for any StateChart subclass
smcheck validate my_module:MyMachine

# Export a Mermaid diagram
smcheck diagram my_module:MyMachine --output diagram.mmd

# Print graph analysis
smcheck graph my_module:MyMachine
```

---

## Design Notes

### `StateChart` vs `StateMachine`

smcheck works with both, but `StateChart` (the SCXML-compliant base class
introduced in python-statemachine v3) is the recommended target.
`StateMachine` is a subclass with stricter defaults (`allow_event_without_transition=False`,
`atomic_configuration_update=True`, etc.) that change certain behavioral
guarantees.  The validator's check ⑥ reports the active flag values so you
always know which semantics apply.

### Parallel-track constraints

Guard methods that cross track boundaries (e.g. `ready_to_ship` checking
both `_inventory_allocated` and `_payment_authorized`) represent ordering
constraints between parallel regions.  smcheck surfaces these via:

- `[guard_name]` labels on Mermaid transition arrows.
- `note right of` annotations on source states (derived from guard docstrings).
- `derive_guard_setup_map` — automatic flag extraction for test generation.

### Loop semantics

Back-edges (cycles) are detected with DFS.  Path enumeration allows each
distinct back-edge to be traversed **at most once** per path, but a path
may include multiple different back-edges.  This gives a finite path count
even for machines with multiple feedback loops.
