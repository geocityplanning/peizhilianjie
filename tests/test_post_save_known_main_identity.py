# -*- coding: utf-8 -*-
"""Offline fail-closed coverage for the post-save known-main-ID fallback path."""
from __future__ import annotations

import ast
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


def _col(text):
    return {"text": text, "classes": ""}


def _payload(*rows, page=1, headers=("ID", "应用名称", "所属渠道")):
    return {
        "main_table_count": 1,
        "page_number": page,
        "headers": [_col(header) for header in headers],
        "rows": [
            {
                "row_idx": index,
                "row_key": row[3] if len(row) > 3 else "",
                "cells": [_col(value) for value in row[:3]],
                "detail_id": row[4] if len(row) > 4 else "",
            }
            for index, row in enumerate(rows)
        ],
    }


class SequencePage:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.waits = []

    def evaluate(self, script, data=None):
        assert self.payloads, "unexpected extra DOM read"
        return self.payloads.pop(0)

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


def _install_navigation(monkeypatch, cap, *, next_results=()):
    resets = []
    next_results = iter(next_results)
    monkeypatch.setattr(cap, "_read_list_restore_state", lambda page: {"table_signature": "filtered", "row_count": 1})
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda page: cap._ListRequestObserver())
    monkeypatch.setattr(cap, "_wait_for_unfiltered_list_restore", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda observations: None)
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: resets.append(True))
    monkeypatch.setattr(cap, "_trigger_unfiltered_list_refresh", lambda page: False)
    monkeypatch.setattr(cap, "_go_to_first_page", lambda page: True)
    monkeypatch.setattr(cap, "_click_next_page_and_wait", lambda page: next(next_results, False))
    return resets


def test_known_locator_zero_candidate_is_bounded_read_only_polling(monkeypatch):
    cap = _cap()
    page = SequencePage([_payload(), _payload(), _payload()])
    resets = _install_navigation(monkeypatch, cap)

    result = cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")

    assert result["success"] is False
    assert result["reason"] == "zero_candidates_after_poll"
    assert result["candidate_category"] == "0"
    assert result["id_field_source"] == "main"
    assert len(resets) == cap._POST_SAVE_KNOWN_ID_POLL_ATTEMPTS
    assert page.waits == [cap._POST_SAVE_KNOWN_ID_POLL_MS] * (cap._POST_SAVE_KNOWN_ID_POLL_ATTEMPTS - 1)


def test_known_locator_logs_gate_passed_id_not_found(monkeypatch, capsys):
    cap = _cap()
    page = SequencePage([_payload(), _payload(), _payload()])
    _install_navigation(monkeypatch, cap)

    result = cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")

    assert result["reason"] == "zero_candidates_after_poll"
    output = capsys.readouterr().out
    assert '"reason":"unfiltered_gate_passed_id_not_found"' in output
    assert '"gate_passed":true' in output
    assert "secret-id" not in output and "secret-name" not in output and "secret-channel" not in output


def test_known_locator_aggregates_gate_reason_by_deepest_evidence(monkeypatch, capsys):
    cap = _cap()
    page = SequencePage([])
    _install_navigation(monkeypatch, cap)
    reasons = iter([
        cap._LIST_GATE_STRUCTURE_UNPARSEABLE,
        cap._LIST_GATE_RESPONSE_DOM_MISMATCH,
        cap._LIST_GATE_DOM_UNSTABLE,
    ])
    monkeypatch.setattr(
        cap, "_wait_for_unfiltered_list_restore",
        lambda *args, **kwargs: {"restored": False, "reason": next(reasons)},
    )

    result = cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")

    assert result["reason"] == "zero_candidates_after_poll"
    assert '"reason":"unfiltered_dom_unstable"' in capsys.readouterr().out


def test_known_locator_no_request_uses_only_one_scoped_refresh(monkeypatch):
    cap = _cap()
    page = SequencePage([])
    _install_navigation(monkeypatch, cap)
    refreshes = {"count": 0}
    monkeypatch.setattr(cap, "_wait_for_unfiltered_list_restore", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        cap, "_trigger_unfiltered_list_refresh",
        lambda page: refreshes.__setitem__("count", refreshes["count"] + 1) or True,
    )

    cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")

    assert refreshes["count"] == 1


