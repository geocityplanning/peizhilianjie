import json
from typing import Any

from app.db.database import get_connection


def save_channel(result: dict[str, Any]) -> None:
    channel_data = result.get("channel_data") or {}
    channel_id = channel_data.get("id")
    channel_name = channel_data.get("channel") or result.get("actual_channel_name")
    if not channel_id or not channel_name:
        return

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO channels (channel_id, channel_name, base_platform, channel_data_json, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(channel_id) DO UPDATE SET
                channel_name = excluded.channel_name,
                base_platform = excluded.base_platform,
                channel_data_json = excluded.channel_data_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(channel_id),
                str(channel_name),
                str(channel_data.get("basePlatform") or ""),
                json.dumps(channel_data, ensure_ascii=False),
            ),
        )


def save_app(result: dict[str, Any]) -> None:
    row_data = result.get("row_data") or {}
    platform_id = row_data.get("ID")
    app_name = row_data.get("应用名称")
    if not platform_id or not app_name:
        return

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO apps (
                platform_id, app_name, channel_name, cloud_app_link, cloud_app_short_link,
                base, settlement_type, status, row_data_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(platform_id) DO UPDATE SET
                app_name = excluded.app_name,
                channel_name = excluded.channel_name,
                cloud_app_link = excluded.cloud_app_link,
                cloud_app_short_link = excluded.cloud_app_short_link,
                base = excluded.base,
                settlement_type = excluded.settlement_type,
                status = excluded.status,
                row_data_json = excluded.row_data_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(platform_id),
                str(app_name),
                str(row_data.get("所属渠道") or ""),
                str(result.get("cloud_app_link") or row_data.get("长连接") or ""),
                str(result.get("cloud_app_short_link") or row_data.get("应用链接") or ""),
                str(row_data.get("底座") or ""),
                str(row_data.get("结算类型") or ""),
                str(row_data.get("_status") or row_data.get("状态") or ""),
                json.dumps(row_data, ensure_ascii=False),
            ),
        )


def save_operation(operation_type: str, result: dict[str, Any], batch_id: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO operations (
                batch_id, operation_type, execution_id, success, previous_values_json,
                row_data_json, channel_data_json, error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                operation_type,
                result.get("execution_id"),
                1 if result.get("success") else 0,
                json.dumps(result.get("previous_values") or {}, ensure_ascii=False),
                json.dumps(result.get("row_data") or {}, ensure_ascii=False),
                json.dumps(result.get("channel_data") or {}, ensure_ascii=False),
                None if result.get("success") else result.get("message"),
            ),
        )

