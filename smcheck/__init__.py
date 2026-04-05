"""smcheck
========
Static analysis, path enumeration, LLM-powered explanation, and automatic
test generation for python-statemachine ``StateChart`` subclasses.

Public API
----------
**Graph extraction**::

    from smcheck.graph import extract_sm_graph, top_level_graph, track_graph
    from smcheck.graph import find_back_edges, enumerate_paths, count_paths_with_loops

**Validation**::

    from smcheck.validator import SMValidator, ValidationFinding

**Rich paths**::

    from smcheck.paths import analyze_paths, PathAnalysis, SMPath, PathEdge

**LLM explanation**::

    from smcheck.explainer import explain_paths, explanations_to_markdown

**Test generation**::

    from smcheck.testgen import generate_all, generate_validator_tests, write_tests, render_pytest

**Mermaid export**::

    from smcheck.mermaid import to_mermaid, write_mermaid

**Console reports**::

    from smcheck.report import run_graph_analysis, run_validation

Convenience re-exports (most common symbols)::

    from smcheck import analyze_paths, SMValidator, run_graph_analysis, run_validation
"""
from __future__ import annotations

from .graph import (
    AdjMap,
    derive_compound_traversal,
    derive_guard_setup_map,
    extract_sm_graph,
    top_level_graph,
    track_graph,
    find_back_edges,
    enumerate_paths,
    count_paths_with_loops,
    discover_parallel_tracks,
    discover_invoke_states,
    discover_self_transitions,
    extract_transition_actions,
)
from .validator import SMValidator, ValidationFinding
from .paths import (
    analyze_paths,
    PathAnalysis,
    SMPath,
    PathEdge,
    path_to_event_sequence,
    _build_guard_map,
    _build_transition_meta_map,
)
from .report import run_graph_analysis, run_validation
from .testgen import generate_all, generate_validator_tests, ValidatorErrorMap
from .mermaid import to_mermaid, write_mermaid

__all__ = [
    # graph
    "AdjMap",
    "derive_compound_traversal",
    "derive_guard_setup_map",
    "extract_sm_graph",
    "top_level_graph",
    "track_graph",
    "find_back_edges",
    "enumerate_paths",
    "count_paths_with_loops",
    "discover_parallel_tracks",
    "discover_invoke_states",
    "discover_self_transitions",
    "extract_transition_actions",
    # validator
    "SMValidator",
    "ValidationFinding",
    # paths
    "analyze_paths",
    "PathAnalysis",
    "SMPath",
    "PathEdge",
    "path_to_event_sequence",
    "_build_guard_map",
    "_build_transition_meta_map",
    # report
    "run_graph_analysis",
    "run_validation",
    # testgen
    "generate_all",
    "generate_validator_tests",
    "ValidatorErrorMap",
    # mermaid
    "to_mermaid",
    "write_mermaid",
]
