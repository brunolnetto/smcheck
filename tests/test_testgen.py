"""
tests/test_testgen.py
=====================
Unit tests for smcheck.testgen — test case generation + code rendering.
"""

from __future__ import annotations

import os
from smcheck.testgen import (
    TestStep,
    TestCase,
    generate_transition_tests,
    generate_top_level_path_tests,
    generate_track_path_tests,
    generate_validator_tests,
    generate_all,
    render_pytest,
    write_tests,
    GuardSetupMap,
    _render_step,
)
from smcheck.paths import analyze_paths


# ---------------------------------------------------------------------------
# TestStep
# ---------------------------------------------------------------------------


class TestTestStep:
    def test_call_step(self):
        s = TestStep(kind="call", event="go")
        assert s.kind == "call"
        assert s.event == "go"

    def test_attr_step(self):
        s = TestStep(kind="attr", attr="_flag", value=True)
        assert s.attr == "_flag"
        assert s.value is True

    def test_comment_step(self):
        s = TestStep(kind="comment", text="foo")
        assert s.text == "foo"


# ---------------------------------------------------------------------------
# TestCase
# ---------------------------------------------------------------------------


class TestTestCaseDataclass:
    def test_fields(self):
        tc = TestCase(
            name="test_a",
            level="transition",
            steps=[],
            assert_state="b",
            description="test desc",
        )
        assert tc.name == "test_a"
        assert tc.assert_flags == {}

    def test_assert_flags_default(self):
        tc = TestCase(name="x", level="path_top", steps=[], assert_state=None)
        assert tc.assert_flags == {}


# ---------------------------------------------------------------------------
# generate_transition_tests
# ---------------------------------------------------------------------------


class TestGenerateTransitionTests:
    def test_generates_for_linear(self, linear_sm):
        tests = generate_transition_tests(linear_sm)
        assert len(tests) >= 2  # go, done
        names = {t.name for t in tests}
        assert any("go" in n for n in names)
        assert any("done" in n for n in names)

    def test_all_are_transition_level(self, linear_sm):
        for t in generate_transition_tests(linear_sm):
            assert t.level == "transition"

    def test_branch_sm_generates_3_edges(self, branch_sm):
        tests = generate_transition_tests(branch_sm)
        # step (a→b), left (b→c), right (b→d)
        assert len(tests) >= 3

    def test_guard_setup_injected(self, branch_sm):
        gmap: GuardSetupMap = {"left": {"_go_left": True}}
        tests = generate_transition_tests(branch_sm, guard_setup_map=gmap)
        left_test = next(t for t in tests if "left" in t.name)
        attr_steps = [s for s in left_test.steps if s.kind == "attr"]
        assert any(s.attr == "_go_left" and s.value is True for s in attr_steps)

    def test_parallel_sm_generates_edges(self, mini_parallel_sm):
        tests = generate_transition_tests(mini_parallel_sm)
        assert len(tests) >= 4  # begin, cancel, step_a, step_b1, step_b2

    def test_no_duplicate_names(self, branch_sm):
        tests = generate_transition_tests(branch_sm)
        names = [t.name for t in tests]
        # Duplicates should be suffixed
        assert len(names) == len(set(names))

    def test_sequential_compound_sub_states_get_setup(self, compound_sink_child_sm):
        # CompoundSinkChildSM: 'acknowledged' is inside sequential compound 'work'.
        # The new code block must compute setup["acknowledged"] = ["begin", "step"].
        tests = generate_transition_tests(compound_sink_child_sm)
        # 'step' (w1→acknowledged) is the transition under test
        step_test = next((t for t in tests if "step" in t.name), None)
        assert step_test is not None
        call_events = [s.event for s in step_test.steps if s.kind == "call"]
        assert "begin" in call_events  # setup enters the compound

    def test_guard_flags_injected_in_setup_path(self, guarded_path_sm):
        # GuardedPathSM: reaching 'mid' requires guarded 'go' event.
        # generate_transition_tests must set _ready=True before 'go' in setup.
        gmap: GuardSetupMap = {"go": {"_ready": True}}
        tests = generate_transition_tests(guarded_path_sm, guard_setup_map=gmap)
        done_test = next((t for t in tests if "done" in t.name), None)
        assert done_test is not None
        attr_steps = [s for s in done_test.steps if s.kind == "attr"]
        assert any(s.attr == "_ready" and s.value is True for s in attr_steps)


