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


def _install_locate_mocks(monkeypatch, cap, payload, *, row_count=10):
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(
        cap,
        "_read_pagination_state",
        lambda page: {"page_number": 1, "row_count": row_count, "table_signature": "A"},
    )
    monkeypatch.setattr(cap, "wait_for_table_update", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_wait_for_unfiltered_list_restore", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda *args, **kwargs: cap._ListRequestObserver())
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda *args, **kwargs: None)
    monkeypatch.setattr(cap, "_go_to_first_page", lambda page: True)
    monkeypatch.setattr(cap, "_expand_visible_rows", lambda page: None)
    monkeypatch.setattr(cap, "_click_next_page_and_wait", lambda page: False)

    class FixedPage:
        def evaluate(self, script, data=None):
            return payload

    return FixedPage()


def test_find_row_prefers_detail_over_wrong_main(monkeypatch):
    cap = _stub_login_and_import()
    page = _install_locate_mocks(
        monkeypatch,
        cap,
        {
            "headers": [_col("", "el-table__expand-column"), _col("ID"), _col("所属渠道")],
            "rows": [{
                "cells": [
                    _col("", "el-table__expand-column"),
                    _col("12052"),
                    _col("错位单元格"),
                ],
                "detail_channel": "chan-a",
                "detail_id": "",
                "row_idx": 3,
            }],
        },
    )
    located = cap._find_target_row_by_id(page, "12052", "chan-a")
    assert located["found"] is True
    assert located["channel_source"] == "detail"
    assert located["row_idx"] == 3
    assert located["detail_channel_matches_expected"] is True
    assert located["main_channel_matches_expected"] is False
    assert located["mismatch_source"] is None


def test_find_row_mismatch_when_id_exists_and_channel_differs(monkeypatch):
    cap = _stub_login_and_import()
    page = _install_locate_mocks(
        monkeypatch,
        cap,
        {
            "headers": [_col("ID"), _col("所属渠道")],
            "rows": [{
                "cells": [_col("12052"), _col("chan-b")],
                "detail_channel": "chan-b",
                "detail_id": "",
                "row_idx": 1,
            }],
        },
    )
    located = cap._find_target_row_by_id(page, "12052", "chan-a")
    assert located["found"] is False
    assert located["reason"] == "channel_mismatch"
    assert located["mismatch_source"] == "both"
    assert located["main_channel_present"] is True
    assert located["detail_channel_present"] is True


def test_find_row_unverified_when_channel_fields_missing(monkeypatch):
    cap = _stub_login_and_import()
    page = _install_locate_mocks(
        monkeypatch,
        cap,
        {
            "headers": [_col("ID"), _col("应用名称")],
            "rows": [{
                "cells": [_col("12052"), _col("演示应用")],
                "detail_channel": "",
                "detail_id": "",
                "row_idx": 1,
            }],
        },
    )
    located = cap._find_target_row_by_id(page, "12052", "chan-a")
    assert located["found"] is False
    assert located["reason"] == "channel_unverified"
    assert located["mismatch_source"] == "none"


def test_id_match_ignores_non_id_cells():
    cap = _stub_login_and_import()
    headers = [_col("ID"), _col("应用名称")]
    cells = [_col("99"), _col("12052")]
    matched, source = cap._id_matched_in_row("12052", headers, cells, detail_id="")
    assert matched is False
    assert source is None
    matched, source = cap._id_matched_in_row("12052", headers, cells, detail_id="12052")
    assert matched is True
    assert source == "detail"
    matched, source = cap._id_matched_in_row("12052", [_col("ID"), _col("应用名称")], [_col("12052"), _col("12052")], "")
    assert matched is True
    assert source == "main"


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


def test_empty_table_reset_not_stable_is_not_not_found(monkeypatch):
    cap = _stub_login_and_import()
    page = _install_locate_mocks(
        monkeypatch,
        cap,
        {"headers": [_col("ID")], "rows": []},
        row_count=0,
    )
    monkeypatch.setattr(cap, "_wait_for_unfiltered_list_restore", lambda *args, **kwargs: False)
    located = cap._find_target_row_by_id(page, "12052", "chan-a")
    assert located["found"] is False
    assert located["reason"] == "list_not_restored_after_reset"
    assert located["reason"] != "not_found"


