"""smcheck.report
==============
Human-readable console reports for graph analysis and static validation.

Both :func:`run_graph_analysis` and :func:`run_validation` accept a
``StateChart`` subclass as their first argument so they work with any machine,
not just ``OrderProcessing``.
"""
from __future__ import annotations

from functools import reduce
import operator

from .graph import (
    discover_invoke_states,
    discover_parallel_tracks,
    enumerate_paths,
    extract_sm_graph,
    find_back_edges,
    count_paths_with_loops,
    top_level_graph,
    track_graph,
)
from .validator import SMValidator


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _banner(title: str) -> None:
    width = 64
    print(f"\n{'═' * width}")
    print(f"  {title}")
    print(f"{'═' * width}")


# ---------------------------------------------------------------------------
# Graph analysis report
# ---------------------------------------------------------------------------

def run_graph_analysis(sm_class: type) -> None:
    """
    Extract the transition graph for *sm_class*, detect back-edges, and print
    path counts at two levels:

    1. Top-level control flow (depth ≤ 1 states).
    2. Per-track flows for each parallel sub-track independently.

    The loop model: each back-edge may be traversed at most once per path,
    but a single path may include multiple distinct back-edges.
    """
    _banner("Graph Analysis — path enumeration")

    full_adj = extract_sm_graph(sm_class)
    sm       = sm_class()

    # ── Class-level behavioral flags ──────────────────────────────────────────
    flag_attrs = (
        "allow_event_without_transition",
        "enable_self_transition_entries",
        "catch_errors_as_events",
        "atomic_configuration_update",
    )
    print("\n  Class-level behavior flags:")
    for attr in flag_attrs:
        val = getattr(sm_class, attr, "N/A")
        print(f"    {attr}: {val}")

    # ── Invoke-bearing states ─────────────────────────────────────────────────
    invoked = discover_invoke_states(sm_class)
    if invoked:
        print(f"\n  Invoke-bearing states ({len(invoked)}):")
        for sid, handler in sorted(invoked.items()):
            print(f"    {sid}: {handler}")
    else:
        print("\n  No states with invoke= handlers.")

    # ── Full flat graph ───────────────────────────────────────────────────────
    print("\n  Full transition graph (named edges only):")
    for src in sorted(full_adj):
        for ev, dst in full_adj[src]:
            print(f"    {src:20s} --[{ev:20s}]--> {dst}")

    # ── Top-level graph ───────────────────────────────────────────────────────
    top_adj = top_level_graph(sm_class)
    top_terminals: set[str] = {
        s.id for s in sm.states_map.values() if s.final and s.parent is None
    }
    all_top_nodes = (
        set(top_adj.keys()) | {dst for outs in top_adj.values() for _, dst in outs}
    )
    top_sinks     = {n for n in all_top_nodes if not top_adj.get(n)}
    top_effective = top_terminals | top_sinks

    initial = next(s.id for s in sm.states_map.values() if s.initial and s.parent is None)

    print(f"\n  Top-level initial   : {initial}")
    print(f"  Top-level terminals : {sorted(top_effective)}")

    top_back = find_back_edges(top_adj, initial)
    print(f"\n  Top-level back-edges (loops): {len(top_back)}")
    for ev, src, dst in top_back:
        print(f"    {src:20s} --[{ev}]--> {dst}  <- loop")

    top_counts = count_paths_with_loops(top_adj, initial, top_effective, top_back)
    top_paths  = enumerate_paths(top_adj, initial, top_effective, top_back)
    print("\n  Top-level path counts:")
    print(f"    Simple paths (no loop)   : {top_counts['simple']}")
    print(f"    Paths with >=1 loop      : {top_counts['with_loops']}")
    print(f"    Total                    : {top_counts['total']}")

    # ── Per-track graphs ──────────────────────────────────────────────────────
    track_totals: list[int] = []
    track_names = discover_parallel_tracks(sm_class)
    for track in track_names:
        t_adj = track_graph(sm_class, track)
        if not t_adj:
            track_totals.append(1)
            continue
        t_all = (
            set(t_adj.keys()) | {dst for outs in t_adj.values() for _, dst in outs}
        )
        t_sinks = {n for n in t_all if not t_adj.get(n)}
        t_initial = next(
            (s.id for s in sm.states_map.values()
             if s.parent is not None and s.parent.id == track and s.initial),
            None,
        )
        if t_initial is None:  # pragma: no cover
            track_totals.append(1)
            continue
        t_back   = find_back_edges(t_adj, t_initial)
        t_counts = count_paths_with_loops(t_adj, t_initial, t_sinks, t_back)
        total    = t_counts["total"]
        track_totals.append(total)
        loop_hint = f"{len(t_back)} loop(s)" if t_back else "no loops"
        print(f"\n  Track [{track}]  initial={t_initial}  {loop_hint}")
        for src in sorted(t_adj):
            for ev, dst in t_adj[src]:
                print(f"    {src:20s} --[{ev}]--> {dst}")
        print(f"    Paths: simple={t_counts['simple']}  "
              f"with_loops={t_counts['with_loops']}  total={total}")

    # ── Combined totals ───────────────────────────────────────────────────────
    track_product = reduce(operator.mul, track_totals, 1)

    # Detect the name of the parallel compound state
    parallel_id = next(
        (s.id for s in sm.states_map.values() if s.parallel and s.parent is None),
        None,
    )
    fulfillment_entries = (
        [p for p in top_paths if parallel_id in p] if parallel_id else []
    )
    n_top_no_fulfill = top_counts["total"] - len(fulfillment_entries)
    total_paths = n_top_no_fulfill + len(fulfillment_entries) * track_product

    print("\n  ============================================================")
    print("  Combined path analysis")
    print("  ============================================================")
    print(f"  Top-level paths total                : {top_counts['total']}")
    print(f"    of which bypass {parallel_id or 'parallel'}        : {n_top_no_fulfill}")
    print(f"    of which enter  {parallel_id or 'parallel'}        : {len(fulfillment_entries)}")
    for track, tc in zip(track_names, track_totals):
        print(f"      * {track:<12} track paths : {tc}")
    print(f"  Track combinations per {parallel_id or 'parallel'}   : {track_product}")
    print("  ------------------------------------------------------------")
    print(f"  TOTAL unique execution paths         : {total_paths}")
    print(f"  Paths with >=1 loop (approx)         : {top_counts['with_loops']}"
          f"  (top-level loops only; track loops add more if present)")
    print("  ============================================================")


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------

