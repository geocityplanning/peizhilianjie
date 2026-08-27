# -*- coding: utf-8 -*-
"""
修改应用配置 — 契约版（增量更新 PATCH 语义）。

入口 execute_update_app(request: CommonWriteRequest) -> ExecutionResult
字段映射表 FIELD_MAP 定义了所有可修改字段及其在编辑对话框中的位置。
"""
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import get_browser_page
from core.error_capture import capture_page_errors, build_error_message
from core.error_codes import err, NEXT_STOP, NEXT_QUERY, NEXT_MANUAL
from core import executor as ex
from actions.ensure_login import ensure_login

OPERATION = "UPDATE_APP"
BASE_URL_H5 = "https://plus.buy.139.com/cloudappadmin/#/cloudAppManager"
DIR = Path(__file__).resolve().parent.parent
OUT = DIR / "output"
TMP_DIR = DIR / "data" / "tmp"


def _set_stage_upd(execution_id, stage):
    """更新执行记录的阶段，供前端轮询显示进度。"""
    try:
        ex.update_execution(execution_id, output_json=json.dumps({"_stage": stage}, ensure_ascii=False))
        print(f"[update_app] stage: {stage}")
    except Exception:
        pass
TMP_DIR.mkdir(parents=True, exist_ok=True)

# ---- 字段映射表 ----
# key = FastAPI 传入的 JSON key
# tab = 编辑对话框中的选项卡名
# label = 后台表单 label 文字（去冒号去星号）
# type = input / textarea / select / switch / upload / radio
# required = 是否必填（空字符串不可清空）
FIELD_MAP = {
    # ---- 体验配置 ----
    "app_name":            {"tab": "体验配置", "label": "应用名称",         "type": "input",   "required": True},
    "package_name":        {"tab": "体验配置", "label": "包名",             "type": "input",   "required": True},
    "t_version_app_id":    {"tab": "体验配置", "label": "T版AppId",         "type": "input",   "required": False},
    "icon":                {"tab": "体验配置", "label": "应用图标",         "type": "upload",  "required": True},
    "idle_timeout":        {"tab": "体验配置", "label": "无操作断流",       "type": "input",   "required": False},
    "keepalive":           {"tab": "体验配置", "label": "保活",             "type": "input",   "required": False},
    "external_sso":       {"tab": "体验配置", "label": "外链单点功能",     "type": "switch",  "required": False},
    "idle_float_window":   {"tab": "体验配置", "label": "剩余时长展示悬浮窗","type": "switch", "required": False},
    "location_popup":      {"tab": "体验配置", "label": "定位授权弹窗",     "type": "switch",  "required": False},
    "browser_unsupported": {"tab": "体验配置", "label": "浏览器不支持中间页链接","type":"switch","required": False},
    "free_flow_display":   {"tab": "体验配置", "label": "免流显示",         "type": "select",  "required": False},
    # ---- 登录页配置 ----
    "login_method":        {"tab": "登录页配置", "label": "登录方式展示配置","type": "select", "required": True},
    "specified_page":      {"tab": "登录页配置", "label": "指定访问页面",   "type": "switch",  "required": False},
    "local_app_priority":  {"tab": "登录页配置", "label": "优先唤起本地应用","type": "switch", "required": False},
    "t0c_fusion":          {"tab": "登录页配置", "label": "T0C融合开关",   "type": "switch",  "required": False},
    # ---- 营销配置 ----
    "loading_page_config": {"tab": "营销配置", "label": "加载页配置",       "type": "select",  "required": False},
    "end_popup_config":    {"tab": "营销配置", "label": "体验结束弹窗配置", "type": "select",  "required": True},
    "top_operation":       {"tab": "营销配置", "label": "顶部运营位",       "type": "switch",  "required": False},
    "gift_bag":            {"tab": "营销配置", "label": "礼包",             "type": "switch",  "required": False},
    "drainage_position":   {"tab": "营销配置", "label": "引流位",           "type": "switch",  "required": False},
    "random_promo":        {"tab": "营销配置", "label": "随机活动推广",     "type": "switch",  "required": False},
    # ---- 基础配置 ----
    "detail_app_name":     {"tab": "基础配置", "label": "应用名称",         "type": "input",   "required": True},
    "short_intro":         {"tab": "基础配置", "label": "一句话简介",       "type": "input",   "required": True},
    "app_remark":          {"tab": "基础配置", "label": "应用备注",         "type": "input",   "required": True},
    "app_tag":             {"tab": "基础配置", "label": "应用标签",         "type": "input",   "required": False},
    "share_text":          {"tab": "基础配置", "label": "分享文案",         "type": "input",   "required": False},
    "button_text":         {"tab": "基础配置", "label": "按钮文案",         "type": "input",   "required": True},
    "android_jump_link":   {"tab": "基础配置", "label": "安卓跳转链接",     "type": "input",   "required": True},
    "ios_jump_link":       {"tab": "基础配置", "label": "ios跳转链接",      "type": "input",   "required": True},
    "guide_text":          {"tab": "基础配置", "label": "引导跳转文案",     "type": "input",   "required": True},
    "version":             {"tab": "基础配置", "label": "版本号",           "type": "input",   "required": True},
    "resource_fallback":   {"tab": "基础配置", "label": "资源不足中间页链接","type": "input",  "required": False},
    "tip_button_icon":     {"tab": "基础配置", "label": "提示按钮图标",     "type": "upload",  "required": False},
    "tip_page_bg":         {"tab": "基础配置", "label": "提示页面背景图",   "type": "upload",  "required": False},
    "tip_page_logo":       {"tab": "基础配置", "label": "提示页面LOGO",    "type": "upload",  "required": False},
    "settlement_type":     {"tab": "基础配置", "label": "结算类型",         "type": "select",  "required": True},
    "app_intro":           {"tab": "基础配置", "label": "应用介绍",         "type": "textarea","required": False},
    "ipv6_enabled":        {"tab": "基础配置", "label": "ipv6是否生效",     "type": "switch",  "required": False},
    "tracking_enabled":    {"tab": "基础配置", "label": "插码是否生效",     "type": "switch",  "required": False},
    # ---- Tab配置 ----
    "tab_guide_bg":        {"tab": "Tab配置", "label": "引导页面背景图",   "type": "upload",  "required": False},
    "scheme_config":       {"tab": "Tab配置", "label": "Scheme配置",       "type": "input",   "required": False},
    "h5_link":             {"tab": "Tab配置", "label": "H5链接",            "type": "input",   "required": False},
    # ---- 悬浮球配置 ----
    "show_floating_ball":  {"tab": "悬浮球配置", "label": "显示悬浮球",     "type": "switch",  "required": True},
    "ball_default_style":  {"tab": "悬浮球配置", "label": "悬浮球默认样式", "type": "upload",  "required": True},
    "ball_docked_style":   {"tab": "悬浮球配置", "label": "悬浮球停靠样式", "type": "upload",  "required": True},
    "ball_click_target":   {"tab": "悬浮球配置", "label": "点击悬浮球跳转页面","type":"select","required": True},
    "bar_close_button":    {"tab": "悬浮球配置", "label": "悬浮条关闭按钮", "type": "switch",  "required": True},
    "bar_expand_text":     {"tab": "悬浮球配置", "label": "悬浮条展开文案", "type": "input",   "required": False},
    "bar_bg_image":        {"tab": "悬浮球配置", "label": "悬浮条背景图片", "type": "upload",  "required": False},
    "remark":              {"tab": "悬浮球配置", "label": "备注",           "type": "input",   "required": False},
}


