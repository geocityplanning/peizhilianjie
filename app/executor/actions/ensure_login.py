# -*- coding: utf-8 -*-
"""CDP 版自动登录：检测 session 失效 → 填账密 → OCR 识别验证码 → 登录 → 失败重试。

在已连接的 CDP 浏览器页面上执行（复用已打开的 Edge，不另起浏览器）。
凭据从 config.json 读取（密码 Fernet 加密存储）。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import get_browser_page
from core.security import PasswordDecryptError, decrypt_password
from core.error_capture import capture_page_errors, build_error_message
from ocr import CaptchaRecognizer

DIR = Path(__file__).resolve().parent.parent


class LoginConfigError(RuntimeError):
    """config.json 存在但无法使用。消息不得包含凭据。"""


def _read_login_config(path=None):
    config_path = Path(path) if path is not None else DIR / "config.json"
    if not config_path.is_file():
        return {}
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise LoginConfigError("无法读取登录配置") from None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        raise LoginConfigError("登录配置不是合法 JSON") from None
    login = loaded.get("login") if isinstance(loaded, dict) else None
    if not isinstance(login, dict):
        raise LoginConfigError("登录配置缺少 login 对象")
    return login


CONFIG = _read_login_config()
LOGIN_URL = CONFIG.get("url") or ""


def _dismiss_403_dialog(page):
    """关掉 403 错误弹窗（el-message-box）。"""
    try:
        mbs = page.locator(".el-message-box__wrapper")
        for i in range(mbs.count()):
            w = mbs.nth(i).get_attribute("style") or ""
            if "none" not in w:
                mbs.nth(i).get_by_text("确定", exact=True).first.click(timeout=3000)
                page.wait_for_timeout(400)
                return True
    except Exception:
        pass
    return False


def _check_session_heartbeat(page) -> bool:
    """有 token 时发一个轻量 API 探测，403 说明 session 已过期。
    返回 True = session 有效，False = 需要重新登录。"""
    try:
        token = page.evaluate("() => window.sessionStorage.getItem('token')")
        if not token:
            return False
        # 用一个轻量 API 探测（渠道列表 pageSize=1）
        from core import make_headers, api_post
        r = api_post(page, "/backend/cloudTrial/channel/getList", {
            "channelIdList": [], "basePlatform": "", "creator": "",
            "pageNum": 1, "pageSize": 1, "startTime": None, "endTime": None,
        })
        status = (r.get("json", {}).get("header", {}) or {}).get("status", "")
        if status == "200":
            return True
        else:
            print(f"[login] session heartbeat failed: status={status}")
            return False
    except Exception as e:
        print(f"[login] heartbeat error: {e}")
        return False


def is_logged_in(page) -> bool:
    """session 是否有效：有 token 且心跳探测通过。
    如果 token 存在但 403，清掉 token 返回 False。"""
    try:
        token = page.evaluate("() => window.sessionStorage.getItem('token')")
        if not token:
            return False
        # 有 token，检查是否真的有效
        # 先关掉可能存在的 403 弹窗
        _dismiss_403_dialog(page)
        if _check_session_heartbeat(page):
            return True
        else:
            # token 过期了，清掉
            page.evaluate("() => window.sessionStorage.clear()")
            print("[login] token expired (403), cleared session")
            return False
    except Exception:
        pass
    return "/login" not in (page.url or "")


def _get_captcha_bytes(page) -> bytes:
    el = page.locator(CONFIG["captcha_image_xpath"])
    el.wait_for(state="visible", timeout=5000)
    return el.screenshot()


def _dismiss_message_box(page):
    """关掉挡住页面的 el-message-box 提示弹窗。"""
    try:
        mb = page.locator(".el-message-box__wrapper")
        for i in range(mb.count()):
            w = mb.nth(i).get_attribute("style") or ""
            if "none" not in w:
                mb.nth(i).get_by_text("确定", exact=True).first.click(timeout=2000)
                page.wait_for_timeout(400)
                return
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass


def _is_captcha_error(page) -> bool:
    """提交后判断是否验证码错误（区分于账号密码错误）。"""
    try:
        page.wait_for_timeout(400)
        for sel in [".el-message:visible", ".el-message-box:visible"]:
            loc = page.locator(sel)
            if loc.count() > 0:
                txt = loc.last.inner_text().strip()
                for kw in ["验证码", "captcha", "验证码错误", "验证码不正确"]:
                    if kw in txt.lower() or kw in txt:
                        return True
        # 兜底：整页文本
        body = page.evaluate("() => document.body.innerText") or ""
        for kw in ["验证码错误", "验证码不正确"]:
            if kw in body:
                return True
    except Exception:
        pass
    return False


def _dismiss_save_password_popup(page):
    """关掉浏览器自带的"保存密码"弹窗（Chrome/Edge）。"""
    # 方式1：浏览器原生弹窗（Windows 系统级，Playwright 拦不到，用 Escape 关）
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass
    # 方式2：页面内弹窗（有些网站会弹 el-message-box 问要不要保存）
    try:
        mbs = page.locator(".el-message-box__wrapper")
        for i in range(mbs.count()):
            w = mbs.nth(i).get_attribute("style") or ""
            if "none" not in w:
                txt = mbs.nth(i).inner_text()
                if "保存" in txt or "密码" in txt or "save" in txt.lower():
                    # 找"永不"或"取消"或"以后再说"
                    for btn_text in ["永不", "以后再说", "取消", "Never", "Not now", "Close"]:
                        btn = mbs.nth(i).get_by_text(btn_text, exact=True).first
                        if btn.count() > 0:
                            btn.click(timeout=2000)
                            page.wait_for_timeout(400)
                            return
                    # 兜底点确定
                    mbs.nth(i).get_by_text("确定", exact=True).first.click(timeout=2000)
                    page.wait_for_timeout(400)
                    return
    except Exception:
        pass


def do_login(page, username: str, password: str, max_captcha_retry: int = 5) -> dict:
    """在给定 page 上执行一次完整登录（page 应已停在 /login）。"""
    ocr = CaptchaRecognizer()
    result = {"success": False, "message": "", "captcha_tries": 0}
    try:
        page.bring_to_front()
        # 清掉残留弹窗 + 强制刷新到登录页（避免 403 残留干扰）
        _dismiss_403_dialog(page)
        _dismiss_message_box(page)
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.wait_for_selector(CONFIG["captcha_image_xpath"], timeout=5000)
        # 再清一次（刷新后可能还有残留弹窗）
        _dismiss_403_dialog(page)
        _dismiss_message_box(page)

        for attempt in range(1, max_captcha_retry + 1):
            result["captcha_tries"] = attempt
            _dismiss_message_box(page)  # 清理上一轮残留弹窗
            # 填用户名/密码（每次重填以防被清空）
            page.fill(CONFIG["username_xpath"], username)
            time.sleep(0.1)
            page.fill(CONFIG["password_xpath"], password)
            time.sleep(0.1)
            # 识别验证码
            cb = _get_captcha_bytes(page)
            rec = ocr.recognize(cb, preprocess=False)  # 不预处理效果最好
            code = rec.get("text", "").strip()
            if not code:
                # 退回带重试
                code = ocr.recognize_with_retry(cb).get("text", "").strip()
            if not code:
                print(f"[login] 验证码识别为空，重试 {attempt}/{max_captcha_retry}")
                page.click(CONFIG["captcha_image_xpath"])
                time.sleep(0.4)
                continue
            page.fill(CONFIG["captcha_input_xpath"], code)
            time.sleep(0.1)
            page.click(CONFIG["login_button_xpath"])

            # 等待结果：成功(URL变/welcame) 或 错误提示
            try:
                page.wait_for_url(f"**{CONFIG['success_url_contains']}**", timeout=8000)
                result["success"] = True
                result["message"] = "登录成功"
                # 登录成功后关掉"保存密码"弹窗（Chrome/Edge 自动弹）
                time.sleep(0.4)
                _dismiss_save_password_popup(page)
                print(f"[login] 登录成功（第 {attempt} 次验证码）")
                return result
            except Exception:
                pass
            # 未跳转 -> 判断错误类型
            if _is_captcha_error(page):
                print(f"[login] 验证码错误，重试 {attempt}/{max_captcha_retry}")
                _dismiss_message_box(page)
                page.click(CONFIG["captcha_image_xpath"])
                time.sleep(0.4)
                continue
            # 非验证码错误（多半是账号密码错误）
            _dismiss_message_box(page)
            err_info = capture_page_errors(page, screenshot_name=f"login_fail_{attempt}")
            msg = build_error_message(err_info, "登录失败")
            result["message"] = msg
            result["error_detail"] = err_info
            print(f"[login] {result['message']}")
            return result

        err_info = capture_page_errors(page, screenshot_name="login_max_retry")
        result["message"] = build_error_message(err_info, f"验证码重试 {max_captcha_retry} 次仍未成功")
        result["error_detail"] = err_info
        return result
    except Exception as e:
        try:
            err_info = capture_page_errors(page, screenshot_name="login_exception")
            result["error_detail"] = err_info
            result["message"] = f"登录异常: {e} | {build_error_message(err_info)}"
        except Exception:
            result["message"] = f"登录异常: {e}"
        print(f"[login] {result['message']}")
        return result


def ensure_login(page=None) -> dict:
    """确保已登录；未登录则自动登录。返回 {success, message, already_logged_in}。"""
    pw = browser = None
    own = False
    try:
        if page is None:
            pw, browser, page = get_browser_page()
            own = True
        if is_logged_in(page):
            return {"success": True, "message": "已登录", "already_logged_in": True}
        username = CONFIG.get("username", "")
        enc = CONFIG.get("password_encrypted", "")
        if not username or not enc:
            return {
                "success": False,
                "message": "config.json 未配置凭据（username / password_encrypted）",
                "already_logged_in": False,
            }
        try:
            password = decrypt_password(enc)
        except PasswordDecryptError:
            return {
                "success": False,
                "message": "密码解密失败",
                "already_logged_in": False,
            }
        if not password:
            return {
                "success": False,
                "message": "config.json 未配置凭据（username / password_encrypted）",
                "already_logged_in": False,
            }
        r = do_login(page, username, password)
        r["already_logged_in"] = False
        return r
    finally:
        if own and pw:
            try:
                pw.stop()
            except Exception:
                pass


if __name__ == "__main__":
    # 用法：python actions\ensure_login.py [encrypt 你的密码]
    if len(sys.argv) >= 3 and sys.argv[1] == "encrypt":
        from core.security import encrypt_password
        print("password_encrypted =")
        print(encrypt_password(sys.argv[2]))
    else:
        print(json.dumps(ensure_login(), ensure_ascii=False, indent=2))

