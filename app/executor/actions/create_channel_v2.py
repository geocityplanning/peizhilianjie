# -*- coding: utf-8 -*-
"""
创建渠道 — 契约版。

入口 execute_create_channel(request: CommonWriteRequest) -> ExecutionResult
内部逻辑复用原有 create_channel()，但包裹了：
  幂等检查 → 单实例锁 → SQLite 记录 → 业务执行 → 终态校验 → finalize
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import (
    get_browser_page,
    get_all_channels,
    get_base_platforms,
    make_unique_channel_name,
    ADMIN_URL,
)
from core.error_capture import capture_page_errors, build_error_message
from core.error_codes import err, NEXT_STOP, NEXT_QUERY
from core import executor as ex
from actions.ensure_login import ensure_login

OPERATION = "CREATE_CHANNEL"
DEFAULT_BASE_PLATFORM = "华为底座2.0"


def _set_stage_ch(execution_id, stage):
    """更新执行记录的阶段，供前端轮询显示进度。"""
    try:
        ex.update_execution(execution_id, output_json=json.dumps({"_stage": stage}, ensure_ascii=False))
        print(f"[create_channel] stage: {stage}")
    except Exception:
        pass


def execute_create_channel(request: dict) -> dict:
    """
    request: CommonWriteRequest 包络
    返回:    ExecutionResult
    """
    task_id = request["task_id"]
    data = request["data"]
    environment = request.get("environment", "TEST")
    idempotency_key = request["idempotency_key"]

    # ---- 1. 幂等检查 ----
    existing = ex.find_by_idempotency(idempotency_key)
    if existing:
        return ex.build_result(
            task_id, OPERATION, idempotency_key,
            execution=existing,
            accepted=True,
            data=json.loads(existing.get("output_json") or "{}"),
            error=json.loads(existing.get("error_json") or "null") if existing.get("error_json") else None,
            environment=existing.get("environment"),
        )

    # ---- 2. 单实例锁 ----
    if ex.is_locked():
        active = ex.get_active_execution_id()
        return ex.build_result(
            task_id, OPERATION, idempotency_key,
            accepted=False,
            error=err("EXECUTOR_BUSY", "LOCK", f"执行端忙碌中，占用执行: {active}", NEXT_QUERY),
        )

    # ---- 3. 字段校验 ----
    channel_base_name = (data.get("channel_base_name") or "").strip()
    base_platform = (data.get("base_platform") or "").strip() or DEFAULT_BASE_PLATFORM
    if not channel_base_name:
        return ex.build_result(
            task_id, OPERATION, idempotency_key,
            accepted=False,
            error=err("FIELD_VALIDATION", "VALIDATE", "channel_base_name 不能为空", NEXT_STOP),
        )

    # ---- 4. 建立执行记录 ----
    execution = ex.create_execution(task_id, OPERATION, idempotency_key, data, environment)
    execution_id = execution["execution_id"]

    if not ex.acquire_lock(execution_id):
        return ex.build_result(
            task_id, OPERATION, idempotency_key,
            accepted=False,
            error=err("EXECUTOR_BUSY", "LOCK", "获取执行锁失败", NEXT_QUERY),
        )

    # ---- 5. 更新为 RUNNING ----
    ex.update_execution(execution_id, execution_state=ex.STATE_RUNNING)

    pw = None
    try:
        pw, browser, page = get_browser_page()
        page.bring_to_front()

        # 登录
        _set_stage_ch(execution_id, "正在登录")
        auth = ensure_login(page)
        if not auth["success"]:
            ex.finalize_execution(execution_id, ex.BIZ_FAILED,
                                  error=err("NOT_LOGGED_IN", "LOGIN", f"登录失败: {auth['message']}", NEXT_QUERY))
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                   execution=ex.find_by_execution_id(execution_id),
                                   accepted=True, environment=environment,
                                   error=err("NOT_LOGGED_IN", "LOGIN", f"登录失败: {auth['message']}", NEXT_QUERY))

        if "cloudAppChannelManager" not in page.url:
            page.goto(ADMIN_URL, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("table tbody tr", timeout=5000)
            except Exception:
                page.wait_for_timeout(800)

        # 查重
        _set_stage_ch(execution_id, "正在查询渠道列表查重")
        channels = get_all_channels(page)
        existing_names = {c.get("channel", "") for c in channels}
        actual_name = make_unique_channel_name(existing_names, channel_base_name)

        # 底座映射
        platforms = get_base_platforms(page)
        base_map = {p["name"]: p["base"] for p in platforms}
        if base_platform not in base_map:
            ex.finalize_execution(execution_id, ex.BIZ_FAILED,
                                  error=err("FIELD_VALIDATION", "VALIDATE", f"底座不存在: {base_platform}", NEXT_STOP))
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                   execution=ex.find_by_execution_id(execution_id),
                                   accepted=True, environment=environment,
                                   error=err("FIELD_VALIDATION", "VALIDATE", f"底座不存在: {base_platform}", NEXT_STOP))

        # 关残留弹窗
        try:
            closes = page.locator(".el-dialog__wrapper:visible .el-dialog__headerbtn")
            for i in range(closes.count()):
                closes.nth(i).click()
                page.wait_for_timeout(120)
        except Exception:
            pass

        # 新增
        _set_stage_ch(execution_id, "正在新建渠道")
        page.get_by_text("新增所属渠道", exact=False).first.click()
        page.wait_for_selector(".el-dialog:visible", timeout=5000)
        page.wait_for_timeout(200)

        dlg = page.locator(".el-dialog:visible")
        dlg.locator("input[placeholder='请输入内容']").fill(actual_name)
        page.wait_for_timeout(120)

        dlg.locator(".el-select").click()
        page.wait_for_selector(".el-select-dropdown:visible", timeout=5000)
        page.locator(".el-select-dropdown:visible .el-select-dropdown__item").filter(has_text=base_platform).first.click()
        page.wait_for_timeout(120)

        dlg.locator("button.green-button").click()
        try:
            page.wait_for_selector(".el-dialog:visible", state="detached", timeout=5000)
        except Exception:
            page.wait_for_timeout(800)

        # 终态校验
        _set_stage_ch(execution_id, "正在校验渠道创建结果")
        fresh = get_all_channels(page)
        fresh_names = {c.get("channel", "") for c in fresh}
        if actual_name in fresh_names:
            # 找到新建的渠道完整记录，全量返回
            channel_record = next((c for c in fresh if c.get("channel") == actual_name), {})
            output = {"actual_channel_name": actual_name, "channel_data": channel_record}
            ex.finalize_execution(execution_id, ex.BIZ_SUCCESS, output_data=output,
                                  evidence_ref=f"{execution_id}/channel-final.json")
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, data=output, environment=environment)

        # 失败
        err_info = capture_page_errors(page, screenshot_name=f"channel_fail_{actual_name}")
        error = err("CHANNEL_VERIFY_FAILED", "VERIFY",
                     build_error_message(err_info, f"创建渠道{actual_name}失败: 列表中未找到"),
                     NEXT_MANUAL)
        ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=error)
        return ex.build_result(task_id, OPERATION, idempotency_key,
                              execution=ex.find_by_execution_id(execution_id),
                              accepted=True, error=error, environment=environment)

    except Exception as e:
        try:
            err_info = capture_page_errors(page, screenshot_name=f"channel_exc_{channel_base_name}")
            error = err("CHANNEL_CREATE_FAILED", "EXECUTE",
                        f"创建渠道异常: {e} | {build_error_message(err_info)}", NEXT_QUERY)
        except Exception:
            error = err("CHANNEL_CREATE_FAILED", "EXECUTE", f"创建渠道异常: {e}", NEXT_QUERY)
        ex.finalize_execution(execution_id, ex.BIZ_UNKNOWN, error=error)
        return ex.build_result(task_id, OPERATION, idempotency_key,
                              execution=ex.find_by_execution_id(execution_id),
                              accepted=True, error=error, environment=environment)
    finally:
        ex.release_lock(execution_id)
        if pw:
            try:
                pw.stop()
            except Exception:
                pass