# ---------------------------------------------------------------------------
# generate_top_level_path_tests
# ---------------------------------------------------------------------------


class TestGenerateTopLevelPathTests:
    def test_one_per_top_path(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        tests = generate_top_level_path_tests(analysis, linear_sm)
        assert len(tests) == len(analysis.top_level_paths)

    def test_level_is_path_top(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        for t in generate_top_level_path_tests(analysis, linear_sm):
            assert t.level == "path_top"

    def test_loop_in_name_for_looping(self, loop_sm):
        analysis = analyze_paths(loop_sm)
        tests = generate_top_level_path_tests(analysis, loop_sm)
        loop_tests = [t for t in tests if "loop" in t.name]
        assert len(loop_tests) >= 1

    def test_compound_traversal_inserts_events(self, branch_sm):
        analysis = analyze_paths(branch_sm)
        ct = {"a": {"c": ["step"]}}  # before leaving 'a' to 'c', fire 'step'
        tests = generate_top_level_path_tests(analysis, branch_sm, compound_traversal=ct)
        assert len(tests) >= 1


# ---------------------------------------------------------------------------
# generate_track_path_tests
# ---------------------------------------------------------------------------


class TestGenerateTrackPathTests:
    def test_generates_for_parallel(self, mini_parallel_sm):
        analysis = analyze_paths(mini_parallel_sm)
        tests = generate_track_path_tests(analysis, mini_parallel_sm)
        assert len(tests) >= 1

    def test_level_is_path_track(self, mini_parallel_sm):
        analysis = analyze_paths(mini_parallel_sm)
        for t in generate_track_path_tests(analysis, mini_parallel_sm):
            assert t.level == "path_track"

    def test_no_tracks_for_linear(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        tests = generate_track_path_tests(analysis, linear_sm)
        assert tests == []


# ---------------------------------------------------------------------------
# generate_all
# ---------------------------------------------------------------------------


class TestGenerateAll:
    def test_combines_all_levels(self, mini_parallel_sm):
        tests = generate_all(mini_parallel_sm)
        levels = {t.level for t in tests}
        assert "transition" in levels

    def test_accepts_precomputed_analysis(self, linear_sm):
        analysis = analyze_paths(linear_sm)
        tests = generate_all(linear_sm, analysis=analysis)
        assert len(tests) >= 2  # at least transitions + 1 top-level path

    def test_guard_setup_map_propagated(self, branch_sm):
        gmap: GuardSetupMap = {"left": {"_go_left": True}}
        tests = generate_all(branch_sm, guard_setup_map=gmap)
        left_tests = [t for t in tests if "left" in t.name and t.level == "transition"]
        assert len(left_tests) >= 1


# ---------------------------------------------------------------------------
# render_pytest
# ---------------------------------------------------------------------------


class TestRenderPytest:
    def test_produces_valid_python(self, linear_sm):
        tests = generate_all(linear_sm)
        code = render_pytest(tests, "tests.conftest", "LinearSM")
        # Must be valid Python
        compile(code, "<testgen>", "exec")

    def test_contains_import(self, linear_sm):
        tests = generate_all(linear_sm)
        code = render_pytest(tests, "tests.conftest", "LinearSM")
        assert "from tests.conftest import LinearSM" in code

    def test_contains_test_functions(self, linear_sm):
        tests = generate_all(linear_sm)
        code = render_pytest(tests, "tests.conftest", "LinearSM")
        assert "def test_" in code

    def test_contains_assertions(self, linear_sm):
        tests = generate_all(linear_sm)
        code = render_pytest(tests, "tests.conftest", "LinearSM")
        assert "assert" in code

    def test_renders_comment_steps(self, linear_sm):
        tests = generate_all(linear_sm)
        code = render_pytest(tests, "tests.conftest", "LinearSM")
        assert "#" in code  # comments present

    def test_branch_renders_attr_steps(self, branch_sm):
        gmap: GuardSetupMap = {"left": {"_go_left": True}}
        tests = generate_all(branch_sm, guard_setup_map=gmap)
        code = render_pytest(tests, "tests.conftest", "BranchSM")
        assert "_go_left" in code

    def test_unknown_step_kind(self):
        tc = TestCase(
            name="test_weird",
            level="transition",
            steps=[TestStep(kind="banana")],
            assert_state="x",
        )
        code = render_pytest([tc], "mod", "Cls")
        assert "unknown step kind" in code

    def test_assert_flags_rendered(self):
        tc = TestCase(
            name="test_flags",
            level="transition",
            steps=[TestStep(kind="call", event="go")],
            assert_state=None,
            assert_flags={"_flag": True},
        )
        code = render_pytest([tc], "mod", "Cls")
        assert "sm._flag == True" in code


# ---------------------------------------------------------------------------
# write_tests
# ---------------------------------------------------------------------------


class TestWriteTests:
    def test_creates_files(self, linear_sm, tmp_path):
        tests = generate_all(linear_sm)
        written = write_tests(tests, "tests.conftest", str(tmp_path), "LinearSM")
        assert len(written) >= 1
        for path in written:
            assert os.path.isfile(path)

    def test_files_are_valid_python(self, linear_sm, tmp_path):
        tests = generate_all(linear_sm)
        written = write_tests(tests, "tests.conftest", str(tmp_path), "LinearSM")
        for path in written:
            with open(path) as f:
                code = f.read()
            compile(code, path, "exec")

    def test_creates_output_dir(self, linear_sm, tmp_path):
        out = str(tmp_path / "subdir" / "tests")
        tests = generate_all(linear_sm)
        write_tests(tests, "tests.conftest", out, "LinearSM")
        assert os.path.isdir(out)

    def test_skips_empty_subsets(self, linear_sm, tmp_path):
        # Only transition tests → should write test_transitions.py only
        tests = generate_transition_tests(linear_sm)
        written = write_tests(tests, "tests.conftest", str(tmp_path), "LinearSM")
        names = {os.path.basename(p) for p in written}
        assert "test_transitions.py" in names


# ---------------------------------------------------------------------------
# _path_to_steps guard flags — testgen.py lines 301-303
# ---------------------------------------------------------------------------


class TestPathToStepsGuardFlags:
    """Cover the ``if flags:`` branch inside ``_path_to_steps`` (called via
    generate_track_path_tests) when guard_setup_map has an entry matching a
    track edge event."""

    def test_attr_step_injected_for_matched_event(self, mini_parallel_sm):
        analysis = analyze_paths(mini_parallel_sm)
        # step_a is a track edge event; supply a guard flag for it
        gmap: GuardSetupMap = {"step_a": {"_some_flag": True}}
        tests = generate_track_path_tests(analysis, mini_parallel_sm, guard_setup_map=gmap)
        assert len(tests) >= 1
        # At least one test should contain an attr step for _some_flag
        all_steps = [s for t in tests for s in t.steps]
        attr_steps = [s for s in all_steps if s.kind == "attr" and s.attr == "_some_flag"]
        assert len(attr_steps) >= 1

    def test_comment_step_injected_for_matched_event(self, mini_parallel_sm):
        analysis = analyze_paths(mini_parallel_sm)
        gmap: GuardSetupMap = {"step_a": {"_some_flag": True}}
        tests = generate_track_path_tests(analysis, mini_parallel_sm, guard_setup_map=gmap)
        all_steps = [s for t in tests for s in t.steps]
        comment_steps = [s for s in all_steps if s.kind == "comment" and "step_a" in (s.text or "")]
        assert len(comment_steps) >= 1


# ---------------------------------------------------------------------------
# generate_top_level_path_tests compound_traversal loop — testgen.py 332-336
# ---------------------------------------------------------------------------


class TestTopLevelPathTestsCompoundLoop:
    """Cover the ``for ie in internal:`` loop body inside
    generate_top_level_path_tests, including the guard-flags sub-block."""

    def test_internal_event_call_step_added(self, branch_sm):
        """compound_traversal entry causes internal events to be fired first."""
        analysis = analyze_paths(branch_sm)
        # When current==a and edge.target==b, add an internal "warmup" event
        ct = {"a": {"b": ["warmup"]}}
        tests = generate_top_level_path_tests(analysis, branch_sm, compound_traversal=ct)
        assert len(tests) >= 1
        all_steps = [s for t in tests for s in t.steps]
        call_evs = [s.event for s in all_steps if s.kind == "call"]
        assert "warmup" in call_evs

    def test_internal_event_guard_attr_step_added(self, branch_sm):
        """When guard_setup_map matches an internal event, attr steps are injected."""
        analysis = analyze_paths(branch_sm)
        ct = {"a": {"b": ["warmup"]}}
        gmap: GuardSetupMap = {"warmup": {"_warmup_flag": True}}
        tests = generate_top_level_path_tests(
            analysis, branch_sm, guard_setup_map=gmap, compound_traversal=ct
        )
        assert len(tests) >= 1
        all_steps = [s for t in tests for s in t.steps]
        attr_steps = [s for s in all_steps if s.kind == "attr" and s.attr == "_warmup_flag"]
        assert len(attr_steps) >= 1

    def test_main_edge_guard_attr_step_added(self, linear_sm):
        """guard_setup_map matching a main edge event injects attr steps."""
        analysis = analyze_paths(linear_sm)
        gmap: GuardSetupMap = {"go": {"_go_flag": True}}
        tests = generate_top_level_path_tests(analysis, linear_sm, guard_setup_map=gmap)
        assert len(tests) >= 1
        all_steps = [s for t in tests for s in t.steps]
        attr_steps = [s for s in all_steps if s.kind == "attr" and s.attr == "_go_flag"]
        assert len(attr_steps) >= 1


# ---------------------------------------------------------------------------
# render_pytest track section — testgen.py lines 542, 547-567
# ---------------------------------------------------------------------------


class TestRenderPytestTrackSection:
    """Cover the ``if path_tests_track:`` branch in render_pytest by passing
    tests that include path_track level entries (from a parallel SM)."""

    def test_renders_track_section_header(self, mini_parallel_sm):
        tests = generate_all(mini_parallel_sm)
        code = render_pytest(tests, "tests.conftest", "MiniParallelSM")
        assert "Per-track path tests" in code

    def test_renders_track_test_functions(self, mini_parallel_sm):
        tests = generate_all(mini_parallel_sm)
        code = render_pytest(tests, "tests.conftest", "MiniParallelSM")
        track_tests = [t for t in tests if t.level == "path_track"]
        assert len(track_tests) >= 1
        # Each track test function name should appear in the rendered code
        assert track_tests[0].name in code

    def test_track_section_is_valid_python(self, mini_parallel_sm):
        tests = generate_all(mini_parallel_sm)
        code = render_pytest(tests, "tests.conftest", "MiniParallelSM")
        compile(code, "<testgen_track>", "exec")

    def test_path_top_with_assert_flags_renders_flag_assertion(self):
        """Cover the assert_flags loop body for path_top level tests (line 542)."""
        tc = TestCase(
            name="test_top_flagged",
            level="path_top",
            steps=[TestStep(kind="call", event="go")],
            assert_state=None,
            assert_flags={"_payed": True},
        )
        code = render_pytest([tc], "mod", "Cls")
        assert "sm._payed == True" in code

    def test_path_track_with_assert_state_renders_current_state(self):
        """Cover the assert_state branch for path_track level tests (line 564)."""
        tc = TestCase(
            name="test_track_with_state",
            level="path_track",
            steps=[TestStep(kind="call", event="step")],
            assert_state="some_state",
            assert_flags={},
        )
        code = render_pytest([tc], "mod", "Cls")
        assert "_active(sm, 'some_state')" in code

    def test_path_track_with_assert_flags_renders_flag_assertion(self):
        """Cover the assert_flags loop body for path_track level tests (line 566)."""
        tc = TestCase(
            name="test_track_flagged",
            level="path_track",
            steps=[TestStep(kind="call", event="step")],
            assert_state=None,
            assert_flags={"_track_flag": True},
        )
        code = render_pytest([tc], "mod", "Cls")
        assert "sm._track_flag == True" in code


# ---------------------------------------------------------------------------
# generate_validator_tests
# ---------------------------------------------------------------------------


class TestGenerateValidatorTests:
    def test_generates_for_validator_sm(self, validator_sm):
        tests = generate_validator_tests(validator_sm)
        assert len(tests) >= 1

    def test_no_validators_for_linear(self, linear_sm):
        tests = generate_validator_tests(linear_sm)
        assert tests == []

    def test_level_is_validator(self, validator_sm):
        for t in generate_validator_tests(validator_sm):
            assert t.level == "validator"

    def test_has_raises_step(self, validator_sm):
        tests = generate_validator_tests(validator_sm)
        all_steps = [s for t in tests for s in t.steps]
        assert any(s.kind == "raises" for s in all_steps)

    def test_default_exception_is_exception(self, validator_sm):
        tests = generate_validator_tests(validator_sm)
        raises_steps = [s for t in tests for s in t.steps if s.kind == "raises"]
        assert all(s.value == "Exception" for s in raises_steps)

    def test_custom_exception_used(self, validator_sm):
        tests = generate_validator_tests(validator_sm, validator_error_map={"proceed": ValueError})
        raises_steps = [s for t in tests for s in t.steps if s.kind == "raises"]
        assert any(s.value == "ValueError" for s in raises_steps)

    def test_event_name_in_raises_step(self, validator_sm):
        tests = generate_validator_tests(validator_sm)
        raises_steps = [s for t in tests for s in t.steps if s.kind == "raises"]
        assert all(s.event == "proceed" for s in raises_steps)

    def test_setup_events_emitted_for_non_initial_source(self, multi_step_validator_sm):
        # MultiStepValidatorSM: 'proceed' source is 'mid' (non-initial).
        # generate_validator_tests must emit setup step 'go' before raises.
        tests = generate_validator_tests(multi_step_validator_sm)
        assert len(tests) == 1
        steps = tests[0].steps
        call_events = [s.event for s in steps if s.kind == "call"]
        assert "go" in call_events, "setup step 'go' must appear before raises"


# ---------------------------------------------------------------------------
# _render_step  "raises" kind
# ---------------------------------------------------------------------------


class TestRenderStepRaises:
    def test_renders_with_block(self):
        step = TestStep(kind="raises", event="go", value="ValueError")
        rendered = _render_step(step)
        assert "pytest.raises(ValueError)" in rendered
        assert "sm.go()" in rendered

    def test_defaults_to_exception_when_value_none(self):
        step = TestStep(kind="raises", event="go", value=None)
        rendered = _render_step(step)
        assert "pytest.raises(Exception)" in rendered

    def test_rendered_is_multiline(self):
        step = TestStep(kind="raises", event="go", value="RuntimeError")
        rendered = _render_step(step)
        assert "\n" in rendered


# ---------------------------------------------------------------------------
# generate_all  with validator_error_map
# ---------------------------------------------------------------------------


class TestGenerateAllValidators:
    def test_includes_validator_level_when_present(self, validator_sm):
        tests = generate_all(validator_sm)
        levels = {t.level for t in tests}
        assert "validator" in levels

    def test_no_validator_tests_for_linear(self, linear_sm):
        tests = generate_all(linear_sm)
        assert all(t.level != "validator" for t in tests)

    def test_validator_error_map_propagated(self, validator_sm):
        tests = generate_all(validator_sm, validator_error_map={"proceed": ValueError})
        val_tests = [t for t in tests if t.level == "validator"]
        raises_steps = [s for t in val_tests for s in t.steps if s.kind == "raises"]
        assert any(s.value == "ValueError" for s in raises_steps)


# ---------------------------------------------------------------------------
# render_pytest  validator section
# ---------------------------------------------------------------------------


class TestRenderPytestValidators:
    def test_contains_pytest_raises(self, validator_sm):
        tests = generate_all(validator_sm)
        code = render_pytest(tests, "tests.conftest", "ValidatorSM")
        assert "pytest.raises" in code

    def test_validator_section_header(self, validator_sm):
        tests = generate_all(validator_sm)
        code = render_pytest(tests, "tests.conftest", "ValidatorSM")
        assert "Validator tests" in code

    def test_valid_python_with_validators(self, validator_sm):
        tests = generate_all(validator_sm)
        code = render_pytest(tests, "tests.conftest", "ValidatorSM")
        compile(code, "<testgen_validators>", "exec")


# ---------------------------------------------------------------------------
# write_tests  validator file
# ---------------------------------------------------------------------------


class TestWriteTestsValidators:
    def test_creates_validator_file(self, validator_sm, tmp_path):
        tests = generate_all(validator_sm)
        written = write_tests(tests, "tests.conftest", str(tmp_path), "ValidatorSM")
        names = {os.path.basename(p) for p in written}
        assert "test_validators.py" in names

    def test_validator_file_is_valid_python(self, validator_sm, tmp_path):
        tests = generate_all(validator_sm)
        write_tests(tests, "tests.conftest", str(tmp_path), "ValidatorSM")
        fpath = os.path.join(str(tmp_path), "test_validators.py")
        with open(fpath) as f:
            code = f.read()
        compile(code, fpath, "exec")

    def test_skips_validator_file_when_no_validators(self, linear_sm, tmp_path):
        tests = generate_all(linear_sm)
        written = write_tests(tests, "tests.conftest", str(tmp_path), "LinearSM")
        names = {os.path.basename(p) for p in written}
        assert "test_validators.py" not in names
