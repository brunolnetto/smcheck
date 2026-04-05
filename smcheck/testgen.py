"""smcheck.testgen
===============
Automatic pytest test generation for python-statemachine ``StateChart``
subclasses.

Three granularities are generated:

Transition-level
    One test per named edge in the full transition graph.  The test reaches
    the source state (via a pre-computed setup sequence), fires the event,
    and asserts the resulting condition (top-level state ID for terminal
    states, or a side-effect flag for sub-states in parallel regions).

Path-level
    One test per top-level path (typically 5–10), plus one test per track
    path for every parallel sub-track.  Each test fires the complete event
    sequence and asserts the final top-level state.

Validator tests
    One negative test per transition that declares ``validators=``.  The
    generated test reaches the source state, then expects the event to raise
    because the validator blocks it.  Use ``validator_error_map`` to specify
    which exception type each event's validator raises (defaults to
    ``Exception``).

``guard_setup_map`` and ``unless=`` guards
    For ``cond=`` guards, the attribute on the SM instance must be ``True``
    for the transition to fire — supply ``{event: {attr: True}}``.
    For ``unless=`` guards, the transition fires when the callable returns
    ``False`` — supply ``{event: {attr: False}}`` so the generated test sets
    the flag correctly before firing the event.

Why not 27 combined parallel-track tests?
    The mathematical cross-product of top-level × per-track paths represents
    graph-level reachability, not runtime realizability.  Several combinations
    violate the SM's own ordering constraints (e.g., payment authorized while
    inventory is still out-of-stock), making those sequences invalid to fire
    on a live SM instance.  The generated tests stay within the constraints
    enforced by guard functions.  Combined-scenario coverage is achieved by the
    SimPy integration tests (``_gen_a`` through ``_gen_f`` in the demo app).

Usage::

    from smcheck.testgen import generate_all, write_tests
    tests   = generate_all(OrderProcessing, guard_setup_map=GUARD_SETUP)
    written = write_tests(tests, "order_processing.main", "generated_tests/")
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

try:
    from statemachine.callbacks import CallbackGroup as _CallbackGroup
    _VALIDATOR_GROUP = _CallbackGroup.VALIDATOR
except ImportError:  # pragma: no cover
    _VALIDATOR_GROUP = None

from .graph import (
    bfs_shortest_paths,
    derive_compound_traversal,
    derive_guard_setup_map,
    discover_parallel_tracks,
    extract_sm_graph,
    top_level_graph,
    track_graph,
)
from .paths import PathAnalysis, SMPath, analyze_paths


# ---------------------------------------------------------------------------
# Guard setup maps
# ---------------------------------------------------------------------------

# Default guard→flag mapping for machines that follow the OrderProcessing
# naming convention (guard method name → internal flag attribute).
# Users supply a custom dict[event_name, dict[attr, value]] where needed.
GuardSetupMap = dict[str, dict[str, Any]]


# ---------------------------------------------------------------------------
# TestCase
# ---------------------------------------------------------------------------

@dataclass
class TestStep:
    """A single action in a :class:`TestCase`'s body."""
    kind:  str         # "call"  → fire SM event
                       # "attr"  → set attribute directly (guard bypass)
                       # "comment" → emit a comment line
    event: str = ""    # method name to call (kind == "call")
    attr:  str = ""    # attribute name       (kind == "attr")
    value: Any = None  # value to assign      (kind == "attr")
    text:  str = ""    # comment text         (kind == "comment")


@dataclass
class TestCase:
    """
    A single generated test.

    Attributes
    ----------
    name           : Unique test function name (valid Python identifier).
    level          : ``"transition"`` | ``"path_top"`` | ``"path_track"``
    steps          : Ordered list of :class:`TestStep` actions.
    assert_state   : Expected ``sm.current_state.id`` after all steps.
    assert_flags   : Additional ``{attr: value}`` assertions.
    description    : One-line docstring for the generated function.
    """
    name:         str
    level:        str
    steps:        list[TestStep]
    assert_state:  str | None
    assert_flags:  dict[str, Any] = field(default_factory=dict)
    description:   str = ""


