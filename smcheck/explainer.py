"""smcheck.explainer
=================
LangGraph-powered path explanation.

Architecture
------------
* Pure Python (:mod:`smcheck.paths`) enumerates paths and collects metadata.
* The LLM is used exclusively as a *writer* — it receives fully-structured
  data (SM docstring, hook descriptions, edge sequences, guards) and returns
  natural-language explanations.  No tool use, no agent loop.
* All paths are batched into a **single** LLM call with structured output
  (``list[PathExplanationOutput]``) to keep latency and cost low.

Usage::

    from smcheck.explainer import explain_paths, explanations_to_markdown
    from smcheck.paths import analyze_paths
    from your_module import YourMachine

    analysis = analyze_paths(YourMachine)
    explanations = explain_paths(analysis, YourMachine)
    print(explanations_to_markdown(explanations))

Environment
-----------
Set ``OPENAI_API_KEY`` (or ``ANTHROPIC_API_KEY`` and pass ``model="claude-…"``)
before calling :func:`explain_paths`.
"""

from __future__ import annotations

import inspect
import re
import textwrap
from dataclasses import dataclass
from typing import Any

from .paths import PathAnalysis, SMPath


# ---------------------------------------------------------------------------
# Output data classes
# ---------------------------------------------------------------------------


@dataclass
class PathExplanation:
    """
    LLM-generated plain-English explanation for a single :class:`SMPath`.

    Attributes
    ----------
    path             : The path being explained.
    summary          : One-sentence headline.
    step_descriptions: One sentence per edge explaining what that transition
                       means in business terms.
    business_meaning : One paragraph describing the end-to-end business
                       scenario this path represents.
    """

    path: SMPath
    summary: str
    step_descriptions: list[str]
    business_meaning: str


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


def _sm_metadata(sm_class: type) -> dict[str, Any]:
    """
    Extract rich metadata from a StateChart subclass.

    Returns a dict with:
      ``docstring``      — class-level docstring.
      ``hooks``          — dict[hook_name → __doc__] (kept for backward compat).
      ``state_meanings`` — dict[state_id → human message] extracted from
                           ``_p("...")`` calls inside ``on_enter_*`` hooks.
      ``guard_docs``     — dict[guard_name → docstring] for guard methods.
      ``history_states`` — dict[state_id → description] for HistoryState attrs.
    """
    docstring = textwrap.dedent(sm_class.__doc__ or "").strip()

    # Legacy: hook docstrings (backward-compat with existing tests)
    hooks: dict[str, str] = {}
    for name in dir(sm_class):
        if name.startswith("on_enter_"):
            method = getattr(sm_class, name, None)
            hooks[name] = (getattr(method, "__doc__", None) or "").strip()

    # State meanings: parse _p("...") from on_enter_* hook source
    state_meanings: dict[str, str] = {}
    for name in dir(sm_class):
        if not name.startswith("on_enter_"):
            continue
        method = getattr(sm_class, name, None)
        if not callable(method):
            continue
        try:
            src = inspect.getsource(method)
            found = re.findall(r'_p\(["\'](.+?)["\']\)', src)
            if found:
                state = name[len("on_enter_") :]
                # Strip icon/bracket prefix like "  [Inventory]   ✔ " for clarity
                msg = re.sub(r"^\s*(\[\w+\]\s*)?[\u2000-\uffff]*\s*", "", found[0]).strip()
                state_meanings[state] = msg or found[0].strip()
        except (OSError, TypeError):
            pass

    # Guard docstrings
    guard_docs: dict[str, str] = {}
    for name in dir(sm_class):
        if name.startswith("_"):
            continue
        method = getattr(sm_class, name, None)
        if callable(method):
            doc = (getattr(method, "__doc__", None) or "").strip()
            if doc and len(doc) < 300 and not name[0].isupper():
                guard_docs[name] = textwrap.dedent(doc).strip()

    # HistoryState detection (scan nested classes recursively)
    history_states: dict[str, str] = {}
    try:
        from statemachine import HistoryState as _HS

        def _scan(cls: type) -> None:
            for attr, val in vars(cls).items():
                if isinstance(val, _HS):
                    history_states[attr] = (
                        f"HistoryState inside {cls.__name__} — "
                        "when the machine re-enters this compound state it restores "
                        "whichever sub-state was active when the machine last left it"
                    )
                elif isinstance(val, type):
                    _scan(val)

        _scan(sm_class)
    except ImportError:
        pass

    return {
        "docstring": docstring,
        "hooks": hooks,  # legacy key — kept for existing tests
        "state_meanings": state_meanings,
        "guard_docs": guard_docs,
        "history_states": history_states,
    }


