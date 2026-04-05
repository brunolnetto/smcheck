"""
tests/test_smcheck.py
=====================
Tests for the SMCheck facade in smcheck/__init__.py.
"""
from __future__ import annotations

import pytest
from smcheck import SMCheck, ValidationFinding, PathAnalysis, SMPath, PathEdge


class TestSMCheckFacade:
    def test_validate_returns_validator(self, linear_sm):
        sm = SMCheck(linear_sm)
        v = sm.validate()
        findings = v.run_all()
        assert isinstance(findings, list)
        assert all(isinstance(f, ValidationFinding) for f in findings)

    def test_graph_returns_adj_map(self, linear_sm):
        sm = SMCheck(linear_sm)
        g = sm.graph()
        assert isinstance(g, dict)
        assert "a" in g

    def test_top_level_graph(self, linear_sm):
        sm = SMCheck(linear_sm)
        g = sm.top_level_graph()
        assert isinstance(g, dict)
        assert "a" in g

    def test_analyze_paths_returns_path_analysis(self, linear_sm):
        sm = SMCheck(linear_sm)
        result = sm.analyze_paths()
        assert isinstance(result, PathAnalysis)

    def test_report_graph_prints(self, linear_sm, capsys):
        sm = SMCheck(linear_sm)
        sm.report_graph()
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_report_validation_prints(self, linear_sm, capsys):
        sm = SMCheck(linear_sm)
        sm.report_validation()
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_generate_tests_returns_list(self, linear_sm):
        sm = SMCheck(linear_sm)
        tests = sm.generate_tests()
        assert isinstance(tests, list)
        assert len(tests) > 0

    def test_write_tests_writes_files(self, linear_sm, tmp_path):
        sm = SMCheck(linear_sm)
        written = sm.write_tests("tests.conftest", str(tmp_path))
        assert len(written) >= 1
        for p in written:
            assert (tmp_path / p.split("/")[-1]).exists()

    def test_write_tests_custom_class_name(self, linear_sm, tmp_path):
        sm = SMCheck(linear_sm)
        written = sm.write_tests("tests.conftest", str(tmp_path), class_name="MyClass")
        content = open(written[0]).read()
        assert "MyClass" in content

    def test_to_mermaid_returns_str(self, linear_sm):
        sm = SMCheck(linear_sm)
        result = sm.to_mermaid()
        assert isinstance(result, str)
        assert "stateDiagram-v2" in result

    def test_write_mermaid_writes_file(self, linear_sm, tmp_path):
        sm = SMCheck(linear_sm)
        out = tmp_path / "diagram.mmd"
        path = sm.write_mermaid(str(out))
        assert out.exists()
        assert path == out
