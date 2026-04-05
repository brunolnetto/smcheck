"""
tests/test_cli.py
=================
Unit tests for smcheck.cli — argument parsing, subcommand dispatch, and
the ``_load_sm_class`` loader.

Strategy
--------
* All SM classes come from conftest.py fixtures so the tests are self-contained
  and do not depend on the ``main`` module or ``OrderProcessing``.
* Modules are injected into ``sys.modules`` via ``patch.dict`` to exercise
  ``_load_sm_class`` without touching the real file-system.
* LLM calls in ``_cmd_explain`` are blocked with ``unittest.mock.patch``.
"""
from __future__ import annotations

import argparse
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

from smcheck.cli import build_parser, _load_sm_class, main


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------

class TestBuildParser:
    def test_returns_argument_parser(self):
        assert isinstance(build_parser(), argparse.ArgumentParser)

    def test_prog_name(self):
        assert build_parser().prog == "smcheck"

    # Subcommand presence
    def test_validate_subcommand(self):
        args = build_parser().parse_args(["validate"])
        assert args.command == "validate"

    def test_paths_subcommand(self):
        args = build_parser().parse_args(["paths"])
        assert args.command == "paths"

    def test_explain_subcommand(self):
        args = build_parser().parse_args(["explain"])
        assert args.command == "explain"

    def test_testgen_subcommand(self):
        args = build_parser().parse_args(["testgen"])
        assert args.command == "testgen"

    def test_all_subcommand(self):
        args = build_parser().parse_args(["all"])
        assert args.command == "all"

    # Shared defaults
    def test_default_module(self):
        assert build_parser().parse_args(["validate"]).module is None

    def test_default_class_name(self):
        assert build_parser().parse_args(["validate"]).class_name is None

    # Shared overrides
    def test_custom_module_long(self):
        args = build_parser().parse_args(["validate", "--module", "my.mod"])
        assert args.module == "my.mod"

    def test_custom_module_short(self):
        args = build_parser().parse_args(["paths", "-m", "my.mod"])
        assert args.module == "my.mod"

    def test_custom_class_name(self):
        args = build_parser().parse_args(["validate", "--class-name", "MySM"])
        assert args.class_name == "MySM"

    def test_custom_class_name_short(self):
        args = build_parser().parse_args(["validate", "-c", "MySM"])
        assert args.class_name == "MySM"

    # explain-specific options
    def test_explain_default_model(self):
        args = build_parser().parse_args(["explain"])
        assert args.model == "gpt-4o-mini"

    def test_explain_custom_model(self):
        args = build_parser().parse_args(["explain", "--model", "claude-3-5-haiku"])
        assert args.model == "claude-3-5-haiku"

    def test_explain_default_output_none(self):
        args = build_parser().parse_args(["explain"])
        assert args.output is None

    def test_explain_output_file(self):
        args = build_parser().parse_args(["explain", "--output", "PATHS_auto.md"])
        assert args.output == "PATHS_auto.md"

    # testgen-specific options
    def test_testgen_default_output(self):
        args = build_parser().parse_args(["testgen"])
        assert args.output == "generated_tests"

    def test_testgen_custom_output(self):
        args = build_parser().parse_args(["testgen", "-o", "my_tests"])
        assert args.output == "my_tests"

    # func attribute is set
    def test_func_attribute_set_on_validate(self):
        args = build_parser().parse_args(["validate"])
        assert callable(args.func)

    def test_func_attribute_set_on_all(self):
        args = build_parser().parse_args(["all"])
        assert callable(args.func)

    # No subcommand → error
    def test_no_subcommand_raises(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])


# ---------------------------------------------------------------------------
# _load_sm_class
# ---------------------------------------------------------------------------

class TestLoadSMClass:
    def test_returns_class(self, linear_sm):
        fake_mod = types.ModuleType("fake_load_success")
        fake_mod.LinearSM = linear_sm
        with patch.dict(sys.modules, {"fake_load_success": fake_mod}):
            cls = _load_sm_class("fake_load_success", "LinearSM")
        assert cls is linear_sm

    def test_module_not_found_sys_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            _load_sm_class("no_such_module_xyz_abc_123", "Foo")
        assert exc_info.value.code is not None

    def test_class_not_found_sys_exit(self, linear_sm):
        fake_mod = types.ModuleType("fake_no_class")
        # Deliberately do NOT set any SM class attribute
        with patch.dict(sys.modules, {"fake_no_class": fake_mod}):
            with pytest.raises(SystemExit):
                _load_sm_class("fake_no_class", "NonExistentClass")


# ---------------------------------------------------------------------------
# _cmd_validate
# ---------------------------------------------------------------------------

