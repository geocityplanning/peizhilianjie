# -*- coding: utf-8 -*-
"""Offline behavior tests for resource fallback framework/persistence verification."""
from __future__ import annotations

import base64
import json
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


class FillLocator:
    def __init__(self, *, count=1, fill_error=False):
        self._count = count
        self.fill_error = fill_error
        self.calls = []

    def count(self):
        return self._count

    def fill(self, value, **kwargs):
        self.calls.append(("fill", value, kwargs))
        if self.fill_error:
            raise RuntimeError("fill failed")

    def press(self, value, **kwargs):
        self.calls.append(("press", value, kwargs))


class FillPage:
    def __init__(self, *, marker_result=None, locator=None):
        self.marker_result = marker_result or {"dialog_found": True, "input_found": True}
        self.input_locator = locator or FillLocator()
        self.marker_cleared = False

    def evaluate(self, script, payload=None):
        if "matches[0].setAttribute(marker" in script:
            return dict(self.marker_result)
        if "removeAttribute(marker)" in script:
            self.marker_cleared = True
        return True

    def locator(self, selector):
        assert "data-hermes-resource-fallback-input" in selector
        return self.input_locator


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


class MainTablePage:
    def __init__(self, payload):
        self.payload = payload

    def evaluate(self, *args, **kwargs):
        return self.payload


class CopyOpenPage:
    def __init__(self, result):
        self.result = result
        self.script = ""
        self.payload = None

    def evaluate(self, script, payload):
        self.script = script
        self.payload = payload
        return self.result


def _main_payload(*rows, headers=("ID", "应用名称", "所属渠道"), table_count=1):
    return {
        "main_table_count": table_count,
        "headers": [{"text": header, "classes": ""} for header in headers],
        "rows": [
            {
                "row_idx": index,
                "cells": [{"text": value, "classes": ""} for value in row],
            }
            for index, row in enumerate(rows)
        ],
    }


def _readback(value=EXPECTED, *, model_value=EXPECTED, model_found=True, input_found=True):
    return {
        "dialog_found": True,
        "input_found": input_found,
        "model_found": model_found,
        "value": value,
        "model_value": model_value,
    }