def test_empty_table_reset_restored_then_locates(monkeypatch):
    cap = _stub_login_and_import()
    page = _install_locate_mocks(
        monkeypatch,
        cap,
        {
            "headers": [_col("ID"), _col("所属渠道")],
            "rows": [{
                "cells": [_col("12052"), _col("chan-a")],
                "detail_channel": "chan-a",
                "detail_id": "",
                "row_idx": 0,
            }],
        },
        row_count=0,
    )
    monkeypatch.setattr(cap, "_wait_for_unfiltered_list_restore", lambda *args, **kwargs: True)
    located = cap._find_target_row_by_id(page, "12052", "chan-a")
    assert located["found"] is True


def test_pagination_sync_rejects_rows_without_next_or_total():
    cap = _stub_login_and_import()
    assert cap._pagination_synced_with_unfiltered_list(
        {"row_count": 10, "next_enabled": False, "total_count": 0}
    ) is False
    assert cap._pagination_synced_with_unfiltered_list(
        {"row_count": 10, "next_enabled": False, "total_count": None}
    ) is False
    assert cap._pagination_synced_with_unfiltered_list(
        {"row_count": 10, "next_enabled": True, "total_count": None}
    ) is True
    assert cap._pagination_synced_with_unfiltered_list(
        {"row_count": 10, "next_enabled": False, "total_count": 10}
    ) is True


def test_rows_restore_before_next_button_misses_later_page_without_pager_wait(monkeypatch):
    cap = _stub_login_and_import()
    page = _install_locate_mocks(
        monkeypatch,
        cap,
        {
            "headers": [_col("ID")],
            "rows": [{
                "cells": [_col("1")],
                "detail_channel": "",
                "detail_id": "",
                "row_idx": 0,
            }],
        },
        row_count=0,
    )
    monkeypatch.setattr(cap, "_wait_for_unfiltered_list_restore", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_click_next_page_and_wait", lambda page: False)
    located = cap._find_target_row_by_id(page, "12052", "")
    assert located["found"] is False
    assert located["reason"] == "not_found"


def test_rows_restore_then_next_button_recovers_target_on_later_page(monkeypatch):
    cap = _stub_login_and_import()
    ticks = {"n": 0}
    pages = {"current": 1}

    def restore_state(page):
        ticks["n"] += 1
        if ticks["n"] < 4:
            return {
                "page_number": 1,
                "row_count": 10,
                "table_signature": "PAGE1",
                "next_enabled": False,
                "total_count": 0,
            }
        return {
            "page_number": 1,
            "row_count": 10,
            "table_signature": "PAGE1",
            "next_enabled": True,
            "total_count": 40,
        }

    page1 = {
        "headers": [_col("ID")],
        "rows": [{
            "cells": [_col("1")],
            "detail_channel": "",
            "detail_id": "",
            "row_idx": 0,
        }],
    }
    page2 = {
        "headers": [_col("ID")],
        "rows": [{
            "cells": [_col("12052")],
            "detail_channel": "",
            "detail_id": "",
            "row_idx": 0,
        }],
    }

    class RacePage:
        def wait_for_timeout(self, milliseconds):
            return None

        def evaluate(self, script, data=None):
            return page1 if pages["current"] == 1 else page2

    observations = cap._ListRequestObserver()
    orig_restore = restore_state

    def restore_with_request(page):
        state = orig_restore(page)
        if ticks["n"] == 3 and not observations.success_records:
            observations.append(200)
            observations.success_records.append(
                {"status": 200, "total_count": 40, "item_count": 10, "page_count": 4}
            )
        return state

    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda *args, **kwargs: observations)
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cap,
        "_read_pagination_state",
        lambda page: {"page_number": 1, "row_count": 0, "table_signature": "EMPTY"}
        if pages["current"] == 1 and ticks["n"] == 0
        else {"page_number": pages["current"], "row_count": 10, "table_signature": f"P{pages['current']}"},
    )
    monkeypatch.setattr(cap, "_read_list_restore_state", restore_with_request)
    monkeypatch.setattr(cap, "_go_to_first_page", lambda page: True)
    monkeypatch.setattr(cap, "_expand_visible_rows", lambda page: None)

    def click_next(page):
        pages["current"] = 2
        return True

    monkeypatch.setattr(cap, "_click_next_page_and_wait", click_next)
    located = cap._find_target_row_by_id(RacePage(), "12052", "")
    assert ticks["n"] >= 4
    assert located["found"] is True
    assert located["id_field_source"] == "main"


