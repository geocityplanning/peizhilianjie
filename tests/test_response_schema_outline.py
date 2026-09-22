# -*- coding: utf-8 -*-
"""Offline tests for TEST-only private response schema outlines."""
from __future__ import annotations

import json
import stat
import sys
import types
from pathlib import Path

import pytest


def _cap():
    if "actions.ensure_login" not in sys.modules:
        stub = types.ModuleType("actions.ensure_login")
        stub.ensure_login = lambda page=None: {"success": False}
        stub.is_logged_in = lambda page: True
        sys.modules["actions.ensure_login"] = stub
    from app.executor.actions import create_app_v2 as cap
    return cap


def _private_parent(tmp_path, name="private"):
    parent = tmp_path / name
    parent.mkdir()
    parent.chmod(0o700)
    return parent


def _enable(monkeypatch, cap, target):
    monkeypatch.setattr(cap.ex, "ENVIRONMENT", "TEST")
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_SWITCH, "true")
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_PATH, str(target))


def test_private_outline_is_disabled_without_all_three_gates(monkeypatch, tmp_path):
    cap = _cap()
    target = _private_parent(tmp_path) / "outline.json"
    monkeypatch.delenv(cap._PRIVATE_OUTLINE_SWITCH, raising=False)
    monkeypatch.delenv(cap._PRIVATE_OUTLINE_PATH, raising=False)
    monkeypatch.setattr(cap.ex, "ENVIRONMENT", "TEST")
    assert cap._private_outline_target() is None

    _enable(monkeypatch, cap, target)
    monkeypatch.setattr(cap.ex, "ENVIRONMENT", "FAKE")
    assert cap._private_outline_target() is None

    monkeypatch.setattr(cap.ex, "ENVIRONMENT", "TEST")
    monkeypatch.delenv(cap._PRIVATE_OUTLINE_PATH)
    assert cap._private_outline_target() is None


def test_private_outline_rejects_relative_existing_and_broad_parent(monkeypatch, tmp_path):
    cap = _cap()
    private = _private_parent(tmp_path)
    _enable(monkeypatch, cap, private / "outline.json")
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_PATH, "relative.json")
    assert cap._private_outline_target() is None

    existing = private / "existing.json"
    existing.write_text("x", encoding="utf-8")
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_PATH, str(existing))
    assert cap._private_outline_target() is None

    broad = tmp_path / "broad"
    broad.mkdir()
    broad.chmod(0o755)
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_PATH, str(broad / "outline.json"))
    assert cap._private_outline_target() is None

    monkeypatch.setattr(cap, "PROJECT_ROOT", private)
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_PATH, str(private / "repo-outline.json"))
    assert cap._private_outline_target() is None


def test_outline_is_stable_value_free_and_does_not_descend_arrays():
    cap = _cap()
    payload = {
        "z_root": ["private-array-item"],
        "data": {"z_value": "private-string", "a_number": 91, "nested": {"leak": "never"}},
        "a_root": False,
    }
    outline = cap._response_schema_outline_v1(payload, "cdp")
    encoded = json.dumps(outline, sort_keys=True)
    assert outline["root"] == [
        {"key": "a_root", "type": "boolean"},
        {"key": "data", "type": "object"},
        {"key": "z_root", "type": "array"},
    ]
    assert outline["object_children"] == [{
        "path": ["data"],
        "keys": [
            {"key": "a_number", "type": "number"},
            {"key": "nested", "type": "object"},
            {"key": "z_value", "type": "string"},
        ],
    }]
    for value in ("private-array-item", "private-string", "never", "91", "False"):
        assert value not in encoded


def test_outline_sorts_multiple_object_children_and_rejects_unknown_source():
    cap = _cap()
    outline = cap._response_schema_outline_v1({"z_child": {}, "a_child": {}}, "page_event")
    assert outline["object_children"] == [
        {"path": ["a_child"], "keys": []},
        {"path": ["z_child"], "keys": []},
    ]
    assert cap._response_schema_outline_v1({}, "other") is None
    assert cap._response_schema_outline_v1([], "cdp") is None


