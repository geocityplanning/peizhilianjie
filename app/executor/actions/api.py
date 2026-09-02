# -*- coding: utf-8 -*-
"""
直接调用 API 层 —— FastAPI 可直接 import 使用，无需 MCP 协议。

用法示例:
    from actions.api import create_channel, create_app, update_app, ensure_login, get_status

    # 1. 检查状态
    status = get_status()
    # {"ready": True, "browser": "Chrome/152", "logged_in": True}

    # 2. 自动登录（session 过期时）
    result = ensure_login()
    # {"success": True, "message": "登录成功"}

    # 3. 创建渠道
    result = create_channel(channel_base_name="he0820")
    # {"success": True, "actual_channel_name": "he0820", "message": "创建渠道he0820成功"}

    # 4. 创建应用（复制指定参考应用）
    result = create_app(
        business_object="中国移动云盘",
        activity_name="活动1",
        actual_channel_name="he0820",  # 从 create_channel 返回值拿
        application_type="云盘",
        jump_address="mcloud://main/tab?params=xxx&tk=",
        resource_fallback_page="https://m.mcloud.139.com/portal/...",
        settlement_type="云盘",
        group_name="10028",            # 可选，空则跳过分组设置
        # ref_cloud_app_link="https://...",  # 可选，不传则按 application_type 使用默认长链接
    )
    # {"success": True, "cloud_app_link": "https://l.yun.139.com/...", "message": "操作成功"}

    # 5. 修改应用
    result = update_app(
        app_name="中国移动云盘-活动1",
        fields_to_update={
            "short_intro": "全新云盘体验",
            "icon": "http://127.0.0.1:8000/files/icons/icon_001.png",
            "share_text": "",  # 空字符串 = 清空
        }
    )
    # {"success": True, "updated_fields": ["一句话简介", "分享文案", "应用图标"],
    #  "previous_values": {"一句话简介": "旧值", ...}, "message": "..."}

注意:
    - 所有函数都是同步阻塞的（操作浏览器需要时间，单次约 10-60 秒）
    - 内部有单实例锁，同时只有一个函数在操作浏览器
    - 失败时返回 {"success": False, "message": "...", "error_code": "..."}
    - 不需要传 task_id / idempotency_key / contract_version 等协议字段
"""
import hashlib
import json
import uuid
from datetime import datetime

from core import executor as ex
from actions.create_channel_v2 import execute_create_channel
from actions.create_app_v2 import execute_create_app
from actions.update_app_v2 import execute_update_app, FIELD_MAP
from actions.locate_app import execute_locate_app
from actions.ensure_login import ensure_login as _ensure_login, is_logged_in
from core import get_browser_page


def _make_request(operation: str, data: dict, task_id: str = None) -> dict:
    """组装 MCP 契约包络，内部调用 v2 函数用。"""
    tid = task_id or f"API-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    ikey = hashlib.md5(f"{tid}.{operation}.{canonical}".encode()).hexdigest()
    return {
        "contract_version": ex.CONTRACT_VERSION,
        "task_id": tid,
        "environment": ex.ENVIRONMENT,
        "idempotency_key": ikey,
        "data": data,
    }


def _strip(result: dict) -> dict:
    """从 ExecutionResult 中提取业务数据，返回简洁格式。"""
    data = result.get("data") or {}
    if result.get("business_status") == "SUCCESS":
        return {
            "success": True,
            "message": "操作成功",
            **data,
        }
    else:
        error = result.get("error") or {}
        return {
            "success": False,
            "message": error.get("message", "操作失败"),
            "error_code": error.get("code"),
            "error_stage": error.get("stage"),
            "next_action": error.get("next_action"),
            **data,
        }


def get_status() -> dict:
    """检查执行环境状态：浏览器是否在线、是否已登录、是否空闲。

    Returns:
        {"ready": bool, "browser": str, "logged_in": bool, "locked": bool}
    """
    import requests
    browser_ok = False
    browser_ver = ""
    try:
        r = requests.get("http://127.0.0.1:9222/json/version", timeout=3)
        if r.status_code == 200:
            browser_ok = True
            browser_ver = r.json().get("Browser", "")
    except Exception:
        pass

    login_ok = False
    if browser_ok:
        try:
            pw, browser, page = get_browser_page()
            login_ok = is_logged_in(page)
            pw.stop()
        except Exception:
            pass

    locked = ex.is_locked()

    return {
        "ready": browser_ok and login_ok and not locked,
        "browser": browser_ver if browser_ok else None,
        "logged_in": login_ok,
        "locked": locked,
    }


def login() -> dict:
    """触发自动登录（检测 session 过期 → 填账密 → OCR 验证码 → 登录）。

    Returns:
        {"success": bool, "message": str, "already_logged_in": bool}
    """
    return _ensure_login()


def create_channel(channel_base_name: str, base_platform: str = None, task_id: str = None) -> dict:
    """创建渠道。

    Args:
        channel_base_name: 渠道基础名（重名时自动加数字后缀）
        base_platform: 底座名称，默认"华为底座2.0"，可传其他值
        task_id: 可选，用于追溯

    Returns:
        成功: {"success": True, "actual_channel_name": "he0820", "channel_data": {...}, "message": "操作成功"}
        失败: {"success": False, "message": "...", "error_code": "CHANNEL_VERIFY_FAILED"}
    """
    data = {"channel_base_name": channel_base_name}
    if base_platform:
        data["base_platform"] = base_platform
    req = _make_request("CREATE_CHANNEL", data, task_id)
    result = execute_create_channel(req)
    return _strip(result)