def test_scoped_unfiltered_refresh_requires_unique_main_form_search():
    cap = _cap()

    class Page:
        def __init__(self):
            self.script = ""
        def evaluate(self, script):
            self.script = script
            return True

    page = Page()
    assert cap._trigger_unfiltered_list_refresh(page) is True
    assert ".el-form" in page.script
    assert "buttons.length !== 1" in page.script
    assert "el-dialog" in page.script


def test_known_locator_does_not_scan_id_without_fresh_unfiltered_restore(monkeypatch):
    cap = _cap()
    page = SequencePage([])
    resets = _install_navigation(monkeypatch, cap)
    monkeypatch.setattr(cap, "_wait_for_unfiltered_list_restore", lambda *args, **kwargs: False)

    result = cap._locate_known_main_row_for_resource_fallback(
        page, "secret-id", "secret-name", "secret-channel"
    )

    assert result["success"] is False
    assert result["reason"] == "zero_candidates_after_poll"
    assert result["candidate_category"] == "0"
    assert len(resets) == cap._POST_SAVE_KNOWN_ID_POLL_ATTEMPTS
    assert page.payloads == []


def _restore_state(signature="fresh", *, total=2, rows=2, next_enabled=False):
    return {"table_signature": signature, "total_count": total, "row_count": rows, "next_enabled": next_enabled}


def _restore_observer(cap, *, valid=True):
    observer = cap._ListRequestObserver()
    observer.target_absent_2xx_count = 1
    if valid:
        observer.success_records.append({"total_count": 2, "item_count": 2, "page_count": 1, "status": 200})
    else:
        observer.target_absent_structure_invalid_count = 1
    return observer


def test_unfiltered_restore_reason_no_complete_response():
    cap = _cap()
    observer = cap._ListRequestObserver()
    result = cap._wait_for_unfiltered_list_restore(
        SequencePage([]), _restore_state("old"), read_state=lambda page: _restore_state(),
        observations=observer, timeout_ms=1, poll_interval_ms=1, return_detail=True,
    )
    assert result == {"restored": False, "reason": cap._LIST_GATE_NO_COMPLETE_RESPONSE}


def test_unfiltered_restore_reason_structure_unparseable():
    cap = _cap()
    result = cap._wait_for_unfiltered_list_restore(
        SequencePage([]), _restore_state("old"), read_state=lambda page: _restore_state(),
        observations=_restore_observer(cap, valid=False), target_absent_before=0,
        timeout_ms=1, poll_interval_ms=1, return_detail=True,
    )
    assert result == {"restored": False, "reason": cap._LIST_GATE_STRUCTURE_UNPARSEABLE}


def test_unfiltered_restore_reason_response_dom_mismatch():
    cap = _cap()
    result = cap._wait_for_unfiltered_list_restore(
        SequencePage([]), _restore_state("old"), read_state=lambda page: _restore_state(total=3),
        observations=_restore_observer(cap), target_absent_before=0,
        timeout_ms=1, poll_interval_ms=1, return_detail=True,
    )
    assert result == {"restored": False, "reason": cap._LIST_GATE_RESPONSE_DOM_MISMATCH}


def test_unfiltered_restore_reason_dom_unstable():
    cap = _cap()
    states = iter([_restore_state("one"), _restore_state("two")])
    result = cap._wait_for_unfiltered_list_restore(
        SequencePage([]), _restore_state("old"), read_state=lambda page: next(states),
        observations=_restore_observer(cap), target_absent_before=0,
        timeout_ms=2, poll_interval_ms=1, return_detail=True,
    )
    assert result == {"restored": False, "reason": cap._LIST_GATE_DOM_UNSTABLE}


def test_unfiltered_restore_does_not_reuse_pre_reset_record():
    cap = _cap()
    observer = _restore_observer(cap)
    result = cap._wait_for_unfiltered_list_restore(
        SequencePage([]), _restore_state("old"), read_state=lambda page: _restore_state(),
        observations=observer, records_before=1, target_absent_before=1,
        timeout_ms=1, poll_interval_ms=1, return_detail=True,
    )
    assert result == {"restored": False, "reason": cap._LIST_GATE_NO_COMPLETE_RESPONSE}


