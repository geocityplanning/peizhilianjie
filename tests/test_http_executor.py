from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.http_executor.api import create_app
from app.http_executor.service import ExecutionService
from app.http_executor.settings import Settings
from app.http_executor.store import ExecutorStore, initialize_database


def make_client(tmp_path: Path, *, delay_ms: int = 0, mode: str = "FAKE", real_caller=None) -> TestClient:
    db_path = tmp_path / "executor.db"
    initialize_database(db_path)
    settings = Settings(
        db_path=db_path,
        environment="TEST",
        mode=mode,
        auth_token="test-token",
        allow_anonymous=False,
        fake_delay_ms=delay_ms,
        fake_channel_suffix="-1",
    )
    service = ExecutionService(settings, ExecutorStore(db_path), real_caller=real_caller)
    return TestClient(create_app(service))


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def channel_request(key: str = "key-channel-1") -> dict:
    return {
        "contract_version": "http-executor.v1",
        "task_id": "HERMES-TEST-001",
        "operation": "exec.create_channel",
        "environment": "TEST",
        "idempotency_key": key,
        "input": {"requested_channel_name": "甘肃体验有礼掌厅瀑布流"},
    }


def app_request(key: str = "key-app-1") -> dict:
    return {
        "contract_version": "http-executor.v1",
        "task_id": "HERMES-TEST-002",
        "operation": "exec.create_app",
        "environment": "TEST",
        "idempotency_key": key,
        "input": {
            "application_type": "云盘",
            "business_object": "中国移动云盘",
            "actual_channel_name": "甘肃体验有礼掌厅瀑布流-1",
            "jump_address": "mcloud://main/webView?params=fake",
            "resource_fallback_page": "https://example.invalid/fallback",
            "settlement_type": "云盘",
            "group_name": "10086",
        },
    }


def wait_terminal(client: TestClient, execution_id: str) -> dict:
    for _ in range(30):
        response = client.post(
            "/v1/exec/query",
            headers=headers(),
            json={"contract_version": "http-executor.v1", "execution_id": execution_id},
        )
        body = response.json()
        if body["execution_state"] in {"SUCCEEDED", "FAILED", "UNKNOWN", "REJECTED"}:
            return body
        time.sleep(0.01)
    raise AssertionError("fake execution did not reach a terminal state")


def test_info_is_passive_and_create_channel_returns_full_receipt(tmp_path: Path):
    with make_client(tmp_path) as client:
        info = client.get("/v1/exec/info", headers=headers())
        assert info.status_code == 200
        assert info.json()["capabilities"] == [
            "exec.get_info",
            "exec.create_channel",
            "exec.create_app",
            "exec.query_execution",
        ]
        response = client.post("/v1/exec/create-channel", headers=headers(), json=channel_request())
        body = response.json()
        assert body["execution_id"]
        assert body["task_id"] == "HERMES-TEST-001"
        assert body["idempotency_key"] == "key-channel-1"
        final = wait_terminal(client, body["execution_id"])
        assert final["execution_state"] == "SUCCEEDED"
        assert final["data"]["actual_channel_name"].endswith("-1")


def test_same_idempotency_key_has_one_execution(tmp_path: Path):
    with make_client(tmp_path) as client:
        first = client.post("/v1/exec/create-channel", headers=headers(), json=channel_request()).json()
        second = client.post("/v1/exec/create-channel", headers=headers(), json=channel_request()).json()
        assert first["execution_id"] == second["execution_id"]


def test_same_idempotency_key_with_different_input_is_rejected(tmp_path: Path):
    with make_client(tmp_path) as client:
        client.post("/v1/exec/create-channel", headers=headers(), json=channel_request())
        changed = channel_request()
        changed["input"]["requested_channel_name"] = "另一个渠道"
        response = client.post("/v1/exec/create-channel", headers=headers(), json=changed)
        assert response.status_code == 409
        assert response.json()["error"]["error_code"] == "IDEMPOTENCY_CONFLICT"