def _prepare_stage(monkeypatch, cap, page):
    monkeypatch.setattr(cap, "_set_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(cap, "_cleanup_overlays", lambda page: None)
    monkeypatch.setattr(cap, "_shot", lambda *args, **kwargs: None)
    monkeypatch.setattr(cap, "_collect_all_app_rows", lambda *args, **kwargs: {"old-id": {}})
    monkeypatch.setattr(cap, "_search_by_link", lambda *args, **kwargs: {"clicked": True, "error": None, "uncertain": False})
    monkeypatch.setattr(cap, "_go_tab", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_js_select", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_select_and_verify_create_channel", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(cap, "_fill_visible_copy_dialog_input", lambda *args, **kwargs: True)

    def counted_save(page):
        page.save_clicks += 1
        return True

    monkeypatch.setattr(cap, "_click_save_button", counted_save)
    monkeypatch.setattr(cap, "_attach_save_click_observer", lambda *args, **kwargs: cap._SaveClickObserver())
    monkeypatch.setattr(cap, "_wait_for_save_click_observation", lambda *args, **kwargs: {
        "outcome": "success", "method": "POST", "path_hash": "0123456789abcdef", "http_status": 200,
        "business": {
            "outcome": "success",
            "code": {"present": True, "length": 3, "sha256_16": "abcdef0123456789"},
            "message": {"present": True, "length": 2, "sha256_16": "fedcba9876543210"},
        },
    })
    monkeypatch.setattr(cap, "_detach_save_click_observer", lambda *args, **kwargs: None)


def _stage_data():
    return {"actual_channel_name": "channel-a", "base_platform": ""}


def test_go_tab_requires_visible_dialog_and_active_target_tab():
    cap = _stub_login_and_import()

    class Tab:
        def __init__(self, page):
            self.page = page
        def inner_text(self):
            return "基础配置"
        def click(self, timeout):
            self.page.physical_clicks += 1

    class Tabs:
        def __init__(self, page):
            self.page = page
        def count(self):
            return 1
        def nth(self, index):
            return Tab(self.page)

    class Dialog:
        def __init__(self, page):
            self.page = page
        def is_visible(self):
            return True
        def locator(self, selector):
            assert selector == ".el-tabs__item"
            return Tabs(self.page)

    class Wrappers:
        def __init__(self, page):
            self.page = page
        def count(self):
            return 1
        def nth(self, index):
            return Dialog(self.page)

    class TabPage:
        def __init__(self):
            self.calls = []
            self.physical_clicks = 0
        def locator(self, selector):
            assert selector == ".el-dialog__wrapper"
            return Wrappers(self)
        def evaluate(self, script, payload=None):
            self.calls.append(script)
            if "return Array.from(dialogs[0].querySelectorAll('.el-tabs__item'))" in script:
                return ["基础配置"]
            return True
        def wait_for_timeout(self, milliseconds):
            return None

    page = TabPage()
    assert cap._go_tab(page, "基础配置") is True
    assert page.physical_clicks == 1
    source = "\n".join(page.calls)
    assert "window.getComputedStyle" in source
    assert "dialogs.length !== 1" in source
    assert "tab.classList.contains('is-active')" in source
    assert "tab.getAttribute('aria-selected') !== 'true'" in source
    assert "pane.getAttribute('aria-hidden') === 'true'" in source


def test_go_tab_gives_activation_proof_a_fresh_budget_after_physical_click(monkeypatch):
    cap = _stub_login_and_import()
    clock = [0.0]

    class Tab:
        def inner_text(self):
            return "基础配置"
        def click(self, timeout):
            # Exceeds the old shared six-second deadline, but is within the
            # physical-click timeout and must not suppress post-click proof.
            clock[0] += 6.1

    class Tabs:
        def count(self):
            return 1
        def nth(self, index):
            return Tab()

    class Dialog:
        def is_visible(self):
            return True
        def locator(self, selector):
            return Tabs()

    class Wrappers:
        def count(self):
            return 1
        def nth(self, index):
            return Dialog()

    class Page:
        def locator(self, selector):
            return Wrappers()
        def evaluate(self, script, payload=None):
            if "return Array.from(dialogs[0].querySelectorAll('.el-tabs__item'))" in script:
                return ["基础配置"]
            return True
        def wait_for_timeout(self, milliseconds):
            clock[0] += milliseconds / 1000

    monkeypatch.setattr(cap.time, "monotonic", lambda: clock[0])
    assert cap._go_tab(Page(), "基础配置") is True


def test_native_locator_fill_and_tab_are_required(monkeypatch):
    cap = _stub_login_and_import()
    page = FillPage()

    assert cap._fill_visible_copy_dialog_input(page, "资源不足中间页链接", EXPECTED) is True
    assert page.input_locator.calls[0][0:2] == ("fill", EXPECTED)
    assert page.input_locator.calls[1][0:2] == ("press", "Tab")
    assert page.marker_cleared is True


def _save_observer(cap, *records):
    observer = cap._SaveClickObserver()
    observer.candidates = {f"r{index}": record for index, record in enumerate(records)}
    return observer


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"completed": True, "http_status": 200, "business": {"outcome": "success"}}, "success"),
        ({"completed": True, "http_status": 200, "business": {"outcome": "rejected"}}, "business_rejected"),
        ({"completed": True, "http_status": 500, "business": {"outcome": "success"}}, "non_2xx"),
        ({"completed": False}, "response_timeout"),
    ],
)
def test_save_observation_never_treats_http_2xx_alone_as_success(record, expected):
    cap = _stub_login_and_import()
    assert cap._save_click_observation(_save_observer(cap, record))["outcome"] == expected


def test_save_observation_rejects_missing_or_multiple_candidates():
    cap = _stub_login_and_import()
    assert cap._save_click_observation(_save_observer(cap))["outcome"] == "no_candidate"
    assert cap._save_click_observation(_save_observer(cap, {"completed": True}, {"completed": True}))["outcome"] == "ambiguous_candidate"


