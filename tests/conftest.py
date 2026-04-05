"""
Shared pytest fixtures for smcheck tests.

All fixtures use lightweight StateChart subclasses defined here — the tests
do NOT depend on OrderProcessing, exercising smcheck as a library.
"""

from __future__ import annotations

import pytest
from statemachine import State, StateChart, StateMachine as _SM


# ── Minimal linear machine: A → B → C (final) ──────────────────────────────


class LinearSM(StateChart):
    """Three-state linear machine with no branches."""

    a = State(initial=True)
    b = State()
    c = State(final=True)

    go = a.to(b)
    done = b.to(c)


# ── Branching: A → B → C (final) or B → D (final), with guard ──────────────


class BranchSM(StateChart):
    """Machine with a guarded branch at state B."""

    a = State(initial=True)
    b = State()
    c = State(final=True)
    d = State(final=True)

    step = a.to(b)
    left = b.to(c, cond="is_left")
    right = b.to(d)

    def __init__(self, *args, **kwargs):
        self._go_left = False
        super().__init__(*args, **kwargs)

    def is_left(self):
        return self._go_left


# ── Loop machine: A → B → C (final), B → A (back-edge), A → D (alt exit) ──


class LoopSM(StateChart):
    """
    Machine with a cycle (B can loop back to A) plus an alternative terminal D
    directly reachable from A.  The alternative exit gives the loop-path
    enumerator a way to complete after taking the B→A back-edge:

        a → b → c          (simple path 1)
        a → d              (simple path 2)
        a → b → a → d     (loop path: uses the B→A back-edge then exits via D)
    """

    a = State(initial=True)
    b = State()
    c = State(final=True)
    d = State(final=True)  # alternative terminal reachable from A without loop

    forward = a.to(b)
    loop = b.to(a)  # back-edge
    finish = b.to(c)
    shortcut = a.to(d)  # direct exit — lets loop paths complete


# ── Note on DeadlockSM / TrapCycleSM ─────────────────────────────────────────
# python-statemachine v3 already blocks non-final sinks and trap cycles at
# class definition time (InvalidDefinition).  To test the validator's
# check_liveness / check_completeness / check_trap_cycles code paths, the
# test files inject hand-crafted adjacency maps directly into SMValidator.
# No broken StateChart subclass needed here.


# ── Ambiguous / non-deterministic transitions ────────────────────────────────


class AmbiguousSM(StateChart):
    """Machine with two transitions sharing the same event from the same state."""

    a = State(initial=True)
    b = State(final=True)
    c = State(final=True)

    # Same event name, two targets
    go = a.to(b, cond="pick_b") | a.to(c)

    def __init__(self, *args, **kwargs):
        self._pick_b = True
        super().__init__(*args, **kwargs)

    def pick_b(self):
        return self._pick_b


# ── Parallel machine (mimics order-processing shape at miniature scale) ──────


class MiniParallelSM(StateChart):
    """Minimal parallel machine with two tracks."""

    idle = State(initial=True)
    done = State(final=True)

    class work(State.Parallel, name="Work"):
        class track_a(State.Compound, name="TrackA"):
            ta1 = State(initial=True)
            ta2 = State(final=True)

            step_a = ta1.to(ta2)

        class track_b(State.Compound, name="TrackB"):
            tb1 = State(initial=True)
            tb2 = State()
            tb3 = State(final=True)

            step_b1 = tb1.to(tb2)
            step_b2 = tb2.to(tb3)

    begin = idle.to(work)
    cancel = idle.to(done) | work.to(done)


# ── Self-transition machine (a loops on itself) ──────────────────────────────


class SelfLoopSM(StateChart):
    """Machine with a self-transition (a.to(a)) at state B."""

    x = State(initial=True)
    y = State()
    z = State(final=True)

    go = x.to(y)
    again = y.to(y)  # self-transition: is_self=True
    finish = y.to(z)


# ── Internal-transition machine ───────────────────────────────────────────────


class InternalSM(StateChart):
    """Machine with an internal self-transition (no exit/enter)."""

    p = State(initial=True)
    q = State(final=True)

    tick = p.to(p, internal=True)  # internal=True → no exit/enter
    finish = p.to(q)


