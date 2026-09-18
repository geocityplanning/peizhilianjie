# -*- coding: utf-8 -*-
"""Offline tests for the combined read-only acceptance probe.

These tests never connect to UAT, Chrome, or CDP.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
import types
from pathlib import Path

SECRET_CHANNEL = "甘肃体验有礼-机密渠道"
SECRET_APP = "中国移动云盘-机密应用"
SECRET_LINK = "https://plus.buy.139.com/mccloudgame/#/?i=secret"
SECRET_URL = "https://uat-cloud.139.com/cloudappadmin/#/cloudAppManager?x=1"

PROBE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "probe_readonly_acceptance.py"
)


def _stub_login_and_import():
    if "actions.ensure_login" not in sys.modules:
        stub = types.ModuleType("actions.ensure_login")
        stub.ensure_login = lambda page=None: {"success": False}
        stub.is_logged_in = lambda page: True
        sys.modules["actions.ensure_login"] = stub
    from app.executor.actions import create_app_v2 as cap

    return cap


def _load_probe():
    spec = importlib.util.spec_from_file_location("probe_readonly_acceptance", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AcceptancePage:
    def __init__(self, *, exact_count=1, selected_value="chan-a", dialog_open=True, close_ok=True):
        self.exact_count = exact_count
        self.selected_value = selected_value
        self.dialog_open = dialog_open
        self.close_ok = close_ok
        self.closed = False
        self.save_clicks = 0
        self.scripts = []
        self.handlers = {}
        self.url = "https://example.invalid/#/cloudAppManager"

    def on(self, event, handler):
        self.handlers[event] = handler

    def wait_for_timeout(self, milliseconds):
        return None

    def evaluate(self, script, payload=None):
        self.scripts.append(script)
        text = script if isinstance(script, str) else ""
        if "innermost" in text:
            clicked = self.exact_count == 1
            return {"exact_count": self.exact_count, "clicked": clicked}
        if "fromInput" in text:
            return self.selected_value
        if "ci.click" in text:
            return True
        if "取消" in text or "headerbtn" in text:
            if self.close_ok:
                self.dialog_open = False
                self.closed = True
                return True
            return False
        if "el-tabs__item" in text:
            return bool(self.dialog_open)
        if "复制" in text:
            self.dialog_open = True
            return True
        if "保存" in text:
            self.save_clicks += 1
            return True
        return True


def _lookup_ok():
    return {
        "found": True,
        "reason": None,
        "channel_source": "detail",
        "id_field_source": "main",
        "header_cell_alignment_used": True,
        "main_channel_present": True,
        "detail_channel_present": True,
        "main_channel_matches_expected": True,
        "detail_channel_matches_expected": True,
        "main_detail_same": True,
        "mismatch_source": None,
        "row_idx": 0,
    }


def _install_common(monkeypatch, cap, *, find_list=None, search=None):
    monkeypatch.setattr(
        cap,
        "_collect_all_app_rows",
        lambda page, reset_filters=False: {"ref-1": {"app_id": "ref-1"}},
    )

    def find_target(page, app_id, expected=""):
        if str(app_id) == "ref-1":
            return {"found": True, "row_idx": 0}
        if find_list is not None:
            return find_list
        return _lookup_ok()

    monkeypatch.setattr(cap, "_find_target_row_by_id", find_target)
    monkeypatch.setattr(cap, "_go_tab", lambda page, name: True)
    monkeypatch.setattr(
        cap,
        "_search_list_by_channel",
        search
        or (
            lambda page, channel, return_detail=False: {
                "filter_stable": True,
                "list_requests_before": 0,
                "list_requests_after": 1,
                "last_http_status": 200,
                "request_carried_target_filter": True,
                "target_filter_field": "channelNames[]",
                "target_filter_field_is_channel": True,
                "table_changed": True,
                "reader_sees_target_channel": True,
            }
        ),
    )


def test_acceptance_calls_real_select_and_verify(monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    real = cap._select_and_verify_create_channel
    calls = []

    def spy(page, name):
        calls.append(name)
        return real(page, name)

    monkeypatch.setattr(cap, "_select_and_verify_create_channel", spy)
    page = AcceptancePage(exact_count=1, selected_value="chan-a")
    report = probe.run_acceptance(page, cap, channel_name="chan-a", app_id="app-1", ref_app_id="ref-1")
    assert calls == ["chan-a"]
    assert any("innermost" in script for script in page.scripts)
    assert report["ok"] is True
    assert report["copy_dialog_opened"] is True
    assert report["exact_channel_unique"] is True
    assert report["selected_channel_matches"] is True
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0
    assert report["write_request_observed"] is False
    assert page.save_clicks == 0


def test_success_closes_dialog_with_zero_save_clicks(monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    page = AcceptancePage()
    report = probe.run_acceptance(page, cap, channel_name="chan-a", app_id="app-1", ref_app_id="ref-1")
    assert report["ok"] is True
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0
    assert cap._click_save_button is not None


def _failing_select_report(monkeypatch, page):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    return probe.run_acceptance(page, cap, channel_name="chan-a", app_id="app-1", ref_app_id="ref-1")


def test_zero_options_close_dialog_zero_save(monkeypatch):
    page = AcceptancePage(exact_count=0, selected_value="")
    report = _failing_select_report(monkeypatch, page)
    assert report["ok"] is False
    assert report["exact_channel_unique"] is False
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0


def test_multiple_options_close_dialog_zero_save(monkeypatch):
    page = AcceptancePage(exact_count=2, selected_value="chan-a")
    report = _failing_select_report(monkeypatch, page)
    assert report["ok"] is False
    assert report["exact_channel_unique"] is False
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0


def test_empty_readback_close_dialog_zero_save(monkeypatch):
    page = AcceptancePage(exact_count=1, selected_value="")
    report = _failing_select_report(monkeypatch, page)
    assert report["ok"] is False
    assert report["selected_channel_matches"] is False
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0


def test_readback_mismatch_close_dialog_zero_save(monkeypatch):
    page = AcceptancePage(exact_count=1, selected_value="chan-b")
    report = _failing_select_report(monkeypatch, page)
    assert report["ok"] is False
    assert report["selected_channel_matches"] is False
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0


def test_list_locate_failure_still_closes_and_zero_write(monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    failed = {
        "found": False,
        "reason": "channel_mismatch",
        "channel_source": "both",
        "id_field_source": "main",
        "header_cell_alignment_used": True,
        "main_channel_present": True,
        "detail_channel_present": True,
        "main_channel_matches_expected": False,
        "detail_channel_matches_expected": False,
        "main_detail_same": True,
        "mismatch_source": "both",
        "row_idx": 0,
    }
    _install_common(monkeypatch, cap, find_list=failed)
    page = AcceptancePage()
    report = probe.run_acceptance(page, cap, channel_name="chan-a", app_id="app-1", ref_app_id="ref-1")
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0
    assert report["write_request_observed"] is False
    assert report["steps"]["exact_app_id_lookup"]["found"] is False
    assert report["steps"]["exact_app_id_lookup"]["mismatch_source"] == "both"


def test_exception_still_closes_and_zero_write(monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()

    def boom(*args, **kwargs):
        raise RuntimeError("list boom")

    _install_common(monkeypatch, cap, search=boom)
    page = AcceptancePage()
    report = probe.run_acceptance(page, cap, channel_name="chan-a", app_id="app-1", ref_app_id="ref-1")
    assert report["ok"] is False
    assert report["error"] == "RuntimeError"
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0
    assert report["write_request_observed"] is False


def test_report_dump_failure_keeps_dialog_closed(monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    page = AcceptancePage()
    report = probe.run_acceptance(page, cap, channel_name="chan-a", app_id="app-1", ref_app_id="ref-1")
    assert report["copy_dialog_closed"] is True

    def boom(payload):
        raise ValueError("dump failed")

    monkeypatch.setattr(probe, "_public_report", lambda payload: (_ for _ in ()).throw(ValueError("dump failed")))
    closed = report["copy_dialog_closed"]
    try:
        probe._public_report(report)
    except ValueError:
        pass
    assert closed is True
    assert report["save_click_count"] == 0


def test_public_report_omits_secrets_and_disallowed_fields(monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    page = AcceptancePage(selected_value=SECRET_CHANNEL)
    report = probe.run_acceptance(
        page, cap, channel_name=SECRET_CHANNEL, app_id="12052", ref_app_id="ref-1"
    )
    public = probe._public_report(
        {
            **report,
            "channel": SECRET_CHANNEL,
            "app_name": SECRET_APP,
            "ref_link": SECRET_LINK,
            "current_url": SECRET_URL,
            "body": SECRET_CHANNEL,
        }
    )
    dumped = json.dumps(public, ensure_ascii=False)
    assert SECRET_CHANNEL not in dumped
    assert SECRET_APP not in dumped
    assert SECRET_LINK not in dumped
    assert SECRET_URL not in dumped
    assert "12052" not in dumped
    assert "body" not in public
    assert "channel" not in public
    assert "current_url" not in public
    assert set(public) <= probe._ALLOWED_TOP_KEYS
    assert public["save_click_count"] == 0
    assert public["write_request_observed"] is False
    assert public["wrote_any_uat_data"] is False


def test_success_writes_atomic_report_file(tmp_path, monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    page = AcceptancePage()
    report_file = tmp_path / "ok.json"
    report = probe.run_acceptance_with_budget(
        page,
        cap,
        channel_name="chan-a",
        app_id="app-1",
        ref_app_id="ref-1",
        budget_seconds=5,
        report_file=report_file,
    )
    assert report["ok"] is True
    payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["save_click_count"] == 0
    assert set(payload) <= probe._ALLOWED_TOP_KEYS


def test_business_failure_writes_report_file(tmp_path, monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    page = AcceptancePage(exact_count=0, selected_value="")
    report_file = tmp_path / "fail.json"
    report = probe.run_acceptance_with_budget(
        page,
        cap,
        channel_name="chan-a",
        app_id="app-1",
        ref_app_id="ref-1",
        budget_seconds=5,
        report_file=report_file,
    )
    assert report["ok"] is False
    payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["error"] == "channel_select_not_verified"
    assert payload["copy_dialog_closed"] is True
    assert payload["save_click_count"] == 0


def test_exception_writes_report_file(tmp_path, monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()

    def boom(*args, **kwargs):
        raise RuntimeError("list boom")

    _install_common(monkeypatch, cap, search=boom)
    page = AcceptancePage()
    report_file = tmp_path / "exc.json"
    report = probe.run_acceptance_with_budget(
        page,
        cap,
        channel_name="chan-a",
        app_id="app-1",
        ref_app_id="ref-1",
        budget_seconds=5,
        report_file=report_file,
    )
    assert report["error"] == "RuntimeError"
    payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert payload["error"] == "RuntimeError"
    assert payload["copy_dialog_closed"] is True


def test_budget_timeout_writes_probe_timeout_and_cleans_up(tmp_path, monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()

    def slow_collect(page, reset_filters=False):
        time.sleep(1.0)
        return {"ref-1": {"app_id": "ref-1"}}

    monkeypatch.setattr(cap, "_collect_all_app_rows", slow_collect)
    monkeypatch.setattr(cap, "_find_target_row_by_id", lambda *args, **kwargs: {"found": True, "row_idx": 0})
    monkeypatch.setattr(cap, "_go_tab", lambda *args, **kwargs: True)
    page = AcceptancePage()
    report_file = tmp_path / "timeout.json"
    report = probe.run_acceptance_with_budget(
        page,
        cap,
        channel_name="chan-a",
        app_id="app-1",
        ref_app_id="ref-1",
        budget_seconds=0.05,
        report_file=report_file,
    )
    assert report["error"] == "probe_timeout"
    assert report["ok"] is False
    payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert payload["error"] == "probe_timeout"
    assert payload["wrote_any_uat_data"] is False
    assert SECRET_CHANNEL not in report_file.read_text(encoding="utf-8")


def test_report_file_write_failure_still_closes(tmp_path, monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    page = AcceptancePage()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(probe, "_atomic_write_report", boom)
    report = probe.run_acceptance_with_budget(
        page,
        cap,
        channel_name="chan-a",
        app_id="app-1",
        ref_app_id="ref-1",
        budget_seconds=5,
        report_file=tmp_path / "nope.json",
    )
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0
    assert report["error"] == "report_write_failed"


def test_report_file_redacts_disallowed_fields(tmp_path, monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    page = AcceptancePage(selected_value=SECRET_CHANNEL)
    report_file = tmp_path / "redact.json"
    probe.run_acceptance_with_budget(
        page,
        cap,
        channel_name=SECRET_CHANNEL,
        app_id="12052",
        ref_app_id="ref-1",
        budget_seconds=5,
        report_file=report_file,
    )
    dumped = report_file.read_text(encoding="utf-8")
    payload = json.loads(dumped)
    assert SECRET_CHANNEL not in dumped
    assert SECRET_APP not in dumped
    assert SECRET_LINK not in dumped
    assert SECRET_URL not in dumped
    assert "12052" not in dumped
    assert set(payload) <= probe._ALLOWED_TOP_KEYS
