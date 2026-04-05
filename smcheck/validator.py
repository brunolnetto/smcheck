"""smcheck.validator
================
Static validation layer for python-statemachine ``StateChart`` subclasses.

Covers nine correctness / quality properties:
  ① Reachability       – every state reachable from the initial state
  ② Liveness           – every reachable state can reach a terminal (no deadlocks)
  ③ Determinism        – no ambiguous (state, event) pairs (modulo guards)
  ④ Completeness       – no non-final states without outgoing transitions
  ⑤ Trap cycles        – no SCCs without exit
  ⑥ Class flags        – report behavioral flags that change execution semantics
  ⑦ Invoke states      – flag states with spontaneous-event invoke handlers
  ⑧ Self-transitions   – report self-loops and relevant flag interaction
  ⑨ Hook name audit    – detect near-miss method names (likely typos)
"""
from __future__ import annotations

import inspect
import re
from collections import Counter, deque
from dataclasses import dataclass, field

from .graph import AdjMap, discover_invoke_states, discover_self_transitions, extract_sm_graph


@dataclass
class ValidationFinding:
    """
    A single finding from the static validator.

    Attributes
    ----------
    level    : ``"PASS"`` | ``"WARN"`` | ``"ERROR"``
    category : short tag — one of ``reachability``, ``liveness``,
               ``determinism``, ``completeness``, ``trap_cycles``
    detail   : human-readable explanation
    nodes    : state IDs implicated in the finding (empty when level is PASS)
    """
    level:    str
    category: str
    detail:   str
    nodes:    list[str] = field(default_factory=list)