class TestCmdValidate:
    def test_prints_static_validation_banner(self, linear_sm, capsys):
        from smcheck.cli import _cmd_validate

        fake_mod = types.ModuleType("fake_cmd_validate")
        fake_mod.LinearSM = linear_sm
        args = argparse.Namespace(module="fake_cmd_validate", class_name="LinearSM")
        with patch.dict(sys.modules, {"fake_cmd_validate": fake_mod}):
            _cmd_validate(args)
        out = capsys.readouterr().out
        assert "Static Validation" in out

    def test_prints_all_five_categories(self, linear_sm, capsys):
        from smcheck.cli import _cmd_validate

        fake_mod = types.ModuleType("fake_cmd_validate2")
        fake_mod.LinearSM = linear_sm
        args = argparse.Namespace(module="fake_cmd_validate2", class_name="LinearSM")
        with patch.dict(sys.modules, {"fake_cmd_validate2": fake_mod}):
            _cmd_validate(args)
        out = capsys.readouterr().out
        for cat in ("reachability", "liveness", "determinism", "completeness", "trap_cycles"):
            assert cat in out

    def test_all_clear_for_linear(self, linear_sm, capsys):
        from smcheck.cli import _cmd_validate

        fake_mod = types.ModuleType("fake_cmd_validate3")
        fake_mod.LinearSM = linear_sm
        args = argparse.Namespace(module="fake_cmd_validate3", class_name="LinearSM")
        with patch.dict(sys.modules, {"fake_cmd_validate3": fake_mod}):
            _cmd_validate(args)
        out = capsys.readouterr().out
        assert "ALL CLEAR" in out


# ---------------------------------------------------------------------------
# _cmd_paths
# ---------------------------------------------------------------------------

class TestCmdPaths:
    def test_prints_graph_analysis_banner(self, linear_sm, capsys):
        from smcheck.cli import _cmd_paths

        fake_mod = types.ModuleType("fake_cmd_paths")
        fake_mod.LinearSM = linear_sm
        args = argparse.Namespace(module="fake_cmd_paths", class_name="LinearSM")
        with patch.dict(sys.modules, {"fake_cmd_paths": fake_mod}):
            _cmd_paths(args)
        out = capsys.readouterr().out
        assert "Graph Analysis" in out

    def test_prints_total_execution_paths(self, linear_sm, capsys):
        from smcheck.cli import _cmd_paths

        fake_mod = types.ModuleType("fake_cmd_paths2")
        fake_mod.LinearSM = linear_sm
        args = argparse.Namespace(module="fake_cmd_paths2", class_name="LinearSM")
        with patch.dict(sys.modules, {"fake_cmd_paths2": fake_mod}):
            _cmd_paths(args)
        out = capsys.readouterr().out
        assert "TOTAL unique execution paths" in out


# ---------------------------------------------------------------------------
# _cmd_all
# ---------------------------------------------------------------------------

class TestCmdAll:
    def test_runs_both_paths_and_validate(self, linear_sm, capsys):
        from smcheck.cli import _cmd_all

        fake_mod = types.ModuleType("fake_cmd_all")
        fake_mod.LinearSM = linear_sm
        args = argparse.Namespace(module="fake_cmd_all", class_name="LinearSM")
        with patch.dict(sys.modules, {"fake_cmd_all": fake_mod}):
            _cmd_all(args)
        out = capsys.readouterr().out
        assert "Graph Analysis" in out
        assert "Static Validation" in out


# ---------------------------------------------------------------------------
# _cmd_explain
# ---------------------------------------------------------------------------

