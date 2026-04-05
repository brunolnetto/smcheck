"""smcheck.cli
===========
Command-line interface for smcheck.

Subcommands
-----------
validate    Run the five static-validation checks and print findings.
paths       Print the graph analysis + path enumeration report.
explain     Generate plain-English explanations for all paths via LLM.
testgen     Auto-generate pytest test files and write them to disk.
all         Run validate + paths (explain and testgen need explicit flags).

Usage (module form)::

    python -m smcheck validate
    python -m smcheck paths
    python -m smcheck explain [--model gpt-4o-mini] [--output PATHS_auto.md]
    python -m smcheck testgen [--output generated_tests/] [--module main]
    python -m smcheck all

Usage (entry-point, if installed)::

    smcheck validate
    smcheck testgen --output generated_tests/
"""

from __future__ import annotations

import argparse
import importlib
import sys


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_sm_class(module_path: str, class_name: str) -> type:
    """
    Dynamically import *class_name* from *module_path*.

    Parameters
    ----------
    module_path : Dotted module path, e.g. ``"main"`` or ``"order_processing.main"``.
    class_name  : Name of the ``StateChart`` subclass, e.g. ``"OrderProcessing"``.
    """
    try:
        mod = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        sys.exit(f"[smcheck] Cannot import module '{module_path}': {exc}")
    cls = getattr(mod, class_name, None)
    if cls is None:
        sys.exit(
            f"[smcheck] Class '{class_name}' not found in module '{module_path}'.\n"
            f"          Available names: {[n for n in dir(mod) if not n.startswith('_')]}"
        )
    return cls


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_validate(args: argparse.Namespace) -> None:
    from .report import run_validation

    cls = _load_sm_class(args.module, args.class_name)
    run_validation(cls)


def _cmd_paths(args: argparse.Namespace) -> None:
    from .report import run_graph_analysis

    cls = _load_sm_class(args.module, args.class_name)
    run_graph_analysis(cls)


def _cmd_explain(args: argparse.Namespace) -> None:
    from .paths import analyze_paths
    from .explainer import explain_paths, explanations_to_markdown

    cls = _load_sm_class(args.module, args.class_name)
    print(f"[smcheck] Analysing paths for {args.class_name}…")
    analysis = analyze_paths(cls)
    top_n = len(analysis.top_level_paths)
    trk_n = sum(len(v) for v in analysis.track_paths.values())
    print(f"[smcheck] {top_n} top-level paths + {trk_n} track paths — calling LLM ({args.model})…")

    explanations = explain_paths(analysis, cls, model=args.model)
    md = explanations_to_markdown(explanations)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"[smcheck] Written to {args.output}")
    else:
        print(md)


def _cmd_testgen(args: argparse.Namespace) -> None:
    from .paths import analyze_paths
    from .testgen import generate_all, write_tests

    cls = _load_sm_class(args.module, args.class_name)
    print(f"[smcheck] Generating tests for {args.class_name}…")
    analysis = analyze_paths(cls)
    tests = generate_all(cls, analysis=analysis)

    written = write_tests(
        tests,
        sm_import=args.module,
        output_dir=args.output,
        sm_class=args.class_name,
    )
    for path in written:
        sum(1 for t in tests if path.endswith("transitions") or True)
        print(f"[smcheck] Wrote {path}")
    total = len(tests)
    trans = sum(1 for t in tests if t.level == "transition")
    paths = total - trans
    print(f"[smcheck] {total} tests generated ({trans} transition, {paths} path).")


def _cmd_all(args: argparse.Namespace) -> None:
    _cmd_paths(args)
    _cmd_validate(args)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smcheck",
        description=(
            "Static analysis, path enumeration, LLM explanation, and test "
            "generation for python-statemachine StateChart subclasses."
        ),
    )

    # Shared options added to every subcommand
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--module",
        "-m",
        default=None,
        metavar="MODULE",
        help="Dotted module path containing the StateChart subclass (default: main)",
    )
    shared.add_argument(
        "--class-name",
        "-c",
        default=None,
        dest="class_name",
        metavar="CLASS",
        help="Name of the StateChart subclass (default: OrderProcessing)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # validate
    p_val = sub.add_parser(
        "validate",
        parents=[shared],
        help="Run the five static-validation checks",
    )
    p_val.set_defaults(func=_cmd_validate)

    # paths
    p_paths = sub.add_parser(
        "paths",
        parents=[shared],
        help="Print the graph analysis and path enumeration report",
    )
    p_paths.set_defaults(func=_cmd_paths)

    # explain
    p_exp = sub.add_parser(
        "explain",
        parents=[shared],
        help="Generate plain-English path explanations via LLM",
    )
    p_exp.add_argument(
        "--model",
        default="gpt-4o-mini",
        metavar="MODEL",
        help="LLM model name — gpt-* or claude-* (default: gpt-4o-mini)",
    )
    p_exp.add_argument(
        "--output",
        "-o",
        default=None,
        metavar="FILE",
        help="Write Markdown output to FILE instead of stdout",
    )
    p_exp.set_defaults(func=_cmd_explain)

    # testgen
    p_tg = sub.add_parser(
        "testgen",
        parents=[shared],
        help="Auto-generate pytest test files",
    )
    p_tg.add_argument(
        "--output",
        "-o",
        default="generated_tests",
        metavar="DIR",
        help="Output directory for generated test files (default: generated_tests)",
    )
    p_tg.set_defaults(func=_cmd_testgen)

    # all
    p_all = sub.add_parser(
        "all",
        parents=[shared],
        help="Run paths + validate (equivalent to paths && validate)",
    )
    p_all.set_defaults(func=_cmd_all)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":  # pragma: no cover
    main()
