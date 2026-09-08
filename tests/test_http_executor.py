from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.http_executor.api import create_app
from app.http_executor.service import ExecutionService
from app.http_executor.settings import Settings
from app.http_executor.store import ExecutorStore, initialize_database


HEADERS = {
    "Authorization": "Bearer test-token",
    "X-Contract-Version": "http-executor.v1",
}


def make_client(tmp_path: Path, *, real_caller=None) -> TestClient:
    db_path = tmp_path / "executor.db"
    initialize_database(db_path)
    settings = Settings(db_path=db_path, environment="TEST", auth_token="test-token")

    if real_caller is None:
        def real_caller(name, **kwargs):
            if name == "create_channel":
                return {
                    "success": True,
                    "actual_channel_name": kwargs["channel_base_name"] + "-1",
                    "channel_data": {"source": "test-double"},
                }
            return {
                "success": True,
                "app_id": "12008",
                "app_name": kwargs["business_object"],
                "cloud_app_link": "https://example.invalid/#/?i=12008",
                "cloud_app_short_link": "capp://12008",
                "completed_stages": ["CREATE_SAVE", "ENABLE", "SET_GROUP", "COMPLETED"],
                "row_data": {"ID": "12008"},
            }

    service = ExecutionService(settings, ExecutorStore(db_path), real_caller=real_caller)
    return TestClient(create_app(service))


def channel_request(key: str = "key-channel-1") -> dict:
    return {
        "task_id": "HERMES-TEST-001",
        "operation": "create_channel",
        "environment": "TEST",
        "snapshot_version": "20260908-V1",
        "idempotency_key": key,
        "input": {
            "requested_channel_name": "甘肃体验有礼-0908测试",
            "base_platform": "掌厅",
        },
    }


def app_request(key: str = "key-app-1") -> dict:
    return {
        "task_id": "HERMES-TEST-002",
        "operation": "create_app",
        "environment": "TEST",
        "snapshot_version": "20260908-V1",
        "idempotency_key": key,
        "input": {
            "application_type": "云盘",
            "business_object": "中国移动云盘-0908测试",
            "actual_channel_name": "甘肃体验有礼-0908测试-1",
            "jump_address": "mcloud://main/webView?params=test",
            "resource_fallback_page": "https://example.invalid/fallback",
            "settlement_type": "云盘",
            "group_name": "10086",
            "ref_cloud_app_link": "https://plus.buy.139.com/mccloudgame/#/?i=KWcMvfaFlhw=",
        },
    }


def test_only_four_post_routes_are_exposed(tmp_path: Path):
    with make_client(tmp_path) as client:
        routes = {
            (route.path, method)
            for route in client.app.routes
            for method in getattr(route, "methods", set())
        }
        assert routes == {
            ("/v1/exec/info", "POST"),
            ("/v1/exec/create-channel", "POST"),
            ("/v1/exec/create-app", "POST"),
            ("/v1/exec/query", "POST"),
        }
        assert client.get("/v1/exec/info", headers=HEADERS).status_code == 405
        assert client.get("/openapi.json", headers=HEADERS).status_code == 404


def test_info_reports_real_mode(tmp_path: Path):
    with make_client(tmp_path) as client:
        response = client.post("/v1/exec/info", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "contract_version": "http-executor.v1",
        "service": "hermes-real-executor",
        "environment": "TEST",
        "mode": "REAL",
        "capabilities": ["get_info", "create_channel", "create_app", "query_execution"],
        "active_execution_correlation_id": None,
        "database_status": "AVAILABLE",
    }


def test_every_route_requires_bearer_token_and_contract_header(tmp_path: Path):
    with make_client(tmp_path) as client:
        missing_token = client.post(
            "/v1/exec/info",
            headers={"X-Contract-Version": "http-executor.v1"},
        )
        missing_version = client.post(
            "/v1/exec/info",
            headers={"Authorization": "Bearer test-token"},
        )

    assert missing_token.status_code == 401
    assert missing_token.json()["error"]["error_code"] == "AUTH_FAILED"
    assert missing_version.status_code == 400
    assert missing_version.json()["error"]["error_code"] == "UNSUPPORTED_VERSION"


def test_create_channel_returns_synchronous_final_receipt(tmp_path: Path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/v1/exec/create-channel",
            headers=HEADERS,
            json=channel_request(),
        )

    body = response.json()
    assert response.status_code == 200
    assert body["operation"] == "create_channel"
    assert body["snapshot_version"] == "20260908-V1"
    assert body["state"] == "SUCCEEDED"
    assert body["status"] == "SUCCESS"
    assert body["adjudicated"] is True
    assert body["data"]["actual_channel_name"].endswith("-1")
    assert "execution_id" not in body
    assert "execution_state" not in body
    assert "business_status" not in body


def test_same_idempotency_key_returns_original_execution(tmp_path: Path):
    calls = []

    def caller(name, **kwargs):
        calls.append((name, kwargs))
        return {"success": True, "actual_channel_name": kwargs["channel_base_name"]}

    with make_client(tmp_path, real_caller=caller) as client:
        first = client.post("/v1/exec/create-channel", headers=HEADERS, json=channel_request()).json()
        second = client.post("/v1/exec/create-channel", headers=HEADERS, json=channel_request()).json()

    assert first["execution_correlation_id"] == second["execution_correlation_id"]
    assert len(calls) == 1


