"""smcheck.mermaid
================
Mermaid ``stateDiagram-v2`` export for python-statemachine ``StateChart``
subclasses.

Generates a fully-nested diagram that correctly represents:

* Compound (sequential) states with ``[*]`` initial/final pseudo-state edges
* Parallel regions separated by ``--``
* Guards rendered as ``[guard_method]`` on transition labels
* HistoryState pseudo-states rendered as a labelled node inside their compound

Usage::

    from smcheck.mermaid import to_mermaid, write_mermaid

    # Print to console
    print(to_mermaid(OrderProcessing))

    # Embed in Markdown
    print("```mermaid")
    print(to_mermaid(OrderProcessing))
    print("```")

    # Write to file
    write_mermaid(OrderProcessing, "docs/diagram.mmd")
"""
from __future__ import annotations

import inspect
import re
from collections import defaultdict
from pathlib import Path


def to_mermaid(sm_class: type, *, direction: str = "LR") -> str:
    """
    Return a ``stateDiagram-v2`` Mermaid string for *sm_class*.

    Parameters
    ----------
    sm_class
        A python-statemachine ``StateChart`` subclass (not an instance).
    direction
        Diagram direction: ``"LR"`` (left-to-right, default) or ``"TB"``
        (top-to-bottom).  Pass ``""`` to omit the directive.

    Returns
    -------
    str
        A self-contained Mermaid diagram string, suitable for a ``.mmd``
        file or a fenced ````mermaid```` block.
    """
    sm = sm_class()

    # ── 1. Walk all transitions ───────────────────────────────────────────────
    # Collect guard labels, pseudo-state targets, and per-state constraint notes.
    guard_labels:     dict[tuple[str, str, str], str] = {}
    history_parents:  dict[str, str]                  = {}   # h_id → parent_id
    raw_edges:        list[tuple[str, str, str]]       = []
    # constraint_notes: for each atomic source state of a guarded transition,
    # collect the guard method's docstring (first sentence) as a note.
    constraint_notes: dict[str, list[str]]             = {}

    seen: set[int] = set()
    for state in sm.states_map.values():
        for t in state.transitions.transitions:
            if id(t) in seen:  # pragma: no cover
                continue
            seen.add(id(t))

            ev = next((e.name for e in t.events), "?")
            if ev == "?":
                continue

            src_id = t.source.id
            dst_id = t.target.id
            raw_edges.append((src_id, ev, dst_id))

            # Pseudo-state: not in states_map → HistoryState or similar
            if dst_id not in sm.states_map:
                parent = getattr(t.target, "parent", None)
                if parent is not None:
                    history_parents[dst_id] = parent.id

            # Guard labels: cond= / unless= callback specs
            specs = list(t.cond) if t.cond else []
            if specs:
                parts: list[str] = []
                for sp in specs:
                    attr = getattr(sp, "attr_name", None) or getattr(sp, "func", None)
                    exp  = getattr(sp, "expected_value", True)
                    if attr:
                        parts.append(f"!{attr}" if not exp else attr)
                        # Collect guard docstring to annotate the source state
                        method = getattr(sm_class, attr, None)
                        if method and callable(method):
                            doc = inspect.getdoc(method) or ""
                            first = doc.split(".")[0].strip()
                            if first:
                                prefix = "NOT: " if not exp else ""
                                note   = prefix + first
                                bucket = constraint_notes.setdefault(src_id, [])
                                if note not in bucket:
                                    bucket.append(note)
                guard_labels[(src_id, ev, dst_id)] = "[" + ", ".join(parts) + "]"

    # ── 2. Scope each transition to its innermost common-ancestor compound ────
    def _parent_chain(state_id: str) -> list[str]:
        """Parent IDs from immediate parent up to root (not including state_id)."""
        # Resolve pseudo-states to their real parent compound
        effective = history_parents.get(state_id, state_id)
        s = sm.states_map.get(effective)
        if s is None:  # pragma: no cover
            return []
        chain: list[str] = []
        s = s.parent
        while s is not None:
            chain.append(s.id)
            s = s.parent
        return chain

    def _common_scope(src_id: str, dst_id: str) -> str | None:
        """Innermost compound containing both states, or None for top level."""
        src_parents = _parent_chain(src_id)
        dst_parents_set = set(_parent_chain(dst_id))
        for anc_id in src_parents:           # closest ancestor first
            if anc_id in dst_parents_set:
                return anc_id
        return None

    scoped: dict[str | None, list[tuple[str, str, str]]] = defaultdict(list)
    for src, ev, dst in raw_edges:
        scoped[_common_scope(src, dst)].append((src, ev, dst))

    # ── 3. Sorted-children helper ─────────────────────────────────────────────
    def _children(parent_id: str) -> list:
        return sorted(
            [s for s in sm.states_map.values()
             if s.parent is not None and s.parent.id == parent_id],
            key=lambda s: getattr(s, "document_order", 0),
        )

    # ── 4. Rendering ──────────────────────────────────────────────────────────
    out: list[str] = []

    def _emit_edge(src: str, ev: str, dst: str, indent: int) -> None:
        guard = guard_labels.get((src, ev, dst), "")
        label = f"{ev} {guard}".strip()
        out.append(" " * indent + f"{src} --> {dst} : {label}")

    def _render_state(state, indent: int) -> None:
        """Emit Mermaid lines for *state* and its entire subtree."""
        pad  = " " * indent
        kids = _children(state.id)
        # pseudo-state children (HistoryState) that live inside this compound
        h_kids = [hid for hid, hpar in history_parents.items() if hpar == state.id]

        # ── Atomic ───────────────────────────────────────────────────────────
        if not kids and not h_kids:
            # Always declare with display name so labels are human-readable
            out.append(f'{pad}state "{state.name}" as {state.id}')
            # Emit constraint notes derived from guard docstrings.
            # Use the multiline block syntax; the inline "note ... : text"
            # syntax is rejected by many Mermaid renderers when the text
            # contains colons or non-ASCII characters.
            notes = constraint_notes.get(state.id, [])
            if notes:
                note_text = " / ".join(notes)
                out.append(f"{pad}note right of {state.id}")
                out.append(f"{pad}    {note_text}")
                out.append(f"{pad}end note")
            return

        # ── Composite block header ────────────────────────────────────────────
        out.append(f'{pad}state "{state.name}" as {state.id} {{')
        inner = indent + 4

        # Declare HistoryState pseudo-children inside this compound
        for h_id in h_kids:
            out.append(" " * inner + f'state "[H]" as {h_id}')

        is_parallel = getattr(state, "parallel", False)

        if is_parallel:
            # ── Parallel: no single [*], render each region separated by -- ──
            for i, track in enumerate(kids):
                _render_state(track, inner)
                if i < len(kids) - 1:
                    out.append(" " * inner + "--")
            # Cross-region transitions scoped at this level (edge case)
            for src, ev, dst in sorted(scoped.get(state.id, [])):  # pragma: no cover
                _emit_edge(src, ev, dst, inner)

        else:
            # ── Compound (sequential) ─────────────────────────────────────────
            init_child = next((s for s in kids if s.initial), None)
            if init_child:
                out.append(" " * inner + f"[*] --> {init_child.id}")

            for child in kids:
                _render_state(child, inner)

            # Internal transitions scoped to this compound
            for src, ev, dst in sorted(scoped.get(state.id, [])):
                _emit_edge(src, ev, dst, inner)

            # Final pseudo-state edges for direct final children
            for child in kids:
                if child.final:
                    out.append(" " * inner + f"{child.id} --> [*]")

        out.append(f"{pad}}}")

    # ── 5. Top-level assembly ─────────────────────────────────────────────────
    # The StateChart class is itself the outermost compound state (same as the
    # root <scxml> element in SCXML).  Wrapping everything inside it ensures the
    # rendered diagram is a single connected visual piece rather than a cluster
    # of floating atomic states alongside compound blocks.
    wrapper_id    = sm_class.__name__
    wrapper_label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", wrapper_id)

    out.append("stateDiagram-v2")
    if direction:
        out.append(f"    direction {direction}")
    out.append("")

    out.append(f"    [*] --> {wrapper_id}")
    out.append("")
    out.append(f'    state "{wrapper_label}" as {wrapper_id} {{')
    out.append("")

    top_states = sorted(
        [s for s in sm.states_map.values() if s.parent is None],
        key=lambda s: getattr(s, "document_order", 0),
    )
    top_init = next((s for s in top_states if s.initial), None)
    if top_init:
        out.append(f"        [*] --> {top_init.id}")
        out.append("")

    for s in top_states:
        _render_state(s, 8)
        out.append("")

    # Top-level transitions (scoped at the machine root)
    top_edges = scoped.get(None, [])
    if top_edges:
        for src, ev, dst in sorted(top_edges):
            _emit_edge(src, ev, dst, 8)
        out.append("")

    # Internal final markers indicate that the machine has terminated
    for s in top_states:
        if s.final:
            out.append(f"        {s.id} --> [*]")

    out.append("")
    out.append("    }")
    out.append("")

    return "\n".join(out) + "\n"


def write_mermaid(
    sm_class:    type,
    output_path: str | Path = "diagram.mmd",
    **kwargs,
) -> Path:
    """
    Write the Mermaid diagram for *sm_class* to *output_path*.

    Extra keyword arguments are forwarded to :func:`to_mermaid`
    (e.g. ``direction="TB"``).

    Returns the resolved :class:`~pathlib.Path` that was written.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_mermaid(sm_class, **kwargs), encoding="utf-8")
    return path