def test_cached_full_page_total_without_reset_request_is_not_restore_ready():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()
    states = [{
        "page_number": 1,
        "row_count": 20,
        "table_signature": "CACHED",
        "next_enabled": False,
        "total_count": 20,
    }]

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=lambda page: states[0],
        observations=observations,
        records_before=0,
        timeout_ms=5,
        poll_interval_ms=1,
    )
    assert restored is False


def test_failed_or_non_2xx_response_does_not_unlock_restore():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver([0, 500])
    cached = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "CACHED",
        "next_enabled": True,
        "total_count": 928,
    }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=lambda page: cached,
        observations=observations,
        records_before=0,
        timeout_ms=5,
        poll_interval_ms=1,
    )
    assert restored is False
    assert cap._complete_reset_records(observations, 0) == []


def test_dom_total_missing_does_not_match_response_total():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver([200])
    observations.success_records.append(
        {"status": 200, "total_count": 80, "item_count": 20, "page_count": 4}
    )
    missing_total = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "CACHED",
        "next_enabled": True,
        "total_count": None,
    }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=lambda page: missing_total,
        observations=observations,
        records_before=0,
        timeout_ms=5,
        poll_interval_ms=1,
    )
    assert restored is False
    assert cap._dom_matches_success_structure(missing_total, observations.success_records[0]) is False


def test_dom_total_none_then_80_passes_after_stable():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver([200])
    observations.success_records.append(
        {"status": 200, "total_count": 80, "item_count": 20, "page_count": 4}
    )
    reads = []
    missing = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "CACHED",
        "next_enabled": True,
        "total_count": None,
    }
    aligned = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "FRESH",
        "next_enabled": True,
        "total_count": 80,
    }

    def read_state(page):
        n = len(reads) + 1
        state = missing if n <= 2 else aligned
        reads.append(state.get("total_count"))
        return state

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=read_state,
        observations=observations,
        records_before=0,
        timeout_ms=50,
        poll_interval_ms=1,
    )
    assert restored is True
    assert reads[:2] == [None, None]
    assert reads.count(80) >= 2


def test_dom_total_928_does_not_match_response_80():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver([200])
    observations.success_records.append(
        {"status": 200, "total_count": 80, "item_count": 20, "page_count": 4}
    )
    cached = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "CACHED",
        "next_enabled": True,
        "total_count": 928,
    }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=lambda page: cached,
        observations=observations,
        records_before=0,
        timeout_ms=5,
        poll_interval_ms=1,
    )
    assert restored is False


def test_status_without_structure_record_does_not_unlock_restore():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver([200])
    cached = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "CACHED",
        "next_enabled": True,
        "total_count": 928,
    }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=lambda page: cached,
        observations=observations,
        records_before=0,
        timeout_ms=5,
        poll_interval_ms=1,
    )
    assert restored is False


def test_new_2xx_does_not_reuse_old_success_record():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver([200, 200])
    observations.success_records.append(
        {"status": 200, "total_count": 928, "item_count": 20, "page_count": 47}
    )
    cached = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "CACHED",
        "next_enabled": True,
        "total_count": 928,
    }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=lambda page: cached,
        observations=observations,
        records_before=1,
        timeout_ms=5,
        poll_interval_ms=1,
    )
    assert restored is False
    assert cap._complete_reset_records(observations, 1) == []


def test_extract_list_structure_requires_total_list_and_page_count():
    cap = _stub_login_and_import()
    body = '{"data":{"totalCount":80,"list":[{},{}],"pageCount":4}}'
    meta = cap._extract_list_structure(body)
    assert meta == {"total_count": 80, "item_count": 2, "page_count": 4}
    assert cap._extract_list_structure('{"data":{"totalCount":80}}') is None
    assert cap._extract_list_structure("not-json") is None
    assert cap._extract_list_structure("") is None


