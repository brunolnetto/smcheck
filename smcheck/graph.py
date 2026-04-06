"""smcheck.graph
=============
Graph extraction and path-enumeration algorithms for python-statemachine
``StateChart`` classes.

All public functions accept a StateChart *subclass* (not an instance) so that
a fresh instance can be created for each call without mutating shared state.
"""

from __future__ import annotations

import inspect
import re
from collections import deque
from typing import Any

from statemachine import HistoryState

# ---------------------------------------------------------------------------
# Public type alias
# ---------------------------------------------------------------------------

AdjMap = dict[str, list[tuple[str, str]]]
"""Adjacency map: ``{src_state_id: [(event_name, dst_state_id), ...]}``."""


# ---------------------------------------------------------------------------
# Graph extraction
# ---------------------------------------------------------------------------


def extract_sm_graph(sm_class: type) -> AdjMap:
    """
    Build an adjacency map ``{src_state_id: [(event_name, dst_state_id), ...]}``
    from a StateChart subclass without requiring the ``pydot`` optional dependency.

    Only **explicit, named transitions** (event name ≠ ``"?"``) are included.
    Both compound/parallel container nodes and their atomic children appear so
    the graph captures every observable transition.

    Uses the public ``states_map`` property (dict of ``id → State``) and each
    State's ``transitions`` descriptor (a ``TransitionList``), whose ``.transitions``
    attribute yields individual ``Transition`` objects with ``.source``,
    ``.target``, and ``.events``.
    """
    sm = sm_class()
    adj: AdjMap = {}
    seen: set[int] = set()

    for state in sm.states_map.values():
        for t in state.transitions.transitions:
            if id(t) in seen:  # pragma: no cover
                continue
            seen.add(id(t))
            ev_name = next((e.name for e in t.events), "?")
            if ev_name == "?":
                continue  # skip internal compound-entry transitions
            src = t.source.id
            if isinstance(t.target, HistoryState):
                # HistoryState is a pseudo-state; resolve to its parent compound
                # state so the adjacency map only contains real, catalogued nodes.
                parent = getattr(t.target, "parent", None)
                dst = parent.id if parent is not None else t.target.id
            else:
                dst = t.target.id
            adj.setdefault(src, []).append((ev_name, dst))

    return adj


GuardMap = dict[tuple[str, str, str], list[str]]
"""Maps ``(src_state_id, event_name, dst_state_id)`` to a list of guard condition names."""


def extract_transition_guards(sm_class: type) -> GuardMap:
    """
    Return a map of ``{(src, event, dst): [guard_name, ...]}`` for every
    transition that has at least one user-defined guard condition.

    Only non-convention ``CallbackSpec`` entries (``is_convention=False``) are
    included — convention-named lifecycle hooks such as ``before_transition``
    are excluded so only the real guard method names surface.
    """
    sm = sm_class()
    result: GuardMap = {}
    seen: set[int] = set()

    for state in sm.states_map.values():
        for t in state.transitions.transitions:
            if id(t) in seen:  # pragma: no cover
                continue
            seen.add(id(t))
            ev_name = next((e.name for e in t.events), "?")
            if ev_name == "?":
                continue
            src = t.source.id
            if isinstance(t.target, HistoryState):
                parent = getattr(t.target, "parent", None)
                dst = parent.id if parent is not None else t.target.id
            else:
                dst = t.target.id
            cond_list = getattr(t.cond, "list", None)
            if cond_list:
                guards = [
                    spec.attr_name
                    for spec in cond_list
                    if not getattr(spec, "is_convention", True) and getattr(spec, "attr_name", None)
                ]
                if guards:
                    result[(src, ev_name, dst)] = guards

    return result


def top_level_graph(sm_class: type) -> AdjMap:
    """
    Return an adjacency map for the **top-level** control flow only.

    Only transitions whose source is at depth ≤ 1 (parent is ``None`` or
    parent's parent is ``None``) are included.  Edges originating inside
    parallel sub-tracks are excluded.
    """
    sm = sm_class()
    top_ids = {
        sid for sid, s in sm.states_map.items() if s.parent is None or s.parent.parent is None
    }
    full = extract_sm_graph(sm_class)
    top: AdjMap = {}
    for src, outs in full.items():
        if src in top_ids:
            for ev, dst in outs:
                top.setdefault(src, []).append((ev, dst))
    return top


