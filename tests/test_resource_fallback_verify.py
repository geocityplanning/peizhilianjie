# -*- coding: utf-8 -*-
"""Offline behavior tests for required resource fallback persistence verification."""
from __future__ import annotations

import sys
import types

import pytest


EXPECTED = "https://example.invalid/resource-fallback"


def _stub_login_and_import():
    if "actions.ensure_login" not in sys.modules:
        stub = types.ModuleType("actions.ensure_login")
        stub.ensure_login = lambda page=None: {"success": False}
        stub.is_logged_in = lambda page: True
        sys.modules["actions.ensure_login"] = stub
    from app.executor.actions import create_app_v2 as cap

    return cap


class DialogPage:
    def __init__(self, readback):
        self.readback = readback
        self.save_clicks = 0

    def evaluate(self, script, payload=None):
        if "const wanted" in script:
            return dict(self.readback)
        return True


class StagePage:
    def __init__(self):
        self.save_clicks = 0

    def goto(self, *args, **kwargs):
        return None

    def wait_for_selector(self, *args, **kwargs):
        return None

    def wait_for_timeout(self, *args, **kwargs):
        return None

    def get_by_text(self, *args, **kwargs):
        class Locator:
            def first(self_inner):
                return self_inner

            def click(self_inner, **kwargs):
                return None

        return Locator()

    @property
    def keyboard(self):
        class Keyboard:
            def press(self_inner, *args, **kwargs):
                return None

        return Keyboard()

    def evaluate(self, script, payload=None):
        return True