def test_same_idempotency_key_with_different_input_is_rejected(tmp_path: Path):
    with make_client(tmp_path) as client:
        client.post("/v1/exec/create-channel", headers=HEADERS, json=channel_request())
        changed = channel_request()
        changed["input"]["requested_channel_name"] = "另一个渠道"
        response = client.post("/v1/exec/create-channel", headers=HEADERS, json=changed)

    assert response.status_code == 409
    assert response.json()["error"]["error_code"] == "IDEMPOTENCY_CONFLICT"


def test_create_app_maps_exact_real_result_fields(tmp_path: Path):
    calls = []

    def caller(name, **kwargs):
        calls.append((name, kwargs))
        return {
            "success": True,
            "app_id": "12008",
            "app_name": kwargs["business_object"],
            "cloud_app_link": "https://example.invalid/#/?i=12008",
            "cloud_app_short_link": "capp://12008",
            "completed_stages": ["CREATE_SAVE", "ENABLE", "SET_GROUP", "COMPLETED"],
            "row_data": {"ID": "12008"},
        }

    with make_client(tmp_path, real_caller=caller) as client:
        response = client.post("/v1/exec/create-app", headers=HEADERS, json=app_request())

    body = response.json()
    assert response.status_code == 200
    assert body["state"] == "SUCCEEDED"
    assert body["status"] == "SUCCESS"
    assert body["data"]["app_id"] == "12008"
    assert body["data"]["app_link"] == "https://example.invalid/#/?i=12008"
    assert "application_id" not in body["data"]
    assert "long_link" not in body["data"]
    assert calls[0][0] == "create_app"
    assert calls[0][1]["business_object"] == "中国移动云盘-0908测试"
    assert calls[0][1]["activity_name"] == ""
    assert calls[0][1]["group_name"] == "10086"
    assert calls[0][1]["ref_cloud_app_link"].endswith("KWcMvfaFlhw=")


def test_request_schema_and_operation_input_are_strict(tmp_path: Path):
    body_version = channel_request()
    body_version["contract_version"] = "http-executor.v1"
    unknown_input = app_request("key-app-unknown")
    unknown_input["input"]["download_link"] = "https://example.invalid/download"
    missing_group = app_request("key-app-missing")
    del missing_group["input"]["group_name"]

    with make_client(tmp_path) as client:
        body_response = client.post("/v1/exec/create-channel", headers=HEADERS, json=body_version)
        unknown_response = client.post("/v1/exec/create-app", headers=HEADERS, json=unknown_input)
        missing_response = client.post("/v1/exec/create-app", headers=HEADERS, json=missing_group)

    assert body_response.status_code == 400
    assert body_response.json()["error"]["error_code"] == "SCHEMA_INVALID"
    assert unknown_response.status_code == 400
    assert unknown_response.json()["error"]["error_code"] == "UNKNOWN_INPUT_FIELD"
    assert missing_response.status_code == 400
    assert missing_response.json()["error"]["error_code"] == "MISSING_REQUIRED_FIELD"


def test_query_uses_correlation_id_and_checks_snapshot(tmp_path: Path):
    with make_client(tmp_path) as client:
        created = client.post("/v1/exec/create-channel", headers=HEADERS, json=channel_request()).json()
        query = {
            "operation": "create_channel",
            "environment": "TEST",
            "snapshot_version": "20260908-V1",
            "execution_correlation_id": created["execution_correlation_id"],
        }
        response = client.post("/v1/exec/query", headers=HEADERS, json=query)
        query["snapshot_version"] = "different"
        conflict = client.post("/v1/exec/query", headers=HEADERS, json=query)

    assert response.status_code == 200
    assert response.json()["execution_correlation_id"] == created["execution_correlation_id"]
    assert conflict.status_code == 400
    assert conflict.json()["error"]["error_code"] == "QUERY_ASSOCIATION_CONFLICT"


def test_query_unknown_execution_returns_404(tmp_path: Path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/v1/exec/query",
            headers=HEADERS,
            json={"execution_correlation_id": "EXEC-NOT-FOUND"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == "EXECUTION_NOT_FOUND"


def test_http_entrypoint_does_not_import_old_app_or_mcp():
    code = """
import json
import sys
import app.http_executor.main
blocked = {
    'app.main',
    'app.services.job_manager',
    'app.executor.mcp_server',
}
print(json.dumps(sorted(blocked.intersection(sys.modules))))
"""
    output = subprocess.check_output([sys.executable, "-c", code], text=True)
    assert json.loads(output) == []


def test_hermes_executor_call_is_allowlisted(monkeypatch):
    from app.services import executor_client

    monkeypatch.setattr(executor_client, "call_executor", lambda name, **kwargs: {"name": name})
    assert executor_client.call_hermes_executor("create_app") == {"name": "create_app"}

    try:
        executor_client.call_hermes_executor("update_app")
    except RuntimeError as exc:
        assert "不允许调用" in str(exc)
    else:
        raise AssertionError("update_app must not be callable through Hermes HTTP")