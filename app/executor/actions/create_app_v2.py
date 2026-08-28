# -*- coding: utf-8 -*-
"""
创建应用 — 契约版（v2，JS 直操 DOM，绕过 Playwright :visible 伪类）。
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
STEP_TIMEOUT = 5000

DIR = Path(__file__).resolve().parent.parent
OUT = DIR / "output"
OUT.mkdir(exist_ok=True)


def _shot(page, name):
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


# ====== JS 直操 DOM 工具函数（不依赖 Playwright :visible） ======

def _available_tabs(page):
    """用 JS 查可见对话框的 Tab 名称。"""
    try:
        return page.evaluate("""
        () => {
          const wrappers = document.querySelectorAll('.el-dialog__wrapper');
          for (const w of wrappers) {
            if (w.style.display === 'none') continue;
            const tabs = w.querySelectorAll('.el-tabs__item');
            if (tabs.length > 0) {
              return Array.from(tabs).map(t => t.innerText.trim());
            }
          }
          return [];
        }
        """)
    except Exception:
        return []


def _go_tab(page, name):
    """用 JS 点击目标 Tab。"""
    deadline = time.monotonic() + 6
    last_tabs = []
    while time.monotonic() < deadline:
        last_tabs = _available_tabs(page)
        if last_tabs:
            break
        page.wait_for_timeout(300)
    if not last_tabs:
        print(f"[create_app] WARN: 未找到Tab {name}，当前Tabs={last_tabs}")
        return False
    clicked = page.evaluate("""
    (tabName) => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const w of wrappers) {
        if (w.style.display === 'none') continue;
        const tabs = w.querySelectorAll('.el-tabs__item');
        for (const t of tabs) {
          if (t.innerText.trim().includes(tabName)) { t.click(); return true; }
        }
      }
      return false;
    }
    """, name)
    if clicked:
        page.wait_for_timeout(500)
        return True
    print(f"[create_app] WARN: Tab '{name}' 不在 {last_tabs} 中")
    return False


def _js_fill(page, label, value):
    """用 JS 找 label 对应的 input 并填值。"""
    return page.evaluate("""
    ({label, value}) => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const w of wrappers) {
        if (w.style.display === 'none') continue;
        const items = w.querySelectorAll('.el-form-item');
        for (const it of items) {
          if (it.offsetParent === null) continue;
          const lblEl = it.querySelector('.el-form-item__label');
          if (!lblEl) continue;
          const lbl = lblEl.innerText.replace(/[ *:]/g, '').trim();
          if (lbl === label) {
            const input = it.querySelector('input.el-input__inner');
            if (input) {
              const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
              setter.call(input, value);
              input.dispatchEvent(new Event('input', {bubbles: true}));
              input.dispatchEvent(new Event('change', {bubbles: true}));
              return true;
            }
          }
        }
      }
      return false;
    }
    """, {"label": label, "value": value})


def _js_select(page, label, value):
    """用 JS 点下拉框并选项。"""
    opened = page.evaluate("""
    (label) => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const w of wrappers) {
        if (w.style.display === 'none') continue;
        const items = w.querySelectorAll('.el-form-item');
        for (const it of items) {
          if (it.offsetParent === null) continue;
          const lblEl = it.querySelector('.el-form-item__label');
          if (!lblEl) continue;
          const lbl = lblEl.innerText.replace(/[ *:]/g, '').trim();
          if (lbl === label) {
            const sel = it.querySelector('.el-select');
            if (sel) { sel.click(); return true; }
          }
        }
      }
      return false;
    }
    """, label)
    if not opened:
        return False
    page.wait_for_timeout(500)
    picked = page.evaluate("""
    (value) => {
      const dropdowns = document.querySelectorAll('.el-select-dropdown');
      for (const dd of dropdowns) {
        if (dd.style.display === 'none') continue;
        const items = dd.querySelectorAll('.el-select-dropdown__item');
        for (const it of items) {
          if (it.innerText.trim().includes(value)) { it.click(); return true; }
        }
      }
      return false;
    }
    """, value)
    if not picked:
        page.keyboard.press("Escape")
    return picked


def _js_channel_popover(page, label, channel_name):
    """用 JS 处理渠道 popover 选择器（带 A-Z 字母索引的自定义组件）。"""
    # 1. 点 channel-input 打开 popover
    opened = page.evaluate("""
    (label) => {
      const wrappers = document.querySelectorAll('.el-dialog__wrapper');
      for (const w of wrappers) {
        if (w.style.display === 'none') continue;
        const items = w.querySelectorAll('.el-form-item');
        for (const it of items) {
          if (it.offsetParent === null) continue;
          const lblEl = it.querySelector('.el-form-item__label');
          if (!lblEl) continue;
          if (lblEl.innerText.includes(label)) {
            const ci = it.querySelector('.channel-input');
            if (ci) { ci.click(); return true; }
          }
        }
      }
      return false;
    }
    """, label)
    if not opened:
        return False
    page.wait_for_timeout(1500)

    # 2. 在 popover 里找渠道名并点击
    picked = page.evaluate("""
    (name) => {
      // 找所有可能的 popover 元素
      const pops = document.querySelectorAll('[id^="el-popover-"], .el-popover, .el-popper, .channel-popover');
      for (const p of pops) {
        if (p.offsetParent === null && !p.style.display) continue;
        if (p.style.display === 'none') continue;
        // 查找所有可能的渠道选项元素
        const candidates = p.querySelectorAll('li, td, span, div, a, p');
        for (const el of candidates) {
          if (el.offsetParent === null) continue;
          const t = el.innerText ? el.innerText.trim() : '';
          // 精确匹配渠道名（排除包含匹配，避免误点）
          if (t === name) {
            el.click();
            return true;
          }
        }
      }
      // 如果精确匹配没找到，试前缀匹配
      for (const p of pops) {
        if (p.style.display === 'none') continue;
        const candidates = p.querySelectorAll('li, td, span, div, a, p');
        for (const el of candidates) {
          if (el.offsetParent === null) continue;
          const t = el.innerText ? el.innerText.trim() : '';
          if (t.startsWith(name) || (name.startsWith(t) && t.length > 3)) {
            el.click();
            return true;
          }
        }
      }
      return false;
    }
    """, channel_name)
    if picked:
        page.wait_for_timeout(500)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    return picked


def _set_stage(execution_id, stage):
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

    existing = ex.find_by_idempotency(idempotency_key)
    if existing:
        return ex.build_result(task_id, OPERATION, idempotency_key,
                              execution=existing, accepted=True,
                              data=json.loads(existing.get("output_json") or "{}"),
                              error=json.loads(existing.get("error_json") or "null") if existing.get("error_json") else None,
                              environment=existing.get("environment"))

    if ex.is_locked():
        active = ex.get_active_execution_id()
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("EXECUTOR_BUSY", "LOCK", f"执行端忙碌中，占用执行: {active}", NEXT_QUERY))

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
        _set_stage(execution_id, "正在登录")
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
        try:
            page.wait_for_selector("table tbody tr", timeout=STEP_TIMEOUT)
        except Exception:
            page.wait_for_timeout(2000)
        try:
            page.get_by_text("确定", exact=True).first.click(timeout=2000)
        except Exception:
            pass
        _shot(page, "01_list")

        # reset + search
        try:
            page.get_by_text("重置", exact=True).first.click(timeout=STEP_TIMEOUT)
            page.wait_for_timeout(500)
        except Exception:
            pass
        try:
            page.get_by_text("搜", exact=False).first.click(timeout=STEP_TIMEOUT)
            try:
                page.wait_for_selector("table tbody tr", timeout=STEP_TIMEOUT)
            except Exception:
                page.wait_for_timeout(500)
        except Exception:
            pass

        # copy template
        _set_stage(execution_id, "正在复制模板应用")
        copy_btns = page.get_by_text("复制", exact=True)
        n_btns = copy_btns.count()
        print(f"[create_app] 找到 {n_btns} 个复制按钮")
        if n_btns > 0:
            try:
                copy_btns.first.click(timeout=STEP_TIMEOUT)
                try:
                    page.wait_for_selector(".el-dialog__wrapper:not([style*='display: none']) .el-tabs__item", timeout=STEP_TIMEOUT)
                except Exception:
                    page.wait_for_timeout(1000)
            except Exception as e:
                print(f"[create_app] WARN: 复制按钮点击失败: {e}")
        _shot(page, "02_copy_dialog")

        # === 体验配置 ===
        _set_stage(execution_id, "正在填写体验配置")
        if not _go_tab(page, "体验配置"):
            raise RuntimeError(f"无法切换到体验配置Tab，当前可见Tabs={_available_tabs(page)}")
        if not _js_fill(page, "应用名称", app_name):
            print("[create_app] WARN: 应用名称 填写失败")
        if not _js_select(page, "底座", DEFAULT_BASE):
            print("[create_app] WARN: 底座 选择失败")
        if not _js_channel_popover(page, "所属渠道", data["actual_channel_name"]):
            print(f"[create_app] WARN: 所属渠道 选择失败: {data['actual_channel_name']}")
        _shot(page, "03_exp_config")

        # === 登录页配置 ===
        _set_stage(execution_id, "正在填写登录页配置")
        if not _go_tab(page, "登录页配置"):
            raise RuntimeError(f"无法切换到登录页配置Tab，当前可见Tabs={_available_tabs(page)}")
        page.evaluate("""
        (jumpAddr) => {
          const wrappers = document.querySelectorAll('.el-dialog__wrapper');
          for (const w of wrappers) {
            if (w.style.display === 'none') continue;
            const items = w.querySelectorAll('.el-form-item');
            for (const it of items) {
              if (it.offsetParent === null) continue;
              if (!it.className.includes('is-required')) continue;
              const lblEl = it.querySelector('.el-form-item__label');
              if (!lblEl || !lblEl.innerText.includes('配置调起路径')) continue;
              const input = it.querySelector('input.el-input__inner');
              if (input) {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(input, jumpAddr);
                input.dispatchEvent(new Event('input', {bubbles: true}));
                return true;
              }
            }
          }
          return false;
        }
        """, data["jump_address"])
        _shot(page, "04_login_config")

        # === 基础配置 ===
        _set_stage(execution_id, "正在填写基础配置")
        if not _go_tab(page, "基础配置"):
            raise RuntimeError(f"无法切换到基础配置Tab，当前可见Tabs={_available_tabs(page)}")
        if not _js_fill(page, "资源不足中间页链接", data["resource_fallback_page"]):
            print("[create_app] WARN: 资源不足中间页链接 填写失败")
        if not _js_select(page, "结算类型", data["settlement_type"]):
            print(f"[create_app] WARN: 结算类型 选择失败: {data['settlement_type']}")
        _shot(page, "05_basic_config")

        # === save ===
        _set_stage(execution_id, "正在保存应用")
        if not _go_tab(page, "悬浮球配置"):
            raise RuntimeError(f"无法切换到悬浮球配置Tab，当前可见Tabs={_available_tabs(page)}")
        # 关掉子弹窗
        page.evaluate("""
        () => {
          const wrappers = document.querySelectorAll('.el-dialog__wrapper');
          for (const w of wrappers) {
            if (w.style.display === 'none') continue;
            if (w.querySelectorAll('.el-tabs__item').length === 0) {
              const closeBtn = w.querySelector('.el-dialog__headerbtn');
              if (closeBtn) closeBtn.click();
              return;
            }
          }
        }
        """)
        page.wait_for_timeout(300)
        # 用 JS 点主对话框保存按钮
        saved = page.evaluate("""
        () => {
          const wrappers = document.querySelectorAll('.el-dialog__wrapper');
          for (const w of wrappers) {
            if (w.style.display === 'none') continue;
            if (w.querySelectorAll('.el-tabs__item').length === 0) continue;
            const btns = w.querySelectorAll('button');
            let saveBtn = null;
            for (const b of btns) {
              if (b.innerText.includes('保存')) saveBtn = b;
            }
            if (saveBtn) { saveBtn.click(); return true; }
          }
          return false;
        }
        """)
        if not saved:
            raise RuntimeError("未找到主对话框的保存按钮")
        # 等对话框消失
        try:
            page.wait_for_selector(".el-dialog__wrapper:not([style*='display: none'])", state="detached", timeout=8000)
        except Exception:
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
                try:
                    page.wait_for_selector(".el-message-box:visible", timeout=3000)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(500)
                except Exception:
                    page.wait_for_timeout(500)
            except Exception as e:
                print(f"[create_app] WARN: 状态开关点击失败: {e}")
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
                    page.wait_for_selector(".el-dialog__wrapper:not([style*='display: none'])", timeout=STEP_TIMEOUT)
                except Exception:
                    page.wait_for_timeout(500)
                # 用 JS 操作分组设置
                page.evaluate("""
                (groupName) => {
                  const wrappers = document.querySelectorAll('.el-dialog__wrapper');
                  for (const w of wrappers) {
                    if (w.style.display === 'none') continue;
                    if (w.querySelectorAll('.el-tabs__item').length > 0) continue;
                    // 这是子弹窗
                    const radios = w.querySelectorAll('.el-radio, .el-radio-button, span');
                    for (const r of radios) {
                      if (r.innerText && r.innerText.includes('按分组')) { r.click(); break; }
                    }
                    const inputs = w.querySelectorAll('input.el-input__inner');
                    if (inputs.length > 0) {
                      const last = inputs[inputs.length - 1];
                      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                      setter.call(last, groupName);
                      last.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                    const btns = w.querySelectorAll('button');
                    for (const b of btns) {
                      if (b.innerText.includes('确定')) { b.click(); return true; }
                    }
                  }
                  return false;
                }
                """, group_name)
                page.wait_for_timeout(2000)
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