# ---------------------------------------------------------------------------
# Setup-sequence computation
# ---------------------------------------------------------------------------

def _compute_setup_sequences(
    sm_class: type,
    compound_traversal: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, list[str]]:
    """
    Return a map ``{state_id: [events_to_fire_from_initial]}``.

    For states inside a parallel region, the path is:
    ``[events_to_reach_parallel_compound] + [track_events_to_reach_sub_state]``.

    *compound_traversal* maps ``{compound_state_id: {exit_neighbor_state_id:
    [internal_events_to_fire]}}``.  This is used when a compound state's exit
    is guarded (e.g. validation needs ``approve`` before ``start`` can fire).
    """
    ct = compound_traversal or {}
    sm   = sm_class()
    top  = top_level_graph(sm_class)
    init = next(s.id for s in sm.states_map.values() if s.initial and s.parent is None)

    # BFS on top-level gives shortest event sequence to each top-level node.
    # We expand it with compound_traversal inserts.
    raw_bfs = bfs_shortest_paths(top, init)

    setup: dict[str, list[str]] = {}

    def _expand(raw_events: list[str]) -> list[str]:
        """Re-play raw_events, inserting compound_traversal events."""
        expanded: list[str] = []
        current  = init
        for ev in raw_events:
            # Find where this event takes us
            nxt = next((dst for e, dst in top.get(current, []) if e == ev), None)
            if nxt is None:  # pragma: no cover
                expanded.append(ev)
                continue
            # Insert internal events needed to reach the next top-level node
            internal = ct.get(current, {}).get(nxt, [])
            expanded.extend(internal)
            expanded.append(ev)
            current = nxt
        return expanded

    for sid, events in raw_bfs.items():
        setup[sid] = _expand(events)

    # For parallel sub-states: prefix the path to the parallel compound state,
    # then add the shortest track-graph events from the track's initial state.
    parallel = next(
        (s for s in sm.states_map.values() if s.parallel and s.parent is None), None
    )
    if parallel:
        parallel_prefix = setup.get(parallel.id, [])
        for track in discover_parallel_tracks(sm_class):
            t_adj = track_graph(sm_class, track)
            t_initial = next(
                (s.id for s in sm.states_map.values()
                 if s.parent is not None and s.parent.id == track and s.initial),
                None,
            )
            if not t_adj or t_initial is None:  # pragma: no cover
                continue
            t_bfs = bfs_shortest_paths(t_adj, t_initial)
            for tsid, tevents in t_bfs.items():
                if tsid not in setup:
                    setup[tsid] = parallel_prefix + tevents

    # For sub-states of sequential (non-parallel) compound states at depth 1
    # (e.g. validation.reviewing/approved/rejected).  These are entered
    # automatically when the compound is entered, but transitions between them
    # must be fired explicitly and therefore require a setup sequence.
    for sid, state_obj in sm.states_map.items():
        if state_obj.parent is None:                           # top-level, already handled
            continue
        parent = state_obj.parent
        if parent.parent is not None:                          # depth > 1, not handled here
            continue
        if getattr(parent, "parallel", False):                 # parallel sub-states handled above
            continue
        parent_setup = setup.get(parent.id, [])
        local_adj    = track_graph(sm_class, parent.id)
        init_sub     = next(
            (s.id for s in sm.states_map.values()
             if s.parent is not None and s.parent.id == parent.id and s.initial),
            None,
        )
        if init_sub is None:  # pragma: no cover — PSM requires an initial child
            continue
        local_bfs = bfs_shortest_paths(local_adj, init_sub)
        for sub_id, sub_evs in local_bfs.items():
            if sub_id not in setup:
                setup[sub_id] = parent_setup + sub_evs

    return setup


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def _assert_for_state(
    state_id: str,
    sm_class: type,
    assert_flags: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    """
    Return ``(assert_state, assert_flags)`` for a given target state.

    For top-level states, asserts current_state.id.
    For parallel sub-states, falls back to side-effect flags that the SM
    sets via ``on_enter_*`` hooks when available.
    """
    sm = sm_class()
    state_obj = sm.states_map.get(state_id)

    # Top-level or compound container → assert current_state.id
    if state_obj is not None and (
        state_obj.parent is None or state_obj.parent.parent is None
    ):
        return state_id, assert_flags

    # Parallel sub-state → check known side-effect flags + merge caller flags
    flag_hints: dict[str, dict[str, Any]] = {
        "reserved":   {"_inventory_reserved": True},
        "allocated":  {"_inventory_reserved": True, "_inventory_allocated": True},
        "authorized": {"_payment_authorized": True},
        "approved":   {"_validation_approved": True},
        # declined / rejected / out_of_stock / etc. have no positive flag
    }
    extra = {**flag_hints.get(state_id, {}), **assert_flags}
    return None, extra


# ---------------------------------------------------------------------------
# Transition test generator
# ---------------------------------------------------------------------------

def generate_transition_tests(
    sm_class:       type,
    guard_setup_map: GuardSetupMap | None = None,
    compound_traversal: dict[str, dict[str, list[str]]] | None = None,
) -> list[TestCase]:
    """
    Generate one :class:`TestCase` per named edge in the full transition graph.

    Parameters
    ----------
    guard_setup_map
        Maps event names to ``{attr: value}`` that must be set on the SM
        instance **before** firing that event (guards that cannot be satisfied
        by the natural event sequence).  Example::

            {"begin_shipping": {"_inventory_allocated": True,
                                "_payment_authorized": True}}

        For ``unless=`` guards the transition fires when the callable returns
        ``False``, so supply ``{attr: False}``.
        For ``cond=`` guards the callable must return ``True``, so supply
        ``{attr: True}``.

    compound_traversal
        See :func:`_compute_setup_sequences`.
    """
    gmap = guard_setup_map or {}
    adj  = extract_sm_graph(sm_class)
    sm_class()
    setup_seqs = _compute_setup_sequences(sm_class, compound_traversal)

    tests: list[TestCase] = []
    counter: dict[str, int] = {}    # de-duplicate names

    for src in sorted(adj):
        for ev, dst in adj[src]:
            base_name = f"test_transition_{src}__{ev}__{dst}"
            counter[base_name] = counter.get(base_name, 0) + 1
            name = base_name if counter[base_name] == 1 else f"{base_name}_{counter[base_name]}"

            # Build setup steps
            steps: list[TestStep] = []
            setup_events = setup_seqs.get(src, [])
            if setup_events:
                steps.append(TestStep("comment", text=f"Reach source state '{src}'"))
                for se in setup_events:
                    # Inject guard-bypass flags for guarded events in the setup path
                    se_flags = gmap.get(se, {})
                    if se_flags:
                        for attr, val in se_flags.items():
                            steps.append(TestStep("attr", attr=attr, value=val))
                    steps.append(TestStep("call", event=se))

            # Guard bypass flags (if needed)
            guard_flags = gmap.get(ev, {})
            if guard_flags:
                steps.append(TestStep("comment", text=f"Set guard flags for '{ev}'"))
                for attr, val in guard_flags.items():
                    steps.append(TestStep("attr", attr=attr, value=val))

            # Fire the event under test
            steps.append(TestStep("comment", text=f"Fire '{ev}'"))
            steps.append(TestStep("call", event=ev))

            assert_state, assert_flags = _assert_for_state(dst, sm_class, {})

            tests.append(TestCase(
                name=name,
                level="transition",
                steps=steps,
                assert_state=assert_state,
                assert_flags=assert_flags,
                description=f"Transition: {src} --[{ev}]--> {dst}",
            ))
    return tests


# ---------------------------------------------------------------------------
# Path test generators
# ---------------------------------------------------------------------------

def _path_to_steps(
    path:           SMPath,
    guard_setup_map: GuardSetupMap,
    preamble:       list[str] | None = None,
) -> list[TestStep]:
    """Convert an :class:`SMPath` to a list of :class:`TestStep`."""
    steps: list[TestStep] = []
    if preamble:
        steps.append(TestStep("comment", text="Common preamble"))
        for ev in preamble:
            steps.append(TestStep("call", event=ev))
    for edge in path.edges:
        flags = guard_setup_map.get(edge.event, {})
        if flags:
            steps.append(TestStep("comment", text=f"Guard flags for '{edge.event}'"))
            for attr, val in flags.items():
                steps.append(TestStep("attr", attr=attr, value=val))
        steps.append(TestStep("call", event=edge.event))
    return steps


def generate_top_level_path_tests(
    analysis:       PathAnalysis,
    sm_class:       type,
    guard_setup_map: GuardSetupMap | None = None,
    compound_traversal: dict[str, dict[str, list[str]]] | None = None,
) -> list[TestCase]:
    """One test per top-level path (typically 7 for ``OrderProcessing``)."""
    gmap = guard_setup_map or {}
    ct   = compound_traversal or {}
    sm   = sm_class()
    init = next(s.id for s in sm.states_map.values() if s.initial and s.parent is None)

    # Re-compute compound-traversal-expanded BFS to know how to navigate
    # compound states encountered along a top-level path.
    top_level_graph(sm_class)

    tests: list[TestCase] = []
    for i, path in enumerate(analysis.top_level_paths, 1):
        # Build expanded event sequence: add internal compound events
        steps: list[TestStep] = []
        current = init
        for edge in path.edges:
            internal = ct.get(current, {}).get(edge.target, [])
            for ie in internal:
                flags = gmap.get(ie, {})
                if flags:
                    for attr, val in flags.items():
                        steps.append(TestStep("attr", attr=attr, value=val))
                steps.append(TestStep("call", event=ie))

            flags = gmap.get(edge.event, {})
            if flags:
                for attr, val in flags.items():
                    steps.append(TestStep("attr", attr=attr, value=val))
            steps.append(TestStep("call", event=edge.event))
            current = edge.target

        loop_tag = "_loop" if path.is_looping else ""
        name = f"test_path_top_{i:02d}_{path.terminal}{loop_tag}"

        assert_state, assert_flags = _assert_for_state(path.terminal, sm_class, {})

        desc_nodes = " → ".join(path.nodes)
        tests.append(TestCase(
            name=name,
            level="path_top",
            steps=steps,
            assert_state=assert_state,
            assert_flags=assert_flags,
            description=f"Top-level path {i}: {desc_nodes}",
        ))
    return tests


def generate_track_path_tests(
    analysis:       PathAnalysis,
    sm_class:       type,
    guard_setup_map: GuardSetupMap | None = None,
    compound_traversal: dict[str, dict[str, list[str]]] | None = None,
) -> list[TestCase]:
    """
    One test per track path.  Each test first reaches the parallel compound
    state (using the compound_traversal preamble), then fires the track events.
    """
    gmap = guard_setup_map or {}
    ct   = compound_traversal or {}

    # Build preamble to enter the parallel region
    sm   = sm_class()
    init = next(s.id for s in sm.states_map.values() if s.initial and s.parent is None)
    parallel = next(
        (s for s in sm.states_map.values() if s.parallel and s.parent is None), None
    )
    preamble: list[str] = []
    if parallel:
        top_adj = top_level_graph(sm_class)
        raw     = bfs_shortest_paths(top_adj, init)
        raw_evs = raw.get(parallel.id, [])
        # Expand with compound traversal
        current = init
        for ev in raw_evs:
            nxt = next((dst for e, dst in top_adj.get(current, []) if e == ev), None)
            if nxt is None:  # pragma: no cover
                preamble.append(ev)
                continue
            preamble.extend(ct.get(current, {}).get(nxt, []))
            preamble.append(ev)
            current = nxt

    tests: list[TestCase] = []
    for track, paths in analysis.track_paths.items():
        for i, path in enumerate(paths, 1):
            steps = _path_to_steps(path, gmap, preamble=preamble)
            name  = f"test_path_track_{track}_{i:02d}_{path.terminal}"

            assert_state, assert_flags = _assert_for_state(path.terminal, sm_class, {})

            desc_nodes = " → ".join(path.nodes)
            tests.append(TestCase(
                name=name,
                level="path_track",
                steps=steps,
                assert_state=assert_state,
                assert_flags=assert_flags,
                description=f"Track '{track}' path {i}: {desc_nodes}",
            ))
    return tests


# ---------------------------------------------------------------------------
# Validator test generator
# ---------------------------------------------------------------------------

ValidatorErrorMap = dict[str, type]


def generate_validator_tests(
    sm_class: type,
    validator_error_map: ValidatorErrorMap | None = None,
    compound_traversal: dict[str, dict[str, list[str]]] | None = None,
) -> list[TestCase]:
    """
    Generate one negative :class:`TestCase` per transition that declares a
    non-convention ``validators=`` callback.

    Each generated test:

    1. Navigates to the transition's source state via the pre-computed setup
       sequence.
    2. Fires the event inside ``pytest.raises(<ExceptionType>)`` — asserting
       that the validator blocks the transition.

    Parameters
    ----------
    validator_error_map
        Maps event names to the exception **type** the validator raises, e.g.
        ``{"proceed": ValueError}``.  Events absent from this map default to
        ``Exception``.
    compound_traversal
        See :func:`_compute_setup_sequences`.
    """
    vmap = validator_error_map or {}
    sm = sm_class()
    setup_seqs = _compute_setup_sequences(sm_class, compound_traversal)
    tests: list[TestCase] = []
    seen: set[int] = set()

    for state in sm.states_map.values():
        for t in state.transitions.transitions:
            if id(t) in seen:  # pragma: no cover
                continue
            seen.add(id(t))

            # Find user-declared validators= specs (NOT cond= guards).
            # In PSM v3, t.validators contains all specs; use CallbackGroup.VALIDATOR
            # to distinguish actual validators (raise on failure) from cond= guards
            # (silently block the transition).
            validators_group = getattr(t, "validators", None)
            spec_list = getattr(validators_group, "list", None) if validators_group else None
            if _VALIDATOR_GROUP is not None:
                non_conv = [
                    sp for sp in (spec_list or [])
                    if getattr(sp, "group", None) is _VALIDATOR_GROUP
                ]
            else:  # pragma: no cover
                non_conv = [  # pragma: no cover
                    sp for sp in (spec_list or [])
                    if not getattr(sp, "is_convention", True)
                ]  # pragma: no cover
            if not non_conv:
                continue

            ev_name = next((e.name for e in t.events), None)
            if not ev_name:  # pragma: no cover
                continue

            src_id  = t.source.id
            exc_cls = vmap.get(ev_name) or Exception
            exc_name = exc_cls.__name__

            steps: list[TestStep] = []
            setup_events = setup_seqs.get(src_id, [])
            if setup_events:
                steps.append(TestStep("comment", text=f"Reach source state '{src_id}'"))
                for se in setup_events:
                    steps.append(TestStep("call", event=se))

            steps.append(TestStep("comment", text=f"Validator blocks '{ev_name}'"))
            steps.append(TestStep(kind="raises", event=ev_name, value=exc_name))

            tests.append(TestCase(
                name=f"test_validator_blocks_{ev_name}",
                level="validator",
                steps=steps,
                assert_state=None,
                description=(
                    f"Validator on '{ev_name}' from '{src_id}' raises {exc_name}"
                ),
            ))

    return tests


# ---------------------------------------------------------------------------
# Convenience aggregator
# ---------------------------------------------------------------------------

def generate_all(
    sm_class:            type,
    guard_setup_map:     GuardSetupMap | None = None,
    compound_traversal:  dict[str, dict[str, list[str]]] | None = None,
    analysis:            PathAnalysis | None = None,
    validator_error_map: ValidatorErrorMap | None = None,
) -> list[TestCase]:
    """Generate transition-level + top-level-path + track-path + validator tests.

    When *guard_setup_map* is ``None`` (the default), it is auto-derived by
    inspecting each guard method's source via
    :func:`~smcheck.graph.derive_guard_setup_map`.  Pass an explicit dict to
    override when your machine uses dynamic guards that cannot be determined
    via source inspection.

    Likewise, *compound_traversal* defaults to the result of
    :func:`~smcheck.graph.derive_compound_traversal` when ``None``.
    """
    if analysis is None:
        analysis = analyze_paths(sm_class)
    gmap = guard_setup_map     if guard_setup_map     is not None else derive_guard_setup_map(sm_class)
    ct   = compound_traversal  if compound_traversal  is not None else derive_compound_traversal(sm_class)
    transition_tests = generate_transition_tests(
        sm_class, gmap, ct
    )
    top_tests = generate_top_level_path_tests(
        analysis, sm_class, gmap, ct
    )
    track_tests = generate_track_path_tests(
        analysis, sm_class, gmap, ct
    )
    validator_tests = generate_validator_tests(
        sm_class, validator_error_map, ct
    )
    return transition_tests + top_tests + track_tests + validator_tests


# ---------------------------------------------------------------------------
# Code renderer
# ---------------------------------------------------------------------------

def _render_step(step: TestStep, indent: str = "    ") -> str:
    if step.kind == "comment":
        return f"{indent}# {step.text}"
    if step.kind == "call":
        return f"{indent}sm.{step.event}()"
    if step.kind == "attr":
        val = repr(step.value)
        return f"{indent}sm.{step.attr} = {val}"
    if step.kind == "raises":
        exc = step.value if step.value else "Exception"
        return (
            f"{indent}with pytest.raises({exc}):\n"
            f"{indent}    sm.{step.event}()"
        )
    return f"{indent}# unknown step kind: {step.kind!r}"


def render_pytest(
    tests:     list[TestCase],
    sm_import: str,
    sm_class:  str = "OrderProcessing",
) -> str:
    """
    Render *tests* as a complete, valid Python pytest module.

    Parameters
    ----------
    tests      : Test cases to render (from :func:`generate_all`).
    sm_import  : Dotted module path, e.g. ``"order_processing.main"``.
    sm_class   : Class name to import from *sm_import*.
    """
    lines = [
        '"""',
        f"Auto-generated by smcheck.testgen for {sm_class}.",
        "",
        "DO NOT edit manually — regenerate with:",
        "  python -m smcheck testgen --output <dir>",
        '"""',
        "from __future__ import annotations",
        "",
        "import pytest",
        f"from {sm_import} import {sm_class}",
        "",
        "",
    ]

    transition_tests  = [t for t in tests if t.level == "transition"]
    path_tests_top    = [t for t in tests if t.level == "path_top"]
    path_tests_track  = [t for t in tests if t.level == "path_track"]
    validator_tests   = [t for t in tests if t.level == "validator"]
    # Helper inserted into every generated file so assertions work for both
    # simple and compound/parallel active configurations.
    lines += [
        "",
        "def _active(sm, state_id: str) -> bool:",
        '    """Return True when *state_id* is part of the current active configuration."""',
        "    return any(s.id == state_id for s in sm.configuration)",
        "",
        "",
    ]
    # ── Transition tests ──────────────────────────────────────────────────────
    if transition_tests:
        lines += [
            "# " + "=" * 72,
            "# Transition-level tests (one per named edge)",
            "# " + "=" * 72,
            "",
        ]
        for tc in transition_tests:
            lines += [
                f"def {tc.name}():",
                f'    """{tc.description}"""',
                f"    sm = {sm_class}(quiet=True)",
                "",
            ]
            for step in tc.steps:
                lines.append(_render_step(step))
            lines.append("")
            if tc.assert_state:
                lines.append(f"    assert _active(sm, {tc.assert_state!r})")
            for attr, val in tc.assert_flags.items():
                lines.append(f"    assert sm.{attr} == {val!r}")
            lines += ["", ""]

    # ── Top-level path tests ──────────────────────────────────────────────────
    if path_tests_top:
        # Build parametrize data
        params = []
        for tc in path_tests_top:
            params.append(f"    pytest.param({tc.steps!r}, {tc.assert_state!r}, "
                          f"{tc.assert_flags!r}, id={tc.name!r}),")

        lines += [
            "# " + "=" * 72,
            "# Top-level path tests (one per top-level path)",
            "# " + "=" * 72,
            "",
        ]
        # Render as individual test functions (cleaner than parametrize for complex step lists)
        for tc in path_tests_top:
            lines += [
                f"def {tc.name}():",
                f'    """{tc.description}"""',
                f"    sm = {sm_class}(quiet=True)",
                "",
            ]
            for step in tc.steps:
                lines.append(_render_step(step))
            lines.append("")
            if tc.assert_state:
                lines.append(f"    assert _active(sm, {tc.assert_state!r})")
            for attr, val in tc.assert_flags.items():
                lines.append(f"    assert sm.{attr} == {val!r}")
            lines += ["", ""]

    # ── Track path tests ──────────────────────────────────────────────────────
    if path_tests_track:
        lines += [
            "# " + "=" * 72,
            "# Per-track path tests (one per track path)",
            "# " + "=" * 72,
            "",
        ]
        for tc in path_tests_track:
            lines += [
                f"def {tc.name}():",
                f'    """{tc.description}"""',
                f"    sm = {sm_class}(quiet=True)",
                "",
            ]
            for step in tc.steps:
                lines.append(_render_step(step))
            lines.append("")
            if tc.assert_state:
                lines.append(f"    assert _active(sm, {tc.assert_state!r})")
            for attr, val in tc.assert_flags.items():
                lines.append(f"    assert sm.{attr} == {val!r}")
            lines += ["", ""]

    # ── Validator tests ───────────────────────────────────────────────────────
    if validator_tests:
        lines += [
            "# " + "=" * 72,
            "# Validator tests (event blocked by validators= constraint)",
            "# " + "=" * 72,
            "",
        ]
        for tc in validator_tests:
            lines += [
                f"def {tc.name}():",
                f'    """{tc.description}"""',
                f"    sm = {sm_class}(quiet=True)",
                "",
            ]
            for step in tc.steps:
                lines.append(_render_step(step))
            lines += ["", ""]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def write_tests(
    tests:     list[TestCase],
    sm_import: str,
    output_dir: str,
    sm_class:  str = "OrderProcessing",
) -> list[str]:
    """
    Render and write test files to *output_dir*.

    Writes:
    * ``test_transitions.py`` — transition-level tests
    * ``test_paths.py``       — top-level path + track path tests

    Returns the list of written file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    written: list[str] = []

    transition_tests = [t for t in tests if t.level == "transition"]
    path_tests       = [t for t in tests if t.level in ("path_top", "path_track")]
    validator_tests  = [t for t in tests if t.level == "validator"]

    for fname, subset in [
        ("test_transitions.py", transition_tests),
        ("test_paths.py",       path_tests),
        ("test_validators.py",  validator_tests),
    ]:
        if not subset:
            continue
        code = render_pytest(subset, sm_import, sm_class)
        fpath = os.path.join(output_dir, fname)
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write(code)
        written.append(fpath)

    return written