def test_known_locator_fresh_restore_can_find_id_on_new_page_15(monkeypatch):
    cap = _cap()
    page = SequencePage([
        _payload(page=1),
        _payload(("secret-id", "secret-name", "secret-channel", "row-15"), page=15),
        _payload(("secret-id", "secret-name", "secret-channel", "row-15"), page=15),
    ])
    _install_navigation(monkeypatch, cap, next_results=(True, False))

    result = cap._locate_known_main_row_for_resource_fallback(
        page, "secret-id", "secret-name", "secret-channel"
    )

    assert result["success"] is True
    assert result["page"] == 15
    assert result["row_key"] == "row-15"


def test_known_locator_never_accepts_detail_id(monkeypatch):
    cap = _cap()
    page = SequencePage([
        _payload(("other-main-id", "secret-name", "secret-channel", "row-1", "secret-id")),
        _payload(("other-main-id", "secret-name", "secret-channel", "row-1", "secret-id")),
        _payload(("other-main-id", "secret-name", "secret-channel", "row-1", "secret-id")),
    ])
    _install_navigation(monkeypatch, cap)

    assert cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")["reason"] == "zero_candidates_after_poll"


def test_known_locator_accepts_async_zero_then_two_same_main_reads(monkeypatch):
    cap = _cap()
    page = SequencePage([
        _payload(page=1),
        _payload(("secret-id", "secret-name", "secret-channel", "row-2"), page=2),
        _payload(("secret-id", "secret-name", "secret-channel", "row-2"), page=2),
    ])
    resets = _install_navigation(monkeypatch, cap, next_results=(True,))

    result = cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")

    assert result["success"] is True
    assert result["page"] == 2
    assert result["row_idx"] == 0
    assert result["row_key"] == "row-2"
    assert result["key_kind"] == "native"
    assert len(resets) == 1


def test_known_locator_rejects_synthetic_key_when_row_index_changes(monkeypatch):
    cap = _cap()
    page = SequencePage([
        _payload(("secret-id", "secret-name", "secret-channel", "")),
        _payload(("old-id", "old", "old-channel", "old-key"), ("secret-id", "secret-name", "secret-channel", "")),
    ])
    _install_navigation(monkeypatch, cap)

    result = cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")

    assert result["success"] is False
    assert result["reason"] == "main_identity_unstable"


def test_known_locator_rejects_unstable_second_main_read(monkeypatch):
    cap = _cap()
    page = SequencePage([
        _payload(("secret-id", "secret-name", "secret-channel", "row-a")),
        _payload(("secret-id", "secret-name", "secret-channel", "row-b")),
    ])
    _install_navigation(monkeypatch, cap)

    result = cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")
    assert result["success"] is False
    assert result["reason"] == "main_identity_unstable"
    assert result["candidate_category"] == "1"


def test_known_locator_collapses_only_provable_dom_mirror(monkeypatch):
    cap = _cap()
    mirror = _payload(
        ("secret-id", "secret-name", "secret-channel", "row-1"),
        ("secret-id", "secret-name", "secret-channel", "row-1"),
    )
    page = SequencePage([mirror, mirror])
    _install_navigation(monkeypatch, cap)

    result = cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")

    assert result["success"] is True
    assert result["row_key"] == "row-1"


def test_known_locator_accepts_single_unkeyed_candidate_with_in_memory_logical_key(monkeypatch):
    cap = _cap()
    payload = _payload(("secret-id", "secret-name", "secret-channel", ""))
    page = SequencePage([payload, payload])
    _install_navigation(monkeypatch, cap)

    result = cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")

    assert result["success"] is True
    assert result["key_kind"] == "synthetic"
    assert result["row_key"].startswith("synthetic:1\x1f0\x1f")


