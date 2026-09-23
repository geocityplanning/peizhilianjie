# -*- coding: utf-8 -*-
"""Offline S5 plan-A guards for the single-row enable path."""
from __future__ import annotations

import sys
import types

import pytest


def _cap():
    if "actions.ensure_login" not in sys.modules:
        stub = types.ModuleType("actions.ensure_login")
        stub.ensure_login = lambda page=None: {"success": False}
        stub.is_logged_in = lambda page: True
        sys.modules["actions.ensure_login"] = stub
    from app.executor.actions import create_app_v2 as cap
    return cap


HANDLE = {"success": True, "page": 1, "row_idx": 0, "row_key": "row-1", "key_kind": "native"}


class Page:
    def __init__(self, message_box_count=0):
        self.message_box_count = message_box_count
        self.scripts = []
        self.waits = []

    def evaluate(self, script, data=None):
        self.scripts.append((script, data))
        if data is None and ".el-message-box__wrapper" in script and "filter" in script:
            return self.message_box_count
        return True

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


def _install_enable_success(monkeypatch, cap, page):
    observer = cap._SaveClickObserver()
    calls = []
    monkeypatch.setattr(cap, "_locate_known_main_row_in_current_view", lambda *args: dict(HANDLE))
    monkeypatch.setattr(cap, "_attach_save_click_observer", lambda _page: observer)
    monkeypatch.setattr(cap, "_detach_save_click_observer", lambda _observer: calls.append("detach"))
    monkeypatch.setattr(cap, "_click_unchecked_switch_by_known_main_row", lambda *args: calls.append("switch") or True)
    monkeypatch.setattr(cap, "_wait_for_switch_action_observation", lambda *args: {"outcome": "success"})
    states = iter(["switch_unchecked", "switch_checked"])
    monkeypatch.setattr(cap, "_post_save_narrow_row_switch_state", lambda *args: next(states))
    monkeypatch.setattr(cap, "_verify_enable_with_fresh_exact_terminal_query", lambda *args: calls.append("terminal") or True)
    monkeypatch.setattr(cap, "_shot", lambda *args, **kwargs: None)
    return calls


def test_enable_happy_path_uses_one_anchored_click_then_fresh_exact_terminal_gate(monkeypatch):
    cap = _cap()
    page = Page()
    calls = _install_enable_success(monkeypatch, cap, page)

    result = cap._stage_enable(page, "exec", "id", "name", "channel", dict(HANDLE))

    assert result == {"success": True, "error": None}
    assert calls == ["switch", "detach", "terminal"]


def test_enable_rejects_existing_message_box_before_observer_or_switch(monkeypatch):
    cap = _cap()
    page = Page(message_box_count=1)
    attached = []
    monkeypatch.setattr(cap, "_locate_known_main_row_in_current_view", lambda *args: dict(HANDLE))
    monkeypatch.setattr(cap, "_post_save_narrow_row_switch_state", lambda *args: "switch_unchecked")
    monkeypatch.setattr(cap, "_attach_save_click_observer", lambda _page: attached.append(True))

    result = cap._stage_enable(page, "exec", "id", "name", "channel", dict(HANDLE))

    assert result["success"] is False
    assert result["error"]["next_action"] == cap.NEXT_QUERY
    assert attached == []


def test_enable_non_success_write_never_runs_checked_or_terminal_gate(monkeypatch):
    cap = _cap()
    page = Page()
    observer = cap._SaveClickObserver()
    monkeypatch.setattr(cap, "_locate_known_main_row_in_current_view", lambda *args: dict(HANDLE))
    monkeypatch.setattr(cap, "_post_save_narrow_row_switch_state", lambda *args: "switch_unchecked")
    monkeypatch.setattr(cap, "_attach_save_click_observer", lambda _page: observer)
    monkeypatch.setattr(cap, "_detach_save_click_observer", lambda _observer: None)
    monkeypatch.setattr(cap, "_click_unchecked_switch_by_known_main_row", lambda *args: True)
    monkeypatch.setattr(cap, "_wait_for_switch_action_observation", lambda *args: {"outcome": "business_rejected"})
    monkeypatch.setattr(cap, "_post_save_narrow_row_switch_state", lambda *args: "switch_unchecked")
    monkeypatch.setattr(cap, "_verify_enable_with_fresh_exact_terminal_query", lambda *args: (_ for _ in ()).throw(AssertionError("no terminal gate")))

    result = cap._stage_enable(page, "exec", "id", "name", "channel", dict(HANDLE))

    assert result["success"] is False
    assert result["error"]["next_action"] == cap.NEXT_QUERY


