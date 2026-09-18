# -*- coding: utf-8 -*-
"""Offline tests for main-list table scoping against the copy dialog.

These tests never connect to UAT, Chrome, or CDP.
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


def _headers():
    return [
        _col("序号"), _col("ID"), _col("应用名称"), _col("所属渠道"), _col("应用链接"),
        _col("长连接"), _col("版本号"), _col("体验时长"), _col("创建时间"), _col("创建人"),
        _col("状态"), _col("白名单"), _col("云机链接"), _col("底座"), _col("结算类型"),
        _col("操作"), _col("", "el-table__cell gutter"),
    ]


def _cells(app_id, channel, app_name="演示应用"):
    return [
        _col("1"), _col(app_id), _col(app_name), _col(channel), _col("https://l.yun.139.com/s/x"),
        _col("https://plus.buy.139.com/mccloudgame/#/?i=x"), _col("v1"), _col("3"),
        _col("2026-01-01"), _col("tester"), _col("启用"), _col(""), _col(""),
        _col("华为底座2.0"), _col("云盘"), _col("复制"),
    ]


def _row(app_id, channel, row_idx=0):
    return {
        "cells": _cells(app_id, channel),
        "detail_id": "",
        "detail_app_name": "",
        "detail_channel": "",
        "row_idx": row_idx,
        "row_text": f"{app_id} {channel}",
    }


def _payload(rows, *, main_table_count=1, page_number=1, headers=None):
    return {
        "main_table_count": main_table_count,
        "page_number": page_number,
        "headers": headers if headers is not None else _headers(),
        "rows": rows,
    }


class CollectorPage:
    def __init__(self, payload):
        self.payload = payload
        self.scripts = []

    def evaluate(self, script, arg=None):
        self.scripts.append(script if isinstance(script, str) else "")
        return self.payload

    def wait_for_timeout(self, milliseconds):
        return None


def test_reader_returns_main_rows_when_dialog_adds_global_headers():
    cap = _stub_login_and_import()
    page = CollectorPage(
        _payload([_row("12026", "lyw-云版", 0), _row("12027", "lyw-云版", 1)])
    )
    rows = cap._read_current_page_app_rows(page)
    assert [row["app_id"] for row in rows] == ["12026", "12027"]
    assert rows[0]["channel_name"] == "lyw-云版"
    script = page.scripts[0]
    assert ".el-dialog" in script
    assert "main_table_count" in script
    assert ".el-table__header-wrapper th" in script


def test_reader_fails_safe_without_unique_main_table():
    cap = _stub_login_and_import()
    assert cap._read_current_page_app_rows(CollectorPage(_payload([], main_table_count=0))) == []
    assert cap._read_current_page_app_rows(CollectorPage(_payload([], main_table_count=2))) == []
    concatenated = _headers() + [
        _col("名称"), _col("编码"), _col("状态"), _col("操作"), _col("备注"),
        _col("类型"), _col("分组"), _col("版本"), _col("作者"), _col(""),
    ]
    page = CollectorPage(_payload([_row("12026", "lyw-云版")], main_table_count=2, headers=concatenated))
    assert cap._read_current_page_app_rows(page) == []


def test_pagination_state_row_count_zero_when_main_table_missing():
    cap = _stub_login_and_import()
    page = CollectorPage(_payload([], main_table_count=0, page_number=3))
    state = cap._read_pagination_state(page)
    assert state["row_count"] == 0
    assert state["page_number"] == 3
    assert state["table_signature"] == ""


def test_pagination_state_signature_comes_from_single_main_table():
    cap = _stub_login_and_import()
    page = CollectorPage(_payload([_row("12026", "lyw-云版", 0)]))
    state = cap._read_pagination_state(page)
    assert state["row_count"] == 1
    assert "12026" in state["table_signature"]


def test_find_target_locates_from_main_id_column_with_dialog_open(monkeypatch):
    cap = _stub_login_and_import()
    page = CollectorPage(_payload([_row("12026", "lyw-云版", 0)]))
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_go_to_first_page", lambda page: True)
    monkeypatch.setattr(cap, "_expand_visible_rows", lambda page: None)
    monkeypatch.setattr(cap, "_click_next_page_and_wait", lambda page: False)
    located = cap._find_target_row_by_id(page, "12026", "lyw-云版")
    assert located["found"] is True
    assert located["id_field_source"] == "main"
    assert located["channel_source"] == "main"
    assert any(".el-dialog" in script for script in page.scripts)


def test_find_target_fails_safe_when_main_table_missing(monkeypatch):
    cap = _stub_login_and_import()
    page = CollectorPage(_payload([], main_table_count=0))
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_go_to_first_page", lambda page: True)
    monkeypatch.setattr(cap, "_expand_visible_rows", lambda page: None)
    monkeypatch.setattr(cap, "_click_next_page_and_wait", lambda page: False)
    monkeypatch.setattr(
        cap,
        "_read_pagination_state",
        lambda page: {"page_number": 1, "row_count": 2, "table_signature": "A"},
    )
    located = cap._find_target_row_by_id(page, "12026", "lyw-云版")
    assert located["found"] is False
    assert located["reason"] == "main_list_table_unavailable"


def test_expand_visible_rows_uses_single_main_table_scope():
    cap = _stub_login_and_import()
    page = CollectorPage(_payload([_row("12026", "lyw-云版")]))
    cap._expand_visible_rows(page)
    script = page.scripts[0]
    assert ".el-dialog" in script
    assert "tables.length !== 1" in script
