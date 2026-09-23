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
    def __init__(self, *, text="target", visible=True, classes="", aria_disabled=None, box=None, click_error=False):
        self.text = text
        self.visible = visible
        self.classes = classes
        self.aria_disabled = aria_disabled
        self.box = {"x": 10, "y": 20, "width": 30, "height": 40} if box is None else box
        self.click_error = click_error
        self.clicks = 0

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

    def click(self):
        self.clicks += 1
        if self.click_error:
            raise RuntimeError("actionability failure")


class _Locator:
    def __init__(self, items, *, click_items=None):
        self.items = list(items)
        self.click_items = self.items if click_items is None else list(click_items)
        self.filter_calls = []

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]

    def filter(self, **kwargs):
        self.filter_calls.append(kwargs)
        return self

    def _strict_item(self, items=None):
        candidates = self.items if items is None else items
        if len(candidates) != 1:
            raise RuntimeError("strict locator violation")
        return candidates[0]

    def is_visible(self):
        return self._strict_item().is_visible()

    def get_attribute(self, name):
        return self._strict_item().get_attribute(name)

    def inner_text(self):
        return self._strict_item().inner_text()

    def bounding_box(self):
        return self._strict_item().bounding_box()

    def click(self):
        self._strict_item(self.click_items).click()


class _Dropdown(_Option):
    def __init__(self, options, *, click_options=None, **kwargs):
        super().__init__(**kwargs)
        self.options = _Locator(options, click_items=click_options)

    def locator(self, selector):
        assert selector == ".el-select-dropdown__item:visible"
        return self.options


class LocatorClickPage:
    """Offline Playwright-shaped page; the helper drives locator.click directly."""

    def __init__(self, dropdowns, *, exact_options=None):
        self.dropdowns = _Locator(dropdowns)
        self.exact_options = exact_options or (dropdowns[0].options if len(dropdowns) == 1 else _Locator([]))

    def locator(self, selector):
        if selector == ".el-select-dropdown:visible":
            return self.dropdowns
        assert ".el-select-dropdown__item:visible" in selector
        return self.exact_options


def test_locator_option_helper_rechecks_then_clicks_exact_option_once_without_mouse(capsys):
    cap = _cap()
    option = _Option(text="target")
    dropdown = _Dropdown([option])
    page = LocatorClickPage([dropdown])

    assert cap._click_unique_exact_visible_select_option(page, "target", "channel") is True
    assert page.exact_options.filter_calls and "has_text" in page.exact_options.filter_calls[0]
    assert option.clicks == 1
    assert not hasattr(page, "mouse")
    output = capsys.readouterr().out
    assert '"reason":"success"' in output and '"stage":"channel"' in output
    assert "target" not in output


def test_locator_option_helper_fails_closed_when_strict_click_sees_new_duplicate(capsys):
    cap = _cap()
    first = _Option(text="target")
    second = _Option(text="target")
    dropdown = _Dropdown([first], click_options=[first, second])
    page = LocatorClickPage([dropdown])

    assert cap._click_unique_exact_visible_select_option(page, "target", "channel") is False
    assert first.clicks == 0 and second.clicks == 0
    assert not hasattr(page, "mouse")
    output = capsys.readouterr().out
    assert '"reason":"locator_click_failed"' in output
    assert "target" not in output


def test_locator_option_helper_fails_closed_when_new_dropdown_invalidates_global_guard(capsys):
    cap = _cap()
    first = _Option(text="target")
    second_dropdown = _Dropdown([])
    dropdown = _Dropdown([first])
    page = LocatorClickPage([dropdown])
    guarded_options = _Locator([first])

    def strict_global_click():
        # A second visible dropdown appears after initial count/attribute checks.
        page.dropdowns.items.append(second_dropdown)
        if page.dropdowns.count() != 1:
            raise RuntimeError("strict global dropdown guard")
        guarded_options._strict_item().click()

    guarded_options.click = strict_global_click
    page.exact_options = guarded_options

    assert cap._click_unique_exact_visible_select_option(page, "target", "channel") is False
    assert page.dropdowns.count() == 2
    assert first.clicks == 0
    assert not hasattr(page, "mouse")
    output = capsys.readouterr().out
    assert '"reason":"locator_click_failed"' in output
    assert "target" not in output
    assert second_dropdown.options.count() == 0  # The new menu need not contain the target.


