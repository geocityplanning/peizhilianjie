# -*- coding: utf-8 -*-
"""Offline, value-free diagnostics for an enable action with no CDP candidate."""
from __future__ import annotations

import sys
import types
from pathlib import Path


def _cap():
    if "actions.ensure_login" not in sys.modules:
        stub = types.ModuleType("actions.ensure_login")
        stub.ensure_login = lambda page=None: {"success": False}
        stub.is_logged_in = lambda page: True
        sys.modules["actions.ensure_login"] = stub
    from app.executor.actions import create_app_v2 as cap
    return cap


class _Session:
    def __init__(self):
        self.handlers = {}
        self.detached = False

    def send(self, method, params=None):
        return {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def detach(self):
        self.detached = True


class _Context:
    def __init__(self, session):
        self.session = session

    def new_cdp_session(self, page):
        return self.session


class _Page:
    def __init__(self, session):
        self.context = _Context(session)
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def off(self, event, handler):
        if self.handlers.get(event) is handler:
            del self.handlers[event]


class _Request:
    def __init__(self, url, method):
        self.url = url
        self.method = method


def _observer():
    cap = _cap()
    session = _Session()
    page = _Page(session)
    return cap, session, page, cap._attach_save_click_observer(page)


def _cdp(session, *, url, method, request_id="r1"):
    payload = {"request": {"url": url, "method": method}}
    if request_id is not None:
        payload["requestId"] = request_id
    session.handlers["Network.requestWillBeSent"](payload)


def test_cdp_request_buckets_are_separate_bounded_and_fail_closed():
    cap, session, page, observer = _observer()
    same = "https://uat-cloud.139.com/backend/cloudTrial/appInfo/update"
    _cdp(session, url=same, method="GET", request_id="get")
    _cdp(session, url="https://uat-cloud.139.com/backend/cloudTrial/appInfo/getAppInfoList", method="POST", request_id="list")
    _cdp(session, url="https://other.invalid/backend/cloudTrial/appInfo/update", method="POST", request_id="cross")
    _cdp(session, url=same, method="POST", request_id=None)
    _cdp(session, url=same, method="POST", request_id="accepted")

    facts = observer.cdp_diagnostics.projection()
    assert facts["accepted_write"] == 1
    assert facts["method_rejected"] == 1
    assert facts["list_rejected"] == 1
    assert facts["origin_rejected"] == 1
    assert facts["missing_request_id"] == 1
    assert set(facts["methods"]) == {"GET", "POST"}
    assert len(facts["path_hashes"]) <= 3
    assert "accepted" in observer.candidates
    assert len(observer.candidates) == 1
    assert all(len(value) == 16 for value in facts["path_hashes"])
    assert same not in str(facts) and "other.invalid" not in str(facts)
    directional = cap._enable_action_observation_diagnostic(observer, {"outcome": "no_candidate", "candidate_count": 0})
    assert directional["request_observer"]["cdp"]["observer_event_unseen"] == 0
    assert directional["request_observer"]["page_event"]["observer_event_unseen"] == 1

    cap._detach_save_click_observer(observer)
    _cdp(session, url=same, method="POST", request_id="late")
    assert observer.cdp_diagnostics.projection()["accepted_write"] == 1
    assert page.handlers == {} and session.detached is True


def test_page_and_cdp_visibility_are_projected_independently_without_raw_values():
    cap, session, page, observer = _observer()
    request = _Request("https://uat-cloud.139.com/backend/cloudTrial/appInfo/update?secret=query", "POST")
    page.handlers["request"](request)

    diagnostic = cap._enable_action_observation_diagnostic(observer, {"outcome": "no_candidate", "candidate_count": 0})
    cdp = diagnostic["request_observer"]["cdp"]
    page_event = diagnostic["request_observer"]["page_event"]
    assert cdp["accepted_write"] == 0 and cdp["observer_event_unseen"] == 1
    assert page_event["accepted_write"] == 1 and page_event["observer_event_unseen"] == 0
    assert request.url not in str(diagnostic) and "secret=query" not in str(diagnostic)

    cap._detach_save_click_observer(observer)


def test_both_unseen_is_explicitly_zero_not_a_synthetic_candidate():
    cap, _session, _page, observer = _observer()

    diagnostic = cap._enable_action_observation_diagnostic(observer, {"outcome": "no_candidate", "candidate_count": 0})

    assert diagnostic["request_observer"]["cdp"]["accepted_write"] == 0
    assert diagnostic["request_observer"]["page_event"]["accepted_write"] == 0
    assert diagnostic["request_observer"]["cdp"]["observer_event_unseen"] == 0
    assert diagnostic["request_observer"]["page_event"]["observer_event_unseen"] == 0
    assert diagnostic["observation"]["outcome"] == "no_candidate"


def test_contract_limits_enable_no_candidate_diagnostic_to_value_free_fields():
    contract = (Path(__file__).parents[1] / "contracts" / "hermes-http-v1.md").read_text(encoding="utf-8")
    for key in ("accepted_write", "method_rejected", "list_rejected", "origin_rejected", "missing_request_id", "observer_event_unseen"):
        assert key in contract
    assert "最多三个 16 位 path 哈希" in contract


def test_enable_diagnostic_logs_confirmation_without_old_switch_dom_state(capsys):
    cap, _session, _page, observer = _observer()
    observer.confirmation_trace = "unique_clicked_closed"
    observer.confirmation_clicked = True
    observer.confirmation_closed = True

    cap._log_enable_click_observation(observer, {"outcome": "no_candidate", "candidate_count": 0})

    output = capsys.readouterr().out
    assert '"outcome":"no_candidate"' in output
    assert '"trace":"unique_clicked_closed"' in output
    assert "post_action_switch_state" not in output
    assert "http://" not in output and "https://" not in output
    assert "query" not in output and "body" not in output and "header" not in output
