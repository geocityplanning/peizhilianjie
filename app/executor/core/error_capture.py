# -*- coding: utf-8 -*-
"""全链路错误捕获工具：从页面抓取所有错误提示、表单校验、弹窗文字 + 截图。"""
import time
from pathlib import Path
from typing import Optional


def capture_page_errors(page, screenshot_name: str = None, screenshot_dir: str = None) -> dict:
    """
    从当前页面抓取所有可见的错误信息 + 截图。

    返回:
      {
        "toasts": ["验证码错误", ...],         # el-message 浮层提示
        "message_box": "确定要下线该应用吗？",  # el-message-box 对话框文字
        "form_errors": ["应用名称不能为空", ...],# 表单校验红色错误
        "url": "https://...",                   # 当前 URL
        "dialog_open": True/False,              # 是否还有对话框开着（保存没成功）
        "screenshot": "output/xxx.png"          # 截图路径（如果提供了名字）
      }
    """
    info = {
        "toasts": [],
        "message_box": "",
        "form_errors": [],
        "url": "",
        "dialog_open": False,
        "screenshot": "",
    }
    try:
        info["url"] = page.url
    except Exception:
        pass

    # 1. el-message 浮层提示（绿色成功 / 红色错误 / 黄色警告）
    try:
        msgs = page.locator(".el-message")
        for i in range(msgs.count()):
            try:
                el = msgs.nth(i)
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if txt:
                        info["toasts"].append(txt)
            except Exception:
                pass
    except Exception:
        pass

    # 2. el-message-box 对话框
    try:
        mbs = page.locator(".el-message-box__wrapper")
        for i in range(mbs.count()):
            try:
                w = mbs.nth(i).get_attribute("style") or ""
                if "none" not in w:
                    txt = mbs.nth(i).locator(".el-message-box__message").first.inner_text().strip()
                    if txt:
                        info["message_box"] = txt
            except Exception:
                pass
    except Exception:
        pass

    # 3. 表单校验错误（红色 .el-form-item__error）
    try:
        errs = page.locator(".el-form-item__error")
        for i in range(errs.count()):
            try:
                el = errs.nth(i)
                if el.is_visible():
                    txt = el.inner_text().strip()
                    if txt:
                        info["form_errors"].append(txt)
            except Exception:
                pass
    except Exception:
        pass

    # 4. 对话框是否还开着（保存没成功 = 对话框没关）
    try:
        dlgs = page.locator(".el-dialog__wrapper")
        for i in range(dlgs.count()):
            w = dlgs.nth(i).get_attribute("style") or ""
            if "none" not in w:
                info["dialog_open"] = True
                break
    except Exception:
        pass

    # 5. 截图
    if screenshot_name:
        try:
            if screenshot_dir is None:
                screenshot_dir = str(Path(__file__).resolve().parent.parent / "output")
            Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
            path = str(Path(screenshot_dir) / f"err_{screenshot_name}.png")
            page.screenshot(path=path, timeout=8000)
            info["screenshot"] = path
        except Exception:
            pass

    return info


def build_error_message(err_info: dict, context: str = "") -> str:
    """把 capture_page_errors 的结果拼成一句人话。"""
    parts = []
    if context:
        parts.append(context)
    if err_info.get("toasts"):
        parts.append("页面提示: " + "; ".join(err_info["toasts"]))
    if err_info.get("message_box"):
        parts.append("弹窗提示: " + err_info["message_box"])
    if err_info.get("form_errors"):
        parts.append("表单校验错误: " + "; ".join(err_info["form_errors"]))
    if err_info.get("dialog_open"):
        parts.append("对话框仍开着(保存可能未生效)")
    if err_info.get("url"):
        parts.append(f"当前URL: {err_info['url']}")
    if not parts:
        parts.append("未捕获到明确错误信息")
    return " | ".join(parts)