# ---- 辅助函数 ----

def _shot(page, name):
    try:
        page.screenshot(path=str(OUT / f"upd_{name}.png"), timeout=8000)
    except Exception:
        pass

def _cleanup_overlays(page):
    for _ in range(3):
        try:
            page.keyboard.press("Escape"); page.wait_for_timeout(300)
        except Exception:
            pass
        try:
            mb = page.locator(".el-message-box__wrapper")
            for i in range(mb.count()):
                w = mb.nth(i).get_attribute("style") or ""
                if "none" not in w:
                    mb.nth(i).get_by_text("取消", exact=True).first.click(timeout=2000)
                    page.wait_for_timeout(400)
        except Exception:
            pass
        try:
            for i in range(page.locator(".el-dialog__headerbtn").count()):
                page.locator(".el-dialog__headerbtn").nth(i).click(timeout=2000)
                page.wait_for_timeout(300)
        except Exception:
            pass

def _go_tab(page, name):
    tabs = page.locator(".el-dialog:visible .el-tabs__item")
    for i in range(tabs.count()):
        if name in tabs.nth(i).inner_text():
            tabs.nth(i).click(); page.wait_for_timeout(1000)
            return True
    return False

def _find_form_item(page, label):
    items = page.locator(".el-dialog:visible .el-form-item")
    for i in range(items.count()):
        try:
            it = items.nth(i)
            if not it.is_visible():
                continue
            lbl_el = it.locator(".el-form-item__label").first
            if lbl_el.count() == 0:
                continue
            lbl = lbl_el.inner_text().strip(" *:").strip()
            if lbl == label:
                return it
        except Exception:
            pass
    return None