def test_atomic_switch_helper_contains_all_plan_a_refusal_guards():
    cap = _cap()
    page = Page()

    assert cap._click_unchecked_switch_by_known_main_row(page, "id", 1, 0, "row-1", "native", "name", "channel") is True
    script, data = page.scripts[-1]
    assert data["logicalKey"] == "row-1"
    assert "messageBoxes.length !== 0" in script
    assert "switches.length !== 1" in script
    assert "is-disabled" in script and "aria-disabled" in script
    assert "input && input.disabled" in script and "is-checked" in script
    assert "switchControl.click()" in script
    assert "force" not in script and "Enter" not in script


def test_current_view_locator_is_read_only_and_does_not_reset_filters(monkeypatch):
    cap = _cap()
    page = Page()
    resets = []
    first = {"page": 1, "candidates": []}
    row = {"logical_key": "row-1", "row_idx": 0, "key_kind": "native"}
    monkeypatch.setattr(cap, "_go_to_first_page", lambda _page: True)
    monkeypatch.setattr(cap, "_scan_known_main_id_pages", lambda *args: {"status": "candidate", "snapshot": first, "decision": {"row": row}})
    monkeypatch.setattr(cap, "_snapshot_known_main_id_rows", lambda *args: {"success": True, "page": 1})
    monkeypatch.setattr(cap, "_known_main_identity_from_snapshot", lambda *args: {"success": True, "row": dict(row)})
    monkeypatch.setattr(cap, "_reset_list_filters", lambda _page: resets.append(True))

    result = cap._locate_known_main_row_in_current_view(page, "id", "name", "channel")

    assert result == HANDLE
    assert resets == []


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class _WindowPage:
    """Fake page whose message-box timeline advances only through poll waits."""
    def __init__(self, clock, counts, on_confirm=None):
        self.clock = clock
        self.counts = list(counts)
        self.on_confirm = on_confirm
        self.confirm_clicks = 0
        self.scripts = []

    def evaluate(self, script, data=None):
        self.scripts.append((script, data))
        if "buttons[0].click()" in script:
            self.confirm_clicks += 1
            if self.on_confirm is not None:
                self.on_confirm()
            return True
        if data is None and ".el-message-box__wrapper" in script and ".length" in script:
            return self.counts.pop(0) if self.counts else 0
        return True

    def wait_for_timeout(self, milliseconds):
        assert milliseconds == 150
        self.clock.now += 2.0


def _success_candidate(*, status=200, outcome="success"):
    return {
        "method": "POST",
        "path_hash": "0123456789abcdef",
        "completed": True,
        "http_status": status,
        "response_body": {"fetch_state": "available", "parse_state": "envelope_classified"},
        "business": {"outcome": outcome},
    }


def _action_window(monkeypatch, cap, counts, observer, on_confirm=None):
    clock = _Clock()
    monkeypatch.setattr(cap.time, "monotonic", clock.monotonic)
    return _WindowPage(clock, counts, on_confirm=on_confirm), observer


def test_switch_window_accepts_one_new_exact_confirm_then_one_successful_write(monkeypatch):
    cap = _cap()
    observer = cap._SaveClickObserver()

    def deliver_after_confirm():
        observer.candidates["one"] = _success_candidate()

    page, _ = _action_window(monkeypatch, cap, [1, 0, 0], observer, deliver_after_confirm)
    result = cap._wait_for_switch_action_observation(page, observer)

    assert result["outcome"] == "success"
    assert page.confirm_clicks == 1
    confirm_script = next(script for script, _ in page.scripts if "buttons[0].click()" in script)
    assert "boxes.length !== 1" in confirm_script
    assert "buttons.length !== 1" in confirm_script
    assert "text === '确定'" in confirm_script
    assert "!button.disabled" in confirm_script and "aria-disabled" in confirm_script