def _format_path_for_prompt(
    path: SMPath,
    index: int,
    *,
    state_meanings: dict[str, str] | None = None,
    guard_docs: dict[str, str] | None = None,
) -> str:
    """
    Format a single path for inclusion in the LLM prompt.

    Optional *state_meanings* and *guard_docs* annotate each edge with
    the business meaning of the entered state and the guard docstring.
    """
    sm = state_meanings or {}
    gd = guard_docs or {}
    lines = [f"Path {index + 1} (level={path.level}, terminal={path.terminal}):"]
    for edge in path.edges:
        guard_str = ""
        if edge.guard:
            guard_name = edge.guard.lstrip("!")
            gdoc = gd.get(guard_name, "")
            guard_str = f" [guard: {edge.guard}" + (f" — {gdoc}" if gdoc else "") + "]"
        loop_str = " ← LOOP (back-edge)" if edge.is_back_edge else ""
        lines.append(f"  {edge.source} --[{edge.event}]{guard_str}--> {edge.target}{loop_str}")
        meaning = sm.get(edge.target, "")
        if meaning:
            lines.append(f"    (entering {edge.target!r}: {meaning})")
    return "\n".join(lines)


def _build_prompt(
    analysis: PathAnalysis,
    sm_class: type,
    paths: list[SMPath],
    *,
    level_context: str = "",
) -> str:
    meta = _sm_metadata(sm_class)
    state_meanings = meta["state_meanings"]
    guard_docs = meta["guard_docs"]
    history_states = meta["history_states"]

    if level_context:
        sections: list[str] = [
            f"You are a technical writer documenting `{sm_class.__name__}` for an e-commerce product team.",
            f"You are specifically explaining {level_context}.",
            "Your audience is business analysts and engineers who understand order management,",
            "warehouse operations, payments, and shipping — but do NOT read Python code.",
            "",
        ]
    else:
        sections = [
            f"You are a technical writer documenting the `{sm_class.__name__}` state machine",
            "for an e-commerce product team.  Your audience is business analysts and engineers",
            "who understand order management, warehouse operations, payments, and shipping —",
            "but do NOT read Python code.",
            "",
        ]

    sections += [
        f"## About `{sm_class.__name__}`",
        "",
        meta["docstring"] or "(no docstring available)",
        "",
    ]

    if state_meanings:
        sections += [
            "## State semantic guide",
            "(What entering each state means in the real world)",
            "",
        ]
        for state, meaning in sorted(state_meanings.items()):
            if meaning:
                sections.append(f"  {state:<22} \u2192 {meaning}")
        sections.append("")

    if guard_docs:
        sections += [
            "## Guard descriptions",
            "(A transition fires only when its guard evaluates to True)",
            "",
        ]
        for name, doc in sorted(guard_docs.items()):
            sections.append(f"  {name:<28} \u2192 {doc}")
        sections.append("")

    if history_states:
        sections += ["## Special states", ""]
        for name, desc in history_states.items():
            sections.append(f"  `{name}` \u2014 {desc}")
        sections.append("")

    sections += [
        "## Task",
        "",
        "For EACH numbered path below, return a JSON object with EXACTLY these three keys:",
        "",
        '  "summary":',
        "    One sentence naming WHO is involved and WHAT the final outcome is.",
        '    Format: "<Actor> <action>; <consequence for customer/ops>."',
        '    Good: "Customer cancels approved order during shipping prep; warehouse holds released."',
        '    Bad:  "Order goes from idle to cancelled."',
        "",
        '  "step_descriptions":',
        "    A JSON array with EXACTLY one entry per edge (same count as edges in this path).",
        "    Each entry must explain the BUSINESS significance of that transition:",
        "      \u2014 WHO triggered it (customer, payment gateway, warehouse, carrier, ops team)",
        "      \u2014 WHY it happened and what pre-conditions were met",
        "      \u2014 What side-effect it caused (flag set, system called, hold placed/released)",
        "    Reference ordering constraints \u2460\u2461\u2462 when relevant.",
        "    DO NOT just restate the state/event name in different words.",
        "",
        '  "business_meaning":',
        "    A 2\u20133 sentence paragraph telling the FULL STORY of this execution path.",
        "    Cover: who initiated the flow, which checks or external systems were involved,",
        "    what failed or succeeded, and what the final state means for the customer",
        "    (notification? charge? refund?) and for the operations team (action needed?).",
        "    Write as if explaining to a product manager during a sprint review.",
        "",
        "Respond ONLY with a valid JSON ARRAY.  One element per path, in the SAME ORDER.",
        "No markdown fences, no extra keys, no commentary outside the JSON array.",
        "",
        "## Paths to explain",
        "",
    ]

    for i, p in enumerate(paths):
        sections.append(
            _format_path_for_prompt(p, i, state_meanings=state_meanings, guard_docs=guard_docs)
        )
        sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Token estimation helpers
