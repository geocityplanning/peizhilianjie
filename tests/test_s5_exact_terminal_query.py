# -*- coding: utf-8 -*-
"""Offline D-044 B guards for the fresh exact post-enable terminal proof."""
from __future__ import annotations

import inspect
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


META = {"total_count": 1, "item_count": 1, "page_count": 1}
ROW = {"logical_key": "native-row", "row_idx": 0, "key_kind": "native"}


class Page:
    def __init__(self):
        self.waits = []

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)

    def evaluate(self, script, data=None):
        return 0 if ".el-message-box__wrapper" in script else True


def _install_stable_dom(monkeypatch, cap):
    monkeypatch.setattr(cap, "_read_list_restore_state", lambda page: {
        "row_count": 1, "total_count": 1, "next_enabled": False, "table_signature": "one",
    })
    monkeypatch.setattr(cap, "_snapshot_known_main_id_rows", lambda page, app_id: {
        "success": True, "page": 1, "candidates": [dict(ROW)],
    })
    monkeypatch.setattr(cap, "_known_main_identity_from_snapshot", lambda *args: {
        "success": True, "row": dict(ROW),
    })
    monkeypatch.setattr(cap, "_log_known_main_snapshot", lambda *args: None)


def test_exact_terminal_payload_requires_one_exact_channel_and_app_and_no_dynamic_fields():
    cap = _cap()
    payload = '{"platformType":0,"channelName":"channel","appName":"application","pageNum":1}'

    assert cap._request_carries_exact_terminal_filters("https://example.invalid/getAppInfoList", payload, "channel", "application")
    assert not cap._request_carries_exact_terminal_filters("https://example.invalid/getAppInfoList", payload, "chan", "application")
    assert not cap._request_carries_exact_terminal_filters(
        "https://example.invalid/getAppInfoList", payload[:-1] + ',"unexpected":"x"}', "channel", "application"
    )
    assert not cap._request_carries_exact_terminal_filters(
        "https://example.invalid/getAppInfoList", '{"platformType":0,"channelName":"channel","appName":"application","status":1}',
        "channel", "application"
    )


def test_exact_terminal_observer_records_only_a_fresh_complete_dual_filter_response():
    cap = _cap()

    class Session:
        def __init__(self):
            self.handlers = {}
        def send(self, method, params=None):
            if method == "Network.enable":
                return {}
            if method == "Network.getResponseBody":
                return {"body": '{"data":{"totalCount":1,"pageCount":1,"list":[{}]}}'}
            raise AssertionError(method)
        def on(self, event, handler):
            self.handlers[event] = handler
        def detach(self):
            pass

    class Context:
        def __init__(self, session):
            self.session = session
        def new_cdp_session(self, page):
            return self.session

    class ObserverPage:
        def __init__(self, session):
            self.context = Context(session)

    session = Session()
    observer = cap._attach_list_response_observer(
        ObserverPage(session), exact_terminal_filters=("channel", "application")
    )
    session.handlers["Network.requestWillBeSent"]({"requestId": "one", "request": {
        "url": "https://example.invalid/getAppInfoList", "method": "POST",
        "postData": '{"platformType":0,"channelName":"channel","appName":"application"}',
    }})
    session.handlers["Network.responseReceived"]({"requestId": "one", "response": {
        "url": "https://example.invalid/getAppInfoList", "status": 200,
    }})
    session.handlers["Network.loadingFinished"]({"requestId": "one"})

    assert observer.exact_required_filter_request_count == 1
    assert observer.exact_required_filter_2xx_count == 1
    assert observer.exact_required_filter_success_records == [META]


def test_fresh_exact_proof_requires_one_new_complete_response_and_two_stable_dom_reads(monkeypatch):
    cap = _cap()
    page = Page()
    _install_stable_dom(monkeypatch, cap)
    observations = cap._ListRequestObserver()
    observations.exact_required_filter_request_count = 1
    observations.exact_required_filter_success_records.append(dict(META))

    assert cap._wait_for_fresh_exact_terminal_proof(page, observations, 0, 0, "id", "name", "channel") is True
    assert page.waits == [250, 250]


def test_fresh_exact_proof_rejects_old_response_or_duplicate_request_without_reset_or_scan(monkeypatch):
    cap = _cap()
    page = Page()
    _install_stable_dom(monkeypatch, cap)
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: (_ for _ in ()).throw(AssertionError("no reset")))
    monkeypatch.setattr(cap, "_scan_known_main_id_pages", lambda *args: (_ for _ in ()).throw(AssertionError("no scan")))
    observations = cap._ListRequestObserver()
    observations.exact_required_filter_request_count = 1
    observations.exact_required_filter_success_records.append(dict(META))

    # The sole record predates the search baseline and must not unlock it.
    assert cap._wait_for_fresh_exact_terminal_proof(page, observations, 1, 1, "id", "name", "channel") is False
    observations.exact_required_filter_request_count = 3
    observations.exact_required_filter_success_records.extend([dict(META), dict(META)])
    # More than one fresh exact request is equally fail-closed.
    assert cap._wait_for_fresh_exact_terminal_proof(page, observations, 1, 1, "id", "name", "channel") is False