@pytest.mark.parametrize(
    ("dropdowns", "target", "expected_reason", "expected_clicks"),
    [
        ([], "target", "dropdown_count", 0),
        ([_Dropdown([]), _Dropdown([])], "target", "dropdown_count", 0),
        ([_Dropdown([], visible=False)], "target", "option_not_visible", 0),
        ([_Dropdown([], aria_disabled="true")], "target", "option_count", 0),
        ([_Dropdown([])], "target", "option_count", 0),
        ([_Dropdown([_Option(text="target"), _Option(text="target")])], "target", "option_count", 0),
        ([_Dropdown([_Option(text="wrong")])], "target", "option_text_mismatch", 0),
        ([_Dropdown([_Option(text="target", visible=False)])], "target", "option_not_visible", 0),
        ([_Dropdown([_Option(text="target", classes="is-disabled")])], "target", "option_disabled", 0),
        ([_Dropdown([_Option(text="target", aria_disabled="true")])], "target", "option_disabled", 0),
        ([_Dropdown([_Option(text="target", box={})])], "target", "option_box_invalid", 0),
        ([_Dropdown([_Option(text="target", click_error=True)])], "target", "locator_click_failed", 1),
    ],
)
def test_locator_option_helper_fails_closed_with_value_free_category(dropdowns, target, expected_reason, expected_clicks, capsys):
    cap = _cap()
    page = LocatorClickPage(dropdowns)

    assert cap._click_unique_exact_visible_select_option(page, target, "application") is False
    click_count = sum(option.clicks for dropdown in dropdowns for option in dropdown.options.items)
    assert click_count == expected_clicks
    assert not hasattr(page, "mouse")
    output = capsys.readouterr().out
    assert f'"reason":"{expected_reason}"' in output and '"stage":"application"' in output
    assert target not in output and "http" not in output and "body" not in output and "header" not in output


def test_post_click_diagnostics_are_fixed_and_value_free(capsys):
    cap = _cap()

    assert cap._exact_select_post_click_ok("channel", {"ok": False, "reason": "dropdown_not_closed"}) is False
    assert cap._exact_select_post_click_ok("application", {"ok": False, "reason": "selected_value_mismatch"}) is False
    output = capsys.readouterr().out
    assert '"reason":"dropdown_not_closed"' in output
    assert '"reason":"selected_value_mismatch"' in output
    assert "channel-name" not in output and "application-name" not in output


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
    page = ExactSelectPage([{"opened": True}, {"ok": True}, True, {"ok": True}])
    pointer_targets = []
    monkeypatch.setattr(cap, "_click_unique_exact_visible_select_option", lambda _page, target, stage: pointer_targets.append((target, stage)) or True)

    assert cap._prepare_exact_terminal_filters(page, "channel", "application") is True
    assert pointer_targets == [("channel", "channel"), ("application", "application")]
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
        ([{"opened": True}], [False], 1, [("channel", "channel")]),
        ([{"opened": True}, {"ok": False, "reason": "dropdown_not_closed"}], [True], 2, [("channel", "channel")]),
        ([{"opened": True}, {"ok": True}, False], [True], 3, [("channel", "channel")]),
        ([{"opened": True}, {"ok": True}, True], [True, False], 3, [("channel", "channel"), ("application", "application")]),
        ([{"opened": True}, {"ok": True}, True, {"ok": False, "reason": "selected_value_mismatch"}], [True, True], 4, [("channel", "channel"), ("application", "application")]),
    ],
)
def test_prepare_exact_filters_fails_closed_without_lower_stage_or_search(monkeypatch, results, pointer_results, expected_evaluations, expected_pointers):
    cap = _cap()
    page = ExactSelectPage(results)
    targets = []
    pointer_results = iter(pointer_results)
    monkeypatch.setattr(cap, "_click_unique_exact_visible_select_option", lambda _page, target, stage: targets.append((target, stage)) or next(pointer_results))

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
