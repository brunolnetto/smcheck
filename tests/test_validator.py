"""
tests/test_validator.py
=======================
Unit tests for smcheck.validator — all five validation checks.

To exercise liveness/completeness/trap-cycle ERROR paths we inject
hand-crafted adjacency maps, since python-statemachine itself blocks
broken machines at definition time.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
from collections import namedtuple

import pytest
from smcheck.validator import SMValidator, ValidationFinding


# ---------------------------------------------------------------------------
# ValidationFinding basics
# ---------------------------------------------------------------------------

class TestValidationFinding:
    def test_fields(self):
        f = ValidationFinding("PASS", "reachability", "all ok")
        assert f.level == "PASS"
        assert f.category == "reachability"
        assert f.detail == "all ok"
        assert f.nodes == []

    def test_nodes_default_factory(self):
        f1 = ValidationFinding("WARN", "test", "x", ["a"])
        f2 = ValidationFinding("WARN", "test", "y")
        assert f1.nodes == ["a"]
        assert f2.nodes == []
        # default factory must produce independent lists
        f2.nodes.append("b")
        assert f1.nodes == ["a"]


# ---------------------------------------------------------------------------
# SMValidator on clean machines (all PASS)
# ---------------------------------------------------------------------------

class TestValidatorPassLinear:
    """LinearSM: A → B → C(final) — trivial, everything should pass."""

    def test_reachability_pass(self, linear_sm):
        v = SMValidator(linear_sm)
        f = v.check_reachability()
        assert f.level == "PASS"
        assert f.category == "reachability"

    def test_liveness_pass(self, linear_sm):
        f = SMValidator(linear_sm).check_liveness()
        assert f.level == "PASS"

    def test_determinism_pass(self, linear_sm):
        f = SMValidator(linear_sm).check_determinism()
        assert f.level == "PASS"

    def test_completeness_pass(self, linear_sm):
        f = SMValidator(linear_sm).check_completeness()
        assert f.level == "PASS"

    def test_trap_cycles_pass(self, linear_sm):
        f = SMValidator(linear_sm).check_trap_cycles()
        assert f.level == "PASS"

    def test_run_all_returns_nine(self, linear_sm):
        findings = SMValidator(linear_sm).run_all()
        assert len(findings) == 9
        assert all(f.level == "PASS" for f in findings)


class TestValidatorPassBranch:
    """BranchSM: guarded branch, still all PASS."""
    def test_all_pass(self, branch_sm):
        findings = SMValidator(branch_sm).run_all()
        assert len(findings) == 9


class TestValidatorPassLoop:
    """LoopSM: has a back-edge, but no structural defect."""
    def test_all_pass(self, loop_sm):
        findings = SMValidator(loop_sm).run_all()
        assert all(f.level == "PASS" for f in findings)


# ---------------------------------------------------------------------------
# SMValidator on parallel machines
# ---------------------------------------------------------------------------

class TestValidatorParallel:
    """MiniParallelSM: sub-states unreachable in flat graph = expected PASS."""

    def test_reachability_pass_with_compound_children(self, mini_parallel_sm):
        v = SMValidator(mini_parallel_sm)
        f = v.check_reachability()
        # Sub-states (ta1, ta2, tb1, tb2, tb3) unreachable in flat graph
        # → PASS with informational note + listed nodes
        assert f.level == "PASS"
        assert len(f.nodes) > 0  # compound children listed

    def test_all_checks(self, mini_parallel_sm):
        findings = SMValidator(mini_parallel_sm).run_all()
        assert len(findings) == 9


# ---------------------------------------------------------------------------
# SMValidator — ambiguous transitions (WARN on determinism)
# ---------------------------------------------------------------------------

class TestValidatorAmbiguous:
    def test_determinism_warns(self, ambiguous_sm):
        f = SMValidator(ambiguous_sm).check_determinism()
        assert f.level == "WARN"
        assert f.category == "determinism"
        assert len(f.nodes) >= 1


# ---------------------------------------------------------------------------
# SMValidator — injected broken graphs for ERROR/WARN paths
# ---------------------------------------------------------------------------

class TestValidatorInjectedDeadlock:
    """Patch _adj to create a reachable non-final state with no path to final."""

    def test_liveness_error(self, linear_sm):
        v = SMValidator(linear_sm)
        # Inject: A → B, A → Z(dead-end), B → C(final)
        v._adj = {
            "a": [("go", "b"), ("trap", "z")],
            "b": [("done", "c")],
        }
        v._all_nodes = {"a", "b", "c", "z"}
        v._finals = {"c"}
        v._pseudo = set()
        v._initial = "a"

        f = v.check_liveness()
        assert f.level == "ERROR"
        assert "z" in f.nodes

    def test_completeness_warns_on_non_final_sink(self, linear_sm):
        v = SMValidator(linear_sm)
        # Z is a non-final, non-pseudo node with no outgoing edges
        v._adj = {"a": [("go", "b"), ("trap", "z")], "b": [("done", "c")]}
        v._all_nodes = {"a", "b", "c", "z"}
        v._finals = {"c"}
        v._pseudo = set()

        f = v.check_completeness()
        assert f.level == "WARN"
        assert "z" in f.nodes


class TestValidatorInjectedTrapCycle:
    """Patch _adj to create an SCC with no exit."""

    def test_trap_cycles_error(self, linear_sm):
        v = SMValidator(linear_sm)
        # A → B → C → B (trap cycle at {B,C}), A → D(final)
        v._adj = {
            "a": [("go", "b"), ("bail", "d")],
            "b": [("c1", "c")],
            "c": [("c2", "b")],
        }
        v._all_nodes = {"a", "b", "c", "d"}
        v._finals = {"d"}
        v._pseudo = set()
        v._initial = "a"

        f = v.check_trap_cycles()
        assert f.level == "ERROR"
        assert "b" in f.nodes
        assert "c" in f.nodes


class TestValidatorInjectedUnreachable:
    """Patch to create a root-level state unreachable (not a compound child)."""

    def test_reachability_warns_on_root_unreachable(self, linear_sm):
        v = SMValidator(linear_sm)
        # Z is a root-level state (not a compound child)
        v._adj = {"a": [("go", "b")], "b": [("done", "c")]}
        v._all_nodes = {"a", "b", "c", "z"}
        v._finals = {"c"}
        v._pseudo = set()
        v._initial = "a"
        # _sm.states_map has no state with parent for 'z', so compound_children
        # won't include 'z'

        f = v.check_reachability()
        assert f.level == "WARN"
        assert "z" in f.nodes


# ---------------------------------------------------------------------------
# Tarjan SCC (indirectly via check_trap_cycles)
# ---------------------------------------------------------------------------

class TestTarjanSCCs:
    def test_no_cycles_gives_singleton_sccs(self, linear_sm):
        v = SMValidator(linear_sm)
        sccs = v._tarjan_sccs()
        # Each SCC should be a single node
        assert all(len(s) == 1 for s in sccs)

    def test_loop_detected_as_scc(self, loop_sm):
        v = SMValidator(loop_sm)
        sccs = v._tarjan_sccs()
        multi_sccs = [s for s in sccs if len(s) >= 2]
        assert len(multi_sccs) == 1
        # a and b form the cycle; c and d are singleton SCCs
        assert multi_sccs[0] == {"a", "b"}

    def test_trap_cycle_scc_captured(self, linear_sm):
        v = SMValidator(linear_sm)
        v._adj = {"a": [("x", "b")], "b": [("y", "c")], "c": [("z", "b")]}
        v._all_nodes = {"a", "b", "c"}
        sccs = v._tarjan_sccs()
        multi = [s for s in sccs if len(s) >= 2]
        assert len(multi) == 1
        assert multi[0] == {"b", "c"}


# ---------------------------------------------------------------------------
# BFS helper
# ---------------------------------------------------------------------------

class TestBFS:
    def test_bfs_from_start(self, linear_sm):
        v = SMValidator(linear_sm)
        reachable = v._bfs(v._adj, {"a"})
        assert "a" in reachable
        assert "b" in reachable
        assert "c" in reachable

    def test_bfs_empty_graph(self, linear_sm):
        v = SMValidator(linear_sm)
        reachable = v._bfs({}, {"x"})
        assert reachable == {"x"}


# ---------------------------------------------------------------------------
# Check ⑥ : Class flags
# ---------------------------------------------------------------------------

class TestCheckClassFlags:
    def test_statechart_pass(self, linear_sm):
        # LinearSM extends StateChart with all default flags → PASS
        f = SMValidator(linear_sm).check_class_flags()
        assert f.level == "PASS"
        assert f.category == "class_flags"
        assert "allow_event_without_transition" in f.detail

    def test_statemachine_warns(self, flag_override_sm):
        # FlagOverrideSM extends StateMachine → has non-default flags → WARN
        f = SMValidator(flag_override_sm).check_class_flags()
        assert f.level == "WARN"
        assert "False" in f.detail   # at least one flag has changed

    def test_detail_lists_all_four_flags(self, linear_sm):
        f = SMValidator(linear_sm).check_class_flags()
        assert "catch_errors_as_events" in f.detail
        assert "enable_self_transition_entries" in f.detail
        assert "atomic_configuration_update" in f.detail


# ---------------------------------------------------------------------------
# Check ⑦ : Invoke states
# ---------------------------------------------------------------------------

class TestCheckInvokeStates:
    def test_no_invoke_pass(self, linear_sm):
        f = SMValidator(linear_sm).check_invoke_states()
        assert f.level == "PASS"
        assert f.category == "invoke_states"

    def test_invoke_detected_warn(self, invoke_states_sm):
        f = SMValidator(invoke_states_sm).check_invoke_states()
        assert f.level == "WARN"
        assert "m" in f.nodes

    def test_invoke_detail_contains_handler_name(self, invoke_states_sm):
        f = SMValidator(invoke_states_sm).check_invoke_states()
        assert "_my_invoke_handler" in f.detail


# ---------------------------------------------------------------------------
# Check ⑧ : Self-transitions
# ---------------------------------------------------------------------------

class TestCheckSelfTransitions:
    def test_no_self_transitions_pass(self, linear_sm):
        f = SMValidator(linear_sm).check_self_transitions()
        assert f.level == "PASS"
        assert f.category == "self_transitions"

    def test_self_transition_warns(self, self_loop_sm):
        f = SMValidator(self_loop_sm).check_self_transitions()
        assert f.level == "WARN"
        assert "y" in f.nodes

    def test_detail_mentions_event(self, self_loop_sm):
        f = SMValidator(self_loop_sm).check_self_transitions()
        assert "again" in f.detail

    def test_detail_mentions_flag(self, self_loop_sm):
        f = SMValidator(self_loop_sm).check_self_transitions()
        assert "enable_self_transition_entries" in f.detail

    def test_internal_self_transition_also_detected(self, internal_sm):
        # internal=True + same src/dst → is_self=True in the library
        f = SMValidator(internal_sm).check_self_transitions()
        assert f.level == "WARN"
        assert "p" in f.nodes


# ---------------------------------------------------------------------------
# Check ⑨ : Hook name audit
# ---------------------------------------------------------------------------

class TestCheckHookNames:
    def test_no_typos_pass(self, linear_sm):
        f = SMValidator(linear_sm).check_hook_names()
        assert f.level == "PASS"
        assert f.category == "hook_names"

    def test_hook_typo_warns(self, hook_typo_sm):
        # HookTypoSM has on_enter_ghost; 'ghost' is not a state id
        f = SMValidator(hook_typo_sm).check_hook_names()
        assert f.level == "WARN"
        # The near-miss method is in the nodes list
        assert "on_enter_ghost" in f.nodes

    def test_valid_hooks_not_flagged(self, branch_sm):
        # BranchSM has no explicit hook methods besides 'is_left' which
        # doesn't match any convention prefix → should PASS
        f = SMValidator(branch_sm).check_hook_names()
        assert f.level == "PASS"

    def test_state_attribute_starting_with_on_not_flagged(self, on_hold_name_sm):
        # OnHoldNameSM has a state `on_hold = State()` — it starts with "on_"
        # but is a State descriptor, not a method.  Must not produce WARN.
        f = SMValidator(on_hold_name_sm).check_hook_names()
        assert f.level == "PASS"
        assert "on_hold" not in f.nodes


# ---------------------------------------------------------------------------
# Check ④ : Completeness — ancestor-exit aware
# ---------------------------------------------------------------------------

class TestCheckCompletenessAncestor:
    def test_compound_child_with_parent_exit_passes(self, compound_sink_child_sm):
        # 'acknowledged' is a non-final sub-state with no outgoing edges.
        # Its parent 'work' has the 'finish' exit → not a design gap.
        f = SMValidator(compound_sink_child_sm).check_completeness()
        assert f.level == "PASS"
        assert "acknowledged" not in f.nodes

    def test_genuine_sink_still_warns(self, linear_sm):
        # Regression guard: an injected root-level non-final sink (no ancestors
        # with edges) still triggers WARN after the ancestor-aware fix.
        v = SMValidator(linear_sm)
        v._adj = {"a": [("go", "b"), ("trap", "z")], "b": [("done", "c")]}
        v._all_nodes = {"a", "b", "c", "z"}
        v._finals = {"c"}
        v._pseudo = set()

        f = v.check_completeness()
        assert f.level == "WARN"
        assert "z" in f.nodes


# ---------------------------------------------------------------------------
# run_all — nine checks
# ---------------------------------------------------------------------------

class TestRunAllNineChecks:
    def test_returns_nine_findings(self, linear_sm):
        v = SMValidator(linear_sm)
        findings = v.run_all()
        assert len(findings) == 9

    def test_all_categories_present(self, linear_sm):
        findings = SMValidator(linear_sm).run_all()
        categories = {f.category for f in findings}
        expected = {
            "reachability", "liveness", "determinism", "completeness",
            "trap_cycles", "class_flags", "invoke_states",
            "self_transitions", "hook_names",
        }
        assert expected == categories
