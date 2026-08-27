# -*- coding: utf-8 -*-
"""
定位已存在应用 — 契约版。

入口 execute_locate_app(request) -> ExecutionResult
用于历史手动创建的应用，通过渠道名前缀匹配 + 应用名消歧义定位到唯一应用。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import get_browser_page
from core.error_capture import capture_page_errors, build_error_message
from core.error_codes import err, NEXT_STOP, NEXT_MANUAL
from core import executor as ex
from actions.ensure_login import ensure_login

OPERATION = "LOCATE_APP"
BASE_URL_H5 = "https://plus.buy.139.com/cloudappadmin/#/cloudAppManager"
DIR = Path(__file__).resolve().parent.parent
OUT = DIR / "output"
OUT.mkdir(exist_ok=True)


def execute_locate_app(request: dict) -> dict:
    task_id = request["task_id"]
    data = request["data"]
    environment = request.get("environment", "TEST")
    idempotency_key = request["idempotency_key"]

    # 1. 幂等（locate 是只读，也走幂等以便查回）
    existing = ex.find_by_idempotency(idempotency_key)
    if existing:
        return ex.build_result(task_id, OPERATION, idempotency_key,
                              execution=existing, accepted=True,
                              data=json.loads(existing.get("output_json") or "{}"),
                              error=json.loads(existing.get("error_json") or "null") if existing.get("error_json") else None,
                              environment=existing.get("environment"))

    # 2. 锁（只读操作也上锁，避免和写操作冲突）
    if ex.is_locked():
        active = ex.get_active_execution_id()
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("EXECUTOR_BUSY", "LOCK", f"执行端忙碌中，占用执行: {active}", "QUERY"))

    # 3. 字段校验
    channel_name = (data.get("channel_name") or "").strip()
    app_name = (data.get("app_name") or "").strip()
    if not channel_name and not app_name:
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("FIELD_VALIDATION", "VALIDATE", "channel_name 和 app_name 至少提供一个", "STOP"))

    # 4. 建执行记录
    execution = ex.create_execution(task_id, OPERATION, idempotency_key, data, environment)
    execution_id = execution["execution_id"]
    if not ex.acquire_lock(execution_id):
        return ex.build_result(task_id, OPERATION, idempotency_key, accepted=False,
                              error=err("EXECUTOR_BUSY", "LOCK", "获取执行锁失败", "QUERY"))
    ex.update_execution(execution_id, execution_state=ex.STATE_RUNNING)

    pw = None
    try:
        pw, browser, page = get_browser_page()
        page.bring_to_front()
        auth = ensure_login(page)
        if not auth["success"]:
            e = err("NOT_LOGGED_IN", "LOGIN", f"登录失败: {auth['message']}", "QUERY")
            ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)

        # 进入 H5 管理页
        page.goto(BASE_URL_H5, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        try:
            page.get_by_text("确定", exact=True).first.click(timeout=2000)
            page.wait_for_timeout(400)
        except Exception:
            pass

        # 重置 + 搜索全部
        try:
            page.get_by_text("重置", exact=True).first.click(timeout=5000)
            page.wait_for_timeout(1200)
        except Exception:
            pass
        try:
            page.get_by_text("搜 索", exact=False).first.click()
            page.wait_for_timeout(2500)
        except Exception:
            pass

        # 收集所有页的应用记录
        candidates = []
        page_num = 1
        while True:
            rows = page.locator("table tbody tr")
            for ri in range(rows.count()):
                try:
                    cells = rows.nth(ri).locator("td")
                    ncells = cells.count()
                    if ncells < 15:
                        continue
                    # 全量读取整行
                    from core.row_reader import read_row_full
                    row_data = read_row_full(page, rows.nth(ri))

                    candidates.append({
                        "row_index": ri,
                        **row_data,
                    })
                except Exception:
                    pass

            # 翻页：找"下一页"按钮
            next_btn = page.locator(".el-pagination .btn-next")
            if next_btn.count() > 0:
                btn_cls = next_btn.first.get_attribute("class") or ""
                btn_disabled = next_btn.first.get_attribute("disabled") or ""
                if "disabled" in btn_cls or btn_disabled:
                    break  # 没有下一页了
                try:
                    next_btn.first.click(timeout=3000)
                except Exception:
                    break  # 点击失败也跳出
                page.wait_for_timeout(2000)
                page_num += 1
                if page_num > 50:
                    break
            else:
                break

        print(f"[locate] scanned {page_num} pages, {len(candidates)} apps total")

        # ---- 匹配逻辑 ----
        matches = []
        for c in candidates:
            score = 0
            reasons = []

            row_channel = c.get("所属渠道", "")
            row_app_name = c.get("应用名称", "")

            # 1. 渠道名前缀匹配
            if channel_name:
                if row_channel == channel_name:
                    score += 100
                    reasons.append("渠道名完全匹配")
                elif row_channel.startswith(channel_name):
                    score += 80
                    reasons.append(f"渠道名前缀匹配({row_channel})")
                elif channel_name in row_channel:
                    score += 50
                    reasons.append(f"渠道名包含匹配({row_channel})")

            # 2. 应用名匹配
            if app_name:
                if row_app_name == app_name:
                    score += 100
                    reasons.append("应用名完全匹配")
                elif row_app_name.startswith(app_name):
                    score += 80
                    reasons.append(f"应用名前缀匹配({row_app_name})")
                elif app_name in row_app_name:
                    score += 50
                    reasons.append(f"应用名包含匹配({row_app_name})")

            if score > 0:
                c["score"] = score
                c["match_reasons"] = reasons
                matches.append(c)

        # 按分数排序
        matches.sort(key=lambda x: x["score"], reverse=True)

        if len(matches) == 0:
            e = err("APP_NOT_FOUND", "MATCH",
                    f"未找到匹配应用: channel={channel_name}, app={app_name}", "MANUAL_CHECK")
            ex.finalize_execution(execution_id, ex.BIZ_FAILED, error=e)
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, error=e, environment=environment)

        if len(matches) == 1:
            m = matches[0]
            output = {
                "cloud_app_link": m.get("长连接", ""),
                "cloud_app_short_link": m.get("应用链接", ""),
                "match_confidence": "HIGH" if m["score"] >= 150 else "MEDIUM",
                "match_reasons": m["match_reasons"],
                "candidates_total": len(candidates),
                "row_data": m,
            }
            ex.finalize_execution(execution_id, ex.BIZ_SUCCESS, output_data=output)
            print(f"[locate] UNIQUE MATCH: {m.get('应用名称','')} | {m.get('所属渠道','')} | score={m['score']}")
            return ex.build_result(task_id, OPERATION, idempotency_key,
                                  execution=ex.find_by_execution_id(execution_id),
                                  accepted=True, data=output, environment=environment)

        # 多条匹配：取前 5 条返回，让调用方消歧义
        top = matches[:5]
        output = {
            "match_confidence": "AMBIGUOUS",
            "candidates_total": len(candidates),
            "matches": [{
                "cloud_app_link": m.get("长连接", ""),
                "cloud_app_short_link": m.get("应用链接", ""),
                "row_data": m,
                "score": m["score"],
                "match_reasons": m["match_reasons"],
            } for m in top],
            "match_count": len(matches),
        }
        e = err("APP_AMBIGUOUS", "MATCH",
                f"匹配到 {len(matches)} 条记录，需人工确认: " + "; ".join([f"{m.get('应用名称','')}({m.get('所属渠道','')})" for m in top[:3]]),
                "MANUAL_CHECK")
        ex.finalize_execution(execution_id, ex.BIZ_UNKNOWN, output_data=output, error=e)
        print(f"[locate] AMBIGUOUS: {len(matches)} matches, top scores: {[m['score'] for m in top]}")
        return ex.build_result(task_id, OPERATION, idempotency_key,
                              execution=ex.find_by_execution_id(execution_id),
                              accepted=True, data=output, error=e, environment=environment)

    except Exception as e:
        try:
            err_info = capture_page_errors(page, screenshot_name=f"locate_exc")
            error = err("LOCATE_FAILED", "EXECUTE", str(e) + " | " + build_error_message(err_info), "QUERY")
        except Exception:
            error = err("LOCATE_FAILED", "EXECUTE", str(e), "QUERY")
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
