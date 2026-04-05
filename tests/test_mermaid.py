"""
tests/test_mermaid.py
=====================
Unit tests for smcheck.mermaid — Mermaid stateDiagram-v2 export.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from smcheck.mermaid import to_mermaid, write_mermaid


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sm_class():
    _ex = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "examples", "order-processing")
    )
    if _ex not in sys.path:
        sys.path.insert(0, _ex)
    from machine import OrderProcessing

    return OrderProcessing


@pytest.fixture(scope="module")
def diagram(sm_class):
    return to_mermaid(sm_class)


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


class TestBasicStructure:
    def test_returns_string(self, sm_class):
        result = to_mermaid(sm_class)
        assert isinstance(result, str)

    def test_starts_with_statediagram_v2(self, diagram):
        assert diagram.startswith("stateDiagram-v2\n")

    def test_ends_with_newline(self, diagram):
        assert diagram.endswith("\n")

    def test_direction_lr_present_by_default(self, diagram):
        assert "direction LR" in diagram

    def test_direction_tb_when_specified(self, sm_class):
        result = to_mermaid(sm_class, direction="TB")
        assert "direction TB" in result
        assert "direction LR" not in result

    def test_direction_omitted_when_empty(self, sm_class):
        result = to_mermaid(sm_class, direction="")
        assert "direction" not in result

    def test_is_deterministic(self, sm_class):
        assert to_mermaid(sm_class) == to_mermaid(sm_class)


# ---------------------------------------------------------------------------
# Initial / final pseudo-states
# ---------------------------------------------------------------------------


class TestInitialFinalMarkers:
    def test_top_level_initial_idle(self, diagram):
        assert "[*] --> idle" in diagram

    def test_top_level_final_cancelled(self, diagram):
        assert "cancelled --> [*]" in diagram

    def test_top_level_final_failed(self, diagram):
        assert "failed --> [*]" in diagram

    def test_validation_internal_initial(self, diagram):
        assert "[*] --> reviewing" in diagram

    def test_validation_final_approved(self, diagram):
        assert "approved --> [*]" in diagram

    def test_validation_final_rejected(self, diagram):
        assert "rejected --> [*]" in diagram

    def test_inventory_internal_initial(self, diagram):
        assert "[*] --> checking" in diagram

    def test_inventory_final_allocated(self, diagram):
        assert "allocated --> [*]" in diagram

    def test_inventory_final_out_of_stock(self, diagram):
        assert "out_of_stock --> [*]" in diagram

    def test_payment_internal_initial(self, diagram):
        assert "[*] --> pay_hold" in diagram

    def test_payment_final_authorized(self, diagram):
        assert "authorized --> [*]" in diagram

    def test_payment_final_declined(self, diagram):
        assert "declined --> [*]" in diagram

    def test_shipping_internal_initial(self, diagram):
        assert "[*] --> ship_hold" in diagram

    def test_shipping_acknowledged_not_final(self, diagram):
        # acknowledged is no longer a final state (complete fires at top-level)
        assert "acknowledged --> [*]" not in diagram


# ---------------------------------------------------------------------------
# State declarations with display names
# ---------------------------------------------------------------------------


class TestStateDeclarations:
    def test_compound_validation_declared(self, diagram):
        assert 'state "Validation" as validation' in diagram

    def test_parallel_fulfillment_declared(self, diagram):
        assert 'state "Fulfillment" as fulfillment' in diagram

    def test_compound_inventory_declared(self, diagram):
        assert 'state "Inventory" as inventory' in diagram

    def test_compound_payment_declared(self, diagram):
        assert 'state "Payment" as payment' in diagram

    def test_compound_shipping_declared(self, diagram):
        assert 'state "Shipping" as shipping' in diagram

    def test_atomic_top_states_declared(self, diagram):
        assert 'state "Idle" as idle' in diagram
        assert 'state "Cancelled" as cancelled' in diagram
        assert 'state "Failed" as failed' in diagram

    def test_multi_word_state_name_backordered(self, diagram):
        # backordered → "Backordered"
        assert 'state "Backordered" as backordered' in diagram

    def test_multi_word_state_name_stock_review(self, diagram):
        # stock_review → "Stock review"
        assert 'state "Stock review" as stock_review' in diagram

    def test_multi_word_state_name_out_of_stock(self, diagram):
        assert 'state "Out of stock" as out_of_stock' in diagram

    def test_multi_word_state_pay_hold(self, diagram):
        assert 'state "Pay hold" as pay_hold' in diagram


# ---------------------------------------------------------------------------
# Parallel region separator
# ---------------------------------------------------------------------------


class TestParallelSeparator:
    def test_double_dash_separator_present(self, diagram):
        assert "\n            --\n" in diagram

    def test_two_separators_for_three_tracks(self, diagram):
        # Three tracks → two -- separators (inside fulfillment at 12-space indent)
        assert diagram.count("\n            --\n") == 2


# ---------------------------------------------------------------------------
# Guards on transitions
# ---------------------------------------------------------------------------


class TestGuards:
    def test_is_approved_guard_on_start(self, diagram):
        assert "start [is_approved]" in diagram

    def test_inventory_reserved_guard_on_process_payment(self, diagram):
        assert "process_payment [inventory_is_reserved]" in diagram

    def test_ready_to_ship_guard_on_begin_shipping(self, diagram):
        assert "begin_shipping [ready_to_ship]" in diagram


# ---------------------------------------------------------------------------
# HistoryState handling
# ---------------------------------------------------------------------------


class TestHistoryState:
    def test_history_state_declared_inside_shipping(self, diagram):
        # [H] is the label for the HistoryState pseudo-node
        assert 'state "[H]" as h' in diagram

    def test_resume_targets_history_pseudo_state(self, diagram):
        assert "on_hold --> h : resume" in diagram


# ---------------------------------------------------------------------------
# Transition correctness
# ---------------------------------------------------------------------------


class TestTransitions:
    def test_submit(self, diagram):
        assert "idle --> validation : submit" in diagram

    def test_pause(self, diagram):
        assert "fulfillment --> on_hold : pause [before_dispatch]" in diagram

    def test_fail(self, diagram):
        assert "fulfillment --> failed : fail" in diagram

    def test_cancel_from_idle(self, diagram):
        assert "idle --> cancelled : cancel" in diagram

    def test_cancel_from_validation(self, diagram):
        assert "validation --> cancelled : cancel" in diagram

    def test_cancel_from_fulfillment(self, diagram):
        assert "fulfillment --> cancelled : cancel [before_dispatch]" in diagram

    def test_approve_internal_to_validation(self, diagram):
        assert "reviewing --> approved : approve" in diagram

    def test_reject_internal_to_validation(self, diagram):
        assert "reviewing --> rejected : reject" in diagram

    def test_reserve_internal_to_inventory(self, diagram):
        assert "checking --> reserved : reserve" in diagram

    def test_backorder_transition(self, diagram):
        assert "checking --> backordered : backorder" in diagram

    def test_stock_available_transition(self, diagram):
        assert "backordered --> reserved : stock_available" in diagram

    def test_request_approval_transition(self, diagram):
        assert "backordered --> stock_review : request_approval" in diagram

    def test_approve_partial_transition(self, diagram):
        assert "stock_review --> reserved : approve_partial" in diagram

    def test_decline_partial_transition(self, diagram):
        assert "stock_review --> out_of_stock : decline_partial" in diagram

    def test_mark_unavailable_from_reserved(self, diagram):
        assert "reserved --> out_of_stock : mark_unavailable" in diagram

    def test_begin_shipping_internal_to_shipping(self, diagram):
        assert "ship_hold --> preparing : begin_shipping [ready_to_ship]" in diagram

    def test_acknowledge_internal_to_shipping(self, diagram):
        assert "delivered --> acknowledged : acknowledge" in diagram


# ---------------------------------------------------------------------------
# Scope: internal transitions not duplicated at top level
# ---------------------------------------------------------------------------


class TestScopingNoDuplicates:
    def test_approve_not_at_top_level(self, diagram):
        """'approve' should only appear once (inside the validation block)."""
        assert diagram.count("reviewing --> approved : approve") == 1

    def test_reserve_not_at_top_level(self, diagram):
        assert diagram.count("checking --> reserved : reserve") == 1

    def test_begin_shipping_appears_once(self, diagram):
        assert diagram.count("begin_shipping") == 1


# ---------------------------------------------------------------------------
# Machine-class wrapper (outermost compound state)
# ---------------------------------------------------------------------------


class TestMachineWrapper:
    def test_wrapper_state_declared(self, diagram):
        assert 'state "Order Processing" as OrderProcessing' in diagram

    def test_outermost_entry_is_wrapper(self, diagram):
        """Global [*] must point to the wrapper, not directly to idle."""
        lines = diagram.splitlines()
        init_line = next(ln.strip() for ln in lines if "[*] -->" in ln)
        assert init_line == "[*] --> OrderProcessing"

    def test_idle_initial_is_inside_wrapper(self, diagram):
        """[*] --> idle must appear inside the wrapper block (8+ leading spaces)."""
        lines = diagram.splitlines()
        for ln in lines:
            if "[*] --> idle" in ln:
                assert ln.startswith(" " * 8), f"Expected 8+ leading spaces: {ln!r}"
                break
        else:
            pytest.fail("[*] --> idle not found")

    def test_cancelled_final_inside_wrapper(self, diagram):
        lines = diagram.splitlines()
        for ln in lines:
            if "cancelled --> [*]" in ln:
                assert ln.startswith(" " * 8)
                break
        else:
            pytest.fail("cancelled --> [*] not found")

    def test_wrapper_closes_before_end(self, diagram):
        assert "    }" in diagram


# ---------------------------------------------------------------------------
# write_mermaid
# ---------------------------------------------------------------------------


class TestWriteMermaid:
    def test_writes_file(self, sm_class, tmp_path):
        out = tmp_path / "test_diagram.mmd"
        result = write_mermaid(sm_class, out)
        assert result == out
        assert out.exists()

    def test_file_content_matches_to_mermaid(self, sm_class, tmp_path):
        out = tmp_path / "diagram.mmd"
        write_mermaid(sm_class, out)
        assert out.read_text(encoding="utf-8") == to_mermaid(sm_class)

    def test_creates_parent_dirs(self, sm_class, tmp_path):
        nested = tmp_path / "a" / "b" / "diagram.mmd"
        write_mermaid(sm_class, nested)
        assert nested.exists()

    def test_direction_kwarg_forwarded(self, sm_class, tmp_path):
        out = tmp_path / "tb.mmd"
        write_mermaid(sm_class, out, direction="TB")
        content = out.read_text(encoding="utf-8")
        assert "direction TB" in content