def test_2xx_already_present_final_multipage_before_wait_passes():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver([200])
    observations.success_records.append(
        {"total_count": 80, "item_count": 20, "page_count": 4}
    )
    fresh = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "FRESH",
        "next_enabled": True,
        "total_count": 80,
    }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=lambda page: fresh,
        observations=observations,
        records_before=0,
        timeout_ms=50,
        poll_interval_ms=1,
    )
    assert restored is True


def test_2xx_already_present_final_single_page_before_wait_passes():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver([200])
    observations.success_records.append(
        {"total_count": 20, "item_count": 20, "page_count": 1}
    )
    single = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "SINGLE",
        "next_enabled": False,
        "total_count": 20,
    }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=lambda page: single,
        observations=observations,
        records_before=0,
        timeout_ms=50,
        poll_interval_ms=1,
    )
    assert restored is True


def test_success_structure_same_as_cache_fingerprint_passes():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver([200])
    observations.success_records.append(
        {"total_count": 928, "item_count": 20, "page_count": 47}
    )
    same = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "CACHED",
        "next_enabled": True,
        "total_count": 928,
    }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=lambda page: same,
        observations=observations,
        records_before=0,
        timeout_ms=50,
        poll_interval_ms=1,
    )
    assert restored is True


def test_non_2xx_reset_response_returns_list_not_restored(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver([0])

    def restore_state(page):
        return {
            "page_number": 1,
            "row_count": 20,
            "table_signature": "CACHED",
            "next_enabled": True,
            "total_count": 928,
        }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

        def evaluate(self, script, data=None):
            return {"headers": [_col("ID")], "rows": []}

    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda *args, **kwargs: observations)
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cap,
        "_read_pagination_state",
        lambda page: {"page_number": 1, "row_count": 0, "table_signature": "EMPTY"},
    )
    monkeypatch.setattr(cap, "_read_list_restore_state", restore_state)
    located = cap._find_target_row_by_id(Page(), "12052", "")
    assert located["found"] is False
    assert located["reason"] == "list_not_restored_after_reset"


def test_http_200_keeps_cached_dom_for_two_polls_then_fresh():
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()
    reads = []
    cached = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "CACHED",
        "next_enabled": True,
        "total_count": 928,
    }
    fresh = {
        "page_number": 1,
        "row_count": 20,
        "table_signature": "FRESH",
        "next_enabled": True,
        "total_count": 80,
    }

    def read_state(page):
        n = len(reads) + 1
        if n == 1:
            observations.append(200)
            observations.success_records.append(
                {"total_count": 80, "item_count": 20, "page_count": 4}
            )
        state = cached if n <= 3 else fresh
        reads.append(state["table_signature"])
        return state

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=read_state,
        observations=observations,
        records_before=0,
        timeout_ms=50,
        poll_interval_ms=1,
    )
    assert restored is True
    assert reads[:3] == ["CACHED", "CACHED", "CACHED"]
    assert reads.count("FRESH") >= 2


def test_cached_rows_and_pager_then_later_page_target(monkeypatch):
    cap = _stub_login_and_import()
    ticks = {"n": 0}
    pages = {"current": 1}
    observations = cap._ListRequestObserver()

    def restore_state(page):
        ticks["n"] += 1
        if ticks["n"] == 2 and len(observations) == 0:
            observations.append(200)
            observations.success_records.append(
                {"total_count": 80, "item_count": 20, "page_count": 4}
            )
        if ticks["n"] < 5:
            return {
                "page_number": 1,
                "row_count": 20,
                "table_signature": "CACHED",
                "next_enabled": True,
                "total_count": 928,
            }
        return {
            "page_number": 1,
            "row_count": 20,
            "table_signature": "REAL",
            "next_enabled": True,
            "total_count": 80,
        }

    page1 = {
        "headers": [_col("ID")],
        "rows": [{
            "cells": [_col("1")],
            "detail_channel": "",
            "detail_id": "",
            "row_idx": 0,
        }],
    }
    page2 = {
        "headers": [_col("ID")],
        "rows": [{
            "cells": [_col("12052")],
            "detail_channel": "",
            "detail_id": "",
            "row_idx": 0,
        }],
    }

    class RacePage:
        def wait_for_timeout(self, milliseconds):
            return None

        def evaluate(self, script, data=None):
            return page1 if pages["current"] == 1 else page2

    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda *args, **kwargs: observations)
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cap,
        "_read_pagination_state",
        lambda page: {"page_number": 1, "row_count": 0, "table_signature": "EMPTY"},
    )
    monkeypatch.setattr(cap, "_read_list_restore_state", restore_state)
    monkeypatch.setattr(cap, "_go_to_first_page", lambda page: True)
    monkeypatch.setattr(cap, "_expand_visible_rows", lambda page: None)
    monkeypatch.setattr(cap, "_click_next_page_and_wait", lambda page: pages.__setitem__("current", 2) or True)
    located = cap._find_target_row_by_id(RacePage(), "12052", "")
    assert ticks["n"] >= 6
    assert located["found"] is True
    assert located["id_field_source"] == "main"