def test_terminal_query_fails_when_exact_filter_form_is_not_unique(monkeypatch):
    cap = _cap()

    class MissingFormPage(Page):
        def evaluate(self, script, data=None):
            return {"opened": False}

    assert cap._prepare_exact_terminal_filters(MissingFormPage(), "channel", "name") is False

    page = Page()
    observations = cap._ListRequestObserver()
    calls = []
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda *args, **kwargs: calls.append("attach") or observations)
    monkeypatch.setattr(cap, "_prepare_exact_terminal_filters", lambda *args: calls.append("prepare") or False)
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda *args: calls.append("detach"))
    monkeypatch.setattr(cap, "_click_exact_terminal_search_once", lambda *args: (_ for _ in ()).throw(AssertionError("no second search")))

    assert cap._verify_enable_with_fresh_exact_terminal_query(page, "id", "name", "channel") is False
    assert calls == ["attach", "prepare", "detach"]


class _Option:
    def __init__(self, *, text="target", visible=True, classes="", aria_disabled=None, box=None):
        self.text = text
        self.visible = visible
        self.classes = classes
        self.aria_disabled = aria_disabled
        self.box = {"x": 10, "y": 20, "width": 30, "height": 40} if box is None else box

    def is_visible(self):
        return self.visible

    def get_attribute(self, name):
        if name == "class":
            return self.classes
        if name == "aria-disabled":
            return self.aria_disabled
        return None

    def inner_text(self):
        return self.text

    def bounding_box(self):
        return self.box


class _Locator:
    def __init__(self, items):
        self.items = list(items)
        self.filter_calls = []

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]

    def filter(self, **kwargs):
        self.filter_calls.append(kwargs)
        return self


class _Dropdown(_Option):
    def __init__(self, options, **kwargs):
        super().__init__(**kwargs)
        self.options = _Locator(options)

    def locator(self, selector):
        assert selector == ".el-select-dropdown__item:visible"
        return self.options


class _Mouse:
    def __init__(self, error=False):
        self.error = error
        self.clicks = []

    def click(self, x, y):
        if self.error:
            raise RuntimeError("click failure")
        self.clicks.append((x, y))


class LocatorPointerPage:
    """Offline Playwright-shaped page; the helper drives every locator/mouse branch."""

    def __init__(self, dropdowns, *, mouse_error=False):
        self.dropdowns = _Locator(dropdowns)
        self.mouse = _Mouse(mouse_error)

    def locator(self, selector):
        assert selector == ".el-select-dropdown:visible"
        return self.dropdowns


def test_real_pointer_option_helper_uses_locator_rechecks_then_one_mouse_click():
    cap = _cap()
    option = _Option(text="target")
    dropdown = _Dropdown([option])
    page = LocatorPointerPage([dropdown])

    assert cap._click_unique_exact_visible_select_option(page, "target") is True
    assert dropdown.options.filter_calls and "has_text" in dropdown.options.filter_calls[0]
    assert page.mouse.clicks == [(25.0, 40.0)]


@pytest.mark.parametrize(
    ("dropdowns", "target", "mouse_error"),
    [
        ([], "target", False),
        ([_Dropdown([]), _Dropdown([])], "target", False),
        ([_Dropdown([], visible=False)], "target", False),
        ([_Dropdown([], aria_disabled="true")], "target", False),
        ([_Dropdown([])], "target", False),
        ([_Dropdown([_Option(text="target"), _Option(text="target")])], "target", False),
        ([_Dropdown([_Option(text="wrong")])], "target", False),
        ([_Dropdown([_Option(text="target", visible=False)])], "target", False),
        ([_Dropdown([_Option(text="target", classes="is-disabled")])], "target", False),
        ([_Dropdown([_Option(text="target", aria_disabled="true")])], "target", False),
        ([_Dropdown([_Option(text="target", box={})])], "target", False),
        ([_Dropdown([_Option(text="target")])], "target", True),
    ],
)
def test_real_pointer_option_helper_fails_closed_for_locator_and_click_guards(dropdowns, target, mouse_error):
    cap = _cap()
    page = LocatorPointerPage(dropdowns, mouse_error=mouse_error)

    assert cap._click_unique_exact_visible_select_option(page, target) is False
    assert page.mouse.clicks == []