def test_outline_counts_unsafe_keys_without_recording_them():
    cap = _cap()
    outline = cap._response_schema_outline_v1({"safe": {"also_safe": None, "bad-key": True}, "bad key": 1}, "page_event")
    encoded = json.dumps(outline, sort_keys=True)
    assert outline["unsafe_key_count"] == 2
    assert "bad-key" not in encoded and "bad key" not in encoded
    assert outline["observer_source"] == "page_event"


@pytest.mark.parametrize("payload", [
    {f"root_{index}": None for index in range(33)},
    {f"child_{index}": {} for index in range(9)},
    {"data": {f"entry_{index}": None for index in range(65)}},
    {"data": {f"entry_{index}": None for index in range(128)}},
])
def test_outline_limits_are_truncated_without_partial_keys(payload):
    cap = _cap()
    outline = cap._response_schema_outline_v1(payload, "cdp")
    assert outline["outline_status"] == "truncated"
    assert outline["limit_exceeded"] is True
    assert "root" not in outline and "object_children" not in outline


def test_private_batch_caps_records_and_creates_0600_new_file(tmp_path):
    cap = _cap()
    target = _private_parent(tmp_path) / "outline.json"
    batch = cap._PrivateOutlineBatch(target)
    record = cap._response_schema_outline_v1({"data": {}}, "cdp")
    for _ in range(4):
        batch.add(record)
    assert batch.write_once() is True
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    written = json.loads(target.read_text(encoding="utf-8"))
    assert len(written["outlines"]) == 3
    assert written["outline_overflow_count"] == 1
    assert batch.write_once() is False


def test_cdp_and_page_event_share_outline_and_do_not_change_structure_gate(monkeypatch, tmp_path):
    from tests.test_channel_filter_observer import FakeCdpSession, FakePage, FakeRequest, FakeResponse, LIST_URL, _emit_filtered_list_cycle

    cap = _cap()
    cdp_target = _private_parent(tmp_path) / "cdp.json"
    _enable(monkeypatch, cap, cdp_target)
    session = FakeCdpSession(response_bodies={"r1": {"body": '{"data":{"items":[]}}', "base64Encoded": False}})
    observations = cap._attach_list_response_observer(FakePage(session=session))
    _emit_filtered_list_cycle(session, post_data='{"pageNum":1}')
    assert observations.structure_diagnostic_counts["required_structure_missing"] == 1
    assert observations.success_records == []
    cap._detach_list_response_observer(observations)
    assert json.loads(cdp_target.read_text(encoding="utf-8"))["outlines"][0]["observer_source"] == "cdp"

    page_target = _private_parent(tmp_path, "private-page") / "page.json"
    _enable(monkeypatch, cap, page_target)
    page = FakePage(cdp_error=RuntimeError("offline fallback"))
    observations = cap._attach_list_response_observer(page)
    request = FakeRequest(LIST_URL, '{"pageNum":1}')
    page.handlers["request"](request)
    page.handlers["response"](FakeResponse(LIST_URL, 200, '{"data":{"items":[]}}', request=request))
    assert observations.structure_diagnostic_counts["required_structure_missing"] == 1
    assert observations.success_records == []
    cap._detach_list_response_observer(observations)
    assert json.loads(page_target.read_text(encoding="utf-8"))["outlines"][0]["observer_source"] == "page_event"


def test_private_write_failure_cannot_change_structure_result(monkeypatch, tmp_path):
    cap = _cap()
    target = _private_parent(tmp_path) / "outline.json"
    batch = cap._PrivateOutlineBatch(target)
    batch.add(cap._response_schema_outline_v1({"data": {}}, "cdp"))
    monkeypatch.setattr(cap.os, "link", lambda source, destination: (_ for _ in ()).throw(OSError("write failure")))
    assert batch.write_once() is False
    assert not target.exists()
