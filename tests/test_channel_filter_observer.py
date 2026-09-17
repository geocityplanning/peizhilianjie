# -*- coding: utf-8 -*-
"""Offline behavior tests for the list-response observer and channel search.

These tests never connect to UAT, Chrome, or CDP.
"""
from __future__ import annotations

import sys
import types

LIST_URL = "https://example.invalid/backend/cloudTrial/appInfo/getAppInfoList"
OTHER_URL = "https://example.invalid/static/app.js"


def _stub_login_and_import():
    if "actions.ensure_login" not in sys.modules:
        stub = types.ModuleType("actions.ensure_login")
        stub.ensure_login = lambda page=None: {"success": False}
        stub.is_logged_in = lambda page: True
        sys.modules["actions.ensure_login"] = stub
    from app.executor.actions import create_app_v2 as cap

    return cap


class FakeCdpSession:
    def __init__(self):
        self.enabled = False
        self.handlers = {}
        self.detached = False
        self.sends = []

    def send(self, method, params=None):
        self.sends.append((method, params))
        if method == "Network.enable":
            self.enabled = True

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


class FakeResponse:
    def __init__(self, url, status):
        self.url = url
        self.status = status


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
):
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_dismiss_stray_dropdowns", lambda page: None)
    states = [
        {"page_number": 1, "table_signature": "A", "row_count": 5},
        {"page_number": 1, "table_signature": "B", "row_count": 2},
    ]
    reads = {"n": 0}

    def read_state(page):
        idx = min(reads["n"], len(states) - 1)
        reads["n"] += 1
        return states[idx]

    monkeypatch.setattr(cap, "_read_pagination_state", read_state)
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda page: observations)

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
