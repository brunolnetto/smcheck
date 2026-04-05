"""analysis.py
=============
smcheck analysis pipeline for the ``order-processing`` example.

Each public function wraps one smcheck concern:

    run_graph(sm_class)                          → graph analysis (structure, back-edges)
    run_validation(sm_class)                     → 9 static checks
    run_paths(sm_class)                          → path analysis, returns PathAnalysis
    run_testgen(sm_class, analysis)              → test generation, writes generated_tests/
    run_mermaid(sm_class)                        → Mermaid diagram, writes diagram.mmd
    run_explanations(sm_class, analysis, *,      → LLM explanation, writes PATHS_auto.md
                     dry_run=False)                (dry_run=True prints token estimate only)
    run_business_rules(sm_class)                 → LLM business rules coherence check

    run_smcheck(sm_class, *, dry_run=False)      → calls all six in order
"""
from __future__ import annotations

from smcheck.report  import run_graph_analysis, run_validation as _validate
from smcheck.paths   import analyze_paths, PathAnalysis
from smcheck.testgen import generate_all, write_tests
from smcheck.mermaid import write_mermaid

from sim            import _banner


def run_graph(sm_class) -> None:
    """Print the graph analysis report (structure, back-edges, path counts)."""
    run_graph_analysis(sm_class)


def run_validation(sm_class) -> None:
    """Run the 9 static validation checks and print results."""
    _validate(sm_class)


def run_paths(sm_class) -> PathAnalysis:
    """Print the path analysis summary and return the PathAnalysis object."""
    _banner("Path Analysis")
    analysis = analyze_paths(sm_class)
    print(f"  Top-level paths : {len(analysis.top_level_paths)}")
    for track, paths in analysis.track_paths.items():
        print(f"  Track [{track}] paths : {len(paths)}")
    print(f"  Combined paths  : {analysis.combined_count}")
    print(f"  (bypass={analysis.bypass_count}, fulfillment={analysis.fulfillment_count})")
    return analysis


def run_testgen(sm_class, analysis: PathAnalysis) -> None:
    """Generate tests and write them to generated_tests/."""
    _banner("Test Generation")
    tests = generate_all(
        sm_class,
        analysis = analysis,
    )
    t_count     = sum(1 for t in tests if t.level == "transition")
    top_count   = sum(1 for t in tests if t.level == "path_top")
    track_count = sum(1 for t in tests if t.level == "path_track")
    print(f"  Transition tests    : {t_count}")
    print(f"  Top-level path tests: {top_count}")
    print(f"  Track path tests    : {track_count}")
    written = write_tests(
        tests,
        sm_import  = "machine",
        output_dir = "generated_tests",
        sm_class   = sm_class.__name__,
    )
    for p in written:
        print(f"  Written: {p}")


def run_mermaid(sm_class) -> None:
    """Write a Mermaid stateDiagram-v2 to ``diagram.mmd``."""
    _banner("Mermaid Export")
    path = write_mermaid(sm_class, "diagram.mmd")
    print(f"  Written: {path}")


