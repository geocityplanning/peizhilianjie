# -*- coding: utf-8 -*-
"""Offline behavior tests for the list-response observer and channel search.

These tests never connect to UAT, Chrome, or CDP.
"""
from __future__ import annotations

import sys
import types

import pytest
from urllib.parse import quote

LIST_URL = "https://example.invalid/backend/cloudTrial/appInfo/getAppInfoList"
OTHER_URL = "https://example.invalid/static/app.js"
SECRET_CHANNEL = "TARGET-CHANNEL-XYZ-机密"


def _stub_login_and_import():
    if "actions.ensure_login" not in sys.modules:
        stub = types.ModuleType("actions.ensure_login")
        stub.ensure_login = lambda page=None: {"success": False}
        stub.is_logged_in = lambda page: True
        sys.modules["actions.ensure_login"] = stub
    from app.executor.actions import create_app_v2 as cap

    return cap


class FakeCdpSession:
    def __init__(self, response_bodies=None):
        self.enabled = False
        self.handlers = {}
        self.detached = False
        self.sends = []
        self.response_bodies = response_bodies or {}

    def send(self, method, params=None):
        self.sends.append((method, params))
        if method == "Network.enable":
            self.enabled = True
            return {}
        if method == "Network.getResponseBody":
            request_id = (params or {}).get("requestId")
            if request_id in self.response_bodies:
                return self.response_bodies[request_id]
            raise RuntimeError("no body")

    def on(self, event, handler):
        self.handlers[event] = handler

    def detach(self):
        self.detached = True

    def emit(self, event, params):
        self.handlers[event](params)


class FakeContext:
    def __init__(self, session=None, error=None):
        self.session = session
        self.error = error

    def new_cdp_session(self, page):
        if self.error is not None:
            raise self.error
        return self.session


class FakePage:
    def __init__(self, session=None, cdp_error=None, page_event_error=None):
        self.context = FakeContext(session, cdp_error)
        self.page_event_error = page_event_error
        self.handlers = {}

    def on(self, event, handler):
        if self.page_event_error is not None:
            raise self.page_event_error
        self.handlers[event] = handler

    def off(self, event, handler):
        if self.handlers.get(event) is handler:
            del self.handlers[event]

    def emit(self, event, payload):
        handler = self.handlers.get(event)
        if handler is not None:
            handler(payload)


class FakeResponse:
    def __init__(self, url, status, text="", request=None):
        self.url = url
        self.status = status
        self._text = text
        self.request = request

    def text(self):
        return self._text


class FakeRequest:
    def __init__(self, url, post_data="", method="POST"):
        self.url = url
        self.post_data = post_data
        self.method = method


class FakeSearchPage:
    def __init__(self, *, opened=True, selected=True, verified=True, click_result=None):
        self.opened = opened
        self.selected = selected
        self.verified = verified
        self.click_result = click_result or {"clicked": True, "scope": "channel-form"}
        self.wait_calls = []
        self.scripts = []

    def evaluate(self, script, payload=None):
        self.scripts.append(script)
        if "scope: 'channel-form'" in script:
            return self.click_result
        if "el-tag__content" in script:
            return self.verified
        if "el-select-dropdown__item" in script:
            if self.selected:
                return {"selected": True}
            return {"selected": False, "exact_option_count": 0}
        if self.opened:
            return {"opened": True, "select_index": 0}
        return {"opened": False, "labeled_count": 0, "candidate_count": 0}

    def wait_for_timeout(self, milliseconds):
        self.wait_calls.append(milliseconds)


def _install_search_mocks(
    monkeypatch,
    cap,
    *,
    observations,
    stable=True,
    rows=None,
    on_wait=None,
    pagination=None,
):
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_dismiss_stray_dropdowns", lambda page: None)
    states = pagination or [
        {"page_number": 1, "table_signature": "A", "row_count": 5},
        {"page_number": 1, "table_signature": "B", "row_count": 2},
    ]
    reads = {"n": 0}

    def read_state(page):
        idx = min(reads["n"], len(states) - 1)
        reads["n"] += 1
        return states[idx]

    monkeypatch.setattr(cap, "_read_pagination_state", read_state)
    monkeypatch.setattr(
        cap,
        "_attach_list_response_observer",
        lambda page, target_filter=None: observations,
    )

    def wait_for_table_update(*args, **kwargs):
        if on_wait is not None:
            on_wait()
        return stable

    monkeypatch.setattr(cap, "wait_for_table_update", wait_for_table_update)
    monkeypatch.setattr(
        cap,
        "_read_current_page_app_rows",
        lambda page: rows if rows is not None else [{"channel_name": "chan"}],
    )


