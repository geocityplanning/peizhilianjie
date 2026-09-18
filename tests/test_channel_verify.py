# -*- coding: utf-8 -*-
"""Offline tests for post-save channel verification.

These tests never connect to UAT, Chrome, or CDP, and never create or save.
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


def _col(text, classes=""):
    return {"text": text, "classes": classes}


def test_wrong_main_cell_does_not_hide_matching_detail():
    cap = _stub_login_and_import()
    decision = cap._channel_verify_decision("chan-a", "应用名称误读", "chan-a")
    assert decision["verified"] is True
    assert decision["source"] == "detail"
    assert decision["reason"] is None


def test_main_column_can_verify_when_detail_missing():
    cap = _stub_login_and_import()
    decision = cap._channel_verify_decision("chan-a", "chan-a", "")
    assert decision["verified"] is True
    assert decision["source"] == "main"


def test_missing_channel_fields_are_unverified_not_mismatch():
    cap = _stub_login_and_import()
    decision = cap._channel_verify_decision("chan-a", "", "")
    assert decision["verified"] is False
    assert decision["reason"] == "channel_unverified"


def test_true_mismatch_when_neither_field_matches():
    cap = _stub_login_and_import()
    decision = cap._channel_verify_decision("chan-a", "chan-b", "chan-c")
    assert decision["verified"] is False
    assert decision["reason"] == "channel_mismatch"


def test_align_skips_expand_select_and_gutter():
    cap = _stub_login_and_import()
    headers = [
        _col("", "el-table__expand-column"),
        _col("", "el-table-column--selection"),
        _col("ID"),
        _col("应用名称"),
        _col("所属渠道"),
        _col("", "gutter"),
    ]
    cells = [
        _col("", "el-table__expand-column"),
        _col("", "el-table-column--selection"),
        _col("12052"),
        _col("演示应用"),
        _col("chan-a"),
        _col("", "gutter"),
    ]
    assert cap._main_channel_from_row(headers, cells) == "chan-a"
    aligned = cap._align_header_cells(headers, cells)
    assert [item["header"] for item in aligned] == ["ID", "应用名称", "所属渠道"]


def test_align_returns_empty_when_counts_differ():
    cap = _stub_login_and_import()
    headers = [_col("ID"), _col("所属渠道")]
    cells = [_col("12052")]
    assert cap._align_header_cells(headers, cells) == []
    assert cap._main_channel_from_row(headers, cells) == ""


def test_channel_id_header_is_not_used_as_channel_name():
    cap = _stub_login_and_import()
    headers = [_col("ID"), _col("渠道ID"), _col("应用名称")]
    cells = [_col("12052"), _col("999"), _col("演示应用")]
    assert cap._main_channel_from_row(headers, cells) == ""


def _install_locate_mocks(monkeypatch, cap, raw):
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_go_to_first_page", lambda page: True)
    monkeypatch.setattr(cap, "_expand_visible_rows", lambda page: None)
    monkeypatch.setattr(cap, "_click_next_page_and_wait", lambda page: False)

    class Page:
        def evaluate(self, script, payload=None):
            return raw

    return Page()


def test_find_row_prefers_detail_over_wrong_main(monkeypatch):
    cap = _stub_login_and_import()
    page = _install_locate_mocks(
        monkeypatch,
        cap,
        {
            "id_matched": True,
            "headers": [_col("", "el-table__expand-column"), _col("ID"), _col("所属渠道")],
            "cells": [
                _col("", "el-table__expand-column"),
                _col("12052"),
                _col("错位单元格"),
            ],
            "detail_channel": "chan-a",
            "row_idx": 3,
        },
    )
    located = cap._find_target_row_by_id(page, "12052", "chan-a")
    assert located["found"] is True
    assert located["channel_source"] == "detail"
    assert located["row_idx"] == 3


def test_find_row_mismatch_when_id_exists_and_channel_differs(monkeypatch):
    cap = _stub_login_and_import()
    page = _install_locate_mocks(
        monkeypatch,
        cap,
        {
            "id_matched": True,
            "headers": [_col("ID"), _col("所属渠道")],
            "cells": [_col("12052"), _col("chan-b")],
            "detail_channel": "chan-b",
            "row_idx": 1,
        },
    )
    located = cap._find_target_row_by_id(page, "12052", "chan-a")
    assert located["found"] is False
    assert located["reason"] == "channel_mismatch"


def test_find_row_unverified_when_channel_fields_missing(monkeypatch):
    cap = _stub_login_and_import()
    page = _install_locate_mocks(
        monkeypatch,
        cap,
        {
            "id_matched": True,
            "headers": [_col("ID"), _col("应用名称")],
            "cells": [_col("12052"), _col("演示应用")],
            "detail_channel": "",
            "row_idx": 1,
        },
    )
    located = cap._find_target_row_by_id(page, "12052", "chan-a")
    assert located["found"] is False
    assert located["reason"] == "channel_unverified"


def test_identify_retries_filter_then_falls_back_without_create(monkeypatch):
    cap = _stub_login_and_import()
    calls = {"search": 0, "create": 0, "save": 0}

    def search(page, channel, return_detail=False):
        calls["search"] += 1
        return {
            "filter_stable": False,
            "request_carried_target_filter": True,
            "table_changed": True,
            "final_row_count": 0,
        }

    def collect(page, reset_filters=False):
        return {"12052": {"app_id": "12052", "app_name": "demo", "channel_name": "chan-a"}}

    monkeypatch.setattr(cap, "_search_list_by_channel", search)
    monkeypatch.setattr(cap, "_collect_all_app_rows", collect)
    monkeypatch.setattr(cap, "execute_create_app", lambda *args, **kwargs: calls.__setitem__("create", calls["create"] + 1))

    class Page:
        def wait_for_timeout(self, ms):
            return None

    result = cap._identify_new_app(Page(), {"1"}, "chan-a", "demo")
    assert result["success"] is True
    assert result["app_id"] == "12052"
    assert calls["search"] == cap._POST_SAVE_FILTER_ATTEMPTS
    assert calls["create"] == 0


def test_identify_stops_on_pagination_unstable(monkeypatch):
    cap = _stub_login_and_import()
    monkeypatch.setattr(
        cap,
        "_search_list_by_channel",
        lambda *args, **kwargs: {"filter_stable": False, "request_carried_target_filter": False},
    )
    monkeypatch.setattr(cap, "_collect_all_app_rows", lambda *args, **kwargs: None)
    result = cap._identify_new_app(object(), {"1"}, "chan-a", "demo")
    assert result["success"] is False
    assert result["error"]["code"] == "APP_ID_SNAPSHOT_FAILED"


def test_identify_multiple_new_ids_stop(monkeypatch):
    cap = _stub_login_and_import()
    monkeypatch.setattr(
        cap,
        "_search_list_by_channel",
        lambda *args, **kwargs: {"filter_stable": False, "request_carried_target_filter": False},
    )
    monkeypatch.setattr(
        cap,
        "_collect_all_app_rows",
        lambda *args, **kwargs: {
            "12052": {"app_id": "12052"},
            "12053": {"app_id": "12053"},
        },
    )
    result = cap._identify_new_app(object(), set(), "chan-a", "demo")
    assert result["success"] is False
    assert result["error"]["code"] == "NEW_APP_ID_AMBIGUOUS"


def test_identify_zero_new_ids_stop(monkeypatch):
    cap = _stub_login_and_import()
    monkeypatch.setattr(
        cap,
        "_search_list_by_channel",
        lambda *args, **kwargs: {"filter_stable": False, "request_carried_target_filter": False},
    )
    monkeypatch.setattr(cap, "_collect_all_app_rows", lambda *args, **kwargs: {"1": {"app_id": "1"}})

    class Page:
        def wait_for_timeout(self, ms):
            return None

    result = cap._identify_new_app(Page(), {"1"}, "chan-a", "demo")
    assert result["success"] is False
    assert result["error"]["code"] == "NEW_APP_ID_NOT_FOUND"


def test_identify_function_never_creates_or_saves():
    cap = _stub_login_and_import()
    import ast
    from pathlib import Path

    source = Path(cap.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_identify_new_app"
    )
    func_src = ast.get_source_segment(source, func) or ""
    assert "execute_create_app" not in func_src
    assert "create_channel" not in func_src
    assert "_js_fill" not in func_src
    assert "保存按钮" not in func_src
