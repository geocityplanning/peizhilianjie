# -*- coding: utf-8 -*-
"""密码加密工具：Fernet(AES)。

密钥来源优先级：
  1. 环境变量 AMOO_SECRET_KEY（迁移/容器化首选，跨机器一致）
  2. 本地文件 <project_root>/data/.secret_key（本机自动生成，绑机器特征）

启动时确定性加载项目根目录 `.env`，不依赖当前工作目录或调用者是否 source。
decrypt_password 失败必须 fail fast，不得把密文当作明文返回。
"""
from cryptography.fernet import Fernet
from pathlib import Path
import base64
import hashlib
import os
import platform


class PasswordDecryptError(RuntimeError):
    """解密失败。消息不得包含密文、明文密码或密钥。"""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def secret_key_path(root: Path | None = None) -> Path:
    return (root or _project_root()) / "data" / ".secret_key"


def _fallback_load_env(env_file: Path) -> None:
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def load_project_env(root: Path | None = None) -> Path:
    """Load `<root>/.env` into os.environ without overriding existing values."""
    project_root = Path(root) if root is not None else _project_root()
    env_file = project_root / ".env"
    if env_file.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path=env_file, override=False)
        except Exception:
            _fallback_load_env(env_file)
    return project_root


def _get_machine_id() -> str:
    info = f"{platform.node()}-{platform.machine()}-{platform.system()}"
    return hashlib.sha256(info.encode()).hexdigest()


def _get_key(*, create: bool = False) -> bytes:
    load_project_env()
    env_key = os.environ.get("AMOO_SECRET_KEY", "").strip()
    if env_key:
        return env_key.encode()
    key_file = secret_key_path()
    if key_file.exists():
        return key_file.read_bytes()
    if not create:
        raise PasswordDecryptError("密码解密失败")
    material = hashlib.sha256(_get_machine_id().encode()).digest()
    key = base64.urlsafe_b64encode(material)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    with open(key_file, "wb") as handle:
        handle.write(key)
    try:
        os.chmod(key_file, 0o600)
    except Exception:
        pass
    return key


def encrypt_password(plaintext: str) -> str:
    if not plaintext:
        return ""
    try:
        return Fernet(_get_key(create=True)).encrypt(plaintext.encode()).decode()
    except Exception:
        return plaintext


def decrypt_password(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return Fernet(_get_key(create=False)).decrypt(ciphertext.encode()).decode()
    except PasswordDecryptError:
        raise
    except Exception:
        raise PasswordDecryptError("密码解密失败") from None


class PasswordEncryption:
    def encrypt(self, plaintext: str) -> str:
        return encrypt_password(plaintext)

    def decrypt(self, ciphertext: str) -> str:
        return decrypt_password(ciphertext)
