"""
tests/test_report.py
====================
Unit tests for smcheck.report — run_graph_analysis and run_validation.

All tests capture stdout with capsys; they exercise the report module
independently of OrderProcessing so smcheck is testable as a standalone
library.
"""
from __future__ import annotations

from smcheck.report import run_graph_analysis, run_validation


# ---------------------------------------------------------------------------
# run_graph_analysis
# ---------------------------------------------------------------------------

class TestRunGraphAnalysisLinear:
    """Smoke-test: run_graph_analysis on LinearSM must print a non-empty report."""

    def test_prints_banner(self, linear_sm, capsys):
        run_graph_analysis(linear_sm)
        out = capsys.readouterr().out
        assert "Graph Analysis" in out

    def test_prints_full_graph_section(self, linear_sm, capsys):
        run_graph_analysis(linear_sm)
        out = capsys.readouterr().out
        assert "Full transition graph" in out

    def test_prints_top_level_initial(self, linear_sm, capsys):
        run_graph_analysis(linear_sm)
        out = capsys.readouterr().out
        assert "Top-level initial" in out

    def test_prints_path_counts(self, linear_sm, capsys):
        run_graph_analysis(linear_sm)
        out = capsys.readouterr().out
        assert "Simple paths" in out
        assert "Total" in out

    def test_prints_known_transitions(self, linear_sm, capsys):
        run_graph_analysis(linear_sm)
        out = capsys.readouterr().out
        assert "go" in out
        assert "done" in out

    def test_linear_has_no_back_edges(self, linear_sm, capsys):
        run_graph_analysis(linear_sm)
        out = capsys.readouterr().out
        assert "back-edges (loops): 0" in out

    def test_prints_combined_analysis(self, linear_sm, capsys):
        run_graph_analysis(linear_sm)
        out = capsys.readouterr().out
        assert "TOTAL unique execution paths" in out


class TestRunGraphAnalysisBranch:
    def test_two_terminals_present(self, branch_sm, capsys):
        run_graph_analysis(branch_sm)
        out = capsys.readouterr().out
        # Both terminals (c, d) should appear in the output
        assert "c" in out
        assert "d" in out

    def test_path_count_is_two(self, branch_sm, capsys):
        run_graph_analysis(branch_sm)
        out = capsys.readouterr().out
        assert "Total                    : 2" in out


class TestRunGraphAnalysisLoop:
    def test_detects_back_edge(self, loop_sm, capsys):
        run_graph_analysis(loop_sm)
        out = capsys.readouterr().out
        assert "loop" in out     # event name
        assert "<- loop" in out  # marker

    def test_loop_paths_count_positive(self, loop_sm, capsys):
        run_graph_analysis(loop_sm)
        out = capsys.readouterr().out
        # The top-level section prints "Paths with >=1 loop      : N"
        # ("with_loops" only appears in the per-track section, which LoopSM
        # does not have — it has no parallel region)
        assert "Paths with >=1 loop" in out


class TestRunGraphAnalysisParallel:
    def test_prints_track_sections(self, mini_parallel_sm, capsys):
        run_graph_analysis(mini_parallel_sm)
        out = capsys.readouterr().out
        assert "Track [track_a]" in out
        assert "Track [track_b]" in out

    def test_prints_track_combinations(self, mini_parallel_sm, capsys):
        run_graph_analysis(mini_parallel_sm)
        out = capsys.readouterr().out
        assert "Track combinations" in out

    def test_combined_count_present(self, mini_parallel_sm, capsys):
        run_graph_analysis(mini_parallel_sm)
        out = capsys.readouterr().out
        assert "TOTAL unique execution paths" in out


# ---------------------------------------------------------------------------
# run_validation
# ---------------------------------------------------------------------------

class TestRunValidationLinear:
    def test_prints_banner(self, linear_sm, capsys):
        run_validation(linear_sm)
        out = capsys.readouterr().out
        assert "Static Validation" in out

    def test_prints_all_checks(self, linear_sm, capsys):
        run_validation(linear_sm)
        out = capsys.readouterr().out
        for cat in ("reachability", "liveness", "determinism", "completeness", "trap_cycles"):
            assert cat in out

    def test_prints_summary_line(self, linear_sm, capsys):
        run_validation(linear_sm)
        out = capsys.readouterr().out
        assert "9 check(s)" in out
        assert "PASS" in out

    def test_all_clear_verdict_for_clean_machine(self, linear_sm, capsys):
        run_validation(linear_sm)
        out = capsys.readouterr().out
        assert "ALL CLEAR" in out

    def test_prints_pass_icon(self, linear_sm, capsys):
        run_validation(linear_sm)
        out = capsys.readouterr().out
        assert "PASS" in out

    def test_pass_count_nine(self, linear_sm, capsys):
        run_validation(linear_sm)
        out = capsys.readouterr().out
        assert "9 PASS" in out


