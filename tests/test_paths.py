"""
tests/test_paths.py
===================
Unit tests for smcheck.paths — PathEdge, SMPath, PathAnalysis, analyze_paths.
"""

from __future__ import annotations

from smcheck.paths import (
    PathEdge,
    SMPath,
    analyze_paths,
    path_to_event_sequence,
    _build_guard_map,
    _build_transition_meta_map,
)


# ---------------------------------------------------------------------------
# PathEdge
# ---------------------------------------------------------------------------


class TestPathEdge:
    def test_defaults(self):
        e = PathEdge(source="a", event="go", target="b")
        assert e.guard is None
        assert e.is_back_edge is False
        assert e.is_internal is False
        assert e.is_self is False
        assert e.actions is None

    def test_with_guard(self):
        e = PathEdge("a", "go", "b", guard="is_left", is_back_edge=True)
        assert e.guard == "is_left"
        assert e.is_back_edge is True

    def test_is_internal_field(self):
        e = PathEdge("a", "tick", "a", is_internal=True, is_self=True)
        assert e.is_internal is True
        assert e.is_self is True

    def test_actions_field(self):
        e = PathEdge("a", "go", "b", actions="log_transition")
        assert e.actions == "log_transition"


# ---------------------------------------------------------------------------
# SMPath
# ---------------------------------------------------------------------------


class TestSMPath:
    def _make(self, edges, **kwargs):
        return SMPath(
            edges=edges,
            is_looping=kwargs.get("is_looping", False),
            terminal=kwargs.get("terminal", "z"),
            level=kwargs.get("level", "top"),
        )

    def test_nodes(self):
        edges = [
            PathEdge("a", "e1", "b"),
            PathEdge("b", "e2", "c"),
        ]
        path = self._make(edges, terminal="c")
        assert path.nodes == ["a", "b", "c"]

    def test_nodes_empty(self):
        path = self._make([])
        assert path.nodes == []

    def test_events(self):
        edges = [PathEdge("a", "go", "b"), PathEdge("b", "done", "c")]
        path = self._make(edges)
        assert path.events == ["go", "done"]

    def test_len(self):
        edges = [PathEdge("a", "go", "b")]
        path = self._make(edges)
        assert len(path) == 1

    def test_len_empty(self):
        assert len(self._make([])) == 0


# ---------------------------------------------------------------------------
# path_to_event_sequence
# ---------------------------------------------------------------------------


class TestPathToEventSequence:
    def test_extracts_events(self):
        path = SMPath(
            edges=[PathEdge("a", "go", "b"), PathEdge("b", "done", "c")],
            is_looping=False,
            terminal="c",
            level="top",
        )
        assert path_to_event_sequence(path) == ["go", "done"]

    def test_empty_path(self):
        path = SMPath(edges=[], is_looping=False, terminal="x", level="top")
        assert path_to_event_sequence(path) == []


# ---------------------------------------------------------------------------
# analyze_paths — linear machine
# ---------------------------------------------------------------------------


