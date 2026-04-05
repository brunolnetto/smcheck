"""smcheck
========
Static analysis, path enumeration, LLM-powered explanation, and automatic
test generation for python-statemachine ``StateChart`` subclasses.

Usage
-----
::

    from smcheck import SMCheck

    sm = SMCheck(OrderProcessing)

    sm.report_graph()          # print graph-structure summary
    sm.report_validation()     # print 9 static checks

    analysis = sm.analyze_paths()          # PathAnalysis
    findings = sm.validate().findings      # list[ValidationFinding]

    sm.write_tests("machine", "generated_tests/")
    sm.write_mermaid("diagram.mmd")

Data types (for type annotations)::

    from smcheck import ValidationFinding, PathAnalysis, SMPath, PathEdge

LLM explanation (optional dependency)::

    from smcheck.explainer import explain_paths, explanations_to_markdown
"""

from __future__ import annotations

from pathlib import Path as _Path

from .graph import extract_sm_graph as _extract_sm_graph, top_level_graph as _top_level_graph
from .validator import SMValidator, ValidationFinding
from .paths import analyze_paths as _analyze_paths, PathAnalysis, SMPath, PathEdge
from .report import run_graph_analysis as _run_graph, run_validation as _run_validation
from .testgen import generate_all as _generate_all, write_tests as _write_tests
from .mermaid import to_mermaid as _to_mermaid, write_mermaid as _write_mermaid


class SMCheck:
    """Single entry point for all smcheck analysis operations.

    Parameters
    ----------
    sm_class:
        A ``StateChart`` (or ``StateMachine``) subclass to analyse.
    """

    def __init__(self, sm_class: type) -> None:
        self._cls = sm_class

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> SMValidator:
        """Return a populated :class:`~smcheck.validator.SMValidator`."""
        return SMValidator(self._cls)

    # ------------------------------------------------------------------
    # Graph
    # ------------------------------------------------------------------

    def graph(self) -> dict:
        """Return the full adjacency map (all transitions, all states)."""
        return _extract_sm_graph(self._cls)

    def top_level_graph(self) -> dict:
        """Return the top-level adjacency map (compound states collapsed)."""
        return _top_level_graph(self._cls)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def analyze_paths(self) -> PathAnalysis:
        """Enumerate all paths and return a :class:`~smcheck.paths.PathAnalysis`."""
        return _analyze_paths(self._cls)

    # ------------------------------------------------------------------
    # Console reports (side-effectful)
    # ------------------------------------------------------------------

    def report_graph(self) -> None:
        """Print the graph-structure report to stdout."""
        _run_graph(self._cls)

    def report_validation(self) -> None:
        """Print the 9-check validation report to stdout."""
        _run_validation(self._cls)

    # ------------------------------------------------------------------
    # Test generation
    # ------------------------------------------------------------------

    def generate_tests(self, **kwargs) -> list:
        """Generate all test cases.

        Keyword arguments are forwarded to
        :func:`~smcheck.testgen.generate_all` (e.g. ``guard_setup_map``).
        """
        return _generate_all(self._cls, **kwargs)

    def write_tests(
        self,
        sm_import: str,
        output_dir: str,
        class_name: str | None = None,
        **kwargs,
    ) -> list[str]:
        """Generate and write ``test_transitions.py`` + ``test_paths.py``.

        Parameters
        ----------
        sm_import:
            Module path used in the generated ``import`` statement
            (e.g. ``"machine"``).
        output_dir:
            Directory to write the generated test files into.
        class_name:
            Class name used in the generated file header.
            Defaults to ``sm_class.__name__``.

        Returns the list of written file paths.
        """
        tests = _generate_all(self._cls, **kwargs)
        return _write_tests(
            tests,
            sm_import,
            output_dir,
            class_name or self._cls.__name__,
        )

    # ------------------------------------------------------------------
    # Diagram export
    # ------------------------------------------------------------------

    def to_mermaid(self, *, direction: str = "LR") -> str:
        """Return the Mermaid ``stateDiagram-v2`` source as a string."""
        return _to_mermaid(self._cls, direction=direction)

    def write_mermaid(
        self,
        output_path: str | _Path = "diagram.mmd",
        **kwargs,
    ) -> _Path:
        """Write the Mermaid diagram to *output_path* and return the resolved path."""
        return _write_mermaid(self._cls, output_path, **kwargs)


__all__ = [
    # facade
    "SMCheck",
    # data types (for type annotations / isinstance checks)
    "ValidationFinding",
    "PathAnalysis",
    "SMPath",
    "PathEdge",
]