def _prepare_stage(monkeypatch, cap, page):
    monkeypatch.setattr(cap, "_set_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(cap, "_cleanup_overlays", lambda page: None)
    monkeypatch.setattr(cap, "_shot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cap,
        "_collect_all_app_rows",
        lambda page, reset_filters=False: {"old-id": {"app_id": "old-id"}},
    )
    monkeypatch.setattr(
        cap,
        "_search_by_link",
        lambda page, link: {"clicked": True, "error": None, "uncertain": False},
    )
    monkeypatch.setattr(cap, "_go_tab", lambda page, name: True)
    monkeypatch.setattr(cap, "_js_select", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_select_and_verify_create_channel", lambda *args, **kwargs: {"success": True})

    def counted_save(page):
        page.save_clicks += 1
        return True

    monkeypatch.setattr(cap, "_click_save_button", counted_save)


def _stage_data():
    return {"actual_channel_name": "channel-a", "base_platform": ""}


@pytest.mark.parametrize(
    ("readback", "expected_code"),
    [
        ({"dialog_found": True, "input_found": False, "value": ""}, "RESOURCE_FALLBACK_VERIFY_FAILED"),
        ({"dialog_found": False, "input_found": False, "value": ""}, "RESOURCE_FALLBACK_VERIFY_FAILED"),
        ({"dialog_found": True, "input_found": True, "value": "different"}, "RESOURCE_FALLBACK_VERIFY_FAILED"),
    ],
    ids=["input-missing", "copy-dialog-unreadable", "readback-mismatch"],
)
def test_before_save_readback_failures_stop_with_zero_save(monkeypatch, readback, expected_code):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_js_fill", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_read_visible_copy_dialog_input", lambda *args, **kwargs: readback)

    result = cap._stage_create_save(
        page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type"
    )

    assert result["success"] is False
    assert result["error"]["code"] == expected_code
    assert result["error"]["stage"] == "FILL"
    assert page.save_clicks == 0


def test_js_fill_false_stops_with_zero_save(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_js_fill", lambda *args, **kwargs: False)

    result = cap._stage_create_save(
        page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type"
    )

    assert result["success"] is False
    assert result["error"]["code"] == "RESOURCE_FALLBACK_VERIFY_FAILED"
    assert result["error"]["stage"] == "FILL"
    assert page.save_clicks == 0


def test_normal_fill_and_exact_readback_passes(monkeypatch):
    cap = _stub_login_and_import()
    page = DialogPage({"dialog_found": True, "input_found": True, "value": EXPECTED})
    monkeypatch.setattr(cap, "_js_fill", lambda *args, **kwargs: True)

    result = cap._fill_and_verify_resource_fallback(page, EXPECTED)

    assert result == {"success": True}
    assert page.save_clicks == 0


@pytest.mark.parametrize(
    "post_error",
    [
        "保存后资源不足中间页链接回读不一致，已停止",
        "保存后资源不足中间页链接控件不可读取，已停止",
    ],
    ids=["value-lost", "unverifiable"],
)
def test_post_save_fallback_failure_never_returns_success(monkeypatch, post_error):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_fill_and_verify_resource_fallback", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(cap, "capture_page_errors", lambda *args, **kwargs: {"dialog_open": False})
    monkeypatch.setattr(cap, "_identify_new_app", lambda *args, **kwargs: {"success": True, "app_id": "new-id"})
    monkeypatch.setattr(cap, "_find_target_row_by_id", lambda *args, **kwargs: {"found": True, "row_idx": 0})
    monkeypatch.setattr(
        cap,
        "_verify_persisted_resource_fallback",
        lambda *args, **kwargs: {
            "success": False,
            "error": cap.err("RESOURCE_FALLBACK_VERIFY_FAILED", "VERIFY", post_error, cap.NEXT_MANUAL),
        },
    )

    result = cap._stage_create_save(
        page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type"
    )

    assert result["success"] is False
    assert result["error"]["code"] == "RESOURCE_FALLBACK_VERIFY_FAILED"
    assert result["error"]["stage"] == "VERIFY"
    assert result["save_may_have_occurred"] is True
    assert page.save_clicks == 1


def test_post_save_dialog_failure_is_not_success(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    monkeypatch.setattr(cap, "_open_copy_dialog_by_app_id", lambda *args, **kwargs: False)

    result = cap._verify_persisted_resource_fallback(page, "new-id", EXPECTED)

    assert result["success"] is False
    assert result["error"]["code"] == "RESOURCE_FALLBACK_VERIFY_FAILED"
    assert result["error"]["stage"] == "VERIFY"
    assert page.save_clicks == 0


def test_post_save_exact_readback_passes_without_save(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    opened = []
    monkeypatch.setattr(
        cap, "_open_copy_dialog_by_app_id", lambda _page, app_id: opened.append(app_id) or True
    )
    monkeypatch.setattr(cap, "_go_tab", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_close_copy_dialog_after_verify", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        cap, "_verify_resource_fallback_readback", lambda *args, **kwargs: {"success": True}
    )

    result = cap._verify_persisted_resource_fallback(page, "new-id", EXPECTED)

    assert result == {"success": True}
    assert opened == ["new-id"]
    assert page.save_clicks == 0


def test_post_save_value_mismatch_fails_after_readonly_open(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    monkeypatch.setattr(cap, "_open_copy_dialog_by_app_id", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_go_tab", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_close_copy_dialog_after_verify", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        cap,
        "_verify_resource_fallback_readback",
        lambda *args, **kwargs: {
            "success": False,
            "error": cap.err(
                "RESOURCE_FALLBACK_VERIFY_FAILED", "VERIFY", "资源不足中间页链接回读不一致，已停止", cap.NEXT_MANUAL
            ),
        },
    )

    result = cap._verify_persisted_resource_fallback(page, "new-id", EXPECTED)

    assert result["success"] is False
    assert result["error"]["stage"] == "VERIFY"
    assert page.save_clicks == 0


def test_post_save_unready_dialog_fails_and_closes(monkeypatch):
    cap = _stub_login_and_import()

    class UnreadyPage(StagePage):
        def wait_for_selector(self, *args, **kwargs):
            raise RuntimeError("dialog unavailable")

    page = UnreadyPage()
    close_calls = []
    monkeypatch.setattr(cap, "_open_copy_dialog_by_app_id", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        cap, "_close_copy_dialog_after_verify", lambda *args, **kwargs: close_calls.append(True) or True
    )

    result = cap._verify_persisted_resource_fallback(page, "new-id", EXPECTED)

    assert result["success"] is False
    assert result["error"]["stage"] == "VERIFY"
    assert close_calls == [True]
    assert page.save_clicks == 0