def track_graph(sm_class: type, track_id: str) -> AdjMap:
    """
    Return an adjacency map restricted to states inside the given parallel track
    (identified by its compound-state ID, e.g. ``"inventory"``).
    """
    sm = sm_class()
    track_state = sm.states_map.get(track_id)
    if track_state is None:
        return {}
    track_ids = {
        sid
        for sid, s in sm.states_map.items()
        if s is track_state or (s.parent is not None and s.parent.id == track_id)
    }
    full = extract_sm_graph(sm_class)
    sub: AdjMap = {}
    for src, outs in full.items():
        if src in track_ids:
            sub.setdefault(src, []).extend(
                (ev, dst) for ev, dst in outs if dst in track_ids or dst not in full
            )
    return sub


def top_level_terminals(sm_class: type) -> set[str]:
    """
    Return the IDs of states that are both final and top-level (``parent`` is
    ``None``), i.e. the globally observable end states of the machine.
    """
    sm = sm_class()
    return {s.id for s in sm.states_map.values() if s.final and s.parent is None}


def discover_parallel_tracks(sm_class: type) -> list[str]:
    """
    Return the IDs of the direct-child compound states inside the first
    top-level ``State.Parallel`` found on the class, in states_map order.

    Returns an empty list when the machine has no parallel region.
    """
    sm = sm_class()
    parallel = next(
        (s for s in sm.states_map.values() if s.parallel and s.parent is None),
        None,
    )
    if parallel is None:
        return []
    return [
        s.id for s in sm.states_map.values() if s.parent is not None and s.parent.id == parallel.id
    ]


# ---------------------------------------------------------------------------
# Path algorithms
# ---------------------------------------------------------------------------


def find_back_edges(
    adj: AdjMap,
    start: str,
) -> list[tuple[str, str, str]]:
    """
    Return all back-edges ``(event, src, dst)`` found while DFS-ing from
    *start*.  A back-edge is one whose destination is a gray (ancestor) node
    in the DFS tree — i.e., it creates a cycle.
    """
    back: list[tuple[str, str, str]] = []
    colour: dict[str, str] = {}

    def dfs(node: str) -> None:
        colour[node] = "gray"
        for ev, neighbour in adj.get(node, []):
            if colour.get(neighbour) == "gray":
                back.append((ev, node, neighbour))
            elif colour.get(neighbour) != "black":
                dfs(neighbour)
        colour[node] = "black"

    dfs(start)
    return back


def count_paths_with_loops(
    adj: AdjMap,
    start: str,
    terminals: set[str],
    back_edges: list[tuple[str, str, str]],
) -> dict[str, int]:
    """
    Enumerate all paths from *start* to any terminal, counting:

    * ``simple``     – paths that traverse no back-edge
    * ``with_loops`` – paths that traverse ≥ 1 back-edge (each at most once)
    * ``total``      – simple + with_loops

    Each distinct back-edge may be traversed at most once per path, but a
    single path may contain multiple *different* back-edges.
    """
    back_set: dict[tuple[str, str], int] = {
        (src, dst): idx for idx, (_, src, dst) in enumerate(back_edges)
    }
    simple = 0
    with_loops = 0
    stack: list[tuple[str, frozenset[str], frozenset[int]]] = [
        (start, frozenset({start}), frozenset())
    ]
    while stack:
        node, visited, used_loops = stack.pop()
        if node in terminals:
            if used_loops:
                with_loops += 1
            else:
                simple += 1
            continue
        for _ev, nbr in adj.get(node, []):
            edge_idx = back_set.get((node, nbr))
            if edge_idx is not None:
                if edge_idx not in used_loops:
                    stack.append((nbr, visited, used_loops | {edge_idx}))
            else:
                if nbr not in visited:
                    stack.append((nbr, visited | {nbr}, used_loops))
    return {"simple": simple, "with_loops": with_loops, "total": simple + with_loops}


