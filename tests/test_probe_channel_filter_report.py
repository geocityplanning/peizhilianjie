# -*- coding: utf-8 -*-
"""Offline tests for probe diagnosis wording and default redaction.

These tests never connect to UAT, Chrome, or CDP.
"""
from __future__ import annotations

import importlib.util
import json
from io import StringIO
from pathlib import Path

PROBE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "probe_channel_filter.py"
)
SECRET_CHANNEL = "甘肃体验有礼-机密渠道"
SECRET_APP_ID = "12042"
SECRET_URL = "https://uat-cloud.139.com/cloudappadmin/#/cloudAppManager?x=1"


def _load_probe():
    spec = importlib.util.spec_from_file_location("probe_channel_filter", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TtyBuffer(StringIO):
    def isatty(self):
        return True


class PipedBuffer(StringIO):
    def isatty(self):
        return False


def test_diagnosis_uses_http_status_2xx_observed_not_response_ok():
    probe = _load_probe()
    diagnosis = probe._build_diagnosis(
        {
            "filter_stable": True,
            "list_requests_before": 0,
            "list_requests_after": 1,
            "last_http_status": 200,
            "reader_row_count": 2,
            "reader_distinct_channels": 1,
            "reader_sees_target_channel": True,
        }
    )
    assert "response_ok" not in diagnosis
    assert diagnosis["http_status_2xx_observed"] is True
    assert diagnosis["http_2xx_is_not_business_success"] is True
    assert diagnosis["request_reached_backend"] is True


def test_diagnosis_2xx_and_4xx_and_no_response():
    probe = _load_probe()
    assert probe._http_status_2xx_observed(200) is True
    assert probe._http_status_2xx_observed(204) is True
    assert probe._http_status_2xx_observed(403) is False
    assert probe._http_status_2xx_observed(None) == "NO_RESPONSE_OBSERVED"
    none_observer = probe._build_diagnosis({"filter_stable": False})
    assert none_observer["request_reached_backend"] == "UNKNOWN_NO_OBSERVER"
    assert none_observer["http_status_2xx_observed"] == "NO_RESPONSE_OBSERVED"


def test_diagnosis_notes_keep_http_2xx_not_business_success():
    probe = _load_probe()
    notes = probe._diagnosis_notes({"filter_stable": True, "last_http_status": 200})
    assert probe.HTTP_2XX_NOT_BUSINESS_SUCCESS_NOTE in notes
    assert "业务查询成功" in probe.HTTP_2XX_NOT_BUSINESS_SUCCESS_NOTE
    assert "S4 通过" in probe.HTTP_2XX_NOT_BUSINESS_SUCCESS_NOTE


def test_dump_report_redacts_url_sample_ids_and_omits_raw_options():
    probe = _load_probe()
    report = {
        "channel": SECRET_CHANNEL,
        "url_before_navigate": SECRET_URL,
        "current_url": SECRET_URL,
        "steps": {
            "recon": {
                "url": SECRET_URL,
                "selects": [{"label": "渠道", "current_value": SECRET_CHANNEL}],
                "tables": [{"sample_ids": [SECRET_APP_ID, "11926"]}],
            },
            "channel_options": {
                "opened": True,
                "option_count": 2,
                "options": [SECRET_CHANNEL, "另一个机密渠道"],
            },
            "exact_app_id_lookup": {"found": False, "app_id": SECRET_APP_ID},
        },
        "diagnosis": {
            "http_status_2xx_observed": True,
            "http_2xx_is_not_business_success": True,
        },
    }
    dumped = probe._dump_report(report)
    payload = json.loads(dumped)
    assert SECRET_CHANNEL not in dumped
    assert SECRET_URL not in dumped
    assert SECRET_APP_ID not in dumped
    assert "另一个机密渠道" not in dumped
    assert "options" not in payload["steps"]["channel_options"]
    assert payload["steps"]["channel_options"]["options_redacted"] is True
    assert payload["steps"]["channel_options"]["option_count"] == 2
    assert payload["steps"]["recon"]["url"].startswith("[redacted-url]")
    assert payload["steps"]["recon"]["tables"][0]["sample_ids_redacted"] is True
    assert payload["steps"]["exact_app_id_lookup"]["app_id"] != SECRET_APP_ID
    assert "response_ok" not in dumped


def test_filter_path_distinguishes_three_requested_cases():
    probe = _load_probe()
    missing_detail = {
        "list_requests_before": 0,
        "list_requests_after": 1,
        "last_http_status": 200,
        "request_carried_target_filter": False,
        "table_changed": False,
        "reader_sees_target_channel": False,
        "filter_stable": False,
    }
    missing = probe._build_diagnosis(missing_detail)
    assert missing["filter_path"] == "request_missing_target_filter"
    assert missing["request_carried_target_filter"] is False
    assert missing["http_status_2xx_observed"] is True
    assert "加长等待" in "\n".join(probe._diagnosis_notes(missing_detail))

    missing_response = {
        "list_requests_before": 0,
        "list_requests_after": 1,
        "last_http_status": 200,
        "request_carried_target_filter": True,
        "response_contains_target_channel": False,
        "table_changed": False,
        "reader_sees_target_channel": False,
        "filter_stable": False,
    }
    diagnosis = probe._build_diagnosis(missing_response)
    assert diagnosis["filter_path"] == "response_missing_target_channel"
    assert diagnosis["response_contains_target_channel"] is False
    notes = "\n".join(probe._diagnosis_notes(missing_response))
    assert "响应数据未含目标渠道" in notes
    assert SECRET_CHANNEL not in notes

    has_response = {
        "list_requests_before": 0,
        "list_requests_after": 1,
        "last_http_status": 200,
        "request_carried_target_filter": True,
        "response_contains_target_channel": True,
        "table_changed": False,
        "reader_sees_target_channel": False,
        "filter_stable": False,
    }
    diagnosis = probe._build_diagnosis(has_response)
    assert diagnosis["filter_path"] == "response_has_target_dom_not_refreshed"
    notes = "\n".join(probe._diagnosis_notes(has_response))
    assert "响应数据已含目标渠道" in notes

    no_body = {
        "list_requests_before": 0,
        "list_requests_after": 1,
        "last_http_status": 200,
        "request_carried_target_filter": True,
        "response_contains_target_channel": None,
        "table_changed": False,
        "reader_sees_target_channel": False,
        "filter_stable": False,
    }
    diagnosis = probe._build_diagnosis(no_body)
    assert diagnosis["filter_path"] == "unknown_no_response_body"
    assert diagnosis["response_contains_target_channel"] is None

    refreshed = probe._build_diagnosis(
        {
            "list_requests_before": 0,
            "list_requests_after": 1,
            "last_http_status": 200,
            "request_carried_target_filter": True,
            "table_changed": True,
            "reader_sees_target_channel": True,
            "filter_stable": True,
            "reader_row_count": 2,
            "reader_distinct_channels": 1,
        }
    )
    assert refreshed["filter_path"] == "dom_refreshed_with_target_channel"
    assert refreshed["table_changed"] is True
    assert refreshed["reader_sees_target_channel"] is True


def test_dump_report_drops_payload_keys_and_keeps_booleans():
    probe = _load_probe()
    dumped = probe._dump_report(
        {
            "channel": SECRET_CHANNEL,
            "diagnosis": {
                "request_carried_target_filter": False,
                "table_changed": False,
                "reader_sees_target_channel": False,
                "filter_path": "request_missing_target_filter",
            },
            "steps": {
                "channel_filter": {
                    "postData": f'{{"channelName":"{SECRET_CHANNEL}"}}',
                    "query": "channelName=" + SECRET_CHANNEL,
                    "token": "secret-token",
                    "request_carried_target_filter": False,
                }
            },
        }
    )
    payload = json.loads(dumped)
    assert SECRET_CHANNEL not in dumped
    assert "secret-token" not in dumped
    assert "postData" not in payload["steps"]["channel_filter"]
    assert "query" not in payload["steps"]["channel_filter"]
    assert "token" not in payload["steps"]["channel_filter"]
    assert payload["diagnosis"]["request_carried_target_filter"] is False
    assert payload["diagnosis"]["filter_path"] == "request_missing_target_filter"


def test_local_options_print_only_on_tty():
    probe = _load_probe()
    tty = TtyBuffer()
    piped = PipedBuffer()
    assert probe._emit_local_channel_options([SECRET_CHANNEL], stream=tty) is True
    assert SECRET_CHANNEL in tty.getvalue()
    assert "LOCAL-ONLY" in tty.getvalue()
    assert probe._emit_local_channel_options([SECRET_CHANNEL], stream=piped) is False
    assert piped.getvalue() == ""
