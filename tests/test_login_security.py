# -*- coding: utf-8 -*-
"""Offline tests for project-root env loading and fail-fast password decrypt.

These tests never read real credentials, never print secrets, and never connect to UAT.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.http_executor import settings as http_settings
from app.http_executor.settings import load_project_env, load_settings


ROOT = Path(__file__).resolve().parents[1]
SECURITY_PATH = ROOT / "app" / "executor" / "core" / "security.py"
ENSURE_LOGIN_PATH = ROOT / "app" / "executor" / "actions" / "ensure_login.py"


def _load_security():
    spec = importlib.util.spec_from_file_location("executor_security_under_test", SECURITY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def restore_modules():
    before = dict(sys.modules)
    yield
    for name in ("core", "core.security", "core.error_capture", "ocr", "ensure_login_under_test"):
        previous = before.get(name)
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _load_ensure_login(security_mod):
    core = types.ModuleType("core")
    core.get_browser_page = lambda: (None, None, object())
    error_capture = types.ModuleType("core.error_capture")
    error_capture.capture_page_errors = lambda *args, **kwargs: {}
    error_capture.build_error_message = lambda *args, **kwargs: "err"
    ocr = types.ModuleType("ocr")
    ocr.CaptchaRecognizer = object
    sys.modules["core"] = core
    sys.modules["core.security"] = security_mod
    sys.modules["core.error_capture"] = error_capture
    sys.modules["ocr"] = ocr
    spec = importlib.util.spec_from_file_location("ensure_login_under_test", ENSURE_LOGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_decrypt_succeeds_with_env_key(monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv("AMOO_SECRET_KEY", key.decode())
    plaintext = "unit-test-password"
    token = Fernet(key).encrypt(plaintext.encode()).decode()
    security = _load_security()
    assert security.decrypt_password(token) == plaintext


def test_decrypt_wrong_key_fails_without_leaking(monkeypatch):
    key_a = Fernet.generate_key()
    key_b = Fernet.generate_key()
    monkeypatch.setenv("AMOO_SECRET_KEY", key_b.decode())
    plaintext = "unit-test-secret-plain"
    token = Fernet(key_a).encrypt(plaintext.encode()).decode()
    security = _load_security()
    with pytest.raises(security.PasswordDecryptError) as caught:
        security.decrypt_password(token)
    message = str(caught.value)
    assert message == "密码解密失败"
    assert token not in message
    assert plaintext not in message
    assert key_a.decode() not in message
    assert key_b.decode() not in message
    assert "AMOO_SECRET_KEY" not in message
    assert "gAAAA" not in message


def test_decrypt_does_not_return_ciphertext_on_failure(monkeypatch):
    key_a = Fernet.generate_key()
    key_b = Fernet.generate_key()
    monkeypatch.setenv("AMOO_SECRET_KEY", key_b.decode())
    token = Fernet(key_a).encrypt(b"unit-test-plain").decode()
    security = _load_security()
    with pytest.raises(security.PasswordDecryptError):
        security.decrypt_password(token)


def test_secret_key_path_is_anchored_to_project_root_not_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AMOO_SECRET_KEY", raising=False)
    security = _load_security()
    expected = ROOT / "data" / ".secret_key"
    assert security.secret_key_path() == expected
    assert security.secret_key_path() != tmp_path / "data" / ".secret_key"
    assert not (tmp_path / "data" / ".secret_key").exists()


def test_load_project_env_reads_key_from_explicit_root(monkeypatch, tmp_path):
    monkeypatch.delenv("AMOO_SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text("AMOO_SECRET_KEY=unit-test-env-key\n", encoding="utf-8")
    security = _load_security()
    assert security.load_project_env(tmp_path) == tmp_path
    assert os.environ.get("AMOO_SECRET_KEY") == "unit-test-env-key"


def test_load_project_env_does_not_override_existing(monkeypatch, tmp_path):
    monkeypatch.setenv("AMOO_SECRET_KEY", "already-set-unit-test")
    (tmp_path / ".env").write_text("AMOO_SECRET_KEY=from-file-should-not-win\n", encoding="utf-8")
    security = _load_security()
    security.load_project_env(tmp_path)
    assert os.environ.get("AMOO_SECRET_KEY") == "already-set-unit-test"


def test_load_project_env_default_root_ignores_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("AMOO_SECRET_KEY=cwd-must-not-load\n", encoding="utf-8")
    monkeypatch.delenv("AMOO_SECRET_KEY", raising=False)
    security = _load_security()
    assert security._project_root() == ROOT
    assert security.load_project_env() == ROOT
    assert os.environ.get("AMOO_SECRET_KEY") != "cwd-must-not-load"


def test_settings_load_project_env_from_explicit_root(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_EXECUTOR_AUTO_LOGIN", raising=False)
    (tmp_path / ".env").write_text("HERMES_EXECUTOR_AUTO_LOGIN=true\n", encoding="utf-8")
    assert load_project_env(tmp_path) == tmp_path
    assert os.environ.get("HERMES_EXECUTOR_AUTO_LOGIN") == "true"


def test_load_settings_reads_auto_login_after_env_load(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_EXECUTOR_AUTO_LOGIN", raising=False)
    monkeypatch.delenv("HERMES_EXECUTOR_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_EXECUTOR_ENV", raising=False)
    monkeypatch.delenv("HERMES_EXECUTOR_FAKE_MODE", raising=False)
    monkeypatch.delenv("HERMES_EXECUTOR_DB", raising=False)
    monkeypatch.setattr(http_settings, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "HERMES_EXECUTOR_TOKEN=unit-test-token\nHERMES_EXECUTOR_AUTO_LOGIN=true\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.auth_token == "unit-test-token"
    assert settings.auto_login is True


def test_ensure_login_rejects_when_env_key_missing(restore_modules, monkeypatch):
    key = Fernet.generate_key()
    token = Fernet(key).encrypt(b"unit-test-password").decode()
    monkeypatch.delenv("AMOO_SECRET_KEY", raising=False)
    security = _load_security()
    login = _load_ensure_login(security)
    monkeypatch.setattr(
        login,
        "CONFIG",
        {"username": "unit-test-user", "password_encrypted": token},
    )
    def reject_decrypt(ciphertext):
        raise security.PasswordDecryptError("密码解密失败")

    monkeypatch.setattr(login, "decrypt_password", reject_decrypt)
    called = []
    monkeypatch.setattr(login, "is_logged_in", lambda page: False)
    monkeypatch.setattr(
        login,
        "do_login",
        lambda *args, **kwargs: called.append(args) or {"success": True, "message": "should-not-run"},
    )
    result = login.ensure_login(page=object())
    assert result["success"] is False
    assert result["already_logged_in"] is False
    assert result["message"] == "密码解密失败"
    assert called == []
    serialized = json.dumps(result, ensure_ascii=False)
    assert token not in serialized
    assert "unit-test-password" not in serialized
    assert key.decode() not in serialized


def test_ensure_login_uses_decrypted_password_after_env_load(restore_modules, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv("AMOO_SECRET_KEY", key.decode())
    plaintext = "unit-test-password"
    token = Fernet(key).encrypt(plaintext.encode()).decode()
    security = _load_security()
    login = _load_ensure_login(security)
    monkeypatch.setattr(
        login,
        "CONFIG",
        {"username": "unit-test-user", "password_encrypted": token},
    )
    captured = {}
    monkeypatch.setattr(login, "is_logged_in", lambda page: False)
    monkeypatch.setattr(
        login,
        "do_login",
        lambda page, username, password, **kwargs: captured.update(
            username=username, password=password
        )
        or {"success": True, "message": "登录成功"},
    )
    result = login.ensure_login(page=object())
    assert result["success"] is True
    assert captured["username"] == "unit-test-user"
    assert captured["password"] == plaintext
    assert captured["password"] != token
    serialized = json.dumps({"result": result, "username": captured["username"]}, ensure_ascii=False)
    assert token not in serialized
    assert plaintext not in serialized


def test_encrypt_invalid_key_fails_without_returning_plaintext(monkeypatch):
    monkeypatch.setenv("AMOO_SECRET_KEY", "not-a-fernet-key")
    plaintext = "unit-test-plain-to-encrypt"
    security = _load_security()
    with pytest.raises(security.PasswordEncryptError) as caught:
        security.encrypt_password(plaintext)
    message = str(caught.value)
    assert message == "密码加密失败"
    assert plaintext not in message
    assert "not-a-fernet-key" not in message
    assert "AMOO_SECRET_KEY" not in message


def test_read_login_config_missing_file_is_empty(restore_modules, tmp_path):
    login = _load_ensure_login(_load_security())
    assert login._read_login_config(tmp_path / "missing-config.json") == {}


def test_read_login_config_invalid_json_fails_without_leaking(restore_modules, tmp_path):
    secret = "unit-test-password-in-json"
    path = tmp_path / "config.json"
    path.write_text("{not-json " + secret, encoding="utf-8")
    login = _load_ensure_login(_load_security())
    with pytest.raises(login.LoginConfigError) as caught:
        login._read_login_config(path)
    message = str(caught.value)
    assert message == "登录配置不是合法 JSON"
    assert secret not in message
    assert "未配置" not in message


def test_read_login_config_unreadable_file_fails_without_leaking(restore_modules, tmp_path, monkeypatch):
    secret = "unit-test-password-in-config"
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"login": {"password_encrypted": secret}}), encoding="utf-8")
    login = _load_ensure_login(_load_security())
    original = Path.read_text

    def boom(self, *args, **kwargs):
        if Path(self).resolve() == path.resolve():
            raise PermissionError("denied " + secret)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(login.LoginConfigError) as caught:
        login._read_login_config(path)
    message = str(caught.value)
    assert message == "无法读取登录配置"
    assert secret not in message
    assert "未配置" not in message


def test_load_project_env_dotenv_error_is_fail_fast(monkeypatch, tmp_path):
    secret = "unit-test-env-key"
    (tmp_path / ".env").write_text(f"AMOO_SECRET_KEY={secret}\n", encoding="utf-8")
    import dotenv

    def boom(*args, **kwargs):
        raise RuntimeError(f"dotenv exploded with {secret}")

    monkeypatch.setattr(dotenv, "load_dotenv", boom)
    security = _load_security()
    with pytest.raises(security.ProjectEnvError) as caught:
        security.load_project_env(tmp_path)
    message = str(caught.value)
    assert message == "无法加载环境文件"
    assert secret not in message

    with pytest.raises(http_settings.ProjectEnvError) as settings_caught:
        load_project_env(tmp_path)
    settings_message = str(settings_caught.value)
    assert settings_message == "无法加载环境文件"
    assert secret not in settings_message


def test_fallback_load_env_unreadable_fails_without_leaking():
    secret = "unit-test-env-key"
    security = _load_security()

    class Unreadable:
        def read_text(self, encoding="utf-8"):
            raise PermissionError("denied " + secret)

    with pytest.raises(security.ProjectEnvError) as caught:
        security._fallback_load_env(Unreadable())
    message = str(caught.value)
    assert message == "无法读取环境文件"
    assert secret not in message
