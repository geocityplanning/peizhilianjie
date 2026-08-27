# -*- coding: utf-8 -*-
"""密码加密工具：Fernet(AES)。

密钥来源优先级：
  1. 环境变量 AMOO_SECRET_KEY（迁移/容器化首选，跨机器一致）
  2. 本地文件 data/.secret_key（本机自动生成，绑机器特征）

设置环境变量后即可跨机器使用同一份加密密码，无需拷贝 .secret_key。
"""
from cryptography.fernet import Fernet
from pathlib import Path
import base64
import hashlib
import os
import platform


class PasswordEncryption:
    def __init__(self):
        self.key = self._get_or_create_key()
        self.cipher = Fernet(self.key)

    def _get_machine_id(self) -> str:
        info = f"{platform.node()}-{platform.machine()}-{platform.system()}"
        return hashlib.sha256(info.encode()).hexdigest()

    def _get_or_create_key(self) -> bytes:
        # 1. 优先读环境变量
        env_key = os.environ.get("AMOO_SECRET_KEY", "").strip()
        if env_key:
            return env_key.encode()
        # 2. 退回本地文件
        key_file = Path("data/.secret_key")
        if key_file.exists():
            with open(key_file, "rb") as f:
                return f.read()
        # 3. 首次运行：生成并写入文件
        material = hashlib.sha256(self._get_machine_id().encode()).digest()
        key = base64.urlsafe_b64encode(material)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        with open(key_file, "wb") as f:
            f.write(key)
        try:
            os.chmod(key_file, 0o600)
        except Exception:
            pass
        return key

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        try:
            return self.cipher.encrypt(plaintext.encode()).decode()
        except Exception:
            return plaintext

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        try:
            return self.cipher.decrypt(ciphertext.encode()).decode()
        except Exception:
            return ciphertext


_password_encryption = PasswordEncryption()
encrypt_password = _password_encryption.encrypt
decrypt_password = _password_encryption.decrypt