class TestCmdExplain:
    """The LLM is completely mocked — no real API calls."""

    def _make_args(self, mod_name: str, out_file=None) -> argparse.Namespace:
        return argparse.Namespace(
            module=mod_name,
            class_name="LinearSM",
            model="gpt-4o-mini",
            output=out_file,
        )

    def test_prints_progress_messages(self, linear_sm, capsys):
        from smcheck.cli import _cmd_explain

        fake_mod = types.ModuleType("fake_explain1")
        fake_mod.LinearSM = linear_sm
        with patch.dict(sys.modules, {"fake_explain1": fake_mod}):
            with patch("smcheck.explainer.explain_paths", return_value=[]):
                _cmd_explain(self._make_args("fake_explain1"))
        out = capsys.readouterr().out
        assert "smcheck" in out          # progress lines include "[smcheck]"

    def test_prints_markdown_to_stdout_when_no_output(self, linear_sm, capsys):
        from smcheck.cli import _cmd_explain

        fake_mod = types.ModuleType("fake_explain2")
        fake_mod.LinearSM = linear_sm
        with patch.dict(sys.modules, {"fake_explain2": fake_mod}):
            with patch("smcheck.explainer.explain_paths", return_value=[]):
                _cmd_explain(self._make_args("fake_explain2", out_file=None))
        out = capsys.readouterr().out
        # explanations_to_markdown([]) always returns at least the header
        assert "State Machine" in out

    def test_writes_file_when_output_given(self, linear_sm, tmp_path, capsys):
        from smcheck.cli import _cmd_explain

        out_file = str(tmp_path / "output.md")
        fake_mod = types.ModuleType("fake_explain3")
        fake_mod.LinearSM = linear_sm
        with patch.dict(sys.modules, {"fake_explain3": fake_mod}):
            with patch("smcheck.explainer.explain_paths", return_value=[]):
                _cmd_explain(self._make_args("fake_explain3", out_file=out_file))
        assert (tmp_path / "output.md").exists()

    def test_written_file_contains_markdown_header(self, linear_sm, tmp_path, capsys):
        from smcheck.cli import _cmd_explain

        out_file = str(tmp_path / "paths.md")
        fake_mod = types.ModuleType("fake_explain4")
        fake_mod.LinearSM = linear_sm
        with patch.dict(sys.modules, {"fake_explain4": fake_mod}):
            with patch("smcheck.explainer.explain_paths", return_value=[]):
                _cmd_explain(self._make_args("fake_explain4", out_file=out_file))
        content = (tmp_path / "paths.md").read_text(encoding="utf-8")
        assert "State Machine" in content


# ---------------------------------------------------------------------------
# _cmd_testgen
# ---------------------------------------------------------------------------

class TestCmdTestgen:
    def test_generates_and_writes_tests(self, linear_sm, tmp_path, capsys):
        from smcheck.cli import _cmd_testgen

        fake_mod = types.ModuleType("fake_testgen1")
        fake_mod.LinearSM = linear_sm
        args = argparse.Namespace(
            module="fake_testgen1",
            class_name="LinearSM",
            output=str(tmp_path),
        )
        with patch.dict(sys.modules, {"fake_testgen1": fake_mod}):
            _cmd_testgen(args)
        out = capsys.readouterr().out
        assert "tests generated" in out

    def test_reports_transition_and_path_counts(self, linear_sm, tmp_path, capsys):
        from smcheck.cli import _cmd_testgen

        fake_mod = types.ModuleType("fake_testgen2")
        fake_mod.LinearSM = linear_sm
        args = argparse.Namespace(
            module="fake_testgen2",
            class_name="LinearSM",
            output=str(tmp_path),
        )
        with patch.dict(sys.modules, {"fake_testgen2": fake_mod}):
            _cmd_testgen(args)
        out = capsys.readouterr().out
        assert "transition" in out
        assert "path" in out

    def test_writes_test_files_to_output_dir(self, linear_sm, tmp_path, capsys):
        from smcheck.cli import _cmd_testgen

        fake_mod = types.ModuleType("fake_testgen3")
        fake_mod.LinearSM = linear_sm
        args = argparse.Namespace(
            module="fake_testgen3",
            class_name="LinearSM",
            output=str(tmp_path),
        )
        with patch.dict(sys.modules, {"fake_testgen3": fake_mod}):
            _cmd_testgen(args)
        # At least one .py file should have been written
        py_files = list(tmp_path.glob("*.py"))
        assert len(py_files) >= 1


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_validate(self, linear_sm, capsys):
        fake_mod = types.ModuleType("main_validate_mod")
        fake_mod.LinearSM = linear_sm
        with patch.dict(sys.modules, {"main_validate_mod": fake_mod}):
            main(["validate", "--module", "main_validate_mod", "--class-name", "LinearSM"])
        out = capsys.readouterr().out
        assert "Static Validation" in out

    def test_main_paths(self, linear_sm, capsys):
        fake_mod = types.ModuleType("main_paths_mod")
        fake_mod.LinearSM = linear_sm
        with patch.dict(sys.modules, {"main_paths_mod": fake_mod}):
            main(["paths", "--module", "main_paths_mod", "--class-name", "LinearSM"])
        out = capsys.readouterr().out
        assert "Graph Analysis" in out

    def test_main_all(self, linear_sm, capsys):
        fake_mod = types.ModuleType("main_all_mod")
        fake_mod.LinearSM = linear_sm
        with patch.dict(sys.modules, {"main_all_mod": fake_mod}):
            main(["all", "--module", "main_all_mod", "--class-name", "LinearSM"])
        out = capsys.readouterr().out
        assert "Graph Analysis" in out
        assert "Static Validation" in out

    def test_main_no_args_exits(self):
        with pytest.raises(SystemExit):
            main([])

    def test_main_unknown_command_exits(self):
        with pytest.raises(SystemExit):
            main(["unknown_cmd"])
