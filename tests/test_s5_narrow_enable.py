# -*- coding: utf-8 -*-
"""Offline S5 plan-A guards for the single-row enable path."""
from __future__ import annotations

import sys
import types


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
    monkeypatch.setattr(cap, "_locate_known_main_row_for_resource_fallback", lambda *args: calls.append("terminal") or dict(HANDLE))
    monkeypatch.setattr(cap, "_shot", lambda *args, **kwargs: None)
    return calls


def test_enable_happy_path_uses_one_anchored_click_then_fresh_terminal_gate(monkeypatch):
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
    monkeypatch.setattr(cap, "_locate_known_main_row_for_resource_fallback", lambda *args: (_ for _ in ()).throw(AssertionError("no terminal gate")))

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