# ── Unless-guarded machine ────────────────────────────────────────────────────


class UnlessGuardSM(StateChart):
    """Machine with an unless= guard on one transition."""

    s = State(initial=True)
    t = State(final=True)

    def __init__(self, *args, **kwargs):
        self._locked = False
        super().__init__(*args, **kwargs)

    def is_locked(self):
        return self._locked

    proceed = s.to(t, unless="is_locked")


# ── Machine with non-convention action callbacks ──────────────────────────────


class ActionSM(StateChart):
    """Machine with explicit on= action callbacks declared inline."""

    u = State(initial=True)
    v = State(final=True)

    def log_transition(self):
        pass

    move = u.to(v, on="log_transition")


# ── Machine with invoke= handler ─────────────────────────────────────────────


def _my_invoke_handler(**kwargs):
    pass


class InvokeStatesSM(StateChart):
    """Machine where one state has an invoke= handler."""

    m = State(initial=True, invoke=_my_invoke_handler)
    n = State(final=True)

    proceed = m.to(n)


# ── Machine with a hook name typo  ───────────────────────────────────────────


class HookTypoSM(StateChart):
    """Machine with a method that looks like a convention hook but uses a wrong state id."""

    h1 = State(initial=True)
    h2 = State(final=True)

    hop = h1.to(h2)

    # Deliberate near-miss: 'ghost' is not a valid state id
    def on_enter_ghost(self):
        pass  # pragma: no cover


# ── Machine with a validators= constraint ────────────────────────────────────


class ValidatorSM(StateChart):
    """Machine whose 'proceed' transition declares a validators= constraint.

    When ``_allow_pass`` is ``False`` (the default), firing ``proceed`` raises
    ``ValueError``.  Set ``sm._allow_pass = True`` to permit passage.
    """

    p = State(initial=True)
    q = State(final=True)

    proceed = p.to(q, validators="check_allowed")

    def __init__(self, *args, **kwargs):
        self._allow_pass = False
        super().__init__(*args, **kwargs)

    def check_allowed(self):
        if not self._allow_pass:
            raise ValueError("not allowed")


# ── StateMachine subclass (different flag defaults) ───────────────────────────


class FlagOverrideSM(_SM):
    """StateMachine (not StateChart) — has different defaults for several flags."""

    fa = State(initial=True)
    fb = State(final=True)

    run = fa.to(fb)


# ── Compound state with an *unguarded* exit transition ────────────────────────


class CompoundUnguardedExitSM(StateChart):
    """Compound 'work' exits via an unguarded transition 'finish'.

    Used to cover the branch in derive_compound_traversal that skips
    compound exit events that are not present in the guarded-event dict
    (because they have no cond= specs).
    """

    idle = State(initial=True)
    done = State(final=True)

    class work(State.Compound):
        w1 = State(initial=True)
        w2 = State(final=True)
        step = w1.to(w2)

    begin = idle.to(work)
    finish = work.to(done)  # no cond → not in the guarded dict


# ── Guard method that references no ``self._`` flags ──────────────────────────


class GuardNoFlagsSM(StateChart):
    """Guard 'always_true' inspects no instance flags.

    Used to cover the branch in derive_compound_traversal where
    ``inspect.getsource`` finds zero ``self._xxx`` references —
    making ``guard_flags`` empty and the compound traversal undiscoverable.
    """

    idle = State(initial=True)
    done = State(final=True)

    class work(State.Compound):
        w1 = State(initial=True)
        w2 = State(final=True)
        step = w1.to(w2)

    begin = idle.to(work)
    finish = work.to(done, cond="always_true")

    def always_true(self):
        """Unconditional guard — no instance-flag references."""
        return True


# ── Parallel machine with one trivial (no-transition) track ──────────────────


class TrivialTrackParSM(StateChart):
    """Parallel machine where one track has no transitions (empty adj map).

    Used to cover the ``if not t_adj: continue`` branch in
    analyze_paths / run_graph_analysis when a parallel track is
    structurally trivial (single initial+final state, nothing to traverse).
    """

    class par(State.Parallel, name="Par"):
        class real_track(State.Compound, name="Real"):
            a = State(initial=True)
            b = State(final=True)
            go = a.to(b)

        class trivial_track(State.Compound, name="Trivial"):
            t = State(initial=True, final=True)  # single auto-completing state


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def linear_sm():
    return LinearSM