def test_is_list_request_url_matches_backend_list_paths():
    cap = _stub_login_and_import()
    assert cap._is_list_request_url(LIST_URL) is True
    assert cap._is_list_request_url("https://example.invalid/backend/foo/getList?x=1") is True
    assert cap._is_list_request_url("https://example.invalid/backend/foo/barList") is True
    assert cap._is_list_request_url(OTHER_URL) is False


def test_attach_observer_records_2xx_from_cdp_session():
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    page = FakePage(session=session)

    observations = cap._attach_list_response_observer(page)
    assert observations is not None
    assert observations.keepalive is session
    assert session.enabled is True

    session.emit(
        "Network.responseReceived",
        {"response": {"url": LIST_URL, "status": 200}},
    )
    assert list(observations) == [200]


def test_attach_observer_records_4xx_from_cdp_session():
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page)
    session.emit(
        "Network.responseReceived",
        {"response": {"url": LIST_URL, "status": 403}},
    )
    assert list(observations) == [403]


def test_attach_observer_ignores_non_list_urls():
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page)
    session.emit(
        "Network.responseReceived",
        {"response": {"url": OTHER_URL, "status": 200}},
    )
    assert list(observations) == []


def test_attach_observer_returns_none_when_no_cdp_and_no_page_events():
    cap = _stub_login_and_import()
    page = FakePage(
        cdp_error=RuntimeError("no cdp"),
        page_event_error=RuntimeError("no page events"),
    )
    assert cap._attach_list_response_observer(page) is None


def test_attach_observer_falls_back_to_page_events():
    cap = _stub_login_and_import()
    page = FakePage(cdp_error=RuntimeError("no cdp"))
    observations = cap._attach_list_response_observer(page)
    assert observations is not None
    page.handlers["response"](FakeResponse(LIST_URL, 204))
    page.handlers["response"](FakeResponse(LIST_URL, 500))
    assert list(observations) == [204, 500]


def test_detach_releases_cdp_session():
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    observations = cap._ListRequestObserver()
    observations.keepalive = session
    observations.append(200)

    cap._detach_list_response_observer(observations)
    assert session.detached is True
    assert observations.keepalive is None