def run_explanations(sm_class, analysis: PathAnalysis, *, dry_run: bool = False) -> None:
    """
    Write LLM path explanations to PATHS_auto.md.

    Uses a **hierarchical** strategy: one LLM call for the top-level order
    journey, then one call per parallel track (inventory, payment, shipping).
    Each call is scoped to its own abstraction level so the LLM does not mix
    customer-journey context with warehouse-operations context.

    Parameters
    ----------
    dry_run : When ``True``, print a per-level token-and-cost estimate and
              return without making any API call.
    """
    from smcheck.explainer import (
        PathExplanation, estimate_tokens, explain_paths, explanations_to_markdown,
    )

    _banner("LLM Path Explanation")

    # Human-readable scope label injected into each prompt
    _LEVEL_CONTEXT = {
        "top":       "the top-level order flow (customer journey from idle to a terminal state)",
        "inventory": "the **inventory** track — warehouse stock checking, reservation, and allocation",
        "payment":   "the **payment** track — payment hold, processing, and authorization",
        "shipping":  "the **shipping** track — shipment preparation and delivery",
    }

    levels: list[tuple[str, list]] = [
        ("top", list(analysis.top_level_paths)),
        *[(track, list(paths)) for track, paths in analysis.track_paths.items()],
    ]

    if dry_run:
        tok_flag   = None  # set on first call
        total_tok  = 0
        total_cost = 0.0
        for label, lvl_paths in levels:
            est = estimate_tokens(analysis, sm_class, paths=lvl_paths)
            if tok_flag is None:
                tok_flag = "(tiktoken)" if est["tiktoken_available"] else "(heuristic: chars \u00f7 4)"
            n   = est["num_paths"]
            tok = est["estimated_total_tokens"]
            usd = est["estimated_cost_usd"]
            total_tok  += tok
            total_cost += usd
            print(f"  [{label:<9}]  {n:2d} paths  ~{tok:>5,} tokens  ${usd:.4f}")
        print(f"  {'-' * 44}")
        print(f"  {'TOTAL':<11}  {sum(len(p) for _, p in levels):2d} paths  ~{total_tok:>5,} tokens  ${total_cost:.4f}  {tok_flag}")
        return

    try:
        all_explanations: list[PathExplanation] = []
        for label, lvl_paths in levels:
            if not lvl_paths:
                continue
            ctx = _LEVEL_CONTEXT.get(label, f"the **{label}** track")
            print(f"    [{label}] explaining {len(lvl_paths)} path(s) ...")
            all_explanations += explain_paths(
                analysis, sm_class,
                paths=lvl_paths,
                level_context=ctx,
            )
        md = explanations_to_markdown(all_explanations)
        out_path = "PATHS_auto.md"
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"  Explanations written to {out_path}")
    except Exception as exc:
        print(f"  [skipped] LLM explanation unavailable: {exc}")


def run_business_rules(sm_class) -> None:
    """
    Check the machine implementation against a plain-text specification
    of business rules using an LLM.

    This demonstrates how business rules can be specified in one place
    (e.g., a README section) and then verified against the state machine
    structure for coherence and completeness.
    """
    from smcheck.explainer import check_business_rules, rules_check_to_markdown

    _banner("Business Rules Coherence Check")

    # Example business rules (in production, these would be sourced from your README,
    # PRD, or specification document—see run_business_rules() best practice.)
    business_rules = """\
Orders must be validated before any payment is attempted.

Payment can only proceed once inventory is confirmed as reserved.

Cancelled orders cannot be resumed or reactivated.

Operations staff may place a confirmed order on hold at any point during fulfilment.

Partial fulfilment is allowed when only some items are in stock.
"""

    try:
        result = check_business_rules(business_rules, sm_class)

        # Print summary
        print(f"\n  Summary: {result.summary}\n")

        # Print violations (if any)
        if result.violations:
            print(f"  🚨 Violations ({len(result.violations)}):")
            for v in result.violations:
                print(f"    [{v.rule}] {v.detail}")
                if v.suggestion:
                    print(f"      → {v.suggestion}")
            print()

        # Print recommendations (if any)
        if result.recommendations:
            print(f"  💡 Recommendations ({len(result.recommendations)}):")
            for r in result.recommendations:
                print(f"    [{r.rule}] {r.detail}")
                if r.suggestion:
                    print(f"      → {r.suggestion}")
            print()

        # Print OK rules
        if result.ok:
            print(f"  ✅ Confirmed rules ({len(result.ok)}):")
            for o in result.ok:
                print(f"    [{o.rule}]")
            print()

        # Export to markdown
        md = rules_check_to_markdown(result)
        out_path = "RULES_CHECK.md"
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"  Report written to {out_path}\n")

    except Exception as exc:
        print(f"  [skipped] Business rules check unavailable: {exc}\n")


def run_smcheck(sm_class, *, dry_run: bool = False) -> None:
    """Run the full smcheck pipeline: graph → validation → paths → testgen → mermaid → LLM.

    Parameters
    ----------
    dry_run : Forwarded to :func:`run_explanations`.  When ``True``, the LLM
              step prints a token/cost estimate instead of calling the API.
    """
    run_graph(sm_class)
    run_validation(sm_class)
    analysis = run_paths(sm_class)
    run_testgen(sm_class, analysis)
    run_mermaid(sm_class)
    run_explanations(sm_class, analysis, dry_run=dry_run)