@pytest.fixture
def branch_sm():
    return BranchSM


@pytest.fixture
def loop_sm():
    return LoopSM


@pytest.fixture
def ambiguous_sm():
    return AmbiguousSM


@pytest.fixture
def mini_parallel_sm():
    return MiniParallelSM


@pytest.fixture
def self_loop_sm():
    return SelfLoopSM


@pytest.fixture
def internal_sm():
    return InternalSM


@pytest.fixture
def unless_guard_sm():
    return UnlessGuardSM


@pytest.fixture
def action_sm():
    return ActionSM


@pytest.fixture
def invoke_states_sm():
    return InvokeStatesSM


@pytest.fixture
def hook_typo_sm():
    return HookTypoSM


@pytest.fixture
def flag_override_sm():
    return FlagOverrideSM


@pytest.fixture
def validator_sm():
    return ValidatorSM


@pytest.fixture
def compound_unguarded_exit_sm():
    return CompoundUnguardedExitSM


@pytest.fixture
def guard_no_flags_sm():
    return GuardNoFlagsSM


@pytest.fixture
def trivial_track_par_sm():
    return TrivialTrackParSM


# ── Machine whose state name starts with "on_" ────────────────────────────────


class OnHoldNameSM(StateChart):
    """Machine that has a state named 'on_hold'.

    Used to verify that check_hook_names() does NOT flag state attributes
    (State instances) that happen to start with 'on_', e.g. ``on_hold = State()``.
    """

    idle = State(initial=True)
    on_hold = State()  # state — not a method; must not be flagged as a hook typo
    done = State(final=True)

    pause = idle.to(on_hold)
    resume = on_hold.to(done)


# ── Compound where a non-final child has no own exit edges ────────────────────


class CompoundSinkChildSM(StateChart):
    """Machine with a non-final compound sub-state that has no outgoing transitions.

    'acknowledged' has no direct outgoing edges, but its parent compound 'work'
    has a 'finish' exit.  check_completeness() should PASS because the state
    can leave via its ancestor.
    """

    idle = State(initial=True)
    done = State(final=True)

    class work(State.Compound):
        w1 = State(initial=True)
        acknowledged = State()  # non-final, no outgoing edges of its own

        step = w1.to(acknowledged)

    begin = idle.to(work)
    finish = work.to(done)  # acknowledged exits via this compound exit


@pytest.fixture
def on_hold_name_sm():
    return OnHoldNameSM


@pytest.fixture
def compound_sink_child_sm():
    return CompoundSinkChildSM


# ── Validator on a non-initial state (setup_events branch) ───────────────────


class MultiStepValidatorSM(StateChart):
    """Machine with validators= on a NON-initial state.

    Covers the ``if setup_events:`` branch in ``generate_validator_tests``
    (state 'mid' requires ``["go"]`` as its setup sequence before the
    validator-guarded ``proceed`` can be fired).
    """

    p = State(initial=True)
    mid = State()
    q = State(final=True)

    go = p.to(mid)
    proceed = mid.to(q, validators="check_allowed")

    def check_allowed(self):
        raise ValueError("blocked by validator")


@pytest.fixture
def multi_step_validator_sm():
    return MultiStepValidatorSM


# ── Machine with a guarded intermediate hop (setup path needs guard flags) ───


class GuardedPathSM(StateChart):
    """Machine where reaching state 'mid' requires a guarded 'go' event.

    Used to cover the guard-flag injection for SETUP events:
    ``generate_transition_tests`` must set ``_ready = True`` before firing
    ``go`` in the setup sequence for state ``mid``.
    """

    a = State(initial=True)
    mid = State()
    c = State(final=True)

    go = a.to(mid, cond="is_ready")
    done = mid.to(c)

    def __init__(self, *args, **kwargs):
        self._ready = False
        super().__init__(*args, **kwargs)

    def is_ready(self):
        """Gate on the _ready flag."""
        return self._ready


@pytest.fixture
def guarded_path_sm():
    return GuardedPathSM
