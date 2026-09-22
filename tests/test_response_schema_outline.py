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
    parent.mkdir(exist_ok=True)
    parent.chmod(0o700)
    return parent


def _enable(monkeypatch, cap, tmp_path, name="run"):
    root = _private_parent(tmp_path, "private-tmp")
    monkeypatch.setattr(cap, "_PRIVATE_OUTLINE_TMP_ROOT", root)
    monkeypatch.setattr(cap.ex, "ENVIRONMENT", "TEST")
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_SWITCH, "true")
    target = root / name / "outline.json"
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_PATH, str(target))
    return target


def test_private_outline_is_disabled_without_all_three_gates(monkeypatch, tmp_path):
    cap = _cap()
    monkeypatch.delenv(cap._PRIVATE_OUTLINE_SWITCH, raising=False)
    monkeypatch.delenv(cap._PRIVATE_OUTLINE_PATH, raising=False)
    monkeypatch.setattr(cap.ex, "ENVIRONMENT", "TEST")
    assert cap._private_outline_target() is None

    _enable(monkeypatch, cap, tmp_path)
    monkeypatch.setattr(cap.ex, "ENVIRONMENT", "FAKE")
    assert cap._private_outline_target() is None

    monkeypatch.setattr(cap.ex, "ENVIRONMENT", "TEST")
    monkeypatch.delenv(cap._PRIVATE_OUTLINE_PATH)
    assert cap._private_outline_target() is None


def test_private_outline_rejects_relative_existing_broad_and_repo(monkeypatch, tmp_path):
    cap = _cap()
    _enable(monkeypatch, cap, tmp_path)
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_PATH, "relative.json")
    assert cap._private_outline_target() is None

    root = cap._PRIVATE_OUTLINE_TMP_ROOT
    existing_parent = root / "existing-run"
    existing_parent.mkdir()
    existing = existing_parent / "outline.json"
    existing.write_text("x", encoding="utf-8")
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_PATH, str(existing))
    assert cap._private_outline_target() is None

    broad = root / "broad-run"
    broad.mkdir()
    broad.chmod(0o755)
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_PATH, str(broad / "outline.json"))
    assert cap._private_outline_target() is None

    monkeypatch.setattr(cap, "PROJECT_ROOT", root)
    repo_target = root / "repo-run" / "outline.json"
    monkeypatch.setenv(cap._PRIVATE_OUTLINE_PATH, str(repo_target))
    assert cap._private_outline_target() is None
    assert not repo_target.parent.exists()


def test_private_outline_rejects_symlink_and_target_competition(monkeypatch, tmp_path):
    cap = _cap()
    target = _enable(monkeypatch, cap, tmp_path, "linked-run")
    target.parent.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    assert cap._private_outline_target() is None

    target = _enable(monkeypatch, cap, tmp_path, "race-run")
    target.parent.mkdir()
    target.write_text("competitor", encoding="utf-8")
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


def test_outline_counts_unsafe_keys_without_recording_them_and_caps_work():
    cap = _cap()
    outline = cap._response_schema_outline_v1({"safe": {"also_safe": None, "bad-key": True}, "bad key": 1}, "page_event")
    encoded = json.dumps(outline, sort_keys=True)
    assert outline["unsafe_key_count"] == 2
    assert outline["unsafe_key_count_capped"] is False
    assert "bad-key" not in encoded and "bad key" not in encoded

    capped = cap._response_schema_outline_v1({"data": {f"bad-{index}": None for index in range(200)}}, "cdp")
    assert capped["outline_status"] == "truncated"
    assert capped["unsafe_key_count"] == cap._PRIVATE_OUTLINE_MAX_UNSAFE_KEYS
    assert capped["unsafe_key_count_capped"] is True


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
    cdp_target = _enable(monkeypatch, cap, tmp_path, "cdp-run")
    session = FakeCdpSession(response_bodies={"r1": {"body": '{"data":{"items":[]}}', "base64Encoded": False}})
    batch = cap._PrivateOutlineBatch(cap._safe_private_outline_target())
    observations = cap._attach_list_response_observer(FakePage(session=session), private_outline_batch=batch)
    _emit_filtered_list_cycle(session, post_data='{"pageNum":1}')
    assert observations.structure_diagnostic_counts["required_structure_missing"] == 1
    assert observations.success_records == []
    cap._detach_list_response_observer(observations)
    assert batch.write_once() is True
    assert json.loads(cdp_target.read_text(encoding="utf-8"))["outlines"][0]["observer_source"] == "cdp"

    page_target = _enable(monkeypatch, cap, tmp_path, "page-run")
    page = FakePage(cdp_error=RuntimeError("offline fallback"))
    batch = cap._PrivateOutlineBatch(cap._safe_private_outline_target())
    observations = cap._attach_list_response_observer(page, private_outline_batch=batch)
    request = FakeRequest(LIST_URL, '{"pageNum":1}')
    page.handlers["request"](request)
    page.handlers["response"](FakeResponse(LIST_URL, 200, '{"data":{"items":[]}}', request=request))
    assert observations.structure_diagnostic_counts["required_structure_missing"] == 1
    assert observations.success_records == []
    cap._detach_list_response_observer(observations)
    assert batch.write_once() is True
    assert json.loads(page_target.read_text(encoding="utf-8"))["outlines"][0]["observer_source"] == "page_event"