def enumerate_paths(
    adj: AdjMap,
    start: str,
    terminals: set[str],
    back_edges: list[tuple[str, str, str]],
) -> list[list[str]]:
    """
    Return every path (as a list of node IDs) from *start* to any terminal,
    using the same loop semantics as :func:`count_paths_with_loops`.
    """
    back_set: dict[tuple[str, str], int] = {
        (src, dst): idx for idx, (_, src, dst) in enumerate(back_edges)
    }
    paths: list[list[str]] = []
    stack: list[tuple[str, list[str], frozenset[str], frozenset[int]]] = [
        (start, [start], frozenset({start}), frozenset())
    ]
    while stack:
        node, path, visited, used_loops = stack.pop()
        if node in terminals:
            paths.append(path)
            continue
        for _ev, nbr in adj.get(node, []):
            edge_idx = back_set.get((node, nbr))
            if edge_idx is not None:
                if edge_idx not in used_loops:
                    stack.append((nbr, path + [nbr], visited, used_loops | {edge_idx}))
            else:
                if nbr not in visited:
                    stack.append((nbr, path + [nbr], visited | {nbr}, used_loops))
    return paths


def bfs_shortest_paths(adj: AdjMap, start: str) -> dict[str, list[str]]:
    """
    BFS from *start* returning the shortest event-sequence to every reachable
    node: ``{node_id: [event1, event2, ...]}``.

    Back-edges are not treated specially here; the first (shortest) path to
    each node is returned.
    """
    visited: dict[str, list[str]] = {start: []}
    queue: deque[tuple[str, list[str]]] = deque([(start, [])])
    while queue:
        node, events = queue.popleft()
        for ev, dst in adj.get(node, []):
            if dst not in visited:
                visited[dst] = events + [ev]
                queue.append((dst, events + [ev]))
    return visited


# ---------------------------------------------------------------------------
# Advanced inspection helpers
# ---------------------------------------------------------------------------


def discover_invoke_states(sm_class: type) -> dict[str, str]:
    """
    Return a mapping ``{state_id: handler_description}`` for every state that
    has an ``invoke=`` handler declared.

    Handler description is derived from the callable's ``__name__`` or its
    class name.  ``_InvokeCallableWrapper`` instances expose the real handler
    via their ``_invoke_handler`` attribute, so ``timeout(5, on="expired")``
    appears as ``"_Timeout"`` and a named callable appears as its function name.

    States without invoke handlers are omitted from the returned dict.
    """
    sm = sm_class()
    result: dict[str, str] = {}
    for state in sm.states_map.values():
        invoke_attr = getattr(state, "invoke", None)
        if invoke_attr is None:  # pragma: no cover
            continue
        # SpecListGrouper exposes its callbacks via the .list property
        spec_list = getattr(invoke_attr, "list", None)
        if spec_list is None:  # pragma: no cover
            continue
        handler_names: list[str] = []
        for spec in spec_list:
            # Skip convention hooks (on_invoke_state, on_invoke_{id})
            # — only user-supplied inline callables are meaningful here.
            if getattr(spec, "is_convention", True):
                continue
            func = getattr(spec, "func", None)
            if func is None:  # pragma: no cover
                continue
            # _InvokeCallableWrapper wraps the actual handler
            inner = getattr(func, "_invoke_handler", func)
            name = getattr(inner, "__name__", None) or type(inner).__name__
            handler_names.append(name)
        if handler_names:
            result[state.id] = ", ".join(handler_names)
    return result


