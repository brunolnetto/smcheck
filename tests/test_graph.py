"""
tests/test_graph.py
===================
Unit tests for smcheck.graph — graph extraction, slicing, and path algorithms.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))  # for direct 'from conftest import X'

import pytest
from smcheck.graph import (
    extract_sm_graph,
    extract_transition_guards,
    top_level_graph,
    track_graph,
    discover_parallel_tracks,
    find_back_edges,
    count_paths_with_loops,
    enumerate_paths,
    bfs_shortest_paths,
    derive_guard_setup_map,
    derive_compound_traversal,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sm_class():
    _ex = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "examples", "order-processing")
    )
    if _ex not in sys.path:
        sys.path.insert(0, _ex)
    from machine import OrderProcessing

    return OrderProcessing


@pytest.fixture(scope="module")
def full_adj(sm_class):
    return extract_sm_graph(sm_class)


@pytest.fixture(scope="module")
def top_adj(sm_class):
    return top_level_graph(sm_class)


# ---------------------------------------------------------------------------
# extract_sm_graph
# ---------------------------------------------------------------------------


class TestExtractSmGraph:
    def test_returns_dict(self, full_adj):
        assert isinstance(full_adj, dict)

    def test_no_unnamed_transitions(self, full_adj):
        for outs in full_adj.values():
            for ev, _ in outs:
                assert ev != "?", "Unnamed transition '?' should be excluded"

    def test_known_transitions_present(self, full_adj):
        # Spot-check key transitions
        expected = [
            ("idle", "submit", "validation"),
            ("validation", "start", "fulfillment"),
            ("checking", "reserve", "reserved"),
            ("pay_hold", "process_payment", "processing"),
            ("processing", "authorize", "authorized"),
            ("ship_hold", "begin_shipping", "preparing"),
        ]
        flat = {(src, ev, dst) for src, outs in full_adj.items() for ev, dst in outs}
        for src, ev, dst in expected:
            assert (src, ev, dst) in flat, f"Missing: {src} --[{ev}]--> {dst}"

    def test_cancel_from_multiple_sources(self, full_adj):
        cancel_sources = {src for src, outs in full_adj.items() for ev, _ in outs if ev == "cancel"}
        assert "idle" in cancel_sources
        assert "validation" in cancel_sources
        assert "fulfillment" in cancel_sources

    def test_history_state_resolved_to_parent(self, full_adj):
        # HistoryState targets (h) must be replaced by their parent compound
        # state id so no dangling pseudo-state nodes appear in the graph.
        all_dst = {dst for outs in full_adj.values() for _, dst in outs}
        assert "h" not in all_dst, "HistoryState pseudo-node 'h' must not appear as a destination"
        # resume and release must now point to the shipping compound state
        resume_dsts = {dst for src, outs in full_adj.items() for ev, dst in outs if ev == "resume"}
        assert resume_dsts == {"shipping"}, f"resume should target 'shipping', got {resume_dsts}"
        release_dsts = {dst for src, outs in full_adj.items() for ev, dst in outs if ev == "release"}
        assert release_dsts == {"shipping"}, f"release should target 'shipping', got {release_dsts}"


# ---------------------------------------------------------------------------
# extract_transition_guards
# ---------------------------------------------------------------------------


class TestExtractTransitionGuards:
    def test_returns_dict(self, sm_class):
        guards = extract_transition_guards(sm_class)
        assert isinstance(guards, dict)

    def test_guarded_transition_captured(self, sm_class):
        """Transitions with user-defined guards must appear in the map."""
        guards = extract_transition_guards(sm_class)
        # process_payment has cond="inventory_is_reserved"
        assert ("pay_hold", "process_payment", "processing") in guards
        assert "inventory_is_reserved" in guards[("pay_hold", "process_payment", "processing")]

    def test_unguarded_transition_absent(self, sm_class):
        """Unguarded transitions must NOT appear in the guard map."""
        guards = extract_transition_guards(sm_class)
        assert ("checking", "reserve", "reserved") not in guards

    def test_history_state_targets_resolved(self, sm_class):
        """Guarded transitions to HistoryState must resolve to the parent compound state."""
        guards = extract_transition_guards(sm_class)
        # ops_hold --[release (cond: ops_only)]--> shipping.h → shipping
        assert ("ops_hold", "release", "shipping") in guards
        assert "ops_only" in guards[("ops_hold", "release", "shipping")]


# ---------------------------------------------------------------------------
# top_level_graph
# ---------------------------------------------------------------------------


class TestTopLevelGraph:
    def test_excludes_track_internal_states(self, top_adj):
        # Track-internal states should not appear as sources
        track_internal = {
            "checking",
            "reserved",
            "pay_hold",
            "processing",
            "ship_hold",
            "preparing",
            "ready",
            "in_transit",
        }
        for state in track_internal:
            assert state not in top_adj, (
                f"Track-internal state '{state}' should not be a source in top_level_graph"
            )

    def test_includes_top_level_states(self, top_adj):
        all_nodes = set(top_adj.keys()) | {dst for outs in top_adj.values() for _, dst in outs}
        assert "idle" in all_nodes
        assert "validation" in all_nodes
        assert "fulfillment" in all_nodes
        assert "cancelled" in all_nodes
        assert "failed" in all_nodes
        assert "success" in all_nodes
        assert "on_hold" in all_nodes
        assert "ops_hold" in all_nodes

    def test_submit_leads_to_validation(self, top_adj):
        targets = {dst for ev, dst in top_adj.get("idle", []) if ev == "submit"}
        assert "validation" in targets


# ---------------------------------------------------------------------------
# track_graph
# ---------------------------------------------------------------------------


class TestTrackGraph:
    def test_inventory_track(self, sm_class):
        adj = track_graph(sm_class, "inventory")
        # Must contain all inventory states as sources or destinations
        all_nodes = set(adj.keys()) | {dst for outs in adj.values() for _, dst in outs}
        assert "checking" in all_nodes
        assert "reserved" in all_nodes
        assert "allocated" in all_nodes
        assert "out_of_stock" in all_nodes

    def test_payment_track(self, sm_class):
        adj = track_graph(sm_class, "payment")
        all_nodes = set(adj.keys()) | {dst for outs in adj.values() for _, dst in outs}
        assert "pay_hold" in all_nodes
        assert "processing" in all_nodes
        assert "authorized" in all_nodes
        assert "declined" in all_nodes

    def test_shipping_track(self, sm_class):
        adj = track_graph(sm_class, "shipping")
        all_nodes = set(adj.keys()) | {dst for outs in adj.values() for _, dst in outs}
        assert "ship_hold" in all_nodes
        assert "preparing" in all_nodes
        assert "acknowledged" in all_nodes

    def test_unknown_track_returns_empty(self, sm_class):
        assert track_graph(sm_class, "nonexistent") == {}


# ---------------------------------------------------------------------------
# discover_parallel_tracks
# ---------------------------------------------------------------------------


class TestDiscoverParallelTracks:
    def test_finds_three_tracks(self, sm_class):
        tracks = discover_parallel_tracks(sm_class)
        assert len(tracks) == 3

    def test_track_names(self, sm_class):
        tracks = set(discover_parallel_tracks(sm_class))
        assert tracks == {"inventory", "payment", "shipping"}


# ---------------------------------------------------------------------------
# top_level_terminals
# ---------------------------------------------------------------------------


class TestTopLevelTerminals:
    def test_linear_sm_terminal(self):
        from conftest import LinearSM
        from smcheck.graph import top_level_terminals

        finals = top_level_terminals(LinearSM)
        assert "c" in finals

    def test_branch_sm_two_terminals(self):
        from conftest import BranchSM
        from smcheck.graph import top_level_terminals

        finals = top_level_terminals(BranchSM)
        assert finals == {"c", "d"}

    def test_parallel_sm_terminal_is_top_level(self):
        from conftest import MiniParallelSM
        from smcheck.graph import top_level_terminals

        finals = top_level_terminals(MiniParallelSM)
        assert "done" in finals

    def test_order_processing_terminals(self, sm_class):
        from smcheck.graph import top_level_terminals

        finals = top_level_terminals(sm_class)
        assert "cancelled" in finals
        assert "failed" in finals
        assert "success" in finals


# ---------------------------------------------------------------------------
# find_back_edges
# ---------------------------------------------------------------------------


class TestFindBackEdges:
    def test_pause_no_longer_loops_back_to_idle(self, top_adj):
        # pause now goes to on_hold (not idle), so there are no back-edges
        backs = find_back_edges(top_adj, "idle")
        back_tuples = {(ev, src, dst) for ev, src, dst in backs}
        assert ("pause", "fulfillment", "idle") not in back_tuples

    def test_no_back_edges_top_level(self, top_adj):
        # pause → on_hold breaks the fulfillment → idle cycle
        backs = find_back_edges(top_adj, "idle")
        assert len(backs) == 0

    def test_no_back_edges_in_tracks(self, sm_class):
        for track in ("inventory", "payment", "shipping"):
            adj = track_graph(sm_class, track)
            sm = sm_class()
            initial = next(
                s.id
                for s in sm.states_map.values()
                if s.parent is not None and s.parent.id == track and s.initial
            )
            backs = find_back_edges(adj, initial)
            assert backs == [], f"Unexpected back-edge in track '{track}': {backs}"


# ---------------------------------------------------------------------------
# count_paths_with_loops / enumerate_paths
# ---------------------------------------------------------------------------


class TestPathCounting:
    def _setup(self, top_adj):
        """Return (terminals, back_edges) for the top-level graph."""
        all_nodes = set(top_adj.keys()) | {dst for outs in top_adj.values() for _, dst in outs}
        sinks = {n for n in all_nodes if not top_adj.get(n)}
        backs = find_back_edges(top_adj, "idle")
        return sinks, backs

    def test_total_top_level_paths(self, top_adj):
        terminals, backs = self._setup(top_adj)
        counts = count_paths_with_loops(top_adj, "idle", terminals, backs)
        # 9 simple paths (0 loops): cancel early, validation fails, fulfillment
        # exits to: cancelled, failed, success, on_hold→{shipping,cancelled}, ops_hold→{shipping,cancelled}
        assert counts["total"] == 9

    def test_simple_paths_count(self, top_adj):
        terminals, backs = self._setup(top_adj)
        counts = count_paths_with_loops(top_adj, "idle", terminals, backs)
        assert counts["simple"] == 9

    def test_loop_paths_count(self, top_adj):
        terminals, backs = self._setup(top_adj)
        counts = count_paths_with_loops(top_adj, "idle", terminals, backs)
        # No loops: pause now goes to on_hold, not back to idle
        assert counts["with_loops"] == 0

    def test_enumerate_returns_correct_count(self, top_adj):
        terminals, backs = self._setup(top_adj)
        paths = enumerate_paths(top_adj, "idle", terminals, backs)
        assert len(paths) == 9

    def test_all_paths_start_at_idle(self, top_adj):
        terminals, backs = self._setup(top_adj)
        paths = enumerate_paths(top_adj, "idle", terminals, backs)
        for p in paths:
            assert p[0] == "idle", f"Path does not start at idle: {p}"

    def test_all_paths_end_at_terminal(self, top_adj):
        terminals, backs = self._setup(top_adj)
        paths = enumerate_paths(top_adj, "idle", terminals, backs)
        for p in paths:
            assert p[-1] in terminals, f"Path does not end at terminal: {p}"

    def test_inventory_track_has_3_paths(self, sm_class):
        adj = track_graph(sm_class, "inventory")
        all_nodes = set(adj.keys()) | {dst for outs in adj.values() for _, dst in outs}
        sinks = {n for n in all_nodes if not adj.get(n)}
        backs = find_back_edges(adj, "checking")
        counts = count_paths_with_loops(adj, "checking", sinks, backs)
        # New backorder paths: checking → backordered → reserved → {allocated, out_of_stock}
        # + via stock_review: → stock_review → reserved → {allocated, out_of_stock}
        # + customer declines: → stock_review → out_of_stock
        # + partial fulfillment: checking → partial_stock → allocated
        # Total: 2 (base) + 2 (backorder→reserved) + 2 (via stock_review→reserved) + 1 (decline) + 1 (partial) = 8
        assert counts["total"] == 8

    def test_payment_track_has_2_paths(self, sm_class):
        adj = track_graph(sm_class, "payment")
        all_nodes = set(adj.keys()) | {dst for outs in adj.values() for _, dst in outs}
        sinks = {n for n in all_nodes if not adj.get(n)}
        backs = find_back_edges(adj, "pay_hold")
        counts = count_paths_with_loops(adj, "pay_hold", sinks, backs)
        assert counts["total"] == 2

    def test_shipping_track_has_1_path(self, sm_class):
        adj = track_graph(sm_class, "shipping")
        all_nodes = set(adj.keys()) | {dst for outs in adj.values() for _, dst in outs}
        sinks = {n for n in all_nodes if not adj.get(n)}
        backs = find_back_edges(adj, "ship_hold")
        counts = count_paths_with_loops(adj, "ship_hold", sinks, backs)
        assert counts["total"] == 1


# ---------------------------------------------------------------------------
# derive_guard_setup_map
# ---------------------------------------------------------------------------


class TestDeriveGuardSetupMap:
    def test_returns_dict(self, sm_class):
        result = derive_guard_setup_map(sm_class)
        assert isinstance(result, dict)

    def test_cross_track_guards_present(self, sm_class):
        """process_payment and begin_shipping are guarded by cross-track flags."""
        result = derive_guard_setup_map(sm_class)
        assert "process_payment" in result
        assert "begin_shipping" in result

    def test_process_payment_flags(self, sm_class):
        result = derive_guard_setup_map(sm_class)
        assert result["process_payment"] == {"_inventory_reserved": True}

    def test_begin_shipping_flags(self, sm_class):
        result = derive_guard_setup_map(sm_class)
        assert result["begin_shipping"] == {
            "_inventory_allocated": True,
            "_payment_authorized": True,
        }

    def test_all_flag_values_are_bool(self, sm_class):
        result = derive_guard_setup_map(sm_class)
        for ev, flags in result.items():
            for attr, val in flags.items():
                assert isinstance(val, bool), f"{ev}:{attr} should be bool, got {val!r}"

    def test_no_spurious_accept_partial_entry(self, sm_class):
        """accept_partial has no guard — must NOT appear in the derived map."""
        result = derive_guard_setup_map(sm_class)
        assert "accept_partial" not in result

    def test_is_deterministic(self, sm_class):
        """Calling derive_guard_setup_map twice returns the same result."""
        assert derive_guard_setup_map(sm_class) == derive_guard_setup_map(sm_class)


# ---------------------------------------------------------------------------
# derive_compound_traversal
# ---------------------------------------------------------------------------


class TestDeriveCompoundTraversal:
    def test_returns_dict(self, sm_class):
        result = derive_compound_traversal(sm_class)
        assert isinstance(result, dict)

    def test_validation_compound_present(self, sm_class):
        result = derive_compound_traversal(sm_class)
        assert "validation" in result

    def test_validation_exits_to_fulfillment(self, sm_class):
        result = derive_compound_traversal(sm_class)
        assert "fulfillment" in result["validation"]

    def test_validation_traversal_events(self, sm_class):
        result = derive_compound_traversal(sm_class)
        assert result["validation"]["fulfillment"] == ["approve"]

    def test_parallel_tracks_not_included(self, sm_class):
        """Parallel compound states (inventory/payment/shipping) must not appear."""
        result = derive_compound_traversal(sm_class)
        for parallel_track in ("inventory", "payment", "shipping", "fulfillment"):
            assert parallel_track not in result, (
                f"Parallel track '{parallel_track}' should not be in compound_traversal"
            )

    def test_is_deterministic(self, sm_class):
        """Calling derive_compound_traversal twice returns the same result."""
        assert derive_compound_traversal(sm_class) == derive_compound_traversal(sm_class)


# ---------------------------------------------------------------------------
# bfs_shortest_paths
# ---------------------------------------------------------------------------


class TestBfsShortestPaths:
    def test_initial_has_empty_path(self, top_adj):
        paths = bfs_shortest_paths(top_adj, "idle")
        assert paths["idle"] == []

    def test_validation_reachable(self, top_adj):
        paths = bfs_shortest_paths(top_adj, "idle")
        assert "validation" in paths
        assert paths["validation"] == ["submit"]

    def test_fulfillment_reachable(self, top_adj):
        paths = bfs_shortest_paths(top_adj, "idle")
        assert "fulfillment" in paths
        # idle → validation → fulfillment takes 2 events (internal approve omitted in top graph)
        assert len(paths["fulfillment"]) >= 2