def test_save_business_summary_is_redacted():
    cap = _stub_login_and_import()
    summary = cap._save_business_summary('{"header":{"status":"500","code":"secret-code","message":"secret-message"}}')
    assert summary["outcome"] == "rejected"
    assert summary["code"]["present"] is True
    assert summary["message"]["present"] is True
    assert "secret-code" not in str(summary)
    assert "secret-message" not in str(summary)


def test_unique_save_decision_retains_only_whitelisted_evidence():
    cap = _stub_login_and_import()
    decision = cap._save_click_observation(_save_observer(cap, {
        "method": "POST", "path_hash": "0123456789abcdef", "completed": True, "http_status": 200,
        "business": {
            "outcome": "success",
            "code": {"present": True, "length": 3, "sha256_16": "abcdef0123456789"},
            "message": {"present": True, "length": 4, "sha256_16": "fedcba9876543210"},
        },
        "url": "https://secret.invalid/path?secret=query",
        "body": "secret-body",
    }))
    assert set(decision) == {"outcome", "method", "path_hash", "http_status", "response_body", "business"}
    assert decision["method"] == "POST"
    assert decision["path_hash"] == "0123456789abcdef"
    assert decision["business"]["code"]["sha256_16"] == "abcdef0123456789"
    assert "secret" not in str(decision)


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ({"outcome": "no_candidate", "candidate_count": 0}, {"outcome": "no_candidate", "candidate_count": 0}),
        ({"outcome": "ambiguous_candidate", "candidate_count": 2, "path_hash": "0123456789abcdef"}, {"outcome": "ambiguous_candidate", "candidate_count": 2}),
    ],
)
def test_zero_or_multiple_candidate_diagnostics_never_enumerate_paths(decision, expected):
    cap = _stub_login_and_import()
    assert cap._save_click_diagnostic(decision) == expected


def test_save_response_body_decodes_base64_and_keeps_http_2xx_non_success_without_envelope():
    cap = _stub_login_and_import()
    encoded = base64.b64encode(b'{"header":{"status":"200"}}').decode("ascii")
    body, fetch_state = cap._save_response_body({"body": encoded, "base64Encoded": True})
    business, parse_state = cap._save_business_summary_detail(body)
    assert (fetch_state, parse_state, business["outcome"]) == ("base64_decoded", "envelope_classified", "success")

    body, fetch_state = cap._save_response_body({"body": "not-json", "base64Encoded": False})
    business, parse_state = cap._save_business_summary_detail(body)
    assert (fetch_state, parse_state, business["outcome"]) == ("available", "json_unparsable", "unreadable")
    assert cap._save_response_body({}) == ("", "missing")


def test_save_diagnostic_is_single_redacted_json_line(capsys):
    cap = _stub_login_and_import()
    cap._log_save_click_observation({
        "outcome": "success", "method": "POST", "path_hash": "0123456789abcdef", "http_status": 200,
        "response_body": {"fetch_state": "read_error", "parse_state": "not_applicable", "raw": "secret-body"},
        "business": {
            "outcome": "success",
            "code": {"present": True, "length": 11, "sha256_16": "abcdef0123456789", "raw": "secret-code"},
            "message": {"present": True, "length": 14, "sha256_16": "fedcba9876543210", "raw": "secret-message"},
        },
        "url": "https://secret.invalid/path?secret=query", "body": "secret-body",
    })
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    prefix = "[create_app] save_observation="
    assert lines[0].startswith(prefix)
    logged = json.loads(lines[0][len(prefix):])
    assert logged["outcome"] == "success"
    assert logged["method"] == "POST"
    assert logged["path_hash"] == "0123456789abcdef"
    assert logged["business"]["outcome"] == "success"
    assert logged["response_body"] == {"fetch_state": "read_error", "parse_state": "not_applicable"}
    assert "secret" not in lines[0]
    assert "url" not in lines[0]
    assert '"body":' not in lines[0]


def test_click_save_requires_unique_exact_enabled_handler_bound_control():
    cap = _stub_login_and_import()

    class SavePage:
        def __init__(self):
            self.script = ""
        def evaluate(self, script):
            self.script = script
            return True

    page = SavePage()
    assert cap._click_save_button(page) is True
    assert "dialogs.length !== 1" in page.script
    assert "text === '保存'" in page.script
    assert "!button.disabled" in page.script
    assert "button.getAttribute('aria-disabled') !== 'true'" in page.script
    assert "hasHandler(button)" in page.script


