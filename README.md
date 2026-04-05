# smcheck

[![codecov](https://codecov.io/gh/brunolnetto/smcheck/graph/badge.svg?token=0VL2Z0X4R4)](https://codecov.io/gh/brunolnetto/smcheck)

Static analysis, path enumeration, automatic test generation, and Mermaid
diagram export for [python-statemachine](https://python-statemachine.readthedocs.io/)
`StateChart` subclasses.

---

## Features

| Module | What it does |
|--------|-------------|
| `smcheck.graph` | Extract the transition graph, enumerate paths, detect back-edges, infer guard setups and compound-traversal sequences |
| `smcheck.validator` | Nine static validity checks (reachability, liveness, determinism, completeness, trap cycles, class flags, invoke states, self-transitions, hook-name typos) |
| `smcheck.paths` | Rich `SMPath` / `PathEdge` objects enriched with guard names, action names, back-edge flags, and self-transition flags |
| `smcheck.testgen` | Generate pytest test files for every transition and every enumerated path |
| `smcheck.mermaid` | Export a `stateDiagram-v2` diagram with guard labels and constraint notes |
| `smcheck.report` | Human-readable console reports for graph analysis and validation |
| `smcheck.explainer` | LLM-powered natural-language path explanations (optional; requires `langchain`) |

---

## Installation

```bash
pip install python-statemachine>=3.0.0
# clone this repo, then:
pip install -e .

# For LLM explanation support (optional):
pip install -e ".[llm]"
```

---

## Quick Start

```python
from smcheck.graph    import extract_sm_graph, derive_guard_setup_map
from smcheck.validator import SMValidator
from smcheck.paths    import analyze_paths
from smcheck.testgen  import generate_all, write_tests
from smcheck.mermaid  import write_mermaid
from smcheck.report   import run_graph_analysis, run_validation

from my_machine import MyMachine

# 1 — print graph analysis
run_graph_analysis(MyMachine)

# 2 — run all 9 validity checks and print findings
run_validation(MyMachine)

# 3 — enumerate paths
analysis = analyze_paths(MyMachine)
print(f"{len(analysis.top_level_paths)} top-level paths")

# 4 — generate pytest tests
tests   = generate_all(MyMachine, analysis=analysis)
written = write_tests(tests, sm_import="my_machine", output_dir="generated_tests/")

# 5 — write Mermaid diagram
write_mermaid(MyMachine, "diagram.mmd")
```

---

## Validity Criteria

`SMValidator` runs nine checks.  Each returns a `ValidationFinding` with a
`level` of `"PASS"`, `"WARN"`, or `"ERROR"`.

| # | Check | Level on failure | What is detected |
|---|-------|-----------------|-----------------|
| ① | **Reachability** | `WARN` | States unreachable from the initial state (likely design gaps; compound sub-states entered via auto-transitions are expected and noted separately) |
| ② | **Liveness** | `ERROR` | Reachable non-final states with no path to any terminal (deadlocks) |
| ③ | **Determinism** | `WARN` | `(state, event)` pairs with more than one target (ambiguous transitions; guards may resolve the ambiguity statically) |
| ④ | **Completeness** | `WARN` | Non-final, non-pseudo states with no outgoing transitions (unfinished states) |
| ⑤ | **Trap cycles** | `ERROR` | Strongly-connected components with no exit edge (infinite-loop risk) |
| ⑥ | **Class flags** | `WARN` | High-impact behavioral flags set to non-default values (e.g. `allow_event_without_transition=False`) |
| ⑦ | **Invoke states** | `INFO` | States that declare `invoke=` handlers and can fire events spontaneously |
| ⑧ | **Self-transitions** | `INFO` | Self-loops (`a.to(a)`) that trigger entry/exit callbacks |
| ⑨ | **Hook-name audit** | `WARN` | Method names that look like convention hooks (`on_enter_*`, `on_exit_*`) but reference a non-existent state ID (likely typos) |

```python
v = SMValidator(MyMachine)
for finding in v.run_all():
    print(f"[{finding.level}] {finding.category}: {finding.detail}")
    if finding.nodes:
        print(f"  states: {finding.nodes}")
```

---

## Graph Extraction

### `extract_sm_graph(sm_class)` → `AdjMap`

Returns `{src_id: [(event_name, dst_id), ...]}` for every named transition.
Both compound/parallel container nodes and their atomic children appear, so
the map captures every observable edge.

### `top_level_graph(sm_class)` → `AdjMap`

Filters to states at depth ≤ 1 (direct children of the root).  Transitions
originating inside parallel sub-tracks are excluded.

### `track_graph(sm_class, track_id)` → `AdjMap`

Filters to states inside the named parallel track (e.g. `"inventory"`).

### Path algorithms

```python
from smcheck.graph import find_back_edges, enumerate_paths, count_paths_with_loops

adj       = top_level_graph(MyMachine)
terminals = top_level_terminals(MyMachine)
backs     = find_back_edges(adj, initial_state_id)

counts = count_paths_with_loops(adj, initial_state_id, terminals, backs)
# → {"simple": 5, "with_loops": 2, "total": 7}

paths = enumerate_paths(adj, initial_state_id, terminals, backs)
# → [[node1, node2, ...], ...]  (each back-edge traversed at most once per path)
```

---

## Inferred Transitions and Guard Setup

smcheck can automatically derive the information test generation needs by
inspecting the state machine's source code — no manual guard-setup map
required.

### `derive_guard_setup_map(sm_class)` → `dict[str, dict[str, Any]]`

For every guarded transition (`cond=` / `unless=`):

1. Reads `spec.attr_name` — the guard method name.
2. Calls `inspect.getsource` on that method.
3. Extracts every `self._xxx` reference; each flag must equal
   `spec.expected_value` (`True` for `cond=`, `False` for `unless=`)
   before the event can fire.

Returns `{event_name: {attr: expected_value, ...}}`, suitable for passing
directly to `generate_all` as `guard_setup_map`.

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