def _read_value(page, form_item, ftype):
    """读取当前值用于 previous_values 快照。"""
    try:
        if ftype == "textarea":
            return form_item.locator("textarea").first.input_value() or ""
        elif ftype == "input":
            return form_item.locator("input.el-input__inner").first.input_value() or ""
        elif ftype == "switch":
            sw = form_item.locator(".el-switch").first
            return "is-checked" in (sw.get_attribute("class") or "")
        elif ftype == "select":
            return form_item.locator("input.el-input__inner").first.input_value() or ""
        elif ftype == "upload":
            img = form_item.locator("img").first
            return img.get_attribute("src")[:80] if img.count() > 0 else "[no image]"
    except Exception:
        pass
    return ""

def _download_image(url, tmp_dir):
    import requests
    os.makedirs(tmp_dir, exist_ok=True)
    filename = url.split("/")[-1].split("?")[0] or f"img_{int(time.time())}.png"
    filepath = os.path.join(tmp_dir, filename)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    with open(filepath, "wb") as f:
        f.write(r.content)
    return filepath

def _set_input(form_item, value):
    """填输入框/文本域。空字符串=清空。"""
    ta = form_item.locator("textarea")
    if ta.count() > 0:
        ta.first.fill("")
        if value:
            ta.first.fill(str(value))
        return True
    inp = form_item.locator("input.el-input__inner").first
    inp.fill("")
    if value:
        inp.fill(str(value))
    return True

def _set_select(page, form_item, value):
    form_item.locator(".el-select").first.click()
    page.wait_for_timeout(800)
    opt = page.locator(".el-select-dropdown:visible .el-select-dropdown__item").filter(has_text=str(value)).first
    if opt.count() > 0:
        opt.click(); page.wait_for_timeout(500)
        return True
    # fallback: type to filter
    form_item.locator("input.el-input__inner").first.fill(str(value))
    page.wait_for_timeout(800)
    first_opt = page.locator(".el-select-dropdown:visible .el-select-dropdown__item").first
    if first_opt.count() > 0:
        first_opt.click(); page.wait_for_timeout(500)
        return True
    return False

def _set_switch(form_item, target):
    target_on = bool(target)
    sw = form_item.locator(".el-switch").first
    current_on = "is-checked" in (sw.get_attribute("class") or "")
    if current_on != target_on:
        sw.click()
    return True

def _set_upload(page, form_item, url):
    file_input = form_item.locator("input[type='file']").first
    if file_input.count() == 0:
        raise Exception("upload 组件中未找到 input[type=file]")
    filepath = _download_image(url, str(TMP_DIR))
    file_input.set_input_files(filepath)
    page.wait_for_timeout(2500)
    # cleanup
    try:
        os.remove(filepath)
    except Exception:
        pass
    return True


# ---- 主入口 ----