def test_missing_unique_input_fails_before_native_fill():
    cap = _stub_login_and_import()
    page = FillPage(marker_result={"dialog_found": True, "input_found": False})

    assert cap._fill_visible_copy_dialog_input(page, "资源不足中间页链接", EXPECTED) is False
    assert page.input_locator.calls == []


def test_model_unreadable_stops_with_zero_save(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_read_visible_copy_dialog_input", lambda *args, **kwargs: _readback(model_found=False, model_value=""))

    result = cap._stage_create_save(page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type")

    assert result["success"] is False
    assert result["error"]["code"] == "RESOURCE_FALLBACK_VERIFY_FAILED"
    assert result["error"]["stage"] == "FILL"
    assert page.save_clicks == 0


def test_model_dom_mismatch_stops_with_zero_save(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_read_visible_copy_dialog_input", lambda *args, **kwargs: _readback(model_value="different"))

    result = cap._stage_create_save(page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type")

    assert result["success"] is False
    assert result["error"]["stage"] == "FILL"
    assert page.save_clicks == 0


def test_tab_round_trip_value_loss_stops_with_zero_save(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    reads = iter([_readback(), _readback(value="", model_value="")])
    monkeypatch.setattr(cap, "_read_visible_copy_dialog_input", lambda *args, **kwargs: next(reads))

    result = cap._stage_create_save(page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type")

    assert result["success"] is False
    assert result["error"]["stage"] == "FILL"
    assert page.save_clicks == 0


@pytest.mark.parametrize(
    "selection_failure",
    ["physical_open", "option_not_unique", "dropdown_not_closed", "dom_model_mismatch"],
)
def test_settlement_selection_failure_stops_with_zero_save(monkeypatch, selection_failure):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_fill_and_verify_resource_fallback", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(cap, "_js_select", lambda *args, **kwargs: False)

    result = cap._stage_create_save(
        page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type"
    )

    assert selection_failure
    assert result["success"] is False
    assert result["error"]["code"] == "SETTLEMENT_SELECT_FAILED"
    assert result["error"]["stage"] == "FILL"
    assert page.save_clicks == 0


def test_normal_settlement_selection_reaches_save_after_prior_tab_activation(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    tabs = []
    monkeypatch.setattr(cap, "_fill_and_verify_resource_fallback", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(cap, "_js_select", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_go_tab", lambda _page, name: tabs.append(name) or True)
    monkeypatch.setattr(cap, "_identify_new_app", lambda *args, **kwargs: {
        "success": False,
        "error": cap.err("NEW_APP_ID_NOT_FOUND", "VERIFY", "stop", cap.NEXT_MANUAL),
    })

    result = cap._stage_create_save(
        page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type"
    )

    assert result["success"] is False
    assert page.save_clicks == 1
    assert tabs == ["体验配置", "登录页配置", "基础配置"]


def test_normal_native_fill_model_and_tab_round_trip_pass(monkeypatch):
    cap = _stub_login_and_import()
    page = FillPage()
    monkeypatch.setattr(cap, "_read_visible_copy_dialog_input", lambda *args, **kwargs: _readback())
    monkeypatch.setattr(cap, "_go_tab", lambda *args, **kwargs: True)

    result = cap._fill_and_verify_resource_fallback(page, EXPECTED)

    assert result == {"success": True}
    assert [call[0] for call in page.input_locator.calls] == ["fill", "press"]


def _post_setup(monkeypatch, cap, *, main_identity=None, reads=None):
    monkeypatch.setattr(cap, "_locate_known_main_row_for_resource_fallback", lambda *args, **kwargs: main_identity or {"success": True, "row_idx": 0, "row_key": "row-0"})
    monkeypatch.setattr(cap, "_open_copy_dialog_by_known_main_row", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_go_tab", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_close_copy_dialog_after_verify", lambda *args, **kwargs: True)
    if reads is not None:
        iterator = iter(reads)
        monkeypatch.setattr(cap, "_verify_resource_fallback_readback", lambda *args, **kwargs: next(iterator))


def test_post_save_main_row_three_anchors_match():
    cap = _stub_login_and_import()
    page = MainTablePage(_main_payload(("new-id", "demo", "channel-a")))

    result = cap._verify_persisted_main_row_identity(page, "new-id", "demo", "channel-a")

    assert result == {"success": True, "row_idx": 0}


@pytest.mark.parametrize(
    "payload",
    [
        _main_payload(("new-id", "other-name", "channel-a")),
        _main_payload(("new-id", "demo", "other-channel")),
        _main_payload(("new-id", "demo", "channel-a"), ("new-id", "demo", "channel-a")),
        _main_payload(("new-id", "channel-a"), headers=("ID", "所属渠道")),
        _main_payload(("demo", "channel-a"), headers=("应用名称", "所属渠道")),
        _main_payload(("new-id", "demo"), headers=("ID", "应用名称", "所属渠道")),
        _main_payload(("new-id", "new-id", "demo", "channel-a"), headers=("ID", "应用ID", "应用名称", "所属渠道")),
        _main_payload(("new-id", "demo", "demo", "channel-a"), headers=("ID", "应用名称", "应用名", "所属渠道")),
        _main_payload(("new-id", "demo", "channel-a", "channel-a"), headers=("ID", "应用名称", "所属渠道", "渠道名称")),
        _main_payload(("new-id", "demo", "channel-a"), table_count=2),
    ],
)
def test_post_save_main_row_missing_ambiguous_or_mismatched_anchor_fails(payload):
    cap = _stub_login_and_import()

    result = cap._verify_persisted_main_row_identity(
        MainTablePage(payload), "new-id", "demo", "channel-a"
    )

    assert result["success"] is False
    assert result["error"]["code"] == "RESOURCE_FALLBACK_VERIFY_FAILED"
    assert result["error"]["stage"] == "VERIFY"


def test_open_copy_rechecks_all_anchors_on_verified_row_before_click():
    cap = _stub_login_and_import()
    page = CopyOpenPage(True)

    assert cap._open_copy_dialog_by_app_id(page, "new-id", 3, "demo", "channel-a") is True
    assert page.payload == {
        "appId": "new-id",
        "rowIdx": 3,
        "appName": "demo",
        "channelName": "channel-a",
    }
    assert "idIndexes.length !== 1" in page.script
    assert "cellText(nameIndexes[0])" in page.script
    assert "cellText(channelIndexes[0])" in page.script


def test_open_copy_anchor_recheck_failure_is_fail_closed():
    cap = _stub_login_and_import()

    assert cap._open_copy_dialog_by_app_id(CopyOpenPage(False), "new-id", 0, "demo", "channel-a") is False


def test_post_save_copy_dialog_default_identity_does_not_block_fallback_readback(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    _post_setup(monkeypatch, cap, reads=[{"success": True}, {"success": True}])

    result = cap._verify_persisted_resource_fallback(page, "new-id", EXPECTED, "demo", "channel-a")

    assert result == {"success": True}
    assert page.save_clicks == 0


def test_post_save_delayed_readback_requires_two_stable_matches(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    waits = []
    page.wait_for_timeout = lambda milliseconds: waits.append(milliseconds)
    fail = {"success": False, "error": cap.err("RESOURCE_FALLBACK_VERIFY_FAILED", "VERIFY", "mismatch", cap.NEXT_MANUAL)}
    _post_setup(monkeypatch, cap, reads=[fail, {"success": True}, {"success": True}])

    result = cap._verify_persisted_resource_fallback(page, "new-id", EXPECTED, "demo", "channel-a")

    assert result == {"success": True}
    assert waits == [cap._POST_SAVE_FALLBACK_POLL_MS, cap._POST_SAVE_FALLBACK_POLL_MS]
    assert page.save_clicks == 0


def test_post_save_main_identity_mismatch_is_never_success(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    mismatch = {"success": False, "error": cap.err("RESOURCE_FALLBACK_VERIFY_FAILED", "VERIFY", "identity mismatch", cap.NEXT_MANUAL)}
    _post_setup(monkeypatch, cap, main_identity=mismatch)

    result = cap._verify_persisted_resource_fallback(page, "new-id", EXPECTED, "demo", "channel-a")

    assert result["success"] is False
    assert result["error"]["stage"] == "VERIFY"
    assert page.save_clicks == 0


def test_post_save_empty_value_never_returns_success(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    fail = {"success": False, "error": cap.err("RESOURCE_FALLBACK_VERIFY_FAILED", "VERIFY", "empty", cap.NEXT_MANUAL)}
    _post_setup(monkeypatch, cap, reads=[fail] * cap._POST_SAVE_FALLBACK_POLL_ATTEMPTS)

    result = cap._verify_persisted_resource_fallback(page, "new-id", EXPECTED, "demo", "channel-a")

    assert result["success"] is False
    assert result["error"]["stage"] == "VERIFY"
    assert page.save_clicks == 0


@pytest.mark.parametrize(
    "error",
    [
        {"code": "SAVE_FAILED", "stage": "SAVE", "message": "dialog open", "next_action": "MANUAL_CHECK"},
        {"code": "NEW_APP_ID_AMBIGUOUS", "stage": "VERIFY", "message": "ambiguous", "next_action": "MANUAL_CHECK"},
        {"code": "NEW_APP_ID_NOT_FOUND", "stage": "VERIFY", "message": "not found", "next_action": "MANUAL_CHECK"},
    ],
)
def test_post_save_unconfirmed_failures_preserve_unknown_through_http(error):
    cap = _stub_login_and_import()
    from app.http_executor.real_runner import map_real_failure

    result = cap._post_save_unconfirmed_failure(error)
    state, business, _, envelope_error = map_real_failure({
        "business_status": cap._create_failure_business_status(result),
        "error_code": result["error"]["code"],
        "error_stage": result["error"]["stage"],
        "next_action": result["error"]["next_action"],
    })

    assert result["success"] is False
    assert result["save_may_have_occurred"] is True
    assert result["error"]["code"] == error["code"]
    assert result["error"]["stage"] == error["stage"]
    assert result["error"]["next_action"] == cap.NEXT_QUERY
    assert cap._create_failure_business_status(result) == cap.ex.BIZ_UNKNOWN
    assert (state, business) == ("UNKNOWN", "UNKNOWN")
    assert envelope_error["next_action"] == cap.NEXT_QUERY
    assert envelope_error["error_code"] == error["code"]


def test_save_dialog_still_open_after_click_is_unknown(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_fill_and_verify_resource_fallback", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(cap, "capture_page_errors", lambda *args, **kwargs: {"dialog_open": True})

    result = cap._stage_create_save(page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type")

    assert result["success"] is False
    assert result["error"]["code"] == "SAVE_FAILED"
    assert result["error"]["next_action"] == cap.NEXT_QUERY
    assert result["save_may_have_occurred"] is True
    assert page.save_clicks == 1


def test_stage_logs_successful_save_observation_before_followup_verification(monkeypatch, capsys):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_fill_and_verify_resource_fallback", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(cap, "capture_page_errors", lambda *args, **kwargs: {"dialog_open": False})
    monkeypatch.setattr(cap, "_identify_new_app", lambda *args, **kwargs: {
        "success": False, "error": cap.err("NEW_APP_ID_NOT_FOUND", "VERIFY", "not found", cap.NEXT_MANUAL)
    })

    cap._stage_create_save(
        page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type"
    )

    output = capsys.readouterr().out
    assert "[create_app] save_observation=" in output
    assert '"outcome":"success"' in output
    assert '"method":"POST"' in output
    assert '"path_hash":"0123456789abcdef"' in output


@pytest.mark.parametrize("outcome", ["business_rejected", "non_2xx", "no_candidate", "response_timeout", "ambiguous_candidate"])
def test_save_observer_uncertainty_stops_after_one_click(monkeypatch, outcome):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_fill_and_verify_resource_fallback", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(cap, "_wait_for_save_click_observation", lambda *args, **kwargs: {"outcome": outcome})

    result = cap._stage_create_save(
        page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type"
    )

    assert result["success"] is False
    assert result["save_may_have_occurred"] is True
    assert result["error"]["next_action"] == cap.NEXT_QUERY
    assert page.save_clicks == 1


def test_post_save_ambiguous_new_id_is_unknown(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_fill_and_verify_resource_fallback", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(cap, "capture_page_errors", lambda *args, **kwargs: {"dialog_open": False})
    monkeypatch.setattr(cap, "_identify_new_app", lambda *args, **kwargs: {"success": False, "error": cap.err("NEW_APP_ID_AMBIGUOUS", "VERIFY", "ambiguous", cap.NEXT_MANUAL)})

    result = cap._stage_create_save(page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type")

    assert result["success"] is False
    assert result["error"]["code"] == "NEW_APP_ID_AMBIGUOUS"
    assert result["error"]["next_action"] == cap.NEXT_QUERY
    assert result["save_may_have_occurred"] is True
    assert page.save_clicks == 1


def test_post_save_known_main_identity_failure_is_unknown(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_fill_and_verify_resource_fallback", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(cap, "capture_page_errors", lambda *args, **kwargs: {"dialog_open": False})
    monkeypatch.setattr(cap, "_identify_new_app", lambda *args, **kwargs: {"success": True, "app_id": "new-id"})
    monkeypatch.setattr(cap, "_verify_persisted_resource_fallback", lambda *args, **kwargs: {"success": False, "error": cap.err("RESOURCE_FALLBACK_VERIFY_FAILED", "VERIFY", "identity", cap.NEXT_MANUAL)})

    result = cap._stage_create_save(page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type")

    assert result["success"] is False
    assert result["error"]["code"] == "RESOURCE_FALLBACK_VERIFY_FAILED"
    assert result["error"]["next_action"] == cap.NEXT_QUERY
    assert result["save_may_have_occurred"] is True
    assert page.save_clicks == 1


def test_post_save_failure_marks_stage_as_may_have_saved(monkeypatch):
    cap = _stub_login_and_import()
    page = StagePage()
    _prepare_stage(monkeypatch, cap, page)
    monkeypatch.setattr(cap, "_fill_and_verify_resource_fallback", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(cap, "capture_page_errors", lambda *args, **kwargs: {"dialog_open": False})
    monkeypatch.setattr(cap, "_identify_new_app", lambda *args, **kwargs: {"success": True, "app_id": "new-id"})
    monkeypatch.setattr(cap, "_find_target_row_by_id", lambda *args, **kwargs: {"found": True, "row_idx": 0})
    monkeypatch.setattr(cap, "_verify_persisted_resource_fallback", lambda *args, **kwargs: {"success": False, "error": cap.err("RESOURCE_FALLBACK_VERIFY_FAILED", "VERIFY", "empty", cap.NEXT_MANUAL)})

    result = cap._stage_create_save(page, "exec-1", _stage_data(), "https://example.invalid/ref", "1", "demo", "", EXPECTED, "type")

    assert result["success"] is False
    assert result["save_may_have_occurred"] is True
    assert result["error"]["next_action"] == cap.NEXT_QUERY
    assert cap._create_failure_business_status(result) == cap.ex.BIZ_UNKNOWN
    assert page.save_clicks == 1


def test_action_api_exposes_declared_unknown_to_http_runner():
    _stub_login_and_import()
    from app.executor.actions import api

    stripped = api._strip({
        "business_status": "UNKNOWN",
        "data": {},
        "error": {"code": "RESOURCE_FALLBACK_VERIFY_FAILED", "stage": "VERIFY", "next_action": "MANUAL_CHECK"},
    })

    assert stripped["success"] is False
    assert stripped["business_status"] == "UNKNOWN"


def test_http_failure_mapping_preserves_declared_unknown():
    from app.http_executor.real_runner import map_real_failure

    state, business, data, error = map_real_failure({
        "business_status": "UNKNOWN",
        "next_action": "MANUAL_CHECK",
        "error_code": "RESOURCE_FALLBACK_VERIFY_FAILED",
        "error_stage": "VERIFY",
    })

    assert (state, business) == ("UNKNOWN", "UNKNOWN")
    assert data["failed_stage"] == "VERIFY"
    assert error["error_code"] == "RESOURCE_FALLBACK_VERIFY_FAILED"