def discover_self_transitions(sm_class: type) -> list[tuple[str, str]]:
    """
    Return a list of ``(state_id, event_name)`` for every self-transition
    (``a.to(a)``, ``Transition.is_self == True``) in the machine.

    Self-transitions are external by default (they fire enter/exit callbacks)
    unless declared with ``internal=True``.  The
    ``enable_self_transition_entries`` class-level flag further controls
    whether entry/exit callbacks run on self-transitions in ``StateMachine``
    subclasses.
    """
    sm = sm_class()
    result: list[tuple[str, str]] = []
    seen: set[int] = set()
    for state in sm.states_map.values():
        for t in state.transitions.transitions:
            if id(t) in seen:  # pragma: no cover
                continue
            seen.add(id(t))
            if getattr(t, "is_self", False):
                ev_name = next((e.name for e in t.events), "?")
                result.append((t.source.id, ev_name))
    return result


def extract_transition_actions(sm_class: type) -> dict[tuple[str, str], str]:
    """
    Return a mapping ``{(src_state_id, event_name): action_names}`` for all
    named transitions that declare non-convention ``before``, ``on``, or
    ``after`` callbacks.

    Convention hooks (``before_transition``, ``on_{event}``, etc., which have
    ``is_convention=True``) are excluded.  Only user-supplied inline
    callables or explicitly named action strings are included.

    ``action_names`` is a comma-joined string of the collected names in order:
    ``before`` callbacks first, then ``on``, then ``after``.
    """
    sm = sm_class()
    result: dict[tuple[str, str], str] = {}
    seen: set[int] = set()
    for state in sm.states_map.values():
        for t in state.transitions.transitions:
            if id(t) in seen:  # pragma: no cover
                continue
            seen.add(id(t))
            ev_name = next((e.name for e in t.events), "?")
            if ev_name == "?":
                continue
            # All groups (before, on, after, cond) share the same spec pool.
            # Iterate t.on once and distinguish actions from guards:
            #   - actions: is_convention=False, expected_value is None
            #   - guards:  is_convention=False, expected_value is True/False
            names: list[str] = []
            group = getattr(t, "on", None)
            spec_list = getattr(group, "list", None) if group else None
            for sp in spec_list or []:
                if getattr(sp, "is_convention", True):
                    continue
                if getattr(sp, "expected_value", None) is not None:
                    continue  # guard spec (cond= or unless=), not an action
                attr = getattr(sp, "attr_name", None)
                if attr:
                    names.append(attr)
            if names:
                result[(t.source.id, ev_name)] = ", ".join(names)
    return result


# ---------------------------------------------------------------------------
# Guard and compound-traversal derivation
# ---------------------------------------------------------------------------


def derive_guard_setup_map(sm_class: type) -> dict[str, dict[str, Any]]:
    """
    Auto-derive ``{event: {flag_attr: bool}}`` by inspecting guard source code.

    For every guarded transition (one with ``cond=`` or ``unless=`` specs):

    1. Read ``spec.attr_name`` — the guard method name on the SM class.
    2. Call :func:`inspect.getsource` on that method.
    3. Collect every ``self._xxx`` reference; each flag must equal
       ``spec.expected_value`` (``True`` for ``cond=``, ``False`` for
       ``unless=``) before the event can fire.

    The returned dict is suitable for passing directly to
    :func:`~smcheck.testgen.generate_all` as ``guard_setup_map``.
    """
    sm = sm_class()
    result: dict[str, dict[str, Any]] = {}
    seen: set[int] = set()

    for state in sm.states_map.values():
        for t in state.transitions.transitions:
            if id(t) in seen:  # pragma: no cover
                continue
            seen.add(id(t))
            ev = next((e.name for e in t.events), "?")
            if ev == "?":
                continue
            specs = list(t.cond) if t.cond else []
            flags: dict[str, Any] = {}
            for s in specs:
                guard_name = getattr(s, "attr_name", None) or getattr(s, "func", None)
                expected = getattr(s, "expected_value", True)
                if not guard_name:  # pragma: no cover
                    continue
                method = getattr(sm_class, guard_name, None)
                if not (method and callable(method)):  # pragma: no cover
                    continue
                try:
                    src = inspect.getsource(method)
                except (OSError, TypeError):  # pragma: no cover
                    continue
                # Detect flags used under negation (e.g. `not self._dispatched`
                # should yield {_dispatched: False} when the guard is a cond=).
                negated = set(re.findall(r"\bnot\s+self\._(\w+)", src))
                for flag in re.findall(r"self\._(\w+)", src):
                    flags["_" + flag] = (not expected) if flag in negated else expected
            if flags:
                result[ev] = flags

    return result


