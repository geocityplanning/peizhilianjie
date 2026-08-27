# -*- coding: utf-8 -*-
"""
创建应用 — 契约版（v2，加超时保护+智能等待）。

入口 execute_create_app(request: CommonWriteRequest) -> ExecutionResult
改进：
  - 所有 locator 操作加 5 秒超时，不再默认 30 秒干等
  - 固定 wait_for_timeout 换成 wait_for_selector 智能等待
  - 每步加 try/except，卡住立刻报错返回，不干等
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import get_browser_page
from core.error_capture import capture_page_errors, build_error_message
from core.error_codes import err, NEXT_STOP, NEXT_QUERY, NEXT_MANUAL
from core import executor as ex
from actions.ensure_login import ensure_login

OPERATION = "CREATE_APP"
BASE_URL_H5 = "https://plus.buy.139.com/cloudappadmin/#/cloudAppManager"
DEFAULT_BASE = "华为底座2.0"

# 统一超时：每个 UI 操作最多等 5 秒
STEP_TIMEOUT = 10000

DIR = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parents[3] / "data" / "executor" / "output"
OUT.mkdir(exist_ok=True)


def _shot(page, name):
    if os.environ.get("LINK_EXECUTOR_STEP_SCREENSHOTS") != "1":
        return
    try:
        page.screenshot(path=str(OUT / f"appv3_{name}.png"), timeout=8000)
    except Exception:
        pass


def _cleanup_overlays(page):
    for _ in range(3):
        try:
            page.keyboard.press("Escape"); page.wait_for_timeout(200)
        except Exception:
            pass
        try:
            mb = page.locator(".el-message-box__wrapper")
            for i in range(mb.count()):
                w = mb.nth(i).get_attribute("style") or ""
                if "none" not in w:
                    mb.nth(i).get_by_text("取消", exact=True).first.click(timeout=2000)
                    page.wait_for_timeout(200)
        except Exception:
            pass
        try:
            for i in range(page.locator(".el-dialog__headerbtn").count()):
                page.locator(".el-dialog__headerbtn").nth(i).click(timeout=2000)
                page.wait_for_timeout(200)
        except Exception:
            pass


def _find_form_item(page, label):
    """找到指定 label 的 form-item，找不到返回 None（不干等）。"""
    items = page.locator(".el-dialog:visible .el-form-item")
    n = items.count()
    for i in range(n):
        try:
            lbl_el = items.nth(i).locator(".el-form-item__label").first
            lbl = lbl_el.inner_text(timeout=2000).strip(" *:").strip()
            if lbl == label:
                return items.nth(i)
        except Exception:
            continue
    return None


def _available_tabs(page):
    try:
        tabs = page.locator(".el-dialog:visible .el-tabs__item")
        values = []
        for i in range(tabs.count()):
            try:
                values.append(tabs.nth(i).inner_text(timeout=500).strip())
            except Exception:
                pass
        return values
    except Exception:
        return []


def _go_tab(page, name):
    """切 Tab：先收起浮层，再在短时间内重试找 Tab，避免页面刚渲染时误判失败。"""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass

    deadline = time.monotonic() + 12
    last_tabs = []
    while time.monotonic() < deadline:
        try:
            page.wait_for_selector(".el-dialog:visible .el-tabs__item", timeout=2000)
            tabs = page.locator(".el-dialog:visible .el-tabs__item")
            last_tabs = _available_tabs(page)
            for i in range(tabs.count()):
                tab = tabs.nth(i)
                text = tab.inner_text(timeout=2000).strip()
                if name in text:
                    tab.scroll_into_view_if_needed(timeout=2000)
                    tab.click(timeout=STEP_TIMEOUT)
                    page.wait_for_timeout(500)
                    try:
                        page.wait_for_selector(".el-dialog:visible .el-tab-pane:visible", timeout=3000)
                    except Exception:
                        pass
                    return True
        except Exception:
            pass
        page.wait_for_timeout(200)

    print(f"[create_app] WARN: 未找到Tab {name}，当前Tabs={last_tabs}")
    return False

def _select_dropdown(page, form_item, value):
    """点下拉框选值，超时 5 秒。"""
    try:
        form_item.locator(".el-select").first.click(timeout=STEP_TIMEOUT)
        # 等下拉面板出现（智能等待）
        page.wait_for_selector(".el-select-dropdown:visible", timeout=STEP_TIMEOUT)
        opt = page.locator(".el-select-dropdown:visible .el-select-dropdown__item").filter(has_text=value).first
        if opt.count() > 0:
            opt.click(timeout=STEP_TIMEOUT)
            return True
        # 选项没找到，关掉下拉
        page.keyboard.press("Escape")
        return False
    except Exception:
        return False


def _set_stage(execution_id, stage):
    """更新执行记录的阶段，供前端轮询显示进度。"""
    try:
        ex.update_execution(execution_id, output_json=json.dumps({"_stage": stage}, ensure_ascii=False))
        print(f"[create_app] stage: {stage}")
    except Exception:
        pass


def execute_create_app(request: dict) -> dict:
    task_id = request["task_id"]
    data = request["data"]
    environment = request.get("environment", "TEST")
    idempotency_key = request["idempotency_key"]

    # 1. 幂等
    existing = ex.find_by_idempotency(idempotency_key)
    if existing:
        return ex.build_result(task_id, OPERATION, idempotency_key,
                              execution=existing, accepted=True,
                              data=json.loads(existing.get("output_json") or "{}"),
                              error=json.loads(existing.get("error_json") or "null") if existing.get("error_json") else None,
                              environment=existing.get("environment"))

    # 2. 锁
    if ex.is_locked():
        active = ex.get_active_execution_id()
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("EXECUTOR_BUSY", "LOCK", f"执行端忙碌中，占用执行: {active}", NEXT_QUERY))

    # 3. 字段校验
    required = ["application_type", "business_object", "actual_channel_name",
                "jump_address", "resource_fallback_page", "settlement_type"]
    missing = [f for f in required if not (data.get(f) or "").strip()]
    if missing:
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("FIELD_VALIDATION", "VALIDATE", f"必填字段为空: {', '.join(missing)}", NEXT_STOP))

    group_name = (data.get("group_name") or "").strip()
    bo = data["business_object"]
    activity = data.get("activity_name", "")
    app_name = f"{bo}-{activity}" if activity else bo

    # 4. 建执行记录
    execution = ex.create_execution(task_id, OPERATION, idempotency_key, data, environment)
    execution_id = execution["execution_id"]
    if not ex.acquire_lock(execution_id):
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("EXECUTOR_BUSY", "LOCK", "获取执行锁失败", NEXT_QUERY))
    ex.update_execution(execution_id, execution_state=ex.STATE_RUNNING)

    pw = None
    page = None
    try:
        pw, browser, page = get_browser_page()
        page.bring_to_front()
        auth = ensure_login(page)
        if not auth["success"]:
            e = err("NOT_LOGGED_IN", "LOGIN", f"登录失败: {auth['message']}", NEXT_QUERY)
            ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)

        _set_stage(execution_id, "正在加载应用列表")
        _cleanup_overlays(page)
        page.goto(BASE_URL_H5, wait_until="domcontentloaded")
        # 智能等待表格出现，最多 5 秒
        try:
            page.wait_for_selector("table tbody tr", timeout=STEP_TIMEOUT)
        except Exception:
            page.wait_for_timeout(2000)  # 兜底
        try:
            page.get_by_text("确定", exact=True).first.click(timeout=2000)
        except Exception:
            pass
        _shot(page, "01_list")

        # reset + search
        try:
            page.get_by_text("重置", exact=True).first.click(timeout=STEP_TIMEOUT)
            # 等表格刷新
            page.wait_for_timeout(500)
        except Exception:
            pass
        try:
            page.get_by_text("搜 索", exact=False).first.click(timeout=STEP_TIMEOUT)
            try:
                page.wait_for_selector("table tbody tr", timeout=STEP_TIMEOUT)
            except Exception:
                page.wait_for_timeout(500)
        except Exception:
            pass

        # copy template
        _set_stage(execution_id, "正在复制模板应用")
        copy_btns = page.get_by_text("复制", exact=True)
        copied = False
        for i in range(copy_btns.count()):
            try:
                btn = copy_btns.nth(i)
                if bo in btn.locator("xpath=ancestor::tr").first.inner_text(timeout=2000):
                    btn.click(timeout=STEP_TIMEOUT)
                    # 等对话框出现
                    try:
                        page.wait_for_selector(".el-dialog:visible", timeout=STEP_TIMEOUT)
                    except Exception:
                        page.wait_for_timeout(500)
                    copied = True
                    break
            except Exception:
                continue
        if not copied:
            page.get_by_text("复制", exact=True).first.click(timeout=STEP_TIMEOUT)
            try:
                page.wait_for_selector(".el-dialog:visible", timeout=STEP_TIMEOUT)
            except Exception:
                page.wait_for_timeout(500)
        _shot(page, "02_copy_dialog")

        # === 体验配置 ===
        _set_stage(execution_id, "正在填写体验配置")
        if not _go_tab(page, "体验配置"):
            raise RuntimeError(f"无法切换到体验配置Tab，当前可见Tabs={_available_tabs(page)}")
        fi = _find_form_item(page, "应用名称")
        if fi:
            try:
                fi.locator("input.el-input__inner").first.fill(app_name, timeout=STEP_TIMEOUT)
            except Exception:
                pass
        else:
            print("[create_app] WARN: 应用名称 字段未找到")
        fi = _find_form_item(page, "底座")
        if fi:
            _select_dropdown(page, fi, DEFAULT_BASE)
        else:
            print("[create_app] WARN: 底座 字段未找到")
        fi = _find_form_item(page, "所属渠道")
        if fi:
            try:
                fi.locator(".channel-input").first.click(timeout=STEP_TIMEOUT)
                # 等 popover 出现
                try:
                    page.wait_for_selector(".el-popover:visible, .el-popper:visible", timeout=STEP_TIMEOUT)
                except Exception:
                    page.wait_for_timeout(500)
                pop = page.locator(".el-popover:visible, .el-popper:visible").last
                tgt = pop.get_by_text(data["actual_channel_name"], exact=False).first
                if tgt.count() > 0:
                    tgt.click(timeout=STEP_TIMEOUT)
                    page.wait_for_timeout(500)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
            except Exception as e:
                print(f"[create_app] WARN: 所属渠道选择失败: {e}")
        else:
            print("[create_app] WARN: 所属渠道 字段未找到")
        _shot(page, "03_exp_config")

        # === 登录页配置 ===
        _set_stage(execution_id, "正在填写登录页配置")
        if not _go_tab(page, "登录页配置"):
            raise RuntimeError(f"无法切换到登录页配置Tab，当前可见Tabs={_available_tabs(page)}")
        paths = page.locator(".el-dialog:visible .el-form-item").filter(has_text="配置调起路径")
        for i in range(paths.count()):
            try:
                it = paths.nth(i)
                if "is-required" in (it.get_attribute("class") or ""):
                    it.locator("input.el-input__inner").first.fill(data["jump_address"], timeout=STEP_TIMEOUT)
                    break
            except Exception:
                continue
        _shot(page, "04_login_config")

        # === 基础配置 ===
        _set_stage(execution_id, "正在填写基础配置")
        if not _go_tab(page, "基础配置"):
            raise RuntimeError(f"无法切换到基础配置Tab，当前可见Tabs={_available_tabs(page)}")
        fi = _find_form_item(page, "资源不足中间页链接")
        if fi:
            try:
                fi.locator("input.el-input__inner").first.fill(data["resource_fallback_page"], timeout=STEP_TIMEOUT)
            except Exception as e:
                print(f"[create_app] WARN: 资源不足中间页链接填写失败: {e}")
        else:
            print("[create_app] WARN: 资源不足中间页链接 字段未找到")
        fi = _find_form_item(page, "结算类型")
        if fi:
            if not _select_dropdown(page, fi, data["settlement_type"]):
                print(f"[create_app] WARN: 结算类型选择失败，目标值: {data['settlement_type']}")
        else:
            print("[create_app] WARN: 结算类型 字段未找到")
        _shot(page, "05_basic_config")

        # === save ===
        _set_stage(execution_id, "正在保存应用")
        if not _go_tab(page, "悬浮球配置"):
            raise RuntimeError(f"无法切换到悬浮球配置Tab，当前可见Tabs={_available_tabs(page)}")
        try:
            page.locator(".el-dialog:visible").locator("button").filter(has_text="保存").first.click(timeout=STEP_TIMEOUT)
        except Exception as e:
            raise RuntimeError(f"保存按钮点击失败: {e}")
        # 等对话框消失（保存成功），最多 8 秒
        try:
            page.wait_for_selector(".el-dialog:visible", state="detached", timeout=8000)
        except Exception:
            # 对话框还在 = 可能保存失败或还在处理
            page.wait_for_timeout(2000)
        _shot(page, "06_after_save")
        save_err = capture_page_errors(page, screenshot_name=f"app_save_{app_name}")
        if save_err["dialog_open"]:
            e = err("SAVE_FAILED", "SAVE", build_error_message(save_err, "保存失败(对话框未关闭)"), NEXT_MANUAL)
            ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)

        # verify created
        try:
            page.wait_for_selector("table tbody tr", timeout=STEP_TIMEOUT)
        except Exception:
            pass
        rows = page.locator("table tbody tr")
        found_row = None
        for ri in range(rows.count()):
            try:
                if app_name in rows.nth(ri).inner_text(timeout=2000):
                    found_row = rows.nth(ri); break
            except Exception:
                continue
        if found_row is None:
            e = err("SAVE_FAILED", "VERIFY", f"保存后未在列表中找到应用: {app_name}", NEXT_MANUAL)
            ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)
        _shot(page, "07_created")

        # === publish ===
        _set_stage(execution_id, "正在发布上线")
        sw = found_row.locator(".el-switch").first
        if "is-checked" not in (sw.get_attribute("class") or ""):
            try:
                sw.click(timeout=STEP_TIMEOUT)
                # 等确认弹窗或状态变化
                try:
                    page.wait_for_selector(".el-message-box:visible", timeout=3000)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(500)
                except Exception:
                    page.wait_for_timeout(500)
            except Exception as e:
                print(f"[create_app] WARN: 状态开关点击失败: {e}")
        # 确认开关状态
        sw2 = found_row.locator(".el-switch").first
        if "is-checked" not in (sw2.get_attribute("class") or ""):
            pub_err = capture_page_errors(page, screenshot_name=f"app_publish_fail_{app_name}")
            e = err("PUBLISH_FAILED", "PUBLISH", build_error_message(pub_err, "发布失败(状态未开启)"), NEXT_MANUAL)
            ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)
        _shot(page, "08_published")

        # === set group ===
        if group_name:
            _set_stage(execution_id, "正在设置分组")
            set_btns = found_row.get_by_text("设置", exact=True)
            try:
                set_btns.last.click(timeout=STEP_TIMEOUT)
                try:
                    page.wait_for_selector(".el-dialog:visible", timeout=STEP_TIMEOUT)
                except Exception:
                    page.wait_for_timeout(500)
                dlg = page.locator(".el-dialog:visible").last
                dlg.get_by_text("按分组", exact=True).first.click(timeout=STEP_TIMEOUT)
                page.wait_for_timeout(300)
                dlg.locator("input.el-input__inner").last.fill(group_name, timeout=STEP_TIMEOUT)
                page.wait_for_timeout(200)
                dlg.get_by_text("确定", exact=True).first.click(timeout=STEP_TIMEOUT)
                # 等成功提示或对话框消失
                try:
                    page.wait_for_selector(".el-dialog:visible", state="detached", timeout=5000)
                except Exception:
                    page.wait_for_timeout(700)
            except Exception as ge:
                g_err = capture_page_errors(page, screenshot_name=f"app_group_fail_{app_name}")
                e = err("GROUP_SET_FAILED", "GROUP", f"分组设置异常: {ge}", NEXT_MANUAL)
                ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
                return ex.build_result(task_id, OPERATION, idempotency_key,
                                      execution=ex.find_by_execution_id(execution_id),
                                      accepted=True, error=e, environment=environment)
            _shot(page, "09_group_set")

        # === capture full row data + final verify ===
        _set_stage(execution_id, "正在校验终态")
        row_data = {}
        try:
            rows2 = page.locator("table tbody tr")
            for ri in range(rows2.count()):
                if app_name in rows2.nth(ri).inner_text(timeout=2000):
                    from core.row_reader import read_row_full
                    row_data = read_row_full(page, rows2.nth(ri))
                    break
        except Exception:
            pass

        base_val = row_data.get("底座", "")
        status_val = row_data.get("_status", "")
        cloud_link = row_data.get("长连接", "")
        short_link = row_data.get("应用链接", "")

        if base_val == DEFAULT_BASE and status_val == "ON" and cloud_link:
            output = {"cloud_app_link": cloud_link, "cloud_app_short_link": short_link, "row_data": row_data}
            ex.finalize_execution(execution_id, ex.BIZ_SUCCESS, output_data=output,
                                 evidence_ref=f"{execution_id}/app-final.json")
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, data=output, environment=environment)
        else:
            reasons = []
            if base_val != DEFAULT_BASE: reasons.append(f"底座={base_val}")
            if status_val != "ON": reasons.append("状态未开启")
            if not cloud_link: reasons.append("未获取到链接")
            e = err("FINAL_VERIFY_FAILED", "VERIFY", f"终态校验失败: {'; '.join(reasons)}", NEXT_MANUAL)
            ex.finalize_execution(execution_id, ex.BIZ_UNKNOWN, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)

    except Exception as e:
        try:
            err_info = capture_page_errors(page, screenshot_name=f"app_exc_{app_name}") if page else {}
            error = err("APP_CREATE_FAILED", "EXECUTE", str(e) + " | " + build_error_message(err_info), NEXT_QUERY)
        except Exception:
            error = err("APP_CREATE_FAILED", "EXECUTE", str(e), NEXT_QUERY)
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