def create_app(
    business_object: str,
    activity_name: str,
    actual_channel_name: str,
    application_type: str = "云盘",
    jump_address: str = "",
    resource_fallback_page: str = "",
    settlement_type: str = "云盘",
    group_name: str = "",
    ref_cloud_app_link: str = None,
    task_id: str = None,
    **extra,
) -> dict:
    """创建应用（复制参考应用）。

    通过长链接搜索参考应用，点击"复制"打开预填编辑弹窗，
    只修改必须改的字段（应用名称、所属渠道等），其余字段保留参考应用的值。

    Args:
        business_object: 业务对象（如"中国移动云盘"）
        activity_name: 兼容保留字段，不参与应用名称生成
        actual_channel_name: 所属渠道名（必须来自 create_channel 的返回值）
        application_type: "云盘" / "掌厅"，决定 ref_cloud_app_link 默认值
        jump_address: 登录页"配置调起路径"，必填
        resource_fallback_page: 基础配置"资源不足中间页链接"，必填
        settlement_type: 结算类型
        group_name: 分组名（空则跳过分组设置）
        ref_cloud_app_link: 参考应用长链接（复制源），不传则按 application_type 取默认值
                    （云盘→"https://plus.buy.139.com/mccloudgame/#/?i=KWcMvfaFlhw="，
                     掌厅→"https://plus.buy.139.com/mccloudgame/#/?i=zZVurLOuLsI="）
        task_id: 可选，用于追溯
        **extra: 预留扩展字段

    Returns:
        成功: {"success": True, "cloud_app_link": "...", "cloud_app_short_link": "...",
               "completed_stages": ["CREATE_SAVE", "ENABLE", "SET_GROUP", "COMPLETED"]}
        失败: {"success": False, "message": "...", "error_code": "...",
               "completed_stages": [...], "failed_stage": "..."}
    """
    data = {
        "application_type": application_type,
        "business_object": business_object,
        "activity_name": activity_name,
        "actual_channel_name": actual_channel_name,
        "jump_address": jump_address,
        "resource_fallback_page": resource_fallback_page,
        "settlement_type": settlement_type,
        "group_name": group_name,
        "ref_cloud_app_link": ref_cloud_app_link,
    }
    data.update(extra)
    req = _make_request("CREATE_APP", data, task_id)
    result = execute_create_app(req)
    return _strip(result)


def update_app(
    app_name: str,
    fields_to_update: dict,
    app_id: str = None,
    task_id: str = None,
) -> dict:
    """修改应用配置（增量更新，只改传入的字段）。

    Args:
        app_name: 目标应用名（如"中国移动云盘-活动1"）
        fields_to_update: 要修改的字段键值对
            - 有值 = 设新值
            - 空字符串 "" = 清空
            - 键不存在 = 不动
        app_id: 可选，创建应用时的 execution_id（用于精确定位）
        task_id: 可选，用于追溯

    可用字段名见 FIELD_MAP（共 50 个），常用：
        "icon"(URL), "short_intro", "share_text", "settlement_type",
        "show_floating_ball"(bool), "external_sso"(bool),
        "button_text", "android_jump_link", "ios_jump_link" 等

    Returns:
        成功: {"success": True,
               "updated_fields": ["一句话简介", "应用图标"],
               "previous_values": {"一句话简介": "旧值", ...},
               "final_status": "ONLINE",
               "message": "操作成功"}
        失败: {"success": False, "message": "...", "error_code": "SAVE_FAILED"}
    """
    data = {
        "app_name": app_name,
        "fields_to_update": fields_to_update,
    }
    if app_id:
        data["app_id"] = app_id
    req = _make_request("UPDATE_APP", data, task_id)
    result = execute_update_app(req)
    return _strip(result)


def locate_app(channel_name: str = "", app_name: str = "", task_id: str = None) -> dict:
    """定位已存在应用（历史手动创建的，通过渠道名+应用名模糊匹配）。

    Args:
        channel_name: 渠道名（前缀匹配，可应对后缀数字情况）
        app_name: 应用名（二次消歧义）
        task_id: 可选，用于追溯

    Returns:
        唯一匹配:
            {"success": True, "app_name": "...", "channel": "...",
             "cloud_link": "...", "status": "ON", "match_confidence": "HIGH"}
        多条匹配:
            {"success": False, "match_confidence": "AMBIGUOUS",
             "matches": [{...}, ...], "match_count": 3, "message": "..."}
        无匹配:
            {"success": False, "message": "未找到匹配应用", "error_code": "APP_NOT_FOUND"}
    """
    data = {"channel_name": channel_name, "app_name": app_name}
    req = _make_request("LOCATE_APP", data, task_id)
    result = execute_locate_app(req)
    if result.get("business_status") == "SUCCESS":
        return {
            "success": True,
            "message": "定位成功",
            **(result.get("data") or {}),
        }
    else:
        error = result.get("error") or {}
        data_out = result.get("data") or {}
        return {
            "success": False,
            "message": error.get("message", "定位失败"),
            "error_code": error.get("code"),
            "next_action": error.get("next_action"),
            **(data_out if data_out else {}),
        }


def get_field_list() -> dict:
    """返回所有可修改字段列表（给前端渲染审查表用）。

    Returns:
        {"fields": [{"key": "icon", "tab": "体验配置", "label": "应用图标",
                      "type": "upload", "required": True}, ...]}
    """
    fields = []
    for key, meta in FIELD_MAP.items():
        fields.append({
            "key": key,
            "tab": meta["tab"],
            "label": meta["label"],
            "type": meta["type"],
            "required": meta.get("required", False),
        })
    return {"fields": fields, "total": len(fields)}