def derive_compound_traversal(sm_class: type) -> dict[str, dict[str, list[str]]]:
    """
    Auto-derive compound-traversal event sequences for non-parallel compound
    states that have guarded exits.

    Algorithm (per compound state *C* at top level, not parallel):

    1. Find its guarded exit transition and the guard method's source.
    2. Extract the flag(s) the guard reads (``self._xxx`` pattern).
    3. Scan all ``on_enter_*`` hooks of *C*'s direct children to find which
       one *sets* that flag to ``True`` — that child is the "success" terminal.
    4. BFS inside *C* (via :func:`track_graph`) from its initial child to the
       success terminal; record the resulting event sequence.

    Returns a dict suitable for :func:`~smcheck.testgen.generate_all` as
    ``compound_traversal``.
    """
    sm = sm_class()
    seen: set[int] = set()
    guarded: dict[str, list[tuple[str, bool]]] = {}

    # Collect (guard_method_name, expected_value) keyed by event name
    for state in sm.states_map.values():
        for t in state.transitions.transitions:
            if id(t) in seen:  # pragma: no cover
                continue
            seen.add(id(t))
            ev = next((e.name for e in t.events), "?")
            if ev == "?":
                continue
            for s in list(t.cond) if t.cond else []:
                attr = getattr(s, "attr_name", None) or getattr(s, "func", None)
                exp = getattr(s, "expected_value", True)
                if attr:
                    guarded.setdefault(ev, []).append((attr, exp))

    result: dict[str, dict[str, list[str]]] = {}
    full_adj = extract_sm_graph(sm_class)

    for sid, s in sm.states_map.items():
        # Only non-parallel compound states at depth 0 (top level)
        if s.parent is not None:
            continue
        children = [
            cs for cs in sm.states_map.values() if cs.parent is not None and cs.parent.id == sid
        ]
        if not children or getattr(s, "parallel", False):
            continue

        initial_sub = next((cs.id for cs in children if cs.initial), None)
        if not initial_sub:  # pragma: no cover
            continue

        for exit_ev, exit_target in full_adj.get(sid, []):
            if exit_ev not in guarded:
                continue

            # Collect the flag names the guard reads
            guard_flags: list[str] = []
            for guard_name, _ in guarded[exit_ev]:
                method = getattr(sm_class, guard_name, None)
                if not (method and callable(method)):  # pragma: no cover
                    continue
                try:
                    src = inspect.getsource(method)
                except (OSError, TypeError):  # pragma: no cover
                    continue
                guard_flags.extend(re.findall(r"self\._(\w+)", src))

            if not guard_flags:
                continue

            # Find the child whose on_enter_* hook sets any flag to True
            success_sub: str | None = None
            for flag_bare in guard_flags:
                for hook_name in dir(sm_class):
                    if not hook_name.startswith("on_enter_"):
                        continue
                    hook = getattr(sm_class, hook_name)
                    if not callable(hook):  # pragma: no cover
                        continue
                    try:
                        hook_src = inspect.getsource(hook)
                    except (OSError, TypeError):  # pragma: no cover
                        continue
                    if re.search(rf"self\._{flag_bare}\s*=\s*True", hook_src):
                        candidate = hook_name[len("on_enter_") :]
                        cand = sm.states_map.get(candidate)
                        if cand and cand.parent is not None and cand.parent.id == sid:
                            success_sub = candidate
                            break
                if success_sub:
                    break

            if not success_sub:
                continue

            # BFS inside the compound to the success sub-state
            c_adj = track_graph(sm_class, sid)
            bfs = bfs_shortest_paths(c_adj, initial_sub)
            path_events = bfs.get(success_sub, [])

            if path_events:
                result.setdefault(sid, {})[exit_target] = path_events

    return result