def test_known_locator_rejects_nonmirror_duplicate_rows(monkeypatch):
    cap = _cap()
    cases = [
        _payload(("secret-id", "secret-name", "secret-channel", ""), ("secret-id", "secret-name", "secret-channel", "")),
        _payload(("secret-id", "secret-name", "secret-channel", "row-1"), ("secret-id", "other", "secret-channel", "row-1")),
        _payload(("secret-id", "secret-name", "secret-channel", "row-1"), ("secret-id", "secret-name", "secret-channel", "row-2")),
        # Same anchors but different row keys are real duplicate rows, not a mirror.
        _payload(("secret-id", "secret-name", "secret-channel", "row-1"), ("secret-id", "secret-name", "secret-channel", "row-2")),
    ]
    for payload in cases:
        page = SequencePage([payload])
        _install_navigation(monkeypatch, cap)
        result = cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")
        assert result["success"] is False
        assert result["reason"] == "ambiguous_main_rows"


def test_known_locator_rejects_duplicate_main_anchor_columns(monkeypatch):
    cap = _cap()
    payload = _payload(
        ("secret-id", "secret-id", "secret-name", "secret-channel", "row-1"),
        headers=("ID", "应用ID", "应用名称", "所属渠道"),
    )
    page = SequencePage([payload])
    _install_navigation(monkeypatch, cap)

    result = cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")
    assert result["success"] is False
    assert result["reason"] == "main_anchor_columns_ambiguous"
    assert result["id_field_source"] == "main"


def test_known_locator_resets_filters_starts_first_and_scans_later_page(monkeypatch):
    cap = _cap()
    page = SequencePage([
        _payload(("old-id", "old", "old-channel", "old-row"), page=1),
        _payload(("secret-id", "secret-name", "secret-channel", "row-7"), page=2),
        _payload(("secret-id", "secret-name", "secret-channel", "row-7"), page=2),
    ])
    resets = _install_navigation(monkeypatch, cap, next_results=(True,))

    assert cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")["success"] is True
    assert len(resets) == 1


def test_known_locator_rejects_same_id_on_two_pages_as_true_conflict(monkeypatch):
    cap = _cap()
    pages = {"current": 1}
    payloads = {
        1: _payload(("secret-id", "secret-name", "secret-channel", "row-1"), page=1),
        2: _payload(("secret-id", "secret-name", "secret-channel", "row-2"), page=2),
    }

    class Page:
        def evaluate(self, script, data=None):
            return payloads[pages["current"]]
        def wait_for_timeout(self, milliseconds):
            return None

    monkeypatch.setattr(cap, "_read_list_restore_state", lambda page: {"table_signature": "filtered", "row_count": 1})
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda page: cap._ListRequestObserver())
    monkeypatch.setattr(cap, "_wait_for_unfiltered_list_restore", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda observations: None)
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_go_to_first_page", lambda page: pages.__setitem__("current", 1) or True)
    monkeypatch.setattr(
        cap,
        "_click_next_page_and_wait",
        lambda page: pages.__setitem__("current", 2) or True if pages["current"] == 1 else False,
    )

    result = cap._locate_known_main_row_for_resource_fallback(
        Page(), "secret-id", "secret-name", "secret-channel"
    )

    assert result["success"] is False
    assert result["reason"] == "ambiguous_main_rows"


def test_known_locator_restores_first_page_candidate_before_stable_read(monkeypatch):
    cap = _cap()
    pages = {"current": 1}
    payloads = {
        1: _payload(("secret-id", "secret-name", "secret-channel", "row-1"), page=1),
        2: _payload(("old-id", "old", "old-channel", "old-row"), page=2),
    }

    class Page:
        def evaluate(self, script, data=None):
            return payloads[pages["current"]]
        def wait_for_timeout(self, milliseconds):
            return None

    monkeypatch.setattr(cap, "_read_list_restore_state", lambda page: {"table_signature": "filtered", "row_count": 1})
    monkeypatch.setattr(cap, "_attach_list_response_observer", lambda page: cap._ListRequestObserver())
    monkeypatch.setattr(cap, "_wait_for_unfiltered_list_restore", lambda *args, **kwargs: True)
    monkeypatch.setattr(cap, "_detach_list_response_observer", lambda observations: None)
    monkeypatch.setattr(cap, "_reset_list_filters", lambda page: None)
    monkeypatch.setattr(cap, "_go_to_first_page", lambda page: pages.__setitem__("current", 1) or True)
    monkeypatch.setattr(
        cap,
        "_click_next_page_and_wait",
        lambda page: pages.__setitem__("current", 2) or True if pages["current"] == 1 else False,
    )

    result = cap._locate_known_main_row_for_resource_fallback(
        Page(), "secret-id", "secret-name", "secret-channel"
    )

    assert result["success"] is True
    assert result["page"] == 1
    assert result["row_idx"] == 0
    assert result["row_key"] == "row-1"