def test_page_scan_limit_is_not_not_found(monkeypatch):
    cap = _stub_login_and_import()
    page = _install_locate_mocks(
        monkeypatch,
        cap,
        {
            "headers": [_col("ID")],
            "rows": [{
                "cells": [_col("1")],
                "detail_channel": "",
                "detail_id": "",
                "row_idx": 0,
            }],
        },
        row_count=10,
    )
    monkeypatch.setattr(cap, "_click_next_page_and_wait", lambda page: True)
    located = cap._find_target_row_by_id(page, "12052", "")
    assert located["found"] is False
    assert located["reason"] == "page_scan_limit_reached"
    assert located["reason"] != "not_found"


def test_real_single_page_after_reset_request_can_scan(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()
    ticks = {"n": 0}

    def restore_state(page):
        ticks["n"] += 1
        if not observations:
            observations.append(200)
            observations.success_records.append(
                {"total_count": 20, "item_count": 20, "page_count": 1}
            )
        return {
            "page_number": 1,
            "row_count": 20,
            "table_signature": "SINGLE",
            "next_enabled": False,
            "total_count": 20,
        }

    payload = {
        "headers": [_col("ID")],
        "rows": [{
            "cells": [_col("12052")],
            "detail_channel": "",
            "detail_id": "",
            "row_idx": 0,
        }],
    }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

        def evaluate(self, script, data=None):
            return payload

    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda *args, **kwargs: observations)
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cap,
        "_read_pagination_state",
        lambda page: {"page_number": 1, "row_count": 0, "table_signature": "EMPTY"},
    )
    monkeypatch.setattr(cap, "_read_list_restore_state", restore_state)
    monkeypatch.setattr(cap, "_go_to_first_page", lambda page: True)
    monkeypatch.setattr(cap, "_expand_visible_rows", lambda page: None)
    monkeypatch.setattr(cap, "_click_next_page_and_wait", lambda page: False)
    located = cap._find_target_row_by_id(Page(), "12052", "")
    assert located["found"] is True
    assert located["id_field_source"] == "main"


def test_reset_request_never_completes_is_restore_failure(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()

    def restore_state(page):
        return {
            "page_number": 1,
            "row_count": 20,
            "table_signature": "CACHED",
            "next_enabled": True,
            "total_count": 928,
        }

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

        def evaluate(self, script, data=None):
            return {"headers": [_col("ID")], "rows": []}

    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda *args, **kwargs: observations)
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cap,
        "_read_pagination_state",
        lambda page: {"page_number": 1, "row_count": 0, "table_signature": "EMPTY"},
    )
    monkeypatch.setattr(cap, "_read_list_restore_state", restore_state)
    restored = cap._wait_for_unfiltered_list_restore(
        Page(),
        {"table_signature": "EMPTY", "row_count": 0},
        read_state=restore_state,
        observations=observations,
        records_before=0,
        timeout_ms=5,
        poll_interval_ms=1,
    )
    assert restored is False
    located = cap._find_target_row_by_id(Page(), "12052", "")
    assert located["found"] is False
    assert located["reason"] == "list_not_restored_after_reset"