class SMValidator:
    """
    Static analysis that compensates for the five gaps in python-statemachine's
    own validation.  Works on any ``StateChart`` subclass.

    Usage::

        v = SMValidator(MyMachine)
        for finding in v.run_all():
            print(finding)
    """

    def __init__(self, sm_class: type) -> None:
        self._cls = sm_class
        self._sm  = sm_class()
        self._adj = extract_sm_graph(sm_class)
        self._all_nodes: set[str] = (
            set(self._adj.keys())
            | {dst for outs in self._adj.values() for _, dst in outs}
        )
        self._finals: set[str] = {
            s.id for s in self._sm.states_map.values() if s.final
        }
        # Pseudo-nodes: HistoryState objects and any graph node not in states_map
        # (python-statemachine doesn't always register HistoryState in states_map).
        _tracked: set[str] = set(self._sm.states_map.keys())
        self._pseudo: set[str] = (
            {s.id for s in self._sm.states_map.values() if s.is_history}
            | (self._all_nodes - _tracked)
        )
        self._initial: str = next(
            s.id
            for s in self._sm.states_map.values()
            if s.initial and s.parent is None
        )

    # ── BFS helper ────────────────────────────────────────────────────────────

    def _bfs(self, adj: AdjMap, starts: set[str]) -> set[str]:
        visited: set[str] = set(starts)
        queue: deque[str] = deque(starts)
        while queue:
            n = queue.popleft()
            for _, nb in adj.get(n, []):
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
        return visited

    # ── Check ① : Reachability ────────────────────────────────────────────────

    def check_reachability(self) -> ValidationFinding:
        """
        BFS from the initial state; any state not visited is unreachable.

        Compound sub-states inside parallel regions always appear unreachable in
        the flat graph (entered via compound auto-transitions, not named events)
        — these produce PASS with an informational note.  Root-level states
        that remain unreachable are genuine design gaps and produce WARN.
        """
        reachable   = self._bfs(self._adj, {self._initial})
        unreachable = self._all_nodes - reachable - self._pseudo
        if not unreachable:
            return ValidationFinding(
                "PASS", "reachability",
                f"All {len(self._all_nodes)} node(s) reachable from "
                f"'{self._initial}' (pseudo-states excluded)",
            )
        compound_children: set[str] = {
            s.id for s in self._sm.states_map.values() if s.parent is not None
        }
        expected   = unreachable & compound_children
        unexpected = unreachable - compound_children
        if unexpected:
            return ValidationFinding(
                "WARN", "reachability",
                f"{len(unexpected)} root-level state(s) unreachable from "
                f"'{self._initial}' — likely unreferenced design gaps; "
                f"{len(expected)} compound sub-states unreachable in flat graph "
                f"(expected — entered via compound auto-transitions)",
                sorted(unexpected),
            )
        return ValidationFinding(
            "PASS", "reachability",
            f"All {len(expected)} sub-state(s) unreachable in the flat graph are "
            f"compound children entered via auto-transitions — structurally expected",
            sorted(expected),
        )

    # ── Check ② : Liveness ───────────────────────────────────────────────────

    def check_liveness(self) -> ValidationFinding:
        """
        Reverse-BFS from all terminal states.  Any reachable non-final state
        not co-reachable from a terminal is a deadlock candidate.
        """
        rev: AdjMap = {}
        for src, outs in self._adj.items():
            for ev, dst in outs:
                rev.setdefault(dst, []).append((ev, src))

        co_reachable = self._bfs(rev, self._finals)
        reachable    = self._bfs(self._adj, {self._initial})
        dead         = reachable - co_reachable - self._finals - self._pseudo
        if not dead:
            return ValidationFinding(
                "PASS", "liveness",
                "All reachable non-final states have a path to a terminal "
                "(no deadlocks detected)",
            )
        return ValidationFinding(
            "ERROR", "liveness",
            f"{len(dead)} reachable non-final state(s) cannot reach any terminal "
            f"— deadlock risk",
            sorted(dead),
        )

    # ── Check ③ : Determinism ─────────────────────────────────────────────────

    def check_determinism(self) -> ValidationFinding:
        """
        Count (state, event) pairs.  Any pair with more than one target
        indicates potential non-determinism; guards (not statically inspectable)
        may resolve the ambiguity, so this produces WARN rather than ERROR.
        """
        conflicts: dict[str, list[str]] = {}
        for src, outs in self._adj.items():
            counts    = Counter(ev for ev, _ in outs)
            ambiguous = sorted(ev for ev, c in counts.items() if c > 1)
            if ambiguous:
                conflicts[src] = ambiguous
        if not conflicts:
            return ValidationFinding(
                "PASS", "determinism",
                "All (state, event) pairs are unique — no ambiguous transitions",
            )
        detail = "; ".join(
            f"'{s}' → {evs}" for s, evs in sorted(conflicts.items())
        )
        return ValidationFinding(
            "WARN", "determinism",
            f"Ambiguous transitions in {len(conflicts)} state(s) "
            f"(same event, multiple targets — verify guards): {detail}",
            sorted(conflicts.keys()),
        )

    # ── Ancestor helper ────────────────────────────────────────────────────────

    def _ancestors(self, sid: str) -> set[str]:
        """Return IDs of all strict ancestor states of *sid* in the hierarchy."""
        result: set[str] = set()
        s = self._sm.states_map.get(sid)
        if s is None:
            return result
        s = s.parent
        while s is not None:
            result.add(s.id)
            s = s.parent
        return result

    # ── Check ④ : Completeness ────────────────────────────────────────────────

    def check_completeness(self) -> ValidationFinding:
        """
        Non-final, non-pseudo states with no outgoing transitions are likely
        unfinished design gaps.

        Compound sub-states that have no direct outgoing edges but whose
        ancestor compound carries exit transitions (e.g. a parallel track's
        terminal sub-state waiting for an explicit top-level event) are NOT
        flagged — the state can exit via the parent's edge.
        """
        sinks = {
            n
            for n in self._all_nodes
            if not self._adj.get(n)
            and not any(self._adj.get(a) for a in self._ancestors(n))
            and n not in self._finals
            and n not in self._pseudo
        }
        if not sinks:
            return ValidationFinding(
                "PASS", "completeness",
                "All non-final states have at least one outgoing transition",
            )
        return ValidationFinding(
            "WARN", "completeness",
            f"{len(sinks)} non-final state(s) have no outgoing transitions "
            f"(possible design gaps or unreachable parallel sub-states)",
            sorted(sinks),
        )

    # ── Check ⑤ : Trap cycles ─────────────────────────────────────────────────

    def check_trap_cycles(self) -> ValidationFinding:
        """
        Tarjan SCC to find strongly-connected components with no exit edge.
        Such components trap the machine in an infinite loop.
        """
        sccs  = self._tarjan_sccs()
        traps: list[list[str]] = []
        for scc in sccs:
            if len(scc) < 2:
                continue
            has_exit = any(
                dst not in scc
                for src in scc
                for _, dst in self._adj.get(src, [])
            )
            if not has_exit:
                traps.append(sorted(scc))
        if not traps:
            return ValidationFinding(
                "PASS", "trap_cycles",
                "No trap cycles — all cycles have at least one exit path",
            )
        flat = [n for scc in traps for n in scc]
        return ValidationFinding(
            "ERROR", "trap_cycles",
            f"{len(traps)} trap cycle(s) with no exit to a terminal "
            f"— machine can spin forever",
            flat,
        )

    def _tarjan_sccs(self) -> list[set[str]]:
        """Tarjan's SCC algorithm (recursive; safe for graphs ≤ ~500 nodes)."""
        index:    dict[str, int]  = {}
        lowlink:  dict[str, int]  = {}
        on_stack: dict[str, bool] = {}
        stack:    list[str]       = []
        sccs:     list[set[str]]  = []
        counter   = [0]

        def _visit(v: str) -> None:
            index[v] = lowlink[v] = counter[0]
            counter[0] += 1
            stack.append(v)
            on_stack[v] = True
            for _, w in self._adj.get(v, []):
                if w not in index:
                    _visit(w)
                    lowlink[v] = min(lowlink[v], lowlink.get(w, lowlink[v]))
                elif on_stack.get(w, False):
                    lowlink[v] = min(lowlink[v], index[w])
            if lowlink[v] == index[v]:
                scc: set[str] = set()
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    scc.add(w)
                    if w == v:
                        break
                sccs.append(scc)

        for v in sorted(self._all_nodes):
            if v not in index:
                _visit(v)
        return sccs

    # ── Full suite ────────────────────────────────────────────────────────────

    def run_all(self) -> list[ValidationFinding]:
        """Run all nine checks and return findings in declaration order."""
        return [
            self.check_reachability(),
            self.check_liveness(),
            self.check_determinism(),
            self.check_completeness(),
            self.check_trap_cycles(),
            self.check_class_flags(),
            self.check_invoke_states(),
            self.check_self_transitions(),
            self.check_hook_names(),
        ]

    # ── Check ⑥ : Class flags ─────────────────────────────────────────────────

    def check_class_flags(self) -> ValidationFinding:
        """
        Report class-level behavioral flags that significantly change execution
        semantics.  High-impact non-default values produce a WARN finding.

        Checked flags:

        * ``allow_event_without_transition`` — if ``False``, unknown events
          raise ``TransitionNotAllowed`` instead of being silently ignored.
        * ``catch_errors_as_events`` — if ``False``, callback exceptions
          propagate to the caller rather than becoming ``error.execution``.
        * ``enable_self_transition_entries`` — if ``False``, self-transitions
          skip enter/exit callbacks (legacy ``StateMachine`` behaviour).
        * ``atomic_configuration_update`` — if ``True``, the active
          configuration is updated atomically after all callbacks (SCXML mode).
        """
        flag_names = (
            "allow_event_without_transition",
            "catch_errors_as_events",
            "enable_self_transition_entries",
            "atomic_configuration_update",
        )
        flags: dict[str, object] = {
            n: getattr(self._cls, n, None) for n in flag_names
        }
        issues: list[str] = []
        if flags["allow_event_without_transition"] is False:
            issues.append(
                "allow_event_without_transition=False → unknown events raise TransitionNotAllowed"
            )
        if flags["catch_errors_as_events"] is False:
            issues.append(
                "catch_errors_as_events=False → callback exceptions propagate uncaught"
            )

        flag_str = ", ".join(f"{k}={v}" for k, v in flags.items() if v is not None)
        detail   = f"Class flags: {flag_str}"
        if issues:
            return ValidationFinding(
                "WARN", "class_flags",
                f"{detail}. Note: {'; '.join(issues)}",
            )
        return ValidationFinding("PASS", "class_flags", detail)

    # ── Check ⑦ : Invoke states ───────────────────────────────────────────────

    def check_invoke_states(self) -> ValidationFinding:
        """
        Detect states that declare ``invoke=`` handlers (async work, timeouts,
        child state machines).  These states can spontaneously fire events
        (``done.invoke.<id>`` or user-defined events) when their handler
        completes, which may not be captured in the static transition graph.
        """
        invoked = discover_invoke_states(self._cls)
        if not invoked:
            return ValidationFinding(
                "PASS", "invoke_states",
                "No states with invoke= handlers detected",
            )
        detail = "; ".join(
            f"'{sid}': {handler}" for sid, handler in sorted(invoked.items())
        )
        return ValidationFinding(
            "WARN", "invoke_states",
            f"{len(invoked)} state(s) with invoke= handler(s) — can spontaneously "
            f"fire events on completion: {detail}",
            sorted(invoked.keys()),
        )

    # ── Check ⑧ : Self-transitions ────────────────────────────────────────────

    def check_self_transitions(self) -> ValidationFinding:
        """
        Detect self-transitions (``a.to(a)``) and report their interaction with
        the ``enable_self_transition_entries`` class flag.

        * ``enable_self_transition_entries=True``  → enter/exit callbacks fire
          (default for ``StateChart``).
        * ``enable_self_transition_entries=False`` → enter/exit callbacks skip
          (default for legacy ``StateMachine``).
        """
        self_t = discover_self_transitions(self._cls)
        if not self_t:
            return ValidationFinding(
                "PASS", "self_transitions",
                "No self-transitions detected",
            )
        enter_flag = getattr(self._cls, "enable_self_transition_entries", True)
        semantics  = (
            "WILL execute enter/exit callbacks"
            if enter_flag
            else "will NOT execute enter/exit callbacks"
        )
        descriptions = [f"{sid} --[{ev}]--> {sid}" for sid, ev in self_t]
        state_ids    = [sid for sid, _ in self_t]
        return ValidationFinding(
            "WARN", "self_transitions",
            f"{len(self_t)} self-transition(s) detected "
            f"(enable_self_transition_entries={enter_flag} → {semantics}): "
            f"{'; '.join(descriptions)}",
            state_ids,
        )

    # ── Check ⑨ : Hook name audit ─────────────────────────────────────────────

    def check_hook_names(self) -> ValidationFinding:
        """
        Audit method names **declared directly on the SM class** (not inherited)
        for near-misses against the nine convention naming patterns.
        python-statemachine silently ignores methods whose names almost match a
        convention but don't (e.g., ``on_enter_nonexistent_state`` or
        ``before_typo_event``).

        Only methods that *match a convention prefix pattern* but whose suffix
        is NOT a valid state or event id produce a WARN finding.

        Note: Only ``vars(cls)`` is inspected (direct declarations), so
        inherited StateChart / StateMachine methods are never flagged.
        """
        state_ids = set(self._sm.states_map.keys())
        event_ids: set[str] = {
            e.id
            for e in getattr(self._cls, "_events", {}) or {}
        }

        # Generic suffixes always considered valid
        always_valid_suffixes = {"state", "transition"}

        patterns: list[tuple[re.Pattern[str], set[str]]] = [
            (re.compile(r"^on_enter_(.+)$"),  state_ids),
            (re.compile(r"^on_exit_(.+)$"),   state_ids),
            (re.compile(r"^on_invoke_(.+)$"), state_ids),
            (re.compile(r"^before_(.+)$"),    event_ids),
            (re.compile(r"^on_(.+)$"),        event_ids),
            (re.compile(r"^after_(.+)$"),     event_ids),
        ]
        # Well-known machine-level hooks declared by users are always valid
        always_valid_methods = {
            "prepare_event", "on_transition", "before_transition",
            "after_transition", "on_enter_state", "on_exit_state",
            "on_invoke_state",
        }

        near_misses: list[str] = []
        # Only inspect attributes declared directly on this class (not inherited).
        # Skip non-function attributes (State descriptors, class variables, etc.)
        # so that state names like `on_hold = State()` are never misidentified as
        # mistyped hooks.
        for method_name, method_obj in vars(self._cls).items():
            if not inspect.isfunction(method_obj):
                continue
            if method_name.startswith("_") or method_name in always_valid_methods:
                continue
            for pattern, valid_suffixes in patterns:
                m = pattern.match(method_name)
                if m:
                    suffix = m.group(1)
                    if suffix not in valid_suffixes and suffix not in always_valid_suffixes:
                        near_misses.append(
                            f"{method_name} (suffix '{suffix}' not a recognised id)"
                        )
                    break

        if not near_misses:
            return ValidationFinding(
                "PASS", "hook_names",
                "All convention-named hooks have valid state/event identifier suffixes",
            )
        return ValidationFinding(
            "WARN", "hook_names",
            f"{len(near_misses)} possible hook name typo(s) — "
            f"python-statemachine will silently ignore them: "
            f"{'; '.join(near_misses)}",
            [m.split(" ")[0] for m in near_misses],
        )
