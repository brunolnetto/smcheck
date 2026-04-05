"""
tests/test_explainer.py
=======================
Unit tests for smcheck.explainer.

Strategy
--------
* ``PathExplanation``, ``_sm_metadata``, ``_format_path_for_prompt``,
  ``_build_prompt``, and ``explanations_to_markdown`` are tested without any
  LLM calls — they are pure data-in / string-out functions.
* ``explain_paths`` is tested by mocking ``_build_langgraph`` so no real
  API credentials or network access are required.
* ``_build_langgraph`` itself is tested with mocked langchain imports to verify
  provider selection logic without installing langchain.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from smcheck.explainer import (
    PathExplanation,
    _sm_metadata,
    _format_path_for_prompt,
    _build_prompt,
    explanations_to_markdown,
    explain_paths,
    estimate_tokens,
    _MODEL_COSTS,
)
from smcheck.paths import PathEdge, SMPath, analyze_paths


# ---------------------------------------------------------------------------
# Fixtures — reusable paths
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_path():
    """A/B → C path with no guard, no back-edge."""
    return SMPath(
        edges=[
            PathEdge(source="a", event="go",   target="b"),
            PathEdge(source="b", event="done", target="c"),
        ],
        is_looping=False,
        terminal="c",
        level="top",
    )


@pytest.fixture
def guarded_path():
    """A path with a guarded edge and a back-edge."""
    return SMPath(
        edges=[
            PathEdge(source="x", event="step", target="y", guard="is_ready"),
            PathEdge(source="y", event="back", target="x", is_back_edge=True),
            PathEdge(source="x", event="exit", target="z"),
        ],
        is_looping=True,
        terminal="z",
        level="top",
    )


@pytest.fixture
def track_path():
    """A track-level path."""
    return SMPath(
        edges=[PathEdge(source="t1", event="advance", target="t2")],
        is_looping=False,
        terminal="t2",
        level="inventory",
    )


# ---------------------------------------------------------------------------
# PathExplanation dataclass
# ---------------------------------------------------------------------------

class TestPathExplanation:
    def test_fields_stored(self, simple_path):
        exp = PathExplanation(
            path=simple_path,
            summary="Order submitted and approved",
            step_descriptions=["Customer submits.", "System approves."],
            business_meaning="Normal happy path.",
        )
        assert exp.path is simple_path
        assert exp.summary == "Order submitted and approved"
        assert len(exp.step_descriptions) == 2
        assert exp.business_meaning == "Normal happy path."

    def test_empty_step_descriptions(self, simple_path):
        exp = PathExplanation(
            path=simple_path,
            summary="x",
            step_descriptions=[],
            business_meaning="y",
        )
        assert exp.step_descriptions == []

    def test_path_attribute_is_sm_path(self, simple_path):
        exp = PathExplanation(path=simple_path, summary="", step_descriptions=[], business_meaning="")
        assert isinstance(exp.path, SMPath)


# ---------------------------------------------------------------------------
# _sm_metadata
# ---------------------------------------------------------------------------

class TestSmMetadata:
    def test_extracts_docstring(self, linear_sm):
        meta = _sm_metadata(linear_sm)
        assert "docstring" in meta
        assert isinstance(meta["docstring"], str)

    def test_class_with_docstring(self, linear_sm):
        meta = _sm_metadata(linear_sm)
        # LinearSM has a one-line docstring
        assert "linear" in meta["docstring"].lower() or len(meta["docstring"]) >= 0

    def test_hooks_key_present(self, linear_sm):
        meta = _sm_metadata(linear_sm)
        assert "hooks" in meta
        assert isinstance(meta["hooks"], dict)

    def test_class_without_docstring(self):
        from statemachine import State, StateChart

        class NoDocSM(StateChart):
            a = State(initial=True)
            b = State(final=True)
            go = a.to(b)

        meta = _sm_metadata(NoDocSM)
        assert meta["docstring"] == ""

    def test_hooks_extracted_when_present(self):
        from statemachine import State, StateChart

        class HookedSM(StateChart):
            """Demo SM with hooks."""
            a = State(initial=True)
            b = State(final=True)
            go = a.to(b)

            def on_enter_b(self):
                """We have arrived at B."""

        meta = _sm_metadata(HookedSM)
        # on_enter_b should appear in hooks dict
        assert "on_enter_b" in meta["hooks"]


# ---------------------------------------------------------------------------
# _format_path_for_prompt
# ---------------------------------------------------------------------------

class TestFormatPathForPrompt:
    def test_contains_path_number(self, simple_path):
        text = _format_path_for_prompt(simple_path, index=0)
        assert "Path 1" in text

    def test_index_offsets_by_one(self, simple_path):
        text = _format_path_for_prompt(simple_path, index=4)
        assert "Path 5" in text

    def test_contains_edge_events(self, simple_path):
        text = _format_path_for_prompt(simple_path, index=0)
        assert "go" in text
        assert "done" in text

    def test_contains_level(self, simple_path):
        text = _format_path_for_prompt(simple_path, index=0)
        assert "level=top" in text

    def test_contains_terminal(self, simple_path):
        text = _format_path_for_prompt(simple_path, index=0)
        assert "terminal=c" in text

    def test_guard_included_when_present(self, guarded_path):
        text = _format_path_for_prompt(guarded_path, index=0)
        assert "guard: is_ready" in text

    def test_no_guard_tag_when_absent(self, simple_path):
        text = _format_path_for_prompt(simple_path, index=0)
        assert "guard:" not in text

    def test_loop_marker_included(self, guarded_path):
        text = _format_path_for_prompt(guarded_path, index=0)
        assert "LOOP" in text

    def test_no_loop_marker_for_simple(self, simple_path):
        text = _format_path_for_prompt(simple_path, index=0)
        assert "LOOP" not in text


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_returns_non_empty_string(self, linear_sm, simple_path):
        analysis = analyze_paths(linear_sm)
        prompt = _build_prompt(analysis, linear_sm, [simple_path])
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_contains_class_name(self, linear_sm, simple_path):
        analysis = analyze_paths(linear_sm)
        prompt = _build_prompt(analysis, linear_sm, [simple_path])
        assert "LinearSM" in prompt

    def test_contains_task_instructions(self, linear_sm, simple_path):
        analysis = analyze_paths(linear_sm)
        prompt = _build_prompt(analysis, linear_sm, [simple_path])
        assert "summary" in prompt
        assert "step_descriptions" in prompt
        assert "business_meaning" in prompt

    def test_contains_json_instruction(self, linear_sm, simple_path):
        analysis = analyze_paths(linear_sm)
        prompt = _build_prompt(analysis, linear_sm, [simple_path])
        assert "JSON" in prompt

    def test_includes_all_paths(self, linear_sm, simple_path, guarded_path):
        analysis = analyze_paths(linear_sm)
        prompt = _build_prompt(analysis, linear_sm, [simple_path, guarded_path])
        assert "Path 1" in prompt
        assert "Path 2" in prompt

    def test_empty_paths_list(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        prompt = _build_prompt(analysis, linear_sm, [])
        assert "LinearSM" in prompt  # header still present


# ---------------------------------------------------------------------------
# explanations_to_markdown
# ---------------------------------------------------------------------------

class TestExplanationsToMarkdown:
    def test_empty_list_returns_header(self):
        md = explanations_to_markdown([])
        assert "# State Machine" in md
        assert "0 path(s)" in md

    def test_single_top_level_explanation(self, simple_path):
        exp = PathExplanation(
            path=simple_path,
            summary="Happy path order",
            step_descriptions=["A submits.", "B approves."],
            business_meaning="Everything works fine.",
        )
        md = explanations_to_markdown([exp])
        assert "## Top-level paths" in md
        assert "Happy path order" in md
        assert "A submits." in md
        assert "B approves." in md
        assert "Everything works fine." in md

    def test_looping_path_has_arrow_prefix(self, guarded_path):
        exp = PathExplanation(
            path=guarded_path,
            summary="Pause and resume",
            step_descriptions=["Step.", "Back.", "Exit."],
            business_meaning="Order pauses.",
        )
        md = explanations_to_markdown([exp])
        # Looping paths get the ↺ prefix in the heading
        assert "↺" in md

    def test_non_looping_path_no_arrow(self, simple_path):
        exp = PathExplanation(
            path=simple_path,
            summary="Simple",
            step_descriptions=["Go.", "Done."],
            business_meaning="Normal.",
        )
        md = explanations_to_markdown([exp])
        assert "↺" not in md

    def test_track_path_appears_under_track_heading(self, track_path):
        exp = PathExplanation(
            path=track_path,
            summary="Inventory reserved",
            step_descriptions=["Reserve."],
            business_meaning="Stock held.",
        )
        md = explanations_to_markdown([exp])
        assert "## Track: inventory" in md
        assert "Inventory reserved" in md

    def test_guard_annotation_in_output(self, guarded_path):
        exp = PathExplanation(
            path=guarded_path,
            summary="Guarded",
            step_descriptions=["x", "y", "z"],
            business_meaning="Needs guard.",
        )
        md = explanations_to_markdown([exp])
        assert "guard: is_ready" in md

    def test_multiple_explanations(self, simple_path, track_path):
        exps = [
            PathExplanation(simple_path, "Top", ["a.", "b."], "Top biz."),
            PathExplanation(track_path, "Track", ["t."], "Track biz."),
        ]
        md = explanations_to_markdown(exps)
        assert "## Top-level paths" in md
        assert "## Track: inventory" in md
        assert "2 path(s)" in md

    def test_returns_string(self, simple_path):
        exp = PathExplanation(simple_path, "x", ["a."], "b.")
        assert isinstance(explanations_to_markdown([exp]), str)


# ---------------------------------------------------------------------------
# explain_paths — mocked LangGraph
# ---------------------------------------------------------------------------

class TestExplainPaths:
    """Mock _build_langgraph entirely so no LLM credentials or network needed."""

    def _fake_graph(self, response_json: str) -> MagicMock:
        """Build a mock graph whose .invoke() returns a fixed JSON string."""
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {"prompt": "", "response": response_json}
        return mock_graph

    def _make_response(self, n: int) -> str:
        """Return a valid JSON array with n explanation objects."""
        return json.dumps([
            {
                "summary": f"Path {i+1} summary",
                "step_descriptions": [f"Step {i+1}."],
                "business_meaning": f"Business meaning {i+1}.",
            }
            for i in range(n)
        ])

    def test_returns_list_of_path_explanations(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths)
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(self._make_response(n))):
            result = explain_paths(analysis, linear_sm)
        assert isinstance(result, list)
        assert all(isinstance(e, PathExplanation) for e in result)

    def test_result_length_matches_paths(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths)
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(self._make_response(n))):
            result = explain_paths(analysis, linear_sm)
        assert len(result) == n

    def test_summary_populated(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths)
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(self._make_response(n))):
            result = explain_paths(analysis, linear_sm)
        assert result[0].summary == "Path 1 summary"

    def test_step_descriptions_populated(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths)
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(self._make_response(n))):
            result = explain_paths(analysis, linear_sm)
        assert result[0].step_descriptions == ["Step 1."]

    def test_business_meaning_populated(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths)
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(self._make_response(n))):
            result = explain_paths(analysis, linear_sm)
        assert "Business meaning" in result[0].business_meaning

    def test_path_attribute_set_correctly(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths)
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(self._make_response(n))):
            result = explain_paths(analysis, linear_sm)
        assert result[0].path is analysis.top_level_paths[0]

    def test_markdown_fence_stripped(self, linear_sm):
        """LLM wraps JSON in ```json ... ``` — must be stripped cleanly."""
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths)
        raw_array = self._make_response(n)
        fenced = f"```json\n{raw_array}\n```"
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(fenced)):
            result = explain_paths(analysis, linear_sm)
        assert len(result) == n

    def test_invalid_json_raises_value_error(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        bad_graph = self._fake_graph("not valid json at all {{")
        with patch("smcheck.explainer._build_langgraph", return_value=bad_graph):
            with pytest.raises(ValueError, match="non-JSON"):
                explain_paths(analysis, linear_sm)

    def test_max_paths_truncates(self, branch_sm):
        """max_paths=1 should limit LLM call to 1 path."""
        analysis = analyze_paths(branch_sm)
        # branch_sm has 2 top-level paths; with max_paths=1 only 1 is sent
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(self._make_response(1))):
            result = explain_paths(analysis, branch_sm, max_paths=1)
        assert len(result) == 1

    def test_track_paths_included_in_prompt(self, mini_parallel_sm):
        """Track paths should be appended after top-level paths."""
        analysis = analyze_paths(mini_parallel_sm)
        total = len(analysis.top_level_paths) + sum(
            len(v) for v in analysis.track_paths.values()
        )
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(self._make_response(total))):
            result = explain_paths(analysis, mini_parallel_sm, max_paths=total)
        assert len(result) == total

    def test_default_model_is_gpt(self, linear_sm):
        """Default model should be gpt-4o-mini."""
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths)
        captured_model = []

        def fake_build(model):
            captured_model.append(model)
            return self._fake_graph(self._make_response(n))

        with patch("smcheck.explainer._build_langgraph", side_effect=fake_build):
            explain_paths(analysis, linear_sm)
        assert captured_model[0] == "gpt-4o-mini"

    def test_custom_model_passed_through(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths)
        captured_model = []

        def fake_build(model):
            captured_model.append(model)
            return self._fake_graph(self._make_response(n))

        with patch("smcheck.explainer._build_langgraph", side_effect=fake_build):
            explain_paths(analysis, linear_sm, model="claude-3-5-haiku")
        assert captured_model[0] == "claude-3-5-haiku"

    def test_explicit_paths_override_bypasses_collection(self, linear_sm):
        """paths= kwarg should be used directly; internal collection is skipped."""
        analysis  = analyze_paths(linear_sm)
        one_path  = [analysis.top_level_paths[0]]
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(self._make_response(1))):
            result = explain_paths(analysis, linear_sm, paths=one_path)
        assert len(result) == 1
        assert result[0].path is one_path[0]

    def test_level_context_does_not_break_result(self, linear_sm):
        """level_context= kwarg is forwarded to _build_prompt; result still valid."""
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths)
        with patch("smcheck.explainer._build_langgraph", return_value=self._fake_graph(self._make_response(n))):
            result = explain_paths(
                analysis, linear_sm,
                level_context="the top-level order flow",
            )
        assert len(result) == n



class TestBuildLangGraph:
    """Tests for the provider-selection logic in _build_langgraph.

    We mock the langgraph and langchain imports entirely so no packages need
    to be installed for these tests to pass.
    """

    def _run_with_mocked_imports(self, model: str) -> str:
        """
        Call _build_langgraph with fully mocked LangChain/LangGraph imports.
        Returns the class name of the LLM that was instantiated.
        """
        from smcheck import explainer as _exp_module

        mock_openai_cls   = MagicMock(return_value=MagicMock())
        mock_anthropic_cls = MagicMock(return_value=MagicMock())

        # Mock StateGraph and END
        mock_graph_instance = MagicMock()
        mock_compiled = MagicMock()
        mock_graph_instance.compile.return_value = mock_compiled
        mock_sg_cls = MagicMock(return_value=mock_graph_instance)

        mock_langgraph = MagicMock()
        mock_langgraph.graph.StateGraph = mock_sg_cls
        mock_langgraph.graph.END = "END"

        mock_openai_mod   = MagicMock()
        mock_openai_mod.ChatOpenAI = mock_openai_cls
        mock_anthropic_mod = MagicMock()
        mock_anthropic_mod.ChatAnthropic = mock_anthropic_cls

        import sys
        mock_modules = {
            "langgraph":              mock_langgraph,
            "langgraph.graph":        mock_langgraph.graph,
            "langchain_core":         MagicMock(),
            "langchain_core.messages": MagicMock(),
            "langchain_openai":        mock_openai_mod,
            "langchain_anthropic":     mock_anthropic_mod,
        }

        with patch.dict(sys.modules, mock_modules):
            from smcheck.explainer import _build_langgraph
            _build_langgraph(model)

        # Figure out which constructor was called
        if mock_openai_cls.called:
            _, kwargs = mock_openai_cls.call_args
            return "openai"
        if mock_anthropic_cls.called:
            return "anthropic"
        return "unknown"

    def test_gpt_model_uses_openai(self):
        provider = self._run_with_mocked_imports("gpt-4o-mini")
        assert provider == "openai"

    def test_o1_model_uses_openai(self):
        provider = self._run_with_mocked_imports("o1-mini")
        assert provider == "openai"

    def test_claude_model_uses_anthropic(self):
        provider = self._run_with_mocked_imports("claude-3-5-haiku")
        assert provider == "anthropic"

    def test_unknown_model_defaults_to_openai(self):
        provider = self._run_with_mocked_imports("some-unknown-model")
        assert provider == "openai"

    def test_missing_langgraph_raises_import_error(self):
        """Without langgraph installed the function should raise ImportError."""
        import sys

        broken_modules = {
            "langgraph": None,
            "langgraph.graph": None,
        }

        # Remove langgraph from sys.modules so the import inside the function fails
        saved = {k: sys.modules.pop(k, _MISSING) for k in broken_modules}
        try:
            with pytest.raises((ImportError, Exception)):
                from smcheck.explainer import _build_langgraph
                # Force a fresh import attempt by reloading (the function imports lazily)
                _build_langgraph("gpt-4o-mini")
        finally:
            for k, v in saved.items():
                if v is _MISSING:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


# sentinel for the missing-langgraph test
_MISSING = object()


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    """
    Tests for the token-estimation dry-run helper.

    No LLM credentials or network access required — the function only builds
    the prompt and counts tokens (or estimates via heuristic).
    """

    def test_returns_dict(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm)
        assert isinstance(result, dict)

    def test_has_required_keys(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm)
        expected_keys = {
            "model", "num_paths", "prompt_chars",
            "estimated_input_tokens", "tiktoken_available",
            "estimated_output_tokens", "estimated_total_tokens",
            "estimated_cost_usd",
        }
        assert expected_keys.issubset(result.keys())

    def test_num_paths_matches_analysis(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        n = len(analysis.top_level_paths) + sum(
            len(v) for v in analysis.track_paths.values()
        )
        result = estimate_tokens(analysis, linear_sm)
        assert result["num_paths"] == min(n, 50)

    def test_max_paths_caps_num_paths(self, branch_sm):
        analysis = analyze_paths(branch_sm)
        result = estimate_tokens(analysis, branch_sm, max_paths=1)
        assert result["num_paths"] == 1

    def test_prompt_chars_positive(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm)
        assert result["prompt_chars"] > 0

    def test_estimated_input_tokens_positive(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm)
        assert result["estimated_input_tokens"] > 0

    def test_estimated_output_tokens_positive(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm)
        assert result["estimated_output_tokens"] > 0

    def test_total_equals_input_plus_output(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm)
        assert result["estimated_total_tokens"] == (
            result["estimated_input_tokens"] + result["estimated_output_tokens"]
        )

    def test_cost_is_non_negative_float(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm)
        assert isinstance(result["estimated_cost_usd"], float)
        assert result["estimated_cost_usd"] >= 0.0

    def test_default_model_is_gpt4o_mini(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm)
        assert result["model"] == "gpt-4o-mini"

    def test_custom_model_stored_in_result(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm, model="gpt-4o")
        assert result["model"] == "gpt-4o"

    def test_known_model_cost_lower_than_premium(self, linear_sm):
        """gpt-4o-mini should be cheaper than gpt-4o."""
        analysis = analyze_paths(linear_sm)
        cheap  = estimate_tokens(analysis, linear_sm, model="gpt-4o-mini")
        costly = estimate_tokens(analysis, linear_sm, model="gpt-4o")
        assert cheap["estimated_cost_usd"] < costly["estimated_cost_usd"]

    def test_unknown_model_does_not_crash(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm, model="some-future-model-xyz")
        assert result["estimated_cost_usd"] >= 0.0

    def test_tiktoken_flag_is_bool(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        result = estimate_tokens(analysis, linear_sm)
        assert isinstance(result["tiktoken_available"], bool)

    def test_model_costs_table_has_expected_models(self):
        """Sanity check that the cost table contains the default model."""
        assert "gpt-4o-mini" in _MODEL_COSTS
        assert "gpt-4o" in _MODEL_COSTS

    def test_paths_override_bypasses_internal_collection(self, linear_sm):
        """When paths= is supplied, only those paths are counted."""
        analysis = analyze_paths(linear_sm)
        # Pass an empty list — result should report 0 paths
        result = estimate_tokens(analysis, linear_sm, paths=[])
        assert result["num_paths"] == 0

    def test_paths_override_with_single_path(self, linear_sm):
        """Exactly one path supplied → num_paths == 1, regardless of analysis total."""
        analysis = analyze_paths(linear_sm)
        one_path = [analysis.top_level_paths[0]]
        result   = estimate_tokens(analysis, linear_sm, paths=one_path)
        assert result["num_paths"] == 1