class ExactSelectPage:
    """Offline evaluator that drives real _prepare gates after pointer clicks."""

    def __init__(self, results):
        self.results = list(results)
        self.scripts = []
        self.payloads = []
        self.waits = []

    def evaluate(self, script, payload=None):
        self.scripts.append(script)
        self.payloads.append(payload)
        assert self.results, "unexpected page evaluation"
        return self.results.pop(0)

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


def test_prepare_exact_filters_orders_channel_pointer_then_app_pointer_before_search(monkeypatch):
    cap = _cap()
    page = ExactSelectPage([{"opened": True}, True, True, True])
    pointer_targets = []
    monkeypatch.setattr(cap, "_click_unique_exact_visible_select_option", lambda _page, target: pointer_targets.append(target) or True)

    assert cap._prepare_exact_terminal_filters(page, "channel", "application") is True
    assert pointer_targets == ["channel", "application"]
    assert page.payloads == [None, "channel", None, {"channelName": "channel", "appName": "application"}]
    assert page.waits == [300, 300, 300, 300]
    assert "length !== 0" in page.scripts[1]
    assert "!matches[0].readOnly" in page.scripts[2]
    assert "length !== 0" in page.scripts[3]
    assert "HTMLInputElement.prototype" not in "\n".join(page.scripts)
    assert "dispatchEvent" not in "\n".join(page.scripts)


@pytest.mark.parametrize(
    ("results", "pointer_results", "expected_evaluations", "expected_pointers"),
    [
        ([{"opened": False}], [], 1, []),
        ([{"opened": True}], [False], 1, ["channel"]),
        ([{"opened": True}, False], [True], 2, ["channel"]),
        ([{"opened": True}, True, False], [True], 3, ["channel"]),
        ([{"opened": True}, True, True], [True, False], 3, ["channel", "application"]),
        ([{"opened": True}, True, True, False], [True, True], 4, ["channel", "application"]),
    ],
)
def test_prepare_exact_filters_fails_closed_without_lower_stage_or_search(monkeypatch, results, pointer_results, expected_evaluations, expected_pointers):
    cap = _cap()
    page = ExactSelectPage(results)
    targets = []
    pointer_results = iter(pointer_results)
    monkeypatch.setattr(cap, "_click_unique_exact_visible_select_option", lambda _page, target: targets.append(target) or next(pointer_results))

    assert cap._prepare_exact_terminal_filters(page, "channel", "application") is False
    assert targets == expected_pointers
    assert len(page.scripts) == expected_evaluations


def test_exact_terminal_search_rechecks_readonly_app_value_without_model_write():
    cap = _cap()

    class SearchPage:
        def __init__(self):
            self.script = ""
        def evaluate(self, script, payload=None):
            self.script = script
            return False

    page = SearchPage()
    assert cap._click_exact_terminal_search_once(page, "channel", "application") is False
    assert "input.readOnly" in page.script
    assert "selects.length === 1" in page.script
    assert "appTags" in page.script
    assert "buttons.length !== 1" in page.script
    assert "HTMLInputElement.prototype" not in page.script
    assert "dispatchEvent" not in page.script


def test_exact_terminal_search_is_called_once_and_failure_does_not_retry(monkeypatch):
    cap = _cap()
    page = Page()
    observations = cap._ListRequestObserver()
    calls = []
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda *args, **kwargs: observations)
    monkeypatch.setattr(cap, "_prepare_exact_terminal_filters", lambda *args: True)
    monkeypatch.setattr(cap, "_click_exact_terminal_search_once", lambda *args: calls.append("search") or True)
    monkeypatch.setattr(cap, "_wait_for_fresh_exact_terminal_proof", lambda *args: False)
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda *args: calls.append("detach"))

    assert cap._verify_enable_with_fresh_exact_terminal_query(page, "id", "name", "channel") is False
    assert calls == ["search", "detach"]


def test_fresh_exact_proof_rejects_non2xx_or_incomplete_response(monkeypatch):
    cap = _cap()
    page = Page()
    _install_stable_dom(monkeypatch, cap)
    for records in ([], [{"total_count": 1}]):
        observations = cap._ListRequestObserver()
        observations.exact_required_filter_request_count = 1
        observations.exact_required_filter_success_records.extend(records)
        assert cap._wait_for_fresh_exact_terminal_proof(page, observations, 0, 0, "id", "name", "channel") is False