def run_validation(sm_class: type) -> None:
    """
    Run the full static-validation suite on *sm_class* and print a
    human-readable, categorised report with ✅ / ⚠️  / ❌ icons.
    """
    _banner("Static Validation — python-statemachine gap analysis")

    v        = SMValidator(sm_class)
    findings = v.run_all()

    icons = {"PASS": "\u2705", "WARN": "\u26a0\ufe0f ", "ERROR": "\u274c"}
    for f in findings:
        icon = icons.get(f.level, "?")
        print(f"\n  {icon} [{f.level:5s}] [{f.category}]")
        print(f"          {f.detail}")
        if f.nodes:
            print(f"          Nodes: {f.nodes}")

    errors   = sum(1 for f in findings if f.level == "ERROR")
    warnings = sum(1 for f in findings if f.level == "WARN")
    passes   = sum(1 for f in findings if f.level == "PASS")
    total    = errors + warnings + passes
    verdict  = (
        "ALL CLEAR"       if errors == 0 and warnings == 0 else
        "REVIEW REQUIRED" if errors == 0 else
        "ERRORS DETECTED"
    )
    print(f"\n  {'─' * 60}")
    print(f"  {total} check(s): {passes} PASS  {warnings} WARN  {errors} ERROR"
          f"   →  {verdict}")
