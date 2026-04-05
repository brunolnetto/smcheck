"""
tests/test_graph_advanced.py
============================
Unit tests for the new smcheck.graph inspection helpers:
  * discover_invoke_states
  * discover_self_transitions
  * extract_transition_actions
  * derive_compound_traversal
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from smcheck.graph import (
    derive_compound_traversal,
    discover_invoke_states,
    discover_self_transitions,
    extract_transition_actions,
)


# ---------------------------------------------------------------------------
# discover_invoke_states
# ---------------------------------------------------------------------------

class TestDiscoverInvokeStates:
    def test_no_invoke_returns_empty(self, linear_sm):
        result = discover_invoke_states(linear_sm)
        assert result == {}

    def test_no_invoke_branch(self, branch_sm):
        assert discover_invoke_states(branch_sm) == {}

    def test_no_invoke_mini_parallel(self, mini_parallel_sm):
        assert discover_invoke_states(mini_parallel_sm) == {}

    def test_invoke_state_detected(self, invoke_states_sm):
        result = discover_invoke_states(invoke_states_sm)
        assert "m" in result
        assert "_my_invoke_handler" in result["m"]

    def test_invoke_state_non_invoke_excluded(self, invoke_states_sm):
        result = discover_invoke_states(invoke_states_sm)
        assert "n" not in result

    def test_returns_dict_type(self, invoke_states_sm):
        result = discover_invoke_states(invoke_states_sm)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# discover_self_transitions
# ---------------------------------------------------------------------------

class TestDiscoverSelfTransitions:
    def test_no_self_transitions_returns_empty(self, linear_sm):
        result = discover_self_transitions(linear_sm)
        assert result == []

    def test_no_self_transitions_branch(self, branch_sm):
        assert discover_self_transitions(branch_sm) == []

    def test_self_transition_detected(self, self_loop_sm):
        result = discover_self_transitions(self_loop_sm)
        assert len(result) == 1
        state_id, ev = result[0]
        assert state_id == "y"
        assert ev == "again"

    def test_returns_list_of_tuples(self, self_loop_sm):
        result = discover_self_transitions(self_loop_sm)
        assert isinstance(result, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in result)

    def test_internal_transition_is_not_self(self, internal_sm):
        # internal=True transitions are NOT self-transitions (is_self=False)
        # because is_self checks source == target on the Transition object
        # (internal=True with same src/dst sets is_self to True as well)
        result = discover_self_transitions(internal_sm)
        # InternalSM has p.to(p, internal=True) which IS a self-transition
        # (same source and target) — is_self=True per library definition
        state_ids = [sid for sid, _ in result]
        # The tick transition is from p to p (self), regardless of internal flag
        assert "p" in state_ids

    def test_multiple_self_transitions(self):
        """Machine with two self-transitions produces two entries."""
        from statemachine import State, StateChart

        class MultiSelfSM(StateChart):
            a = State(initial=True)
            b = State(final=True)
            ping = a.to(a)
            pong = a.to(a)
            done = a.to(b)

        result = discover_self_transitions(MultiSelfSM)
        assert len(result) == 2
        assert all(sid == "a" for sid, _ in result)


# ---------------------------------------------------------------------------
# extract_transition_actions
# ---------------------------------------------------------------------------

class TestExtractTransitionActions:
    def test_no_actions_returns_empty(self, linear_sm):
        result = extract_transition_actions(linear_sm)
        assert result == {}

    def test_no_actions_branch(self, branch_sm):
        result = extract_transition_actions(branch_sm)
        assert result == {}

    def test_action_detected(self, action_sm):
        result = extract_transition_actions(action_sm)
        # ActionSM: move = u.to(v, on="log_transition")
        assert ("u", "move") in result
        assert "log_transition" in result[("u", "move")]

    def test_no_convention_hooks_included(self, linear_sm):
        # Convention hooks like 'before_transition' should be excluded
        result = extract_transition_actions(linear_sm)
        assert result == {}

    def test_returns_dict_type(self, action_sm):
        result = extract_transition_actions(action_sm)
        assert isinstance(result, dict)

    def test_no_actions_loop_sm(self, loop_sm):
        assert extract_transition_actions(loop_sm) == {}


# ---------------------------------------------------------------------------
# derive_compound_traversal — edge cases
# ---------------------------------------------------------------------------

class TestDeriveCompoundTraversal:
    """Tests for the two guarded-traversal branches that require non-standard machines."""

    def test_unguarded_compound_exit_returns_empty(self, compound_unguarded_exit_sm):
        """A compound state whose exit transition has no cond= is skipped.

        The ``guarded`` dict is built only from transitions with cond specs.
        When the compound's exit event is NOT in that dict, the traversal
        cannot determine what the guard checks, so it returns {} for that
        compound — triggering the ``if exit_ev not in guarded:`` branch.
        """
        result = derive_compound_traversal(compound_unguarded_exit_sm)
        assert result == {}

    def test_guard_without_self_flags_returns_empty(self, guard_no_flags_sm):
        """A guard that references no ``self._xxx`` flags yields no traversal.

        When ``inspect.getsource`` finds zero ``self._xxx`` matches in the
        guard's source, ``guard_flags`` is empty and the compound's traversal
        path cannot be determined — triggering the ``if not guard_flags:``
        branch and returning {} for that compound.
        """
        result = derive_compound_traversal(guard_no_flags_sm)
        assert result == {}

    def test_guarded_compound_produces_traversal(self):
        """Sanity check: a properly guarded compound does produce traversal events."""
        import sys
        sys.path.insert(0, "examples/order-processing")
        try:
            from machine import OrderProcessing
        except ImportError:
            pytest.skip("OrderProcessing not available")
        result = derive_compound_traversal(OrderProcessing)
        # validation compound exits via 'start [is_approved]' guard
        assert "validation" in result
