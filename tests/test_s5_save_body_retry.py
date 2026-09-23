# -*- coding: utf-8 -*-
"""Offline bounded, post-loadingFinished save-body evidence state machine tests."""
from __future__ import annotations

import sys
import types


def _cap():
    if "actions.ensure_login" not in sys.modules:
        stub = types.ModuleType("actions.ensure_login")
        stub.ensure_login = lambda page=None: {"success": False}
        stub.is_logged_in = lambda page: True
        sys.modules["actions.ensure_login"] = stub
    from app.executor.actions import create_app_v2 as cap
    return cap


class Session:
    def __init__(self, body_results):
        self.handlers = {}
        self.body_results = list(body_results)
        self.body_calls = 0
        self.detached = False

    def send(self, method, params=None):
        if method == "Network.enable":
            return {}
        if method == "Network.getResponseBody":
            self.body_calls += 1
            result = self.body_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        raise AssertionError(method)

    def on(self, event, handler):
        self.handlers[event] = handler

    def detach(self):
        self.detached = True


class Context:
    def __init__(self, session):
        self.session = session

    def new_cdp_session(self, page):
        return self.session


class Page:
    def __init__(self, session, clock):
        self.context = Context(session)
        self.handlers = {}
        self.clock = clock
        self.save_clicks = 1

    def on(self, event, handler):
        self.handlers[event] = handler

    def off(self, event, handler):
        if self.handlers.get(event) is handler:
            del self.handlers[event]

    def wait_for_timeout(self, milliseconds):
        self.clock[0] += milliseconds / 1000


class Request:
    url = "https://uat-cloud.139.com/backend/save?secret=query"
    method = "POST"


class Response:
    status = 200

    def __init__(self, request, *, text_results, finished_results=()):
        self.request = request
        self.text_results = list(text_results)
        self.finished_results = list(finished_results)
        self.finished_calls = 0
        self.text_calls = 0

    def finished(self):
        self.finished_calls += 1
        if self.finished_results:
            result = self.finished_results.pop(0)
            if isinstance(result, BaseException):
                raise result
        return None

    def text(self):
        self.text_calls += 1
        result = self.text_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _setup(monkeypatch, body_results):
    cap = _cap()
    clock = [0.0]
    monkeypatch.setattr(cap.time, "monotonic", lambda: clock[0])
    session = Session(body_results)
    page = Page(session, clock)
    observer = cap._attach_save_click_observer(page)
    request = Request()
    session.handlers["Network.requestWillBeSent"]({"requestId": "r1", "request": {"url": request.url, "method": request.method}})
    session.handlers["Network.responseReceived"]({"requestId": "r1", "response": {"status": 200}})
    session.handlers["Network.loadingFinished"]({"requestId": "r1"})
    return cap, clock, session, page, observer, request


def _advance(page):
    page.wait_for_timeout(150)


def test_loading_finished_only_registers_then_missing_body_retries_same_request(monkeypatch):
    cap, _clock, session, page, observer, _request = _setup(monkeypatch, [
        {}, {"body": '{"header":{"status":"200"}}'},
    ])
    assert session.body_calls == 0
    assert len(observer.candidates) == 1

    assert cap._save_click_observation(observer)["outcome"] == "business_unreadable"
    _advance(page)
    assert cap._save_click_observation(observer)["outcome"] == "success"
    assert session.body_calls == 2
    assert len(observer.candidates) == 1 and page.save_clicks == 1


def test_read_error_then_body_success_uses_same_shared_evidence_budget(monkeypatch):
    cap, _clock, session, page, observer, _request = _setup(monkeypatch, [
        RuntimeError("transient"), {"body": '{"header":{"status":"200"}}'},
    ])
    assert cap._save_click_observation(observer)["outcome"] == "business_unreadable"
    _advance(page)
    assert cap._save_click_observation(observer)["outcome"] == "success"
    assert session.body_calls == 2
    assert observer.candidates["r1"]["evidence_attempts"] == 2


def test_late_page_response_retries_finished_then_text_without_permanent_first_error(monkeypatch):
    cap, _clock, session, page, observer, request = _setup(monkeypatch, [{}, {}, {}])
    assert cap._save_click_observation(observer)["outcome"] == "business_unreadable"
    page.handlers["request"](request)
    response = Response(request, text_results=[RuntimeError("transient"), '{"header":{"status":"200"}}'])
    page.handlers["response"](response)

    _advance(page)
    assert cap._save_click_observation(observer)["outcome"] == "business_unreadable"
    assert observer.candidates["r1"].get("page_fallback_attempted") is not True
    _advance(page)
    assert cap._save_click_observation(observer)["outcome"] == "success"
    assert response.finished_calls == 2 and response.text_calls == 2
    assert session.body_calls == 3


def test_page_finished_transient_error_does_not_permanently_close_same_response(monkeypatch):
    cap, _clock, _session, page, observer, request = _setup(monkeypatch, [{}, {}, {}])
    page.handlers["request"](request)
    response = Response(
        request,
        finished_results=[RuntimeError("transient"), None],
        text_results=['{"header":{"status":"200"}}'],
    )
    page.handlers["response"](response)

    assert cap._save_click_observation(observer)["outcome"] == "business_unreadable"
    assert observer.candidates["r1"].get("page_fallback_attempted") is not True
    _advance(page)
    assert cap._save_click_observation(observer)["outcome"] == "success"
    assert response.finished_calls == 2 and response.text_calls == 1


def test_shared_budget_exhaustion_stays_unreadable_unknown_query_and_detaches(monkeypatch):
    cap, _clock, session, page, observer, _request = _setup(monkeypatch, [{}, {}, {}])
    for _ in range(3):
        assert cap._save_click_observation(observer)["outcome"] == "business_unreadable"
        _advance(page)
    candidate = observer.candidates["r1"]
    assert candidate["evidence_attempts"] == cap._SAVE_EVIDENCE_MAX_ATTEMPTS
    assert candidate["evidence_terminal"] is True
    decision = cap._save_click_observation(observer)
    assert decision["outcome"] == "business_unreadable"
    assert decision["business"]["outcome"] == "unreadable"
    assert session.body_calls == 3 and len(observer.candidates) == 1 and page.save_clicks == 1
    assert session.detached is False and set(page.handlers) == {"request", "response"}
    cap._detach_save_click_observer(observer)
    assert session.detached is True and page.handlers == {}


def test_save_evidence_state_and_decision_do_not_expose_private_values(monkeypatch):
    cap, _clock, session, _page, observer, request = _setup(monkeypatch, [{}])
    decision = cap._save_click_observation(observer)
    projected = cap._save_click_diagnostic(decision)
    assert request.url not in str(projected)
    assert "secret=query" not in str(projected)
    assert "r1" not in str(projected)
    assert '"body":' not in str(projected)
    assert session.body_calls == 1