def test_switch_window_accepts_no_box_direct_unique_success_for_entire_window(monkeypatch):
    cap = _cap()
    observer = cap._SaveClickObserver()
    observer.candidates["one"] = _success_candidate()
    page, _ = _action_window(monkeypatch, cap, [0, 0, 0], observer)

    result = cap._wait_for_switch_action_observation(page, observer)

    assert result["outcome"] == "success"
    assert page.confirm_clicks == 0
    assert page.clock.now >= 6.0


@pytest.mark.parametrize(
    ("counts", "candidate_count", "expected"),
    [
        ([2], 0, "message_box_ambiguous"),
        ([1, 0, 1], 0, "message_box_ambiguous"),
        ([1], 1, "message_box_ambiguous"),
    ],
    ids=["multiple_boxes", "second_box", "write_before_box"],
)
def test_switch_window_rejects_ambiguous_message_box_attribution(monkeypatch, counts, candidate_count, expected):
    cap = _cap()
    observer = cap._SaveClickObserver()
    for index in range(candidate_count):
        observer.candidates[str(index)] = _success_candidate()
    page, _ = _action_window(monkeypatch, cap, counts, observer)

    result = cap._wait_for_switch_action_observation(page, observer)

    assert result["outcome"] == expected
    assert page.confirm_clicks == (1 if counts[:1] == [1] and candidate_count == 0 else 0)


@pytest.mark.parametrize(
    ("counts", "candidates", "expected"),
    [
        ([1, 0, 0], {}, "no_candidate"),
        ([0, 0, 0], {"one": _success_candidate(), "two": _success_candidate()}, "ambiguous_candidate"),
        ([0, 0, 0], {"one": _success_candidate(status=None)}, "non_2xx"),
        ([0, 0, 0], {"one": _success_candidate(outcome="rejected")}, "business_rejected"),
    ],
    ids=["confirm_then_zero_write", "multiple_writes", "status_unreadable", "business_rejected"],
)
def test_switch_window_rejects_missing_ambiguous_or_unsuccessful_write(monkeypatch, counts, candidates, expected):
    cap = _cap()
    observer = cap._SaveClickObserver()
    observer.candidates.update(candidates)
    page, _ = _action_window(monkeypatch, cap, counts, observer)

    result = cap._wait_for_switch_action_observation(page, observer)

    assert result["outcome"] == expected


@pytest.mark.parametrize(
    ("counts", "candidates"),
    [
        ([0, 0, 0], {}),
        ([0], {"one": _success_candidate(), "two": _success_candidate()}),
        ([1], {"one": _success_candidate()}),
        ([1, 0, 0], {"one": _success_candidate(outcome="rejected")}),
    ],
    ids=["no_write", "multiple_writes", "write_before_confirm", "business_rejected"],
)
def test_stage_failure_after_single_switch_detaches_and_never_runs_terminal_gate(monkeypatch, counts, candidates):
    cap = _cap()
    observer = cap._SaveClickObserver()
    observer.candidates.update(candidates)
    clock = _Clock()
    page = _WindowPage(clock, [0, *counts])  # first read is the pre-click zero baseline
    monkeypatch.setattr(cap.time, "monotonic", clock.monotonic)
    calls = []
    monkeypatch.setattr(cap, "_locate_known_main_row_in_current_view", lambda *args: dict(HANDLE))
    monkeypatch.setattr(cap, "_post_save_narrow_row_switch_state", lambda *args: "switch_unchecked")
    monkeypatch.setattr(cap, "_attach_save_click_observer", lambda _page: observer)
    monkeypatch.setattr(cap, "_detach_save_click_observer", lambda _observer: calls.append("detach"))
    monkeypatch.setattr(cap, "_click_unchecked_switch_by_known_main_row", lambda *args: calls.append("switch") or True)
    monkeypatch.setattr(cap, "_verify_enable_with_fresh_exact_terminal_query", lambda *args: calls.append("terminal") or True)

    result = cap._stage_enable(page, "exec", "id", "name", "channel", dict(HANDLE))

    assert result["success"] is False
    assert result["error"]["next_action"] == cap.NEXT_QUERY
    assert calls == ["switch", "detach"]
    assert page.confirm_clicks <= 1