def execute_update_app(request: dict) -> dict:
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
    app_id = data.get("app_id", "")
    app_name = (data.get("app_name") or "").strip()
    fields_to_update = data.get("fields_to_update") or {}

    if not app_name and not app_id:
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("FIELD_VALIDATION", "VALIDATE", "app_id 和 app_name 至少提供一个", NEXT_STOP))
    if not fields_to_update:
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("FIELD_VALIDATION", "VALIDATE", "fields_to_update 不能为空", NEXT_STOP))

    # 校验所有 key 在 FIELD_MAP 中
    unknown_keys = [k for k in fields_to_update if k not in FIELD_MAP]
    if unknown_keys:
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("FIELD_NOT_FOUND", "VALIDATE",
                              f"未知字段: {', '.join(unknown_keys)}", NEXT_STOP))

    # 校验必填字段不允许空字符串
    for k, v in fields_to_update.items():
        if v == "" and FIELD_MAP[k].get("required"):
            return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                                  error=err("FIELD_VALIDATION", "VALIDATE",
                                  f"必填字段不允许清空: {k}", NEXT_STOP))

    # 4. 建执行记录
    execution = ex.create_execution(task_id, OPERATION, idempotency_key, data, environment)
    execution_id = execution["execution_id"]
    if not ex.acquire_lock(execution_id):
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("EXECUTOR_BUSY", "LOCK", "获取执行锁失败", NEXT_QUERY))
    ex.update_execution(execution_id, execution_state=ex.STATE_RUNNING)

    pw = None
    previous_values = {}
    updated_fields = []
    try:
        pw, browser, page = get_browser_page()
        page.bring_to_front()
        _set_stage_upd(execution_id, "正在登录")
        auth = ensure_login(page)
        if not auth["success"]:
            e = err("NOT_LOGGED_IN", "LOGIN", f"登录失败: {auth['message']}", NEXT_QUERY)
            ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)

        _cleanup_overlays(page)
        page.goto(BASE_URL_H5, wait_until="domcontentloaded"); page.wait_for_timeout(3000)
        try:
            page.get_by_text("确定", exact=True).first.click(timeout=2000); page.wait_for_timeout(400)
        except Exception:
            pass

        # reset + search
        try:
            page.get_by_text("重置", exact=True).first.click(timeout=5000); page.wait_for_timeout(1200)
        except Exception:
            pass
        try:
            page.get_by_text("搜 索", exact=False).first.click(); page.wait_for_timeout(2500)
        except Exception:
            pass

        # find app row
        _set_stage_upd(execution_id, "正在查找目标应用")
        rows = page.locator("table tbody tr")
        row = None
        for ri in range(rows.count()):
            try:
                if app_name in rows.nth(ri).inner_text():
                    row = rows.nth(ri); break
            except Exception:
                pass
        if row is None:
            e = err("APP_NOT_FOUND", "FIND", f"列表中未找到应用: {app_name}", NEXT_MANUAL)
            ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)
        _shot(page, "01_found_app")

        # check status → offline if needed
        _set_stage_upd(execution_id, "正在下线应用")
        sw = row.locator(".el-switch").first
        was_online = "is-checked" in (sw.get_attribute("class") or "")
        if was_online:
            print("[update] app is ONLINE, taking offline...")
            sw.click(); page.wait_for_timeout(1000)
            try:
                mb = page.locator(".el-message-box:visible")
                mb.wait_for(timeout=3000)
                mb.get_by_text("确定", exact=True).first.click()
                page.wait_for_timeout(2000)
                print("[update] confirmed offline")
            except Exception as e:
                print(f"[update] confirm offline err: {e}")
                try:
                    page.keyboard.press("Enter"); page.wait_for_timeout(1500)
                except Exception:
                    pass
            _shot(page, "02_offline")
        else:
            print("[update] app already OFFLINE")

        # click edit
        _set_stage_upd(execution_id, "正在打开编辑对话框")
        edit_btn = row.get_by_text("编辑", exact=True).first
        edit_cls = edit_btn.get_attribute("class") or ""
        if "disabled" in edit_cls or "is-disabled" in edit_cls:
            e = err("EDIT_BUTTON_DISABLED", "OFFLINE", "编辑按钮灰色，下线失败", NEXT_MANUAL)
            ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)
        edit_btn.click()
        page.wait_for_timeout(2500)
        _shot(page, "03_edit_dialog")

        # group fields by tab
        _set_stage_upd(execution_id, "正在修改字段")
        tab_fields = defaultdict(list)
        for key, value in fields_to_update.items():
            info = FIELD_MAP[key]
            tab_fields[info["tab"]].append((key, value, info))

        # process each tab
        for tab_name, field_list in tab_fields.items():
            _go_tab(page, tab_name)
            for key, value, info in field_list:
                label = info["label"]
                ftype = info["type"]
                fi = _find_form_item(page, label)
                if fi is None:
                    print(f"[update] WARN: field '{label}' not found in tab '{tab_name}', skipping")
                    updated_fields.append(f"{label}(skip:not found)")
                    continue

                # capture previous value
                old_val = _read_value(page, fi, ftype)
                previous_values[label] = old_val

                # apply new value
                try:
                    if ftype in ("input", "textarea"):
                        _set_input(fi, value)
                    elif ftype == "select":
                        _set_select(page, fi, value)
                    elif ftype == "switch":
                        _set_switch(fi, value)
                    elif ftype == "upload":
                        _set_upload(page, fi, value)
                    updated_fields.append(label)
                    print(f"[update] {tab_name}/{label}: {old_val!r} → {str(value)[:60]!r}")
                except Exception as fe:
                    e = err("UPDATE_VERIFY_FAILED", "EXECUTE",
                            f"修改字段 {label} 失败: {fe}", NEXT_MANUAL)
                    ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
                    return ex.build_result(task_id, OPERATION, idempotency_key,
                                          execution=ex.find_by_execution_id(execution_id),
                                          accepted=True, error=e, environment=environment)
            _shot(page, f"tab_{tab_name}")

        # save
        _set_stage_upd(execution_id, "正在保存修改")
        _go_tab(page, "悬浮球配置"); page.wait_for_timeout(600)
        page.locator(".el-dialog:visible").locator("button").filter(has_text="保存").first.click()
        page.wait_for_timeout(3500)
        _shot(page, "04_after_save")

        save_err = capture_page_errors(page, screenshot_name=f"upd_save_{app_name}")
        if save_err["dialog_open"]:
            e = err("SAVE_FAILED", "SAVE", build_error_message(save_err, "保存失败(对话框未关闭)"), NEXT_MANUAL)
            ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)

        # re-publish (if was online before)
        _set_stage_upd(execution_id, "正在重新上线应用")
        page.wait_for_timeout(1000)
        rows2 = page.locator("table tbody tr")
        final_status = "OFFLINE"
        for ri in range(rows2.count()):
            try:
                if app_name in rows2.nth(ri).inner_text():
                    sw2 = rows2.nth(ri).locator(".el-switch").first
                    if was_online:
                        if "is-checked" not in (sw2.get_attribute("class") or ""):
                            sw2.click(); page.wait_for_timeout(1500)
                            try:
                                mb2 = page.locator(".el-message-box:visible")
                                if mb2.count() > 0:
                                    page.keyboard.press("Enter"); page.wait_for_timeout(1500)
                            except Exception:
                                pass
                        sw3 = rows2.nth(ri).locator(".el-switch").first
                        final_status = "ONLINE" if "is-checked" in (sw3.get_attribute("class") or "") else "OFFLINE"
                    else:
                        final_status = "OFFLINE"
                    break
            except Exception:
                pass
        _shot(page, "05_final")

        output = {
            "app_name": app_name,
            "updated_fields": updated_fields,
            "final_status": final_status,
            "previous_values": previous_values,
            "row_data": {},
        }
        # 读保存后的整行数据
        try:
            rows3 = page.locator("table tbody tr")
            for ri in range(rows3.count()):
                if app_name in rows3.nth(ri).inner_text():
                    from core.row_reader import read_row_full
                    output["row_data"] = read_row_full(page, rows3.nth(ri))
                    break
        except Exception:
            pass
        ex.finalize_execution(execution_id, ex.BIZ_SUCCESS, output_data=output,
                             evidence_ref=f"{execution_id}/update-final.json")
        return ex.build_result(task_id, OPERATION, idempotency_key,
                              execution=ex.find_by_execution_id(execution_id),
                              accepted=True, data=output, environment=environment)

    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            err_info = capture_page_errors(page, screenshot_name=f"upd_exc_{app_name}")
            error = err("UPDATE_VERIFY_FAILED", "EXECUTE", str(e) + " | " + build_error_message(err_info), NEXT_QUERY)
        except Exception:
            error = err("UPDATE_VERIFY_FAILED", "EXECUTE", str(e), NEXT_QUERY)
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