def test_search_return_detail_false_is_boolean(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()
    _install_search_mocks(monkeypatch, cap, observations=observations, stable=True)
    result = cap._search_list_by_channel(FakeSearchPage(), "chan")
    assert result is True


def test_search_return_detail_includes_scope_and_http(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()

    def on_wait():
        observations.append(200)

    _install_search_mocks(
        monkeypatch, cap, observations=observations, stable=True, on_wait=on_wait
    )
    page = FakeSearchPage(click_result={"clicked": True, "scope": "channel-form"})
    detail = cap._search_list_by_channel(page, "chan", return_detail=True)
    assert isinstance(detail, dict)
    assert detail["search_clicked"] is True
    assert detail["search_scope"] == "channel-form"
    assert detail["filter_stable"] is True
    assert detail["list_requests_before"] == 0
    assert detail["list_requests_after"] == 1
    assert detail["last_http_status"] == 200
    assert observations.keepalive is None


def test_search_scope_global(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()
    _install_search_mocks(monkeypatch, cap, observations=observations, stable=False)
    detail = cap._search_list_by_channel(
        FakeSearchPage(click_result={"clicked": True, "scope": "global"}),
        "chan",
        return_detail=True,
    )
    assert detail["search_scope"] == "global"
    assert detail["filter_stable"] is False


def test_search_no_observer_leaves_request_fields_none(monkeypatch):
    cap = _stub_login_and_import()
    _install_search_mocks(monkeypatch, cap, observations=None, stable=True)
    detail = cap._search_list_by_channel(
        FakeSearchPage(), "chan", return_detail=True
    )
    assert detail["list_requests_before"] is None
    assert detail["list_requests_after"] is None
    assert detail["last_http_status"] is None
    assert detail["request_carried_target_filter"] is None
    assert detail["response_contains_target_channel"] is None
    assert detail["search_scope"] == "channel-form"


def test_search_observed_4xx_sets_last_http_status(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()

    def on_wait():
        observations.append(403)

    _install_search_mocks(
        monkeypatch, cap, observations=observations, stable=False, on_wait=on_wait
    )
    detail = cap._search_list_by_channel(
        FakeSearchPage(), "chan", return_detail=True
    )
    assert detail["last_http_status"] == 403
    assert detail["list_requests_after"] == 1


def test_search_releases_session_even_when_search_click_fails(monkeypatch):
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    observations = cap._ListRequestObserver()
    observations.keepalive = session
    _install_search_mocks(monkeypatch, cap, observations=observations, stable=False)
    detail = cap._search_list_by_channel(
        FakeSearchPage(click_result={"clicked": False, "scope": "none"}),
        "chan",
        return_detail=True,
    )
    assert detail["search_clicked"] is False
    assert session.detached is True
    assert observations.keepalive is None


def test_request_carries_target_filter_matches_body_and_encoded_url():
    cap = _stub_login_and_import()
    assert cap._request_carries_target_filter(
        LIST_URL, f'{{"channelName":"{SECRET_CHANNEL}"}}', SECRET_CHANNEL
    ) is True
    encoded_url = f"{LIST_URL}?name={quote(SECRET_CHANNEL, safe='')}"
    assert cap._request_carries_target_filter(encoded_url, "", SECRET_CHANNEL) is True
    assert cap._request_carries_target_filter(LIST_URL, "{\"pageNum\":1}", SECRET_CHANNEL) is False
    assert cap._request_carries_target_filter(LIST_URL, SECRET_CHANNEL, "") is False


def test_locate_correct_channel_json_field():
    cap = _stub_login_and_import()
    located = cap._locate_target_filter_field(
        LIST_URL,
        f'{{"channelName":"{SECRET_CHANNEL}","pageNum":1}}',
        SECRET_CHANNEL,
    )
    assert located == {"field": "channelName", "is_channel": True}
    assert SECRET_CHANNEL not in str(located)


def test_locate_unrelated_json_field():
    cap = _stub_login_and_import()
    located = cap._locate_target_filter_field(
        LIST_URL,
        f'{{"appName":"{SECRET_CHANNEL}"}}',
        SECRET_CHANNEL,
    )
    assert located == {"field": "appName", "is_channel": False}


def test_locate_url_query_channel_key():
    cap = _stub_login_and_import()
    located = cap._locate_target_filter_field(
        f"{LIST_URL}?channelName={quote(SECRET_CHANNEL, safe='')}",
        "",
        SECRET_CHANNEL,
    )
    assert located == {"field": "query.channelName", "is_channel": True}


def test_locate_unrelated_url_query_key():
    cap = _stub_login_and_import()
    located = cap._locate_target_filter_field(
        f"{LIST_URL}?name={quote(SECRET_CHANNEL, safe='')}",
        "",
        SECRET_CHANNEL,
    )
    assert located == {"field": "query.name", "is_channel": False}


def test_locate_raw_text_only_fallback():
    cap = _stub_login_and_import()
    located = cap._locate_target_filter_field(
        LIST_URL,
        f"prefix {SECRET_CHANNEL} suffix",
        SECRET_CHANNEL,
    )
    assert located == {"field": "raw_text_only", "is_channel": False}


def test_locate_nested_channel_json_path():
    cap = _stub_login_and_import()
    located = cap._locate_target_filter_field(
        LIST_URL,
        f'{{"filter":{{"channelName":"{SECRET_CHANNEL}"}}}}',
        SECRET_CHANNEL,
    )
    assert located == {"field": "filter.channelName", "is_channel": True}


def test_observer_marks_request_missing_target_filter():
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    session.emit(
        "Network.requestWillBeSent",
        {
            "requestId": "r1",
            "request": {"url": LIST_URL, "postData": '{"pageNum":1,"pageSize":10}'},
        },
    )
    session.emit(
        "Network.responseReceived",
        {"requestId": "r1", "response": {"url": LIST_URL, "status": 200}},
    )
    assert list(observations) == [200]
    assert observations.carried_target_filter is False
    assert SECRET_CHANNEL not in str(vars(observations))
    assert SECRET_CHANNEL not in str(list(observations))


def test_observer_marks_request_carrying_target_filter_from_post_data():
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    session.emit(
        "Network.requestWillBeSent",
        {
            "requestId": "r1",
            "request": {
                "url": LIST_URL,
                "postData": f'{{"channelName":"{SECRET_CHANNEL}"}}',
            },
        },
    )
    session.emit(
        "Network.responseReceived",
        {"requestId": "r1", "response": {"url": LIST_URL, "status": 200}},
    )
    assert observations.carried_target_filter is True
    assert not hasattr(observations, "target_filter")
    assert SECRET_CHANNEL not in str(vars(observations))


def test_observer_ignores_non_list_request_even_if_body_has_channel():
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    session.emit(
        "Network.requestWillBeSent",
        {
            "requestId": "r1",
            "request": {"url": OTHER_URL, "postData": SECRET_CHANNEL},
        },
    )
    session.emit(
        "Network.responseReceived",
        {"requestId": "r1", "response": {"url": OTHER_URL, "status": 200}},
    )
    assert list(observations) == []
    assert observations.carried_target_filter is False


def test_page_event_fallback_reads_post_data_without_keeping_it():
    cap = _stub_login_and_import()
    page = FakePage(cdp_error=RuntimeError("no cdp"))
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    page.handlers["request"](FakeRequest(LIST_URL, SECRET_CHANNEL))
    page.handlers["response"](FakeResponse(LIST_URL, 200))
    assert list(observations) == [200]
    assert observations.carried_target_filter is True
    assert SECRET_CHANNEL not in str(vars(observations))


def _unchanged_table():
    return [
        {"page_number": 1, "table_signature": "A", "row_count": 10},
        {"page_number": 1, "table_signature": "A", "row_count": 10},
    ]


def test_search_case_request_missing_target_filter(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()

    def on_wait():
        observations.append(200)
        observations.carried_target_filter = False

    _install_search_mocks(
        monkeypatch,
        cap,
        observations=observations,
        stable=False,
        on_wait=on_wait,
        pagination=_unchanged_table(),
        rows=[{"channel_name": "other"}],
    )
    detail = cap._search_list_by_channel(
        FakeSearchPage(), "chan", return_detail=True
    )
    assert detail["last_http_status"] == 200
    assert detail["request_carried_target_filter"] is False
    assert detail["table_changed"] is False
    assert detail["reader_sees_target_channel"] is False


def test_search_case_request_has_filter_dom_not_on_target(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()

    def on_wait():
        observations.append(200)
        observations.carried_target_filter = True

    _install_search_mocks(
        monkeypatch,
        cap,
        observations=observations,
        stable=False,
        on_wait=on_wait,
        pagination=_unchanged_table(),
        rows=[{"channel_name": "other"}],
    )
    detail = cap._search_list_by_channel(
        FakeSearchPage(), "chan", return_detail=True
    )
    assert detail["request_carried_target_filter"] is True
    assert detail["table_changed"] is False
    assert detail["reader_sees_target_channel"] is False


def test_search_case_dom_refreshed_with_target_channel(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()

    def on_wait():
        observations.append(200)
        observations.carried_target_filter = True

    _install_search_mocks(
        monkeypatch,
        cap,
        observations=observations,
        stable=True,
        on_wait=on_wait,
        rows=[{"channel_name": "chan"}],
    )
    detail = cap._search_list_by_channel(
        FakeSearchPage(), "chan", return_detail=True
    )
    assert detail["request_carried_target_filter"] is True
    assert detail["table_changed"] is True
    assert detail["reader_sees_target_channel"] is True
    assert detail["filter_stable"] is True


def test_search_reader_single_read_and_zero_save(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()
    _install_search_mocks(
        monkeypatch, cap, observations=observations, stable=True, rows=[{"channel_name": "chan"}]
    )
    saves = {"n": 0}
    monkeypatch.setattr(
        cap,
        "_click_save_button",
        lambda page: saves.__setitem__("n", saves["n"] + 1) or True,
    )
    page = FakeSearchPage()
    detail = cap._search_list_by_channel(page, "chan", return_detail=True)
    assert detail["reader_sees_target_channel"] is True
    assert sum(1 for script in page.scripts if "scope: 'channel-form'" in script) == 1
    assert saves["n"] == 0


def test_search_reader_exception_no_save(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()
    _install_search_mocks(monkeypatch, cap, observations=observations, stable=True, rows=[])

    def boom(page):
        raise RuntimeError("reader boom")

    monkeypatch.setattr(cap, "_read_current_page_app_rows", boom)
    saves = {"n": 0}
    monkeypatch.setattr(
        cap,
        "_click_save_button",
        lambda page: saves.__setitem__("n", saves["n"] + 1) or True,
    )
    detail = cap._search_list_by_channel(FakeSearchPage(), "chan", return_detail=True)
    assert detail["reader_sees_target_channel"] is None
    assert detail["filter_stable"] is True
    assert saves["n"] == 0


def _emit_filtered_list_cycle(session, request_id="r1", post_data="", status=200):
    session.emit(
        "Network.requestWillBeSent",
        {
            "requestId": request_id,
            "request": {"url": LIST_URL, "postData": post_data},
        },
    )
    session.emit(
        "Network.responseReceived",
        {"requestId": request_id, "response": {"url": LIST_URL, "status": status}},
    )
    session.emit("Network.loadingFinished", {"requestId": request_id})


def test_observer_response_missing_target_channel():
    cap = _stub_login_and_import()
    session = FakeCdpSession(response_bodies={"r1": {"body": '{"rows":[]}', "base64Encoded": False}})
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    _emit_filtered_list_cycle(session, post_data=f'{{"channelName":"{SECRET_CHANNEL}"}}')
    assert observations.carried_target_filter is True
    assert observations.response_contains_target_channel is False
    assert SECRET_CHANNEL not in str(vars(observations))
    assert list(observations) == [200]


def test_observer_response_contains_target_channel():
    cap = _stub_login_and_import()
    session = FakeCdpSession(
        response_bodies={"r1": {"body": f'{{"channel":"{SECRET_CHANNEL}"}}', "base64Encoded": False}}
    )
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    _emit_filtered_list_cycle(session, post_data=SECRET_CHANNEL)
    assert observations.response_contains_target_channel is True
    assert not hasattr(observations, "body")
    assert SECRET_CHANNEL not in str(vars(observations))


def test_observer_no_response_body_stays_none():
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    _emit_filtered_list_cycle(session, post_data=SECRET_CHANNEL)
    assert observations.carried_target_filter is True
    assert observations.response_contains_target_channel is None


def test_observer_does_not_read_body_when_request_missing_filter():
    cap = _stub_login_and_import()
    session = FakeCdpSession(
        response_bodies={"r1": {"body": SECRET_CHANNEL, "base64Encoded": False}}
    )
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    _emit_filtered_list_cycle(session, post_data='{"pageNum":1}')
    assert observations.carried_target_filter is False
    assert observations.response_contains_target_channel is None
    assert SECRET_CHANNEL not in str(vars(observations))
    assert SECRET_CHANNEL not in str(getattr(observations, "success_records", []))
    assert observations.carried_target_filter is False


def test_target_filtered_request_cannot_unlock_unfiltered_structure_gate():
    cap = _stub_login_and_import()
    session = FakeCdpSession(response_bodies={
        "r1": {"body": '{"data":{"totalCount":1,"pageCount":1,"list":[{}]}}', "base64Encoded": False}
    })
    observations = cap._attach_list_response_observer(FakePage(session=session))
    _emit_filtered_list_cycle(session, post_data='{"channelName":"secret-filter"}')
    assert observations.request_filter_states["r1"] == "target_filtered"
    assert observations.target_absent_2xx_count == 0
    assert observations.success_records == []


def test_channel_names_empty_is_target_absent_but_nonempty_is_target_filtered():
    cap = _stub_login_and_import()
    assert cap._list_request_filter_state(LIST_URL, '{"channelNames":[]}') == "target_absent"
    assert cap._list_request_filter_state(LIST_URL, '{"channelNames":["secret"]}') == "target_filtered"


def test_channel_names_query_form_null_and_empty_array_classification():
    cap = _stub_login_and_import()
    assert cap._list_request_filter_state(LIST_URL + '?channelNames%5B%5D=', '') == "target_absent"
    assert cap._list_request_filter_state(LIST_URL, 'channelNames=%5B%5D') == "target_absent"
    assert cap._list_request_filter_state(LIST_URL, '{"channelNames":null}') == "target_absent"
    assert cap._list_request_filter_state(LIST_URL, '{"channelNames":[""]}') == "target_filtered"


@pytest.mark.parametrize("post_data", [None])
def test_page_event_write_request_without_body_is_unknown_and_response_is_not_read(post_data):
    cap = _stub_login_and_import()
    page = FakePage(cdp_error=RuntimeError("no cdp"))
    observations = cap._attach_list_response_observer(page)
    request = FakeRequest(LIST_URL, post_data)
    page.handlers["request"](request)

    class Response(FakeResponse):
        def text(self):
            raise AssertionError("unknown request must not read response body")

    page.handlers["response"](Response(LIST_URL, 200, request=request))
    assert observations.request_filter_states[id(request)] == "unknown"
    assert observations.success_records == []
    assert observations.target_absent_2xx_count == 0


def test_page_event_write_request_post_data_getter_error_is_unknown():
    cap = _stub_login_and_import()
    page = FakePage(cdp_error=RuntimeError("no cdp"))
    observations = cap._attach_list_response_observer(page)

    class BrokenRequest:
        url = LIST_URL
        method = "POST"
        @property
        def post_data(self):
            raise RuntimeError("unavailable")

    request = BrokenRequest()
    page.handlers["request"](request)
    page.handlers["response"](FakeResponse(LIST_URL, 200, request=request))
    assert observations.request_filter_states[id(request)] == "unknown"
    assert observations.success_records == []
    assert observations.target_absent_2xx_count == 0


def test_cdp_missing_post_data_is_unknown_and_cannot_unlock_gate():
    cap = _stub_login_and_import()
    session = FakeCdpSession(response_bodies={
        "r1": {"body": '{"data":{"totalCount":1,"pageCount":1,"list":[{}]}}', "base64Encoded": False}
    })
    observations = cap._attach_list_response_observer(FakePage(session=session))
    session.emit("Network.requestWillBeSent", {
        "requestId": "r1", "request": {"url": LIST_URL, "hasPostData": True},
    })
    session.emit("Network.responseReceived", {"requestId": "r1", "response": {"url": LIST_URL, "status": 200}})
    session.emit("Network.loadingFinished", {"requestId": "r1"})
    assert observations.request_filter_states["r1"] == "unknown"
    assert observations.unknown_request_count == 1
    assert observations.target_absent_2xx_count == 0
    assert observations.success_records == []


def test_unknown_request_shape_cannot_unlock_unfiltered_structure_gate():
    cap = _stub_login_and_import()
    session = FakeCdpSession(response_bodies={
        "r1": {"body": '{"data":{"totalCount":1,"pageCount":1,"list":[{}]}}', "base64Encoded": False}
    })
    observations = cap._attach_list_response_observer(FakePage(session=session))
    _emit_filtered_list_cycle(session, post_data='{"unrecognizedFilter":"secret"}')
    assert observations.request_filter_states["r1"] == "unknown"
    assert observations.target_absent_2xx_count == 0
    assert observations.success_records == []


def test_page_event_fallback_response_missing_target():
    cap = _stub_login_and_import()
    page = FakePage(cdp_error=RuntimeError("no cdp"))
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    request = FakeRequest(LIST_URL, '{"channelName":"' + SECRET_CHANNEL + '"}')
    page.handlers["request"](request)
    page.handlers["response"](FakeResponse(LIST_URL, 200, text='{"rows":[]}', request=request))
    assert observations.carried_target_filter is True
    assert observations.response_contains_target_channel is False
    assert SECRET_CHANNEL not in str(vars(observations))


def test_page_event_fallback_detach_unregisters_all_handlers():
    cap = _stub_login_and_import()
    page = FakePage(cdp_error=RuntimeError("no cdp"))
    observations = cap._attach_list_response_observer(page)
    cap._detach_list_response_observer(observations)
    assert page.handlers == {}
    assert observations.page_event_handlers == []
    page.emit("request", FakeRequest(LIST_URL, '{"pageNum":1}'))
    assert list(observations) == []
    assert observations.success_records == []


def test_search_case_response_missing_target(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()

    def on_wait():
        observations.append(200)
        observations.carried_target_filter = True
        observations.response_contains_target_channel = False

    _install_search_mocks(
        monkeypatch,
        cap,
        observations=observations,
        stable=False,
        on_wait=on_wait,
        pagination=_unchanged_table(),
        rows=[{"channel_name": "other"}],
    )
    detail = cap._search_list_by_channel(
        FakeSearchPage(), "chan", return_detail=True
    )
    assert detail["request_carried_target_filter"] is True
    assert detail["response_contains_target_channel"] is False
    assert detail["table_changed"] is False
    assert detail["reader_sees_target_channel"] is False


def test_search_case_response_has_target_dom_not_refreshed(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()

    def on_wait():
        observations.append(200)
        observations.carried_target_filter = True
        observations.response_contains_target_channel = True

    _install_search_mocks(
        monkeypatch,
        cap,
        observations=observations,
        stable=False,
        on_wait=on_wait,
        pagination=_unchanged_table(),
        rows=[{"channel_name": "other"}],
    )
    detail = cap._search_list_by_channel(
        FakeSearchPage(), "chan", return_detail=True
    )
    assert detail["request_carried_target_filter"] is True
    assert detail["response_contains_target_channel"] is True
    assert detail["table_changed"] is False
    assert detail["reader_sees_target_channel"] is False


def test_search_case_no_response_body(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()

    def on_wait():
        observations.append(200)
        observations.carried_target_filter = True

    _install_search_mocks(
        monkeypatch,
        cap,
        observations=observations,
        stable=False,
        on_wait=on_wait,
        pagination=_unchanged_table(),
        rows=[{"channel_name": "other"}],
    )
    detail = cap._search_list_by_channel(
        FakeSearchPage(), "chan", return_detail=True
    )
    assert detail["request_carried_target_filter"] is True
    assert detail["response_contains_target_channel"] is None


def test_observer_records_channel_field_path_without_value():
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    session.emit(
        "Network.requestWillBeSent",
        {
            "requestId": "r1",
            "request": {
                "url": LIST_URL,
                "postData": f'{{"channelName":"{SECRET_CHANNEL}"}}',
            },
        },
    )
    assert observations.carried_target_filter is True
    assert observations.target_filter_field == "channelName"
    assert observations.target_filter_field_is_channel is True
    assert SECRET_CHANNEL not in str(vars(observations))


def test_observer_records_unrelated_field_and_raw_text():
    cap = _stub_login_and_import()
    session = FakeCdpSession()
    page = FakePage(session=session)
    observations = cap._attach_list_response_observer(page, target_filter=SECRET_CHANNEL)
    session.emit(
        "Network.requestWillBeSent",
        {
            "requestId": "r1",
            "request": {
                "url": LIST_URL,
                "postData": f'{{"appName":"{SECRET_CHANNEL}"}}',
            },
        },
    )
    assert observations.target_filter_field == "appName"
    assert observations.target_filter_field_is_channel is False

    raw_session = FakeCdpSession()
    raw_page = FakePage(session=raw_session)
    raw_obs = cap._attach_list_response_observer(raw_page, target_filter=SECRET_CHANNEL)
    raw_session.emit(
        "Network.requestWillBeSent",
        {
            "requestId": "r2",
            "request": {"url": LIST_URL, "postData": f"prefix {SECRET_CHANNEL} suffix"},
        },
    )
    assert raw_obs.target_filter_field == "raw_text_only"
    assert raw_obs.target_filter_field_is_channel is False


def test_search_copies_filter_field_into_detail(monkeypatch):
    cap = _stub_login_and_import()
    observations = cap._ListRequestObserver()

    def on_wait():
        observations.append(200)
        observations.carried_target_filter = True
        observations.target_filter_field = "channelName"
        observations.target_filter_field_is_channel = True
        observations.response_contains_target_channel = False

    _install_search_mocks(
        monkeypatch,
        cap,
        observations=observations,
        stable=False,
        on_wait=on_wait,
        pagination=_unchanged_table(),
        rows=[{"channel_name": "other"}],
    )
    detail = cap._search_list_by_channel(
        FakeSearchPage(), "chan", return_detail=True
    )
    assert detail["target_filter_field"] == "channelName"
    assert detail["target_filter_field_is_channel"] is True