# ---------------------------------------------------------------------------

# Approximate pricing table: (input $/1M tokens, output $/1M tokens)
# Prices are indicative — update as provider pricing changes.
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "o3-mini": (1.10, 4.40),
    "o3": (10.00, 40.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-7-sonnet-20250219": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
}

_OUTPUT_TOKENS_PER_PATH = 250  # conservative per-explanation estimate


def _count_tokens(text: str, model: str) -> tuple[int, bool]:
    """
    Count tokens in *text*.

    Uses ``tiktoken`` when available; falls back to ``len(text) // 4``.

    Returns
    -------
    (token_count, tiktoken_was_used)
    """
    try:
        import tiktoken

        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            # Anthropic / unknown model — GPT-4 tokeniser is a good approximation
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text)), True
    except ImportError:
        return len(text) // 4, False


def estimate_tokens(
    analysis: PathAnalysis,
    sm_class: type,
    model: str = "gpt-4o-mini",
    max_paths: int = 50,
    *,
    paths: list[SMPath] | None = None,
) -> dict[str, Any]:
    """
    Estimate LLM token usage and cost **without** making any API call.

    Useful for pre-flight budget checks when *analysis* has many paths.

    Parameters
    ----------
    analysis  : Result of :func:`smcheck.paths.analyze_paths`.
    sm_class  : The ``StateChart`` subclass (used for prompt building).
    model     : LLM model name — controls the cost-table lookup.
    max_paths : Same cap used by :func:`explain_paths` when *paths* is ``None``.
    paths     : Explicit list of paths to estimate for.  When supplied,
                *max_paths* is ignored.  Pass ``analysis.top_level_paths`` or
                a single-track list for per-level estimates.

    Returns
    -------
    dict with keys:
        ``model``, ``num_paths``, ``prompt_chars``,
        ``estimated_input_tokens``, ``tiktoken_available``,
        ``estimated_output_tokens``, ``estimated_total_tokens``,
        ``estimated_cost_usd``
    """
    if paths is not None:
        all_paths: list[SMPath] = list(paths)
    else:
        all_paths = list(analysis.top_level_paths)
        for track_list in analysis.track_paths.values():
            all_paths.extend(track_list)
        all_paths = all_paths[:max_paths]

    prompt = _build_prompt(analysis, sm_class, all_paths)
    input_tokens, tiktoken_ok = _count_tokens(prompt, model)
    output_tokens = len(all_paths) * _OUTPUT_TOKENS_PER_PATH

    cost_in, cost_out = _MODEL_COSTS.get(model, (2.50, 10.00))
    cost_usd = (input_tokens * cost_in + output_tokens * cost_out) / 1_000_000

    return {
        "model": model,
        "num_paths": len(all_paths),
        "prompt_chars": len(prompt),
        "estimated_input_tokens": input_tokens,
        "tiktoken_available": tiktoken_ok,
        "estimated_output_tokens": output_tokens,
        "estimated_total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": round(cost_usd, 6),
    }


# ---------------------------------------------------------------------------
# LangGraph graph definition
# ---------------------------------------------------------------------------