class TestRunValidationAmbiguous:
    def test_warns_on_determinism(self, ambiguous_sm, capsys):
        run_validation(ambiguous_sm)
        out = capsys.readouterr().out
        assert "WARN" in out
        assert "determinism" in out

    def test_verdict_review_required(self, ambiguous_sm, capsys):
        run_validation(ambiguous_sm)
        out = capsys.readouterr().out
        assert "REVIEW REQUIRED" in out


class TestRunValidationParallel:
    def test_does_not_raise(self, mini_parallel_sm, capsys):
        run_validation(mini_parallel_sm)
        out = capsys.readouterr().out
        assert "Static Validation" in out

    def test_reachability_pass(self, mini_parallel_sm, capsys):
        run_validation(mini_parallel_sm)
        out = capsys.readouterr().out
        assert "reachability" in out


# ---------------------------------------------------------------------------
# run_validation — ERROR verdict (injected via mock)
# ---------------------------------------------------------------------------

class TestRunValidationError:
    """Force an ERROR finding to exercise the 'ERRORS DETECTED' branch and
    the nodes-list printing path."""

    def test_errors_detected_verdict(self, linear_sm, capsys):
        from unittest.mock import patch
        from smcheck.validator import SMValidator, ValidationFinding

        forced = [
            ValidationFinding("ERROR", "liveness", "deadlock at z", ["z"]),
            ValidationFinding("PASS", "reachability", "ok"),
            ValidationFinding("PASS", "determinism", "ok"),
            ValidationFinding("PASS", "completeness", "ok"),
            ValidationFinding("PASS", "trap_cycles", "ok"),
        ]
        with patch.object(SMValidator, "run_all", return_value=forced):
            run_validation(linear_sm)
        out = capsys.readouterr().out
        assert "ERRORS DETECTED" in out

    def test_error_detail_in_output(self, linear_sm, capsys):
        from unittest.mock import patch
        from smcheck.validator import SMValidator, ValidationFinding

        forced = [
            ValidationFinding("ERROR", "liveness", "deadlock at z", ["z"]),
            ValidationFinding("PASS", "reachability", "ok"),
            ValidationFinding("PASS", "determinism", "ok"),
            ValidationFinding("PASS", "completeness", "ok"),
            ValidationFinding("PASS", "trap_cycles", "ok"),
        ]
        with patch.object(SMValidator, "run_all", return_value=forced):
            run_validation(linear_sm)
        out = capsys.readouterr().out
        assert "deadlock at z" in out

    def test_nodes_list_printed(self, linear_sm, capsys):
        from unittest.mock import patch
        from smcheck.validator import SMValidator, ValidationFinding

        forced = [
            ValidationFinding("ERROR", "liveness", "deadlock", ["z", "w"]),
            ValidationFinding("PASS", "reachability", "ok"),
            ValidationFinding("PASS", "determinism", "ok"),
            ValidationFinding("PASS", "completeness", "ok"),
            ValidationFinding("PASS", "trap_cycles", "ok"),
        ]
        with patch.object(SMValidator, "run_all", return_value=forced):
            run_validation(linear_sm)
        out = capsys.readouterr().out
        assert "Nodes:" in out


# ---------------------------------------------------------------------------
# run_graph_analysis — new sections: class flags + invoke states
# ---------------------------------------------------------------------------

class TestRunGraphAnalysisNewSections:
    def test_class_flags_section_printed(self, linear_sm, capsys):
        run_graph_analysis(linear_sm)
        out = capsys.readouterr().out
        assert "Class-level behavior flags" in out
        assert "allow_event_without_transition" in out

    def test_no_invoke_message_printed(self, linear_sm, capsys):
        run_graph_analysis(linear_sm)
        out = capsys.readouterr().out
        assert "No states with invoke=" in out

    def test_invoke_states_section_printed(self, invoke_states_sm, capsys):
        run_graph_analysis(invoke_states_sm)
        out = capsys.readouterr().out
        assert "Invoke-bearing states" in out
        assert "m" in out   # state id

    def test_flag_values_shown(self, linear_sm, capsys):
        run_graph_analysis(linear_sm)
        out = capsys.readouterr().out
        # StateChart defaults should appear
        assert "True" in out


    def test_trivial_track_no_crash(self, trivial_track_par_sm, capsys):
        """run_graph_analysis handles a parallel track with no transitions.

        Exercises the ``if not t_adj: continue`` branch inside the per-track
        graph-analysis loop in report.py.
        """
        run_graph_analysis(trivial_track_par_sm)
        out = capsys.readouterr().out
        assert "trivial_track" in out
