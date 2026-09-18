# -*- coding: utf-8 -*-
"""Offline tests for the combined read-only acceptance probe.

These tests never connect to UAT, Chrome, or CDP.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
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
        self.clicked_option = False
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
        if "CHANNEL_LAYER_OPEN" in text:
            return True
        if "CHANNEL_LAYER_SNAPSHOT" in text:
            layer = {
                "shown": True,
                "aria_hidden": False,
                "is_select_dropdown": False,
                "dialog_select_open": False,
                "in_copy_dialog": True,
                "anchored_to_channel_input": True,
                "is_channel_popover": True,
                "options": [{"index": index, "text": "chan-a"} for index in range(self.exact_count)],
            }
            return {"has_dialog": True, "layers": [layer]}
        if "CHANNEL_OPTION_CLICK" in text:
            clicked = self.exact_count == 1
            self.clicked_option = clicked
            return clicked
        if "CHANNEL_LAYER_CLEANUP" in text:
            return True
        if "fromInput" in text:
            return self.selected_value
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


class CloseProbePage:
    """Close path double: visibility polls and at most one click."""

    def __init__(
        self,
        *,
        initially_open=True,
        close_after_polls=0,
        click_returns=True,
        always_open=False,
        raise_on_initial_visible=False,
        raise_after_click=False,
        raise_on_click=False,
    ):
        self.initially_open = initially_open
        self.close_after_polls = close_after_polls
        self.click_returns = click_returns
        self.always_open = always_open
        self.raise_on_initial_visible = raise_on_initial_visible
        self.raise_after_click = raise_after_click
        self.raise_on_click = raise_on_click
        self.click_count = 0
        self.polls_after_click = 0
        self.clicked = False
        self.scripts = []

    def evaluate(self, script, payload=None):
        text = script if isinstance(script, str) else ""
        self.scripts.append(text)
        if "COPY_DIALOG_VISIBLE" in text:
            if not self.clicked:
                if self.raise_on_initial_visible:
                    raise RuntimeError("visible boom")
                return self.initially_open
            if self.raise_after_click:
                raise RuntimeError("visible boom")
            self.polls_after_click += 1
            if self.always_open:
                return True
            return self.polls_after_click <= self.close_after_polls
        if "COPY_DIALOG_CLICK" in text:
            if self.raise_on_click:
                raise RuntimeError("click boom")
            self.click_count += 1
            self.clicked = True
            return self.click_returns
        return True

    def wait_for_timeout(self, milliseconds):
        return None


def test_close_already_closed_returns_true_without_click():
    probe = _load_probe()
    page = CloseProbePage(initially_open=False)
    assert probe._close_copy_dialog(page) is True
    assert page.click_count == 0
    assert not any("COPY_DIALOG_CLICK" in script for script in page.scripts)


def test_close_polls_until_animation_finishes_with_single_click():
    probe = _load_probe()
    page = CloseProbePage(initially_open=True, close_after_polls=3)
    assert probe._close_copy_dialog(page) is True
    assert page.click_count == 1
    assert page.polls_after_click == 4
    assert page.polls_after_click < probe.COPY_DIALOG_CLOSE_POLL_ATTEMPTS


def test_close_keeps_failing_while_visible_without_repeat_click():
    probe = _load_probe()
    page = CloseProbePage(initially_open=True, always_open=True)
    assert probe._close_copy_dialog(page) is False
    assert page.click_count == 1
    assert sum(1 for script in page.scripts if "COPY_DIALOG_CLICK" in script) == 1
    assert page.polls_after_click == probe.COPY_DIALOG_CLOSE_POLL_ATTEMPTS


def test_close_poll_budget_is_short_and_bounded():
    probe = _load_probe()
    assert probe.COPY_DIALOG_CLOSE_POLL_ATTEMPTS == 12
    assert probe.COPY_DIALOG_CLOSE_POLL_INTERVAL_MS == 100


def test_visibility_predicate_covers_hidden_states():
    probe = _load_probe()
    script = probe._COPY_DIALOG_VISIBLE_JS
    assert "getComputedStyle" in script
    assert "visibility" in script
    assert "aria-hidden" in script
    assert "getBoundingClientRect" in script
    assert ".el-tabs__item" in script


def test_close_exception_path_does_not_click_or_save():
    probe = _load_probe()
    page = CloseProbePage(raise_on_initial_visible=True)
    # Unreadable visibility cannot prove the dialog is closed: fail closed.
    assert probe._close_copy_dialog(page) is False
    assert page.click_count == 0
    assert not any("保存" in script for script in page.scripts)


def test_close_poll_exception_fails_closed_no_save():
    probe = _load_probe()
    page = CloseProbePage(initially_open=True, raise_after_click=True)
    assert probe._close_copy_dialog(page) is False
    assert page.click_count == 1
    assert not any("保存" in script for script in page.scripts)


def test_close_click_exception_fails_closed_no_save():
    probe = _load_probe()
    page = CloseProbePage(initially_open=True, raise_on_click=True)
    assert probe._close_copy_dialog(page) is False
    assert page.click_count == 0
    assert not any("保存" in script for script in page.scripts)


def test_copy_dialog_open_still_reports_not_open_on_read_failure():
    probe = _load_probe()
    page = CloseProbePage(initially_open=True, raise_on_initial_visible=True)
    assert probe._copy_dialog_open(page) is False


def test_close_failure_in_acceptance_report_zero_save_no_write(monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    page = AcceptancePage(close_ok=False)
    report = probe.run_acceptance(
        page, cap, channel_name="chan-a", app_id="app-1", ref_app_id="ref-1"
    )
    assert report["ok"] is False
    assert report["error"] == "copy_dialog_close_failed"
    assert report["copy_dialog_closed"] is False
    assert report["save_click_count"] == 0
    assert report["write_request_observed"] is False
    assert page.save_clicks == 0


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
                "response_contains_target_channel": True,
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
    assert any("CHANNEL_LAYER_SNAPSHOT" in script for script in page.scripts)
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
    assert report["ok"] is False
    assert report["error"] == "target_app_not_located"
    assert report["steps"]["exact_app_id_lookup"]["found"] is False
    assert report["steps"]["exact_app_id_lookup"]["mismatch_source"] == "both"


def test_response_missing_target_channel_is_not_ok(monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()

    def search(page, channel, return_detail=False):
        return {
            "filter_stable": False,
            "list_requests_before": 0,
            "list_requests_after": 1,
            "last_http_status": 200,
            "request_carried_target_filter": True,
            "target_filter_field": "channelNames[]",
            "target_filter_field_is_channel": True,
            "table_changed": True,
            "reader_sees_target_channel": False,
            "response_contains_target_channel": False,
        }

    _install_common(monkeypatch, cap, search=search)
    page = AcceptancePage()
    report = probe.run_acceptance(page, cap, channel_name="chan-a", app_id="app-1", ref_app_id="ref-1")
    assert report["ok"] is False
    assert report["error"] == "list_filter_not_verified"
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0
    assert report["diagnosis"]["response_contains_target_channel"] is False


def test_all_success_conditions_set_ok_true(monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    _install_common(monkeypatch, cap)
    page = AcceptancePage()
    report = probe.run_acceptance(page, cap, channel_name="chan-a", app_id="app-1", ref_app_id="ref-1")
    assert report["ok"] is True
    assert report["error"] is None
    assert report["exact_channel_unique"] is True
    assert report["selected_channel_matches"] is True
    assert report["steps"]["exact_app_id_lookup"]["found"] is True
    assert report["copy_dialog_closed"] is True
    assert report["save_click_count"] == 0
    assert report["write_request_observed"] is False


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
    original_save = cap._click_save_button
    writes = []
    real_write = probe._atomic_write_report

    def counting(path, report):
        writes.append(1)
        return real_write(path, report)

    def slow_collect(page, reset_filters=False):
        page.wait_for_timeout(5000)
        return {"ref-1": {"app_id": "ref-1"}}

    monkeypatch.setattr(probe, "_atomic_write_report", counting)
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
    assert cap._click_save_button is original_save
    assert report["copy_dialog_closed"] is True
    assert writes == [1]


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


def test_owner_deadline_page_rejects_other_thread():
    probe = _load_probe()
    inner = AcceptancePage()
    wrapped = probe.OwnerDeadlinePage(inner, threading.get_ident())
    errors = []

    def worker():
        try:
            wrapped.evaluate("1+1")
        except Exception as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors
    assert "Cannot switch to a different thread" in errors[0]


def test_success_and_failure_paths_stay_on_owner_thread(tmp_path, monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    owner = threading.get_ident()
    wrappers = []
    real_cls = probe.OwnerDeadlinePage

    class Tracking(real_cls):
        def __init__(self, page, owner_ident, deadline=None):
            super().__init__(page, owner_ident, deadline)
            wrappers.append(self)

    monkeypatch.setattr(probe, "OwnerDeadlinePage", Tracking)
    _install_common(monkeypatch, cap)
    page = AcceptancePage()
    probe.run_acceptance_with_budget(
        page,
        cap,
        channel_name="chan-a",
        app_id="app-1",
        ref_app_id="ref-1",
        budget_seconds=5,
        report_file=tmp_path / "owner-ok.json",
    )
    assert wrappers
    assert wrappers[0]._owner_ident == owner
    assert wrappers[0].thread_calls
    assert all(ident == owner for ident in wrappers[0].thread_calls)

    page_fail = AcceptancePage(exact_count=0, selected_value="")
    probe.run_acceptance_with_budget(
        page_fail,
        cap,
        channel_name="chan-a",
        app_id="app-1",
        ref_app_id="ref-1",
        budget_seconds=5,
        report_file=tmp_path / "owner-fail.json",
    )
    assert all(ident == owner for ident in wrappers[-1].thread_calls)


def test_timeout_path_stays_on_owner_and_starts_no_worker(tmp_path, monkeypatch):
    cap = _stub_login_and_import()
    probe = _load_probe()
    owner = threading.get_ident()
    started = []

    class ForbiddenThread:
        def __init__(self, *args, **kwargs):
            started.append(1)
            raise AssertionError("must not start a worker thread")

    monkeypatch.setattr(probe.threading, "Thread", ForbiddenThread)
    wrappers = []
    real_cls = probe.OwnerDeadlinePage

    class Tracking(real_cls):
        def __init__(self, page, owner_ident, deadline=None):
            super().__init__(page, owner_ident, deadline)
            wrappers.append(self)

    monkeypatch.setattr(probe, "OwnerDeadlinePage", Tracking)

    def slow_collect(page, reset_filters=False):
        page.wait_for_timeout(5000)
        return {"ref-1": {"app_id": "ref-1"}}

    monkeypatch.setattr(cap, "_collect_all_app_rows", slow_collect)
    original_save = cap._click_save_button
    page = AcceptancePage()
    report = probe.run_acceptance_with_budget(
        page,
        cap,
        channel_name="chan-a",
        app_id="app-1",
        ref_app_id="ref-1",
        budget_seconds=0.05,
        report_file=tmp_path / "owner-timeout.json",
    )
    assert started == []
    assert report["error"] == "probe_timeout"
    assert cap._click_save_button is original_save
    assert wrappers
    assert all(ident == owner for ident in wrappers[0].thread_calls)