def test_only_required_structure_missing_with_valid_source_can_generate_outline(tmp_path):
    cap = _cap()
    target = _private_parent(tmp_path) / "outline.json"
    batch = cap._PrivateOutlineBatch(target)
    observations = cap._ListRequestObserver(private_outline_batch=batch)
    cases = [
        (None, False, True, "cdp"),
        ("%%%", True, False, "cdp"),
        ("not-json", False, False, "cdp"),
        ("[]", False, False, "cdp"),
        ('{"data":{"list":[],"total":1,"pageCount":1}}', False, False, "cdp"),
        ('{"data":{}}', False, False, "other"),
    ]
    for body, encoded, read_error, source in cases:
        cap._record_target_absent_structure(
            observations, body, base64_encoded=encoded, read_error=read_error, observer_source=source
        )
    assert batch.records == []
    assert batch.write_once() is False
    assert not target.exists()


def test_target_filtered_and_non_2xx_do_not_generate_outline(tmp_path):
    from tests.test_channel_filter_observer import FakeCdpSession, FakePage, _emit_filtered_list_cycle

    cap = _cap()
    batch = cap._PrivateOutlineBatch(_private_parent(tmp_path) / "outline.json")
    session = FakeCdpSession(response_bodies={"r1": {"body": '{"data":{}}', "base64Encoded": False}})
    observations = cap._attach_list_response_observer(FakePage(session=session), private_outline_batch=batch)
    _emit_filtered_list_cycle(session, post_data='{"appStatus":"nonempty"}')
    assert batch.records == []

    session = FakeCdpSession(response_bodies={"r1": {"body": '{"data":{}}', "base64Encoded": False}})
    observations = cap._attach_list_response_observer(FakePage(session=session), private_outline_batch=batch)
    session.emit("Network.requestWillBeSent", {"requestId": "r1", "request": {"url": "https://example.invalid/backend/cloudTrial/appInfo/getAppInfoList", "postData": '{"pageNum":1}', "method": "POST"}})
    session.emit("Network.responseReceived", {"requestId": "r1", "response": {"url": "https://example.invalid/backend/cloudTrial/appInfo/getAppInfoList", "status": 500}})
    session.emit("Network.loadingFinished", {"requestId": "r1"})
    assert batch.records == []


def test_large_body_is_not_json_loaded_for_outline_and_is_bounded(monkeypatch, tmp_path):
    cap = _cap()
    target = _private_parent(tmp_path) / "outline.json"
    batch = cap._PrivateOutlineBatch(target)
    observations = cap._ListRequestObserver(private_outline_batch=batch)
    body = json.dumps({"data": {}, "padding": "x" * (cap._PRIVATE_OUTLINE_MAX_BODY_BYTES + 1)})
    cap._record_target_absent_structure(observations, body, observer_source="cdp")
    assert observations.structure_diagnostic_counts["required_structure_missing"] == 1
    assert batch.records == [{
        "schema_version": "response_schema_outline_v1", "observer_source": "cdp",
        "outline_status": "too_large_not_outlined", "body_size_capped": True,
    }]

    complete_body = json.dumps({
        "data": {"totalCount": 1, "pageCount": 1, "list": []},
        "padding": "x" * (cap._PRIVATE_OUTLINE_MAX_BODY_BYTES + 1),
    })
    bucket, meta = cap._classify_list_structure_body(complete_body)
    assert bucket == "complete" and meta is not None