def _build_langgraph(model: str) -> Any:
    """
    Build a minimal single-node LangGraph graph.

    The graph has one node (``explain``) that receives the prompt, calls the
    LLM with JSON-mode output, and returns the raw JSON string.
    """
    try:
        from langgraph.graph import StateGraph, END
        from langchain_core.messages import HumanMessage
    except ImportError as exc:
        raise ImportError(
            "smcheck.explainer requires langgraph and langchain-core. "
            "Install with: pip install langgraph langchain-openai"
        ) from exc

    # Dynamically select provider from model name prefix
    if model.startswith("gpt") or model.startswith("o1") or model.startswith("o3"):
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=model, temperature=0)
    elif model.startswith("claude"):
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(model=model, temperature=0)
    else:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=model, temperature=0)

    from typing import TypedDict

    class ExplainerState(TypedDict):
        prompt: str
        response: str

    def explain_node(state: ExplainerState) -> ExplainerState:
        msg = HumanMessage(content=state["prompt"])
        result = llm.invoke([msg])
        return {"prompt": state["prompt"], "response": result.content}

    builder = StateGraph(ExplainerState)
    builder.add_node("explain", explain_node)
    builder.set_entry_point("explain")
    builder.add_edge("explain", END)
    return builder.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def explain_paths(
    analysis: PathAnalysis,
    sm_class: type,
    model: str = "gpt-4o-mini",
    max_paths: int = 50,
    *,
    paths: list[SMPath] | None = None,
    level_context: str = "",
) -> list[PathExplanation]:
    """
    Ask an LLM to explain every path in *analysis* in plain English.

    Parameters
    ----------
    analysis      : Result of :func:`smcheck.paths.analyze_paths`.
    sm_class      : The ``StateChart`` subclass (used for metadata extraction).
    model         : LLM model name.  Prefix determines provider:
                    ``gpt-*`` / ``o1-*`` → OpenAI, ``claude-*`` → Anthropic.
    max_paths     : Cap on paths when *paths* is ``None``.
                    Top-level paths are preferred; track paths fill remaining slots.
    paths         : Explicit list of paths to explain.  When supplied,
                    *max_paths* is ignored and no internal collection happens.
                    Use this for level-by-level (hierarchical) explanation.
    level_context : Short phrase describing the scope of this call, e.g.
                    ``"the top-level customer order journey"`` or
                    ``"the inventory track"``.  Injected into the opening
                    sentence of the prompt to anchor the LLM to the right
                    abstraction level.

    Returns
    -------
    list[PathExplanation]
        One :class:`PathExplanation` per path, in the same order as the prompt.
    """
    import json

    if paths is not None:
        all_paths: list[SMPath] = list(paths)
    else:
        # Collect paths to explain (top-level first, then track paths)
        all_paths = list(analysis.top_level_paths)
        for track_list in analysis.track_paths.values():
            all_paths.extend(track_list)
        all_paths = all_paths[:max_paths]

    prompt = _build_prompt(analysis, sm_class, all_paths, level_context=level_context)
    graph = _build_langgraph(model)
    result = graph.invoke({"prompt": prompt, "response": ""})

    raw = result["response"]
    # Strip markdown code fences if the model wraps JSON in ```json … ```
    if raw.strip().startswith("```"):
        raw = "\n".join(line for line in raw.splitlines() if not line.strip().startswith("```"))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned non-JSON output.  First 300 chars:\n{raw[:300]}") from exc

    explanations: list[PathExplanation] = []
    for path, item in zip(all_paths, data):
        explanations.append(
            PathExplanation(
                path=path,
                summary=item.get("summary", ""),
                step_descriptions=item.get("step_descriptions", []),
                business_meaning=item.get("business_meaning", ""),
            )
        )
    return explanations


def explanations_to_markdown(explanations: list[PathExplanation]) -> str:
    """
    Render a list of :class:`PathExplanation` objects to a Markdown document.

    The output format mirrors the hand-written PATHS.md produced earlier.
    """
    lines: list[str] = [
        "# State Machine — Execution Path Explanations",
        "",
        f"_Auto-generated by `smcheck.explainer` — {len(explanations)} path(s) explained._",
        "",
    ]

    # Group by level
    top_exps = [e for e in explanations if e.path.level == "top"]
    track_groups: dict[str, list[PathExplanation]] = {}
    for exp in explanations:
        if exp.path.level != "top":
            track_groups.setdefault(exp.path.level, []).append(exp)

    if top_exps:
        lines += ["## Top-level paths", ""]
        for i, exp in enumerate(top_exps, 1):
            tag = "↺ " if exp.path.is_looping else ""
            lines += [
                f"### TL{i} · {tag}{exp.summary}",
                "",
                "```",
                " → ".join(exp.path.nodes),
                "```",
                "",
            ]
            for j, (edge, desc) in enumerate(zip(exp.path.edges, exp.step_descriptions), 1):
                guard = f" _(guard: {edge.guard})_" if edge.guard else ""
                lines.append(f"{j}. **`{edge.event}`**{guard} — {desc}")
            lines += [
                "",
                f"> {exp.business_meaning}",
                "",
                "---",
                "",
            ]

    for track, exps in track_groups.items():
        lines += [f"## Track: {track}", ""]
        for i, exp in enumerate(exps, 1):
            lines += [
                f"### {track.upper()[0]}{i} · {exp.summary}",
                "",
                "```",
                " → ".join(exp.path.nodes),
                "```",
                "",
            ]
            for j, (edge, desc) in enumerate(zip(exp.path.edges, exp.step_descriptions), 1):
                guard = f" _(guard: {edge.guard})_" if edge.guard else ""
                lines.append(f"{j}. **`{edge.event}`**{guard} — {desc}")
            lines += ["", f"> {exp.business_meaning}", "", "---", ""]

    return "\n".join(lines)