class TestAnalyzePathsLinear:
    def test_single_top_level_path(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        assert len(analysis.top_level_paths) == 1

    def test_terminal_is_c(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        assert analysis.top_level_paths[0].terminal == "c"

    def test_event_sequence(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        events = path_to_event_sequence(analysis.top_level_paths[0])
        assert events == ["go", "done"]

    def test_no_tracks(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        assert analysis.track_paths == {}

    def test_combined_count_equals_one(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        assert analysis.combined_count == 1

    def test_bypass_count(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        assert analysis.bypass_count == 1
        assert analysis.fulfillment_count == 0

    def test_parallel_state_id_is_none(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        assert analysis.parallel_state_id is None


# ---------------------------------------------------------------------------
# analyze_paths — branch machine
# ---------------------------------------------------------------------------


class TestAnalyzePathsBranch:
    def test_two_paths(self, branch_sm):
        analysis = analyze_paths(branch_sm)
        assert len(analysis.top_level_paths) == 2

    def test_one_path_to_c_one_to_d(self, branch_sm):
        analysis = analyze_paths(branch_sm)
        terminals = {p.terminal for p in analysis.top_level_paths}
        assert terminals == {"c", "d"}

    def test_combined_count(self, branch_sm):
        analysis = analyze_paths(branch_sm)
        assert analysis.combined_count == 2


# ---------------------------------------------------------------------------
# analyze_paths — loop machine
# ---------------------------------------------------------------------------


class TestAnalyzePathsLoop:
    def test_has_looping_path(self, loop_sm):
        analysis = analyze_paths(loop_sm)
        looping = [p for p in analysis.top_level_paths if p.is_looping]
        assert len(looping) >= 1

    def test_total_paths(self, loop_sm):
        # LoopSM has: a→b→c (simple), a→d (simple), a→b→(loop to a)→d (loop)
        analysis = analyze_paths(loop_sm)
        assert len(analysis.top_level_paths) == 3

    def test_back_edge_marked(self, loop_sm):
        analysis = analyze_paths(loop_sm)
        looping_path = next(p for p in analysis.top_level_paths if p.is_looping)
        back_edges = [e for e in looping_path.edges if e.is_back_edge]
        assert len(back_edges) == 1
        assert back_edges[0].source == "b"
        assert back_edges[0].target == "a"


# ---------------------------------------------------------------------------
# analyze_paths — parallel machine
# ---------------------------------------------------------------------------


class TestAnalyzePathsParallel:
    def test_has_track_paths(self, mini_parallel_sm):
        analysis = analyze_paths(mini_parallel_sm)
        assert len(analysis.track_paths) >= 1

    def test_combined_count(self, mini_parallel_sm):
        analysis = analyze_paths(mini_parallel_sm)
        # Top: idle→done (bypass), idle→work→done (enters parallel)
        # Tracks: track_a has 1 path, track_b has 1 path → product = 1
        # Combined = 1 (bypass) + 1 * 1 = 2
        assert analysis.combined_count >= 2

    def test_parallel_state_id_set(self, mini_parallel_sm):
        analysis = analyze_paths(mini_parallel_sm)
        assert analysis.parallel_state_id == "work"

    def test_fulfillment_count(self, mini_parallel_sm):
        analysis = analyze_paths(mini_parallel_sm)
        assert analysis.fulfillment_count >= 1

    def test_track_paths_present(self, mini_parallel_sm):
        analysis = analyze_paths(mini_parallel_sm)
        track_names = set(analysis.track_paths.keys())
        assert "track_a" in track_names
        assert "track_b" in track_names


# ---------------------------------------------------------------------------
# analyze_paths — guard enrichment
# ---------------------------------------------------------------------------


class TestGuardEnrichment:
    def test_guarded_edge_has_guard(self, branch_sm):
        analysis = analyze_paths(branch_sm)
        # The 'left' transition is guarded by 'is_left'
        all_edges = [e for p in analysis.top_level_paths for e in p.edges]
        guarded = [e for e in all_edges if e.guard is not None]
        assert len(guarded) >= 1
        guard_names = {e.guard for e in guarded}
        assert any("is_left" in g for g in guard_names)

    def test_unguarded_edge_has_no_guard(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        all_edges = [e for p in analysis.top_level_paths for e in p.edges]
        assert all(e.guard is None for e in all_edges)


# ---------------------------------------------------------------------------
# _build_guard_map — unless= support
# ---------------------------------------------------------------------------


class TestBuildGuardMapUnless:
    def test_unless_guard_prefixed_with_bang(self, unless_guard_sm):
        gmap = _build_guard_map(unless_guard_sm)
        # UnlessGuardSM: proceed = s.to(t, unless="is_locked")
        # The guard should appear as "!is_locked"
        assert ("s", "proceed") in gmap
        guard_value = gmap[("s", "proceed")]
        assert "!is_locked" in guard_value

    def test_cond_guard_has_no_prefix(self, branch_sm):
        gmap = _build_guard_map(branch_sm)
        # BranchSM: left = b.to(c, cond="is_left")
        assert ("b", "left") in gmap
        assert gmap[("b", "left")] == "is_left"

    def test_no_guards_returns_empty(self, linear_sm):
        gmap = _build_guard_map(linear_sm)
        assert gmap == {}

    def test_unguarded_transitions_absent(self, branch_sm):
        gmap = _build_guard_map(branch_sm)
        # The 'right' transition has no cond
        assert ("b", "right") not in gmap


# ---------------------------------------------------------------------------
# _build_transition_meta_map
# ---------------------------------------------------------------------------


class TestBuildTransitionMetaMap:
    def test_regular_transition_not_internal_not_self(self, linear_sm):
        meta = _build_transition_meta_map(linear_sm)
        # a --[go]--> b: external, not self
        assert ("a", "go", "b") in meta
        is_internal, is_self = meta[("a", "go", "b")]
        assert is_internal is False
        assert is_self is False

    def test_self_transition_detected(self, self_loop_sm):
        meta = _build_transition_meta_map(self_loop_sm)
        # y --[again]--> y: self-transition
        assert ("y", "again", "y") in meta
        is_internal, is_self = meta[("y", "again", "y")]
        assert is_self is True

    def test_internal_flag_set(self, internal_sm):
        meta = _build_transition_meta_map(internal_sm)
        # p --[tick]--> p with internal=True
        assert ("p", "tick", "p") in meta
        is_internal, is_self = meta[("p", "tick", "p")]
        assert is_internal is True

    def test_empty_map_for_no_named_transitions(self):
        from statemachine import State, StateChart

        class TrivialSM(StateChart):
            only = State(initial=True, final=True)

        meta = _build_transition_meta_map(TrivialSM)
        assert meta == {}


# ---------------------------------------------------------------------------
# PathEdge enrichment in analyze_paths
# ---------------------------------------------------------------------------


class TestPathEdgeEnrichment:
    def test_self_transition_edge_enriched(self, self_loop_sm):
        analysis = analyze_paths(self_loop_sm)
        all_edges = [e for p in analysis.top_level_paths for e in p.edges]
        self_edges = [e for e in all_edges if e.is_self]
        assert len(self_edges) >= 1
        assert all(e.source == e.target for e in self_edges)

    def test_internal_transition_edge_enriched(self, internal_sm):
        analysis = analyze_paths(internal_sm)
        all_edges = [e for p in analysis.top_level_paths for e in p.edges]
        internal_edges = [e for e in all_edges if e.is_internal]
        assert len(internal_edges) >= 1

    def test_action_edge_enriched(self, action_sm):
        analysis = analyze_paths(action_sm)
        all_edges = [e for p in analysis.top_level_paths for e in p.edges]
        action_edges = [e for e in all_edges if e.actions]
        assert len(action_edges) >= 1
        assert any("log_transition" in e.actions for e in action_edges)

    def test_unless_guard_in_edge(self, unless_guard_sm):
        analysis = analyze_paths(unless_guard_sm)
        all_edges = [e for p in analysis.top_level_paths for e in p.edges]
        guarded = [e for e in all_edges if e.guard and "!" in e.guard]
        assert len(guarded) >= 1

    def test_trivial_track_skipped_gracefully(self, trivial_track_par_sm):
        """A parallel track with no transitions produces an empty path list.

        Exercises the ``if not t_adj: ... continue`` branch in analyze_paths
        for tracks that have a trivially auto-completing state and no named
        transition events.
        """
        analysis = analyze_paths(trivial_track_par_sm)
        # trivial_track has t_adj={} → its path list is []
        assert "trivial_track" in analysis.track_paths
        assert analysis.track_paths["trivial_track"] == []