def test_fresh_exact_proof_rejects_response_dom_mismatch_and_anchor_conflicts(monkeypatch):
    cap = _cap()
    page = Page()
    observations = cap._ListRequestObserver()
    observations.exact_required_filter_request_count = 1
    observations.exact_required_filter_success_records.append(dict(META))
    for state in (
        {"row_count": 2, "total_count": 2, "next_enabled": False, "table_signature": "two"},
        {"row_count": 1, "total_count": 1, "next_enabled": True, "table_signature": "one"},
    ):
        monkeypatch.setattr(cap, "_read_list_restore_state", lambda page, state=state: state)
        assert cap._wait_for_fresh_exact_terminal_proof(page, observations, 0, 0, "id", "name", "channel") is False

    _install_stable_dom(monkeypatch, cap)
    for reason in ("zero_candidates", "ambiguous_main_rows", "main_anchor_mismatch"):
        monkeypatch.setattr(cap, "_known_main_identity_from_snapshot", lambda *args, reason=reason: {
            "success": False, "reason": reason,
        })
        assert cap._wait_for_fresh_exact_terminal_proof(page, observations, 0, 0, "id", "name", "channel") is False


def test_fresh_exact_proof_rejects_zero_duplicate_and_each_three_anchor_mismatch():
    cap = _cap()
    snapshot = {
        "page": 1,
        "candidates": [{"app_id": "id", "app_name": "name", "channel_name": "channel", "row_key": "row", "row_idx": 0}],
    }
    assert cap._known_main_identity_from_snapshot({"page": 1, "candidates": []}, "id", "name", "channel")["success"] is False
    duplicate = dict(snapshot)
    duplicate["candidates"] = snapshot["candidates"] + [{"app_id": "id", "app_name": "name", "channel_name": "other", "row_key": "other", "row_idx": 1}]
    assert cap._known_main_identity_from_snapshot(duplicate, "id", "name", "channel")["success"] is False
    for expected in (("other", "name", "channel"), ("id", "other", "channel"), ("id", "name", "other")):
        assert cap._known_main_identity_from_snapshot(snapshot, *expected)["success"] is False


def test_fresh_exact_proof_rejects_unstable_double_read(monkeypatch):
    cap = _cap()
    page = Page()
    _install_stable_dom(monkeypatch, cap)
    observations = cap._ListRequestObserver()
    observations.exact_required_filter_request_count = 1
    observations.exact_required_filter_success_records.append(dict(META))
    reads = []
    def changing_identity(*args):
        reads.append(True)
        return {"success": True, "row": {"logical_key": "a" if len(reads) % 2 else "b", "row_idx": 0}}
    monkeypatch.setattr(cap, "_known_main_identity_from_snapshot", changing_identity)

    assert cap._wait_for_fresh_exact_terminal_proof(page, observations, 0, 0, "id", "name", "channel") is False


def test_stage_enable_terminal_failure_stays_query_without_second_enable_or_search(monkeypatch):
    cap = _cap()
    page = Page()
    observer = cap._SaveClickObserver()
    calls = []
    monkeypatch.setattr(cap, "_locate_known_main_row_in_current_view", lambda *args: {
        "success": True, "page": 1, "row_idx": 0, "row_key": "row", "key_kind": "native",
    })
    monkeypatch.setattr(cap, "_post_save_narrow_row_switch_state", lambda *args: "switch_unchecked")
    monkeypatch.setattr(cap, "_attach_save_click_observer", lambda page: observer)
    monkeypatch.setattr(cap, "_detach_save_click_observer", lambda observer: calls.append("detach"))
    monkeypatch.setattr(cap, "_click_unchecked_switch_by_known_main_row", lambda *args: calls.append("enable") or True)
    monkeypatch.setattr(cap, "_wait_for_switch_action_observation", lambda *args: {"outcome": "success"})
    monkeypatch.setattr(cap, "_verify_enable_with_fresh_exact_terminal_query", lambda *args: calls.append("terminal") or False)
    monkeypatch.setattr(cap, "_reset_list_filters", lambda *args: (_ for _ in ()).throw(AssertionError("no unfiltered fallback")))

    result = cap._stage_enable(page, "exec", "id", "name", "channel", {
        "success": True, "page": 1, "row_idx": 0, "row_key": "row", "key_kind": "native",
    })

    assert result["success"] is False
    assert result["error"]["next_action"] == cap.NEXT_QUERY
    assert result["enable_may_have_occurred"] is True
    assert calls == ["enable", "detach", "terminal"]


def test_stage_enable_uses_exact_terminal_query_not_unfiltered_locator_or_reset():
    cap = _cap()
    source = inspect.getsource(cap._stage_enable)

    assert "_verify_enable_with_fresh_exact_terminal_query" in source
    assert "_locate_known_main_row_for_resource_fallback" not in source
    assert "_reset_list_filters" not in source