def test_huge_child_uses_bounded_unsafe_scan_budget():
    cap = _cap()

    class CountingDict(dict):
        scans = 0
        def __iter__(self):
            for key in super().__iter__():
                type(self).scans += 1
                yield key

    child = CountingDict({f"bad-{index}": None for index in range(10000)})
    outline = cap._response_schema_outline_v1({"data": child}, "cdp")
    assert outline["outline_status"] == "truncated"
    assert child.scans <= cap._PRIVATE_OUTLINE_MAX_UNSAFE_SCAN_KEYS


def test_locator_gate_attempts_share_one_batch_and_flush_once(monkeypatch, tmp_path):
    cap = _cap()
    target = _enable(monkeypatch, cap, tmp_path, "shared-run")
    batches = []
    writes = []

    def attach(page, private_outline_batch=None):
        batches.append(private_outline_batch)
        observations = cap._ListRequestObserver(private_outline_batch=private_outline_batch)
        cap._record_target_absent_structure(observations, '{"data":{}}', observer_source="cdp")
        return observations

    original_write = cap._PrivateOutlineBatch.write_once
    monkeypatch.setattr(cap, "_attach_list_response_observer", attach)
    monkeypatch.setattr(cap, "_read_list_restore_state", lambda page: {})
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_wait_for_unfiltered_list_restore", lambda *args, **kwargs: False)
    monkeypatch.setattr(cap._PrivateOutlineBatch, "write_once", lambda batch: writes.append(batch) or original_write(batch))

    class Page:
        def wait_for_timeout(self, milliseconds):
            return None

    result = cap._locate_known_main_row_for_resource_fallback(Page(), "id", "name", "channel")
    assert result["success"] is False
    assert len(batches) == cap._POST_SAVE_KNOWN_ID_POLL_ATTEMPTS
    assert len({id(batch) for batch in batches}) == 1
    assert len(writes) == 1
    assert len(json.loads(target.read_text(encoding="utf-8"))["outlines"]) == cap._POST_SAVE_KNOWN_ID_POLL_ATTEMPTS


def test_private_write_rejects_non_owner_directory(monkeypatch, tmp_path):
    cap = _cap()
    parent = _private_parent(tmp_path)
    target = parent / "outline.json"
    batch = cap._PrivateOutlineBatch(target)
    batch.add(cap._response_schema_outline_v1({"data": {}}, "cdp"))
    actual_lstat = cap.os.lstat

    def different_owner(path):
        info = actual_lstat(path)
        if Path(path) == parent:
            return type("Info", (), {"st_mode": info.st_mode, "st_uid": info.st_uid + 1})()
        return info

    monkeypatch.setattr(cap.os, "lstat", different_owner)
    assert batch.write_once() is False
    assert not target.exists()


def test_non_0600_installed_target_is_removed(monkeypatch, tmp_path):
    cap = _cap()
    parent = _private_parent(tmp_path)
    target = parent / "outline.json"
    batch = cap._PrivateOutlineBatch(target)
    batch.add(cap._response_schema_outline_v1({"data": {}}, "cdp"))
    monkeypatch.setattr(cap.os, "fchmod", lambda fd, mode: None)
    old_umask = cap.os.umask(0o477)
    try:
        assert batch.write_once() is False
    finally:
        cap.os.umask(old_umask)
    assert not target.exists()
    assert list(parent.iterdir()) == []


def test_keyboard_interrupt_cleans_temporary_and_propagates(monkeypatch, tmp_path):
    cap = _cap()
    parent = _private_parent(tmp_path)
    batch = cap._PrivateOutlineBatch(parent / "outline.json")
    batch.add(cap._response_schema_outline_v1({"data": {}}, "cdp"))
    monkeypatch.setattr(cap.os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        batch.write_once()
    assert list(parent.iterdir()) == []


def test_private_write_failure_leaves_no_temporary_file(monkeypatch, tmp_path):
    cap = _cap()
    parent = _private_parent(tmp_path)
    target = parent / "outline.json"
    batch = cap._PrivateOutlineBatch(target)
    batch.add(cap._response_schema_outline_v1({"data": {}}, "cdp"))
    monkeypatch.setattr(cap.os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write failure")))
    assert batch.write_once() is False
    assert list(parent.iterdir()) == []
