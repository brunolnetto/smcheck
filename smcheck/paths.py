"""smcheck.paths
=============
Rich path-object layer built on top of the raw graph algorithms in
:mod:`smcheck.graph`.

The main entry point is :func:`analyze_paths`, which returns a
:class:`PathAnalysis` containing typed :class:`SMPath` objects (each edge
annotated with its event name, guard name, and whether it is a back-edge).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
import operator

from .graph import (
    AdjMap,
    discover_parallel_tracks,
    enumerate_paths,
    extract_sm_graph,
    find_back_edges,
    top_level_graph,
    track_graph,
    extract_transition_actions,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PathEdge:
    """A single directed edge in an :class:`SMPath`."""
    source:       str
    event:        str
    target:       str
    guard:        str | None = None   # guard/condition name (includes !-prefix for unless=)
    is_back_edge: bool       = False
    is_internal:  bool       = False  # internal transition — no exit/enter on common ancestor
    is_self:      bool       = False  # self-transition — source == target
    actions:      str | None = None   # non-convention before/on/after action names


@dataclass
class SMPath:
    """
    A complete path from an initial state to a terminal, expressed as an
    ordered list of :class:`PathEdge` objects.

    Attributes
    ----------
    edges        : Ordered list of edges forming the path.
    is_looping   : ``True`` when the path traverses at least one back-edge.
    terminal     : ID of the terminal (final or sink) state.
    level        : ``"top"`` for the top-level control-flow graph, or the
                   track name (``"inventory"``, ``"payment"``, ...) for
                   a parallel sub-track path.
    """
    edges:       list[PathEdge]
    is_looping:  bool
    terminal:    str
    level:       str

    @property
    def nodes(self) -> list[str]:
        """All node IDs visited, in order (source of first edge → terminal)."""
        if not self.edges:
            return []
        return [self.edges[0].source] + [e.target for e in self.edges]

    @property
    def events(self) -> list[str]:
        """Ordered event names along the path."""
        return [e.event for e in self.edges]

    def __len__(self) -> int:
        return len(self.edges)


@dataclass
class PathAnalysis:
    """
    Complete path analysis for a ``StateChart`` subclass.

    Attributes
    ----------
    top_level_paths  : All paths through the top-level (depth ≤ 1) graph.
    track_paths      : Per-track path lists, keyed by track state ID.
    combined_count   : Total unique execution paths (cross-product of track
                       paths multiplied by the number of top-level paths that
                       enter the parallel region, plus bypass paths).
    bypass_count     : Number of top-level paths that skip the parallel region.
    fulfillment_count: Number of top-level paths that enter the parallel region.
    parallel_state_id: ID of the top-level ``State.Parallel`` (``None`` if
                       the machine has no parallel region).
    """
    top_level_paths:   list[SMPath]
    track_paths:       dict[str, list[SMPath]]
    combined_count:    int
    bypass_count:      int
    fulfillment_count: int
    parallel_state_id: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _edge_from_nodes(
    src: str,
    dst: str,
    adj: AdjMap,
    back_set: set[tuple[str, str]],
    guard_map: dict[tuple[str, str], str],
    meta_map: dict[tuple[str, str, str], tuple[bool, bool]] | None = None,
    actions_map: dict[tuple[str, str], str] | None = None,
) -> PathEdge:
    ev = next((e for e, d in adj.get(src, []) if d == dst), "?")
    is_internal, is_self = False, False
    if meta_map:
        is_internal, is_self = meta_map.get((src, ev, dst), (False, False))
    actions = (actions_map or {}).get((src, ev))
    return PathEdge(
        source=src,
        event=ev,
        target=dst,
        guard=guard_map.get((src, ev)),
        is_back_edge=(src, dst) in back_set,
        is_internal=is_internal,
        is_self=is_self,
        actions=actions,
    )


def _build_guard_map(sm_class: type) -> dict[tuple[str, str], str]:
    """
    Extract guard/condition names keyed by ``(src_state_id, event_name)``.

    python-statemachine v3 stores guards in ``Transition.cond``, which is a
    ``SpecListGrouper``.  Its ``.list`` property yields a ``CallbackSpecList``
    that contains **all** callbacks registered on the transition — both
    convention hooks (``is_convention=True``) **and** user-supplied guard
    functions (``is_convention=False``).  We keep only non-convention entries:

    * ``cond="guard_name"``   → ``"guard_name"``
    * ``unless="guard_name"`` → ``"!guard_name"`` (``expected_value=False``)
    """
    sm = sm_class()
    gmap: dict[tuple[str, str], str] = {}
    seen: set[int] = set()
    for state in sm.states_map.values():
        for t in state.transitions.transitions:
            if id(t) in seen:  # pragma: no cover
                continue
            seen.add(id(t))
            ev_name = next((e.name for e in t.events), "?")
            if ev_name == "?":
                continue
            # t.cond is a SpecListGrouper; .list is the CallbackSpecList
            spec_list = getattr(getattr(t, "cond", None), "list", None)
            if spec_list is None:  # pragma: no cover
                continue
            names: list[str] = []
            for sp in spec_list:
                if getattr(sp, "is_convention", True):
                    continue
                attr = sp.attr_name
                # unless= specs carry expected_value=False → prefix with !
                if not getattr(sp, "expected_value", True):
                    attr = f"!{attr}"
                names.append(attr)
            if names:
                gmap[(t.source.id, ev_name)] = ", ".join(names)
    return gmap


def _build_transition_meta_map(
    sm_class: type,
) -> dict[tuple[str, str, str], tuple[bool, bool]]:
    """
    Return ``{(src_id, ev_name, dst_id): (is_internal, is_self)}`` for every
    named explicit transition.

    The action names for an edge are obtained separately via
    :func:`~smcheck.graph.extract_transition_actions` which is keyed by
    ``(src_id, ev_name)`` — that key is still unique for multi-target cases
    because all parallel targets share the same callbacks.
    """
    sm = sm_class()
    meta: dict[tuple[str, str, str], tuple[bool, bool]] = {}
    seen: set[int] = set()
    for state in sm.states_map.values():
        for t in state.transitions.transitions:
            if id(t) in seen:  # pragma: no cover
                continue
            seen.add(id(t))
            ev_name = next((e.name for e in t.events), "?")
            if ev_name == "?":
                continue
            src_id      = t.source.id
            is_internal = bool(getattr(t, "internal", False))
            is_self_t   = bool(getattr(t, "is_self",   False))
            # Multi-target: record for each target separately
            targets = getattr(t, "targets", None) or ([t.target] if t.target else [])
            for tgt in targets:
                meta[(src_id, ev_name, tgt.id)] = (is_internal, is_self_t)
    return meta


def _nodes_to_smpath(
    nodes:       list[str],
    adj:         AdjMap,
    back_set:    set[tuple[str, str]],
    guard_map:   dict[tuple[str, str], str],
    level:       str,
    meta_map:    dict[tuple[str, str, str], tuple[bool, bool]] | None = None,
    actions_map: dict[tuple[str, str], str] | None = None,
) -> SMPath:
    edges = [
        _edge_from_nodes(
            nodes[i], nodes[i + 1], adj, back_set, guard_map, meta_map, actions_map
        )
        for i in range(len(nodes) - 1)
    ]
    return SMPath(
        edges=edges,
        is_looping=any(e.is_back_edge for e in edges),
        terminal=nodes[-1],
        level=level,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_paths(sm_class: type) -> PathAnalysis:
    """
    Run full path analysis on *sm_class* and return a :class:`PathAnalysis`.

    This function:

    1. Extracts the full transition graph and the top-level subgraph.
    2. Enumerates all top-level paths with loop semantics.
    3. Extracts per-track subgraphs and enumerates their paths.
    4. Computes the combined cross-product count.
    5. Enriches every :class:`PathEdge` with guard names from
       ``Transition.conditions``.
    """
    sm          = sm_class()
    extract_sm_graph(sm_class)
    guard_map   = _build_guard_map(sm_class)
    meta_map    = _build_transition_meta_map(sm_class)
    actions_map = extract_transition_actions(sm_class)

    # ── Top-level paths ───────────────────────────────────────────────────────
    top_adj = top_level_graph(sm_class)
    initial = next(
        s.id for s in sm.states_map.values() if s.initial and s.parent is None
    )
    top_terminals = {
        s.id for s in sm.states_map.values() if s.final and s.parent is None
    }
    all_top_nodes = (
        set(top_adj.keys()) | {dst for outs in top_adj.values() for _, dst in outs}
    )
    top_sinks     = {n for n in all_top_nodes if not top_adj.get(n)}
    top_effective = top_terminals | top_sinks

    top_back    = find_back_edges(top_adj, initial)
    top_back_set = {(src, dst) for _, src, dst in top_back}
    raw_top     = enumerate_paths(top_adj, initial, top_effective, top_back)
    top_paths   = [
        _nodes_to_smpath(p, top_adj, top_back_set, guard_map, "top", meta_map, actions_map)
        for p in raw_top
    ]

    # ── Per-track paths ───────────────────────────────────────────────────────
    track_names = discover_parallel_tracks(sm_class)
    track_paths: dict[str, list[SMPath]] = {}
    track_totals: dict[str, int] = {}

    for track in track_names:
        t_adj = track_graph(sm_class, track)
        if not t_adj:
            track_paths[track]  = []
            track_totals[track] = 1
            continue
        t_all   = set(t_adj.keys()) | {dst for outs in t_adj.values() for _, dst in outs}
        t_sinks = {n for n in t_all if not t_adj.get(n)}
        t_initial = next(
            (s.id for s in sm.states_map.values()
             if s.parent is not None and s.parent.id == track and s.initial),
            None,
        )
        if t_initial is None:  # pragma: no cover
            track_paths[track]  = []
            track_totals[track] = 1
            continue
        t_back    = find_back_edges(t_adj, t_initial)
        t_back_set = {(src, dst) for _, src, dst in t_back}
        raw_t     = enumerate_paths(t_adj, t_initial, t_sinks, t_back)
        t_paths   = [
            _nodes_to_smpath(p, t_adj, t_back_set, guard_map, track, meta_map, actions_map)
            for p in raw_t
        ]
        track_paths[track]  = t_paths
        track_totals[track] = len(t_paths) if t_paths else 1

    # ── Combined count ────────────────────────────────────────────────────────
    parallel_id = next(
        (s.id for s in sm.states_map.values() if s.parallel and s.parent is None),
        None,
    )
    enters = (
        [p for p in top_paths if any(e.target == parallel_id for e in p.edges)]
        if parallel_id else []
    )
    bypass_count     = len(top_paths) - len(enters)
    fulfillment_count = len(enters)
    track_product    = reduce(operator.mul, track_totals.values(), 1)
    combined_count   = bypass_count + fulfillment_count * track_product

    return PathAnalysis(
        top_level_paths=top_paths,
        track_paths=track_paths,
        combined_count=combined_count,
        bypass_count=bypass_count,
        fulfillment_count=fulfillment_count,
        parallel_state_id=parallel_id,
    )


def path_to_event_sequence(path: SMPath) -> list[str]:
    """
    Return the ordered list of event names that must be fired to walk *path*.

    This is simply the ``event`` field of each :class:`PathEdge`, which is
    sufficient for paths within a single graph level.  For combined top-level
    + parallel-track tests, use :mod:`smcheck.testgen` which handles
    cross-level interleaving and guard setup.
    """
    return [e.event for e in path.edges]