def test_create_app_accepts_frozen_hermes_payload_without_ref_app_id(tmp_path: Path):
    with make_client(tmp_path) as client:
        request = app_request()
        assert "ref_app_id" not in request["input"]
        response = client.post("/v1/exec/create-app", headers=headers(), json=request)
        body = response.json()
        assert body["operation"] == "exec.create_app"
        final = wait_terminal(client, body["execution_id"])
        assert final["business_status"] == "SIMULATED_SUCCESS"
        assert final["data"]["actual_channel_name"] == "甘肃体验有礼掌厅瀑布流-1"


def test_missing_required_field_is_rejected_before_execution(tmp_path: Path):
    with make_client(tmp_path) as client:
        invalid = app_request()
        del invalid["input"]["resource_fallback_page"]
        response = client.post("/v1/exec/create-app", headers=headers(), json=invalid)
        assert response.status_code == 400
        assert response.json()["error"]["error_code"] == "MISSING_REQUIRED_FIELD"


def test_group_name_is_required_for_create_app(tmp_path: Path):
    with make_client(tmp_path) as client:
        request = app_request()
        del request["input"]["group_name"]
        response = client.post("/v1/exec/create-app", headers=headers(), json=request)
        assert response.status_code == 400
        assert "group_name" in response.json()["error"]["message"]


def test_real_create_app_maps_frozen_contract_to_automation(tmp_path: Path):
    calls = []

    def real_caller(name, **kwargs):
        calls.append((name, kwargs))
        return {
            "success": True,
            "app_id": "12001",
            "app_name": "中国移动云盘",
            "cloud_app_link": "https://plus.buy.139.com/mccloudgame/#/?i=new",
            "cloud_app_short_link": "capp://new",
            "completed_stages": ["CREATE_SAVE", "ENABLE", "SET_GROUP", "COMPLETED"],
            "row_data": {"ID": "12001"},
        }

    with make_client(tmp_path, mode="REAL", real_caller=real_caller) as client:
        response = client.post("/v1/exec/create-app", headers=headers(), json=app_request())
        final = wait_terminal(client, response.json()["execution_id"])

    assert final["execution_state"] == "SUCCEEDED"
    assert final["business_status"] == "SUCCESS"
    assert final["data"]["application_id"] == "12001"
    assert final["data"]["application_name"] == "中国移动云盘"
    assert calls[0][0] == "create_app"
    assert calls[0][1]["business_object"] == "中国移动云盘"
    assert calls[0][1]["activity_name"] == ""
    assert calls[0][1]["group_name"] == "10086"


def test_real_create_channel_returns_automation_actual_name(tmp_path: Path):
    def real_caller(name, **kwargs):
        assert name == "create_channel"
        assert kwargs["channel_base_name"] == "甘肃体验有礼掌厅瀑布流"
        return {"success": True, "actual_channel_name": "甘肃体验有礼掌厅瀑布流1"}

    with make_client(tmp_path, mode="REAL", real_caller=real_caller) as client:
        response = client.post("/v1/exec/create-channel", headers=headers(), json=channel_request())
        final = wait_terminal(client, response.json()["execution_id"])

    assert final["execution_state"] == "SUCCEEDED"
    assert final["data"]["actual_channel_name"] == "甘肃体验有礼掌厅瀑布流1"


def test_query_unknown_task_does_not_create_execution(tmp_path: Path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/v1/exec/query",
            headers=headers(),
            json={
                "contract_version": "http-executor.v1",
                "execution_id": "EXEC-NOT-FOUND",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["error_code"] == "EXECUTION_NOT_FOUND"


def test_query_rejects_mismatched_execution_association(tmp_path: Path):
    with make_client(tmp_path) as client:
        created = client.post("/v1/exec/create-channel", headers=headers(), json=channel_request()).json()
        response = client.post(
            "/v1/exec/query",
            headers=headers(),
            json={
                "contract_version": "http-executor.v1",
                "execution_id": created["execution_id"],
                "task_id": "HERMES-OTHER-TASK",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["error_code"] == "QUERY_ASSOCIATION_CONFLICT"
