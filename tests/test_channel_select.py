# -*- coding: utf-8 -*-
"""Offline tests for exact channel selection before save.

These tests never connect to UAT and never write real data.
"""
from __future__ import annotations

import sys
import types


def _stub_login_and_import():
    if "actions.ensure_login" not in sys.modules:
        stub = types.ModuleType("actions.ensure_login")
        stub.ensure_login = lambda page=None: {"success": False}
        stub.is_logged_in = lambda page: True
        sys.modules["actions.ensure_login"] = stub
    from app.executor.actions import create_app_v2 as cap

    return cap


def _layer(
    texts,
    *,
    shown=True,
    aria_hidden=False,
    is_select_dropdown=False,
    dialog_select_open=False,
    in_copy_dialog=False,
    anchored_to_channel_input=False,
    is_channel_popover=False,
):
    return {
        "shown": shown,
        "aria_hidden": aria_hidden,
        "is_select_dropdown": is_select_dropdown,
        "dialog_select_open": dialog_select_open,
        "in_copy_dialog": in_copy_dialog,
        "anchored_to_channel_input": anchored_to_channel_input,
        "is_channel_popover": is_channel_popover,
        "options": [{"index": index, "text": text} for index, text in enumerate(texts)],
    }


class ChannelPage:
    def __init__(
        self,
        *,
        layers=None,
        opened=True,
        selected_value="chan-a",
        snapshots=None,
        click_result=True,
        has_dialog=True,
    ):
        self.layers = layers if layers is not None else []
        self.opened = opened
        self.selected_value = selected_value
        self.snapshots = list(snapshots) if snapshots is not None else None
        self.click_result = click_result
        self.has_dialog = has_dialog
        self.save_clicks = 0
        self.clicked_option = False
        self.scripts = []

    def evaluate(self, script, payload=None):
        text = script if isinstance(script, str) else ""
        self.scripts.append(text)
        if "CHANNEL_LAYER_OPEN" in text:
            return self.opened
        if "CHANNEL_LAYER_SNAPSHOT" in text:
            if self.snapshots is not None and self.snapshots:
                return self.snapshots.pop(0)
            return {"has_dialog": self.has_dialog, "layers": self.layers}
        if "CHANNEL_OPTION_CLICK" in text:
            self.clicked_option = bool(self.click_result)
            return self.click_result
        if "CHANNEL_LAYER_CLEANUP" in text:
            return True
        if "fromInput" in text:
            return self.selected_value
        if "保存" in text:
            self.save_clicks += 1
            return True
        return True

    def wait_for_timeout(self, milliseconds):
        return None


def test_unique_exact_channel_is_selected_and_read_back():
    cap = _stub_login_and_import()
    page = ChannelPage(layers=[_layer(["chan-a"], is_channel_popover=True)])
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is True
    assert page.clicked_option is True
    assert page.save_clicks == 0


def test_zero_exact_channel_fails():
    cap = _stub_login_and_import()
    page = ChannelPage(layers=[_layer(["chan-b"], is_channel_popover=True)], selected_value="")
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is False
    assert result["error"]["code"] == "CHANNEL_SELECT_FAILED"
    assert page.clicked_option is False
    assert page.save_clicks == 0


def test_multiple_exact_channels_fail():
    cap = _stub_login_and_import()
    page = ChannelPage(
        layers=[
            _layer(["chan-a"], is_channel_popover=True),
            _layer(["chan-a"], anchored_to_channel_input=True),
        ]
    )
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is False
    assert page.clicked_option is False
    assert page.save_clicks == 0


def test_hidden_old_layer_with_same_name_is_ignored():
    cap = _stub_login_and_import()
    page = ChannelPage(
        layers=[
            _layer(["chan-a"], shown=False, is_channel_popover=True),
            _layer(["chan-a"], is_channel_popover=True),
        ]
    )
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is True
    assert page.clicked_option is True
    assert page.save_clicks == 0


def test_visible_other_form_popover_is_not_counted():
    cap = _stub_login_and_import()
    page = ChannelPage(
        layers=[
            _layer(["chan-a"]),
            _layer(["chan-a"], is_channel_popover=True),
        ]
    )
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is True
    assert page.clicked_option is True
    assert page.save_clicks == 0


def test_hidden_layer_with_other_names_is_ignored():
    cap = _stub_login_and_import()
    page = ChannelPage(
        layers=[
            _layer(["chan-b", "chan-c"], shown=False, is_channel_popover=True),
            _layer(["chan-a"], is_channel_popover=True),
        ]
    )
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is True
    assert page.clicked_option is True


def test_leftover_select_dropdown_is_not_counted():
    cap = _stub_login_and_import()
    page = ChannelPage(
        layers=[
            _layer(["chan-a"], is_select_dropdown=True, dialog_select_open=False),
            _layer(["chan-a"], anchored_to_channel_input=True),
        ]
    )
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is True
    assert page.clicked_option is True


def test_visible_layer_duplicate_match_fails():
    cap = _stub_login_and_import()
    page = ChannelPage(layers=[_layer(["chan-a", "chan-a"], is_channel_popover=True)])
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is False
    assert result["error"]["code"] == "CHANNEL_SELECT_FAILED"
    assert page.clicked_option is False
    assert page.save_clicks == 0


def test_delayed_layer_appears_then_selects():
    cap = _stub_login_and_import()
    empty = {"has_dialog": True, "layers": []}
    present = {"has_dialog": True, "layers": [_layer(["chan-a"], is_channel_popover=True)]}
    page = ChannelPage(snapshots=[empty, empty, present])
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is True
    assert page.clicked_option is True
    assert page.save_clicks == 0


def test_same_prefix_is_not_used_as_fallback():
    cap = _stub_login_and_import()
    page = ChannelPage(layers=[_layer(["chan-ab"], is_channel_popover=True)], selected_value="chan-ab")
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is False
    assert page.clicked_option is False


def test_click_without_selected_value_fails():
    cap = _stub_login_and_import()
    page = ChannelPage(layers=[_layer(["chan-a"], is_channel_popover=True)], selected_value="")
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is False
    assert "回读" in result["error"]["message"]
    assert page.save_clicks == 0


def test_click_then_readback_mismatch_fails():
    cap = _stub_login_and_import()
    page = ChannelPage(layers=[_layer(["chan-a"], is_channel_popover=True)], selected_value="chan-b")
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is False
    assert "回读" in result["error"]["message"]
    assert page.save_clicks == 0


def test_unopened_control_fails():
    cap = _stub_login_and_import()
    page = ChannelPage(opened=False, layers=[_layer(["chan-a"], is_channel_popover=True)])
    result = cap._select_and_verify_create_channel(page, "chan-a")
    assert result["success"] is False
    assert page.clicked_option is False


def test_layer_usable_requires_positive_binding():
    cap = _stub_login_and_import()
    assert cap._channel_layer_is_usable(_layer(["chan-a"], is_channel_popover=True)) is True
    assert cap._channel_layer_is_usable(_layer(["chan-a"], in_copy_dialog=True)) is True
    assert cap._channel_layer_is_usable(_layer(["chan-a"], anchored_to_channel_input=True)) is True
    assert cap._channel_layer_is_usable(_layer(["chan-a"])) is False
    assert cap._channel_layer_is_usable(_layer(["chan-a"], shown=False, is_channel_popover=True)) is False
    assert cap._channel_layer_is_usable(_layer(["chan-a"], aria_hidden=True, is_channel_popover=True)) is False
    assert cap._channel_layer_is_usable(_layer(["chan-a"], is_select_dropdown=True, is_channel_popover=True)) is False


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
        if isinstance(script, str) and "保存" in script:
            self.save_clicks += 1
            return True
        return True


def test_channel_select_failure_clicks_save_zero_times(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    monkeypatch.setattr(cap, "_set_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(cap, "_cleanup_overlays", lambda page: None)
    monkeypatch.setattr(cap, "_shot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cap,
        "_collect_all_app_rows",
        lambda page, reset_filters=False: {"1": {"app_id": "1"}},
    )
    monkeypatch.setattr(
        cap,
        "_search_by_link",
        lambda page, link: {"clicked": True, "error": None, "uncertain": False},
    )
    monkeypatch.setattr(cap, "_go_tab", lambda page, name: True)
    monkeypatch.setattr(cap, "_js_fill", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_js_select", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        cap,
        "_select_and_verify_create_channel",
        lambda page, name: {
            "success": False,
            "error": cap.err(
                "CHANNEL_SELECT_FAILED",
                "FILL",
                "所属渠道回读值与目标不一致，已停止保存",
                cap.NEXT_MANUAL,
            ),
        },
    )
    original_click = cap._click_save_button

    def counted_save(page):
        page.save_clicks += 1
        return original_click(page)

    monkeypatch.setattr(cap, "_click_save_button", counted_save)
    result = cap._stage_create_save(
        page,
        "exec-1",
        {"actual_channel_name": "chan-a", "base_platform": ""},
        "https://example.invalid/#/?i=1",
        "1",
        "demo",
        "",
        "",
        "",
    )
    assert result["success"] is False
    assert result["error"]["code"] == "CHANNEL_SELECT_FAILED"
    assert page.save_clicks == 0