def test_known_main_preclick_recheck_carries_same_key_and_main_anchors():
    cap = _cap()

    class Page:
        def __init__(self):
            self.script = ""
            self.data = None
        def evaluate(self, script, data):
            self.script, self.data = script, data
            return True

    page = Page()
    assert cap._open_copy_dialog_by_known_main_row(page, "secret-id", 2, 3, "row-2", "native", "secret-name", "secret-channel") is True
    assert page.data["logicalKey"] == "row-2"
    assert page.data["keyKind"] == "native"
    assert "const matches" in page.script
    assert "const logicalRows = sourceRows.filter(row => !row.classList.contains('el-table__expanded-row'));" in page.script
    assert "const target = logicalRows[rowIdx]" in page.script
    assert "keyOf(target, rowIdx" in page.script
    assert "String.fromCharCode(31)" in page.script
    assert "values.channel" in page.script


def test_known_main_preclick_toctou_recheck_fails_closed():
    cap = _cap()

    class Page:
        def evaluate(self, script, data):
            # Simulates the atomic in-page recheck seeing a changed row/key.
            return False

    assert cap._open_copy_dialog_by_known_main_row(
        Page(), "secret-id", 1, 0, "row-1", "native", "secret-name", "secret-channel"
    ) is False


def test_known_locator_logs_are_redacted(capsys, monkeypatch):
    cap = _cap()
    payload = _payload(("secret-id", "secret-name", "secret-channel", "row-1"))
    page = SequencePage([payload, payload])
    _install_navigation(monkeypatch, cap)

    assert cap._locate_known_main_row_for_resource_fallback(page, "secret-id", "secret-name", "secret-channel")["success"] is True
    output = capsys.readouterr().out
    assert "candidate_category=1" in output
    assert "id_field_source=main" in output
    assert "secret-id" not in output and "secret-name" not in output and "secret-channel" not in output


def test_known_main_identity_error_exposes_only_redacted_diagnostics(monkeypatch):
    cap = _cap()
    monkeypatch.setattr(
        cap,
        "_locate_known_main_row_for_resource_fallback",
        lambda *args, **kwargs: {
            "success": False,
            "reason": "ambiguous_main_rows",
            "candidate_category": ">1",
            "page": 4,
            "stable_row_key": False,
            "id_field_source": "main",
        },
    )

    result = cap._verify_persisted_resource_fallback(
        object(), "secret-id", "secret-url", "secret-name", "secret-channel"
    )

    message = result["error"]["message"]
    assert "candidate_category=>1" in message
    assert "page=4" in message
    assert "stable_row_key=False" in message
    assert "id_field_source=main" in message
    assert "secret-id" not in message and "secret-url" not in message
    assert "secret-name" not in message and "secret-channel" not in message


def test_identify_snapshot_reader_does_not_promote_detail_id():
    cap = _cap()

    class Page:
        def evaluate(self, script, data=None):
            return _payload(("", "secret-name", "secret-channel", "row-1", "secret-id"))

    assert cap._read_current_page_app_rows(Page()) == []


def test_only_post_save_path_uses_special_locator_and_downstream_find_is_unchanged():
    cap = _cap()
    source = Path(cap.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    stage = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_stage_create_save")
    enable = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_stage_enable")
    stage_source = ast.get_source_segment(source, stage) or ""
    enable_source = ast.get_source_segment(source, enable) or ""
    assert "_verify_persisted_resource_fallback" in stage_source
    assert "_find_target_row_by_id(page, target_app_id" not in stage_source
    assert "_find_target_row_by_id(page, target_app_id" in enable_source
