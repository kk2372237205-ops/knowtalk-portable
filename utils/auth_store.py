# -*- coding: utf-8 -*-
"""本地账号存储与无状态会话令牌。

只有两个文件：``data/auth/users.json`` 与 ``data/auth/secret.key``，都在版本控制之外。

设计取舍：
- **不引第三方依赖**：密码用标准库 ``hashlib.pbkdf2_hmac``，令牌用 ``hmac`` + ``secrets``。
- **无服务端会话表**：令牌自带过期时间戳并整体签名，进程重启后仍然有效。
  代价是"踢人"只能靠轮换签名密钥（改密时轮换），这也正是需求里要的行为。
- **写盘走临时文件 + ``os.replace``**：与 ``utils/settings_store.py`` 同款，
  避免断电或并发写留下半截 JSON 把账号锁死。
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from utils.logger import logger

DEFAULT_DIR = Path("data/auth")
SESSION_COOKIE = "kt_session"
SESSION_TTL = 7 * 24 * 3600

PBKDF2_ITERATIONS = 260_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")

TOKEN_SEP = "."


class AuthError(Exception):
    """账号操作的业务错误（用户名非法、密码太短、旧密码不对……）。"""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return digest.hex()


def issue_token(username: str, secret: bytes, ttl: int = SESSION_TTL) -> str:
    """签发 ``<payload>.<签名>`` 形式的令牌；payload 里有用户名与过期时间戳。"""
    payload = json.dumps(
        {"u": username, "exp": int(time.time()) + int(ttl)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    body = _b64(payload)
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return body + TOKEN_SEP + _b64(sig)


def read_token(token: str, secret: bytes):
    """校验并解出用户名；任何异常（篡改、过期、乱码）一律返回 ``None``。"""
    if not token or TOKEN_SEP not in token:
        return None
    body, _, sig = token.partition(TOKEN_SEP)
    try:
        expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig)):
            return None
        payload = json.loads(_unb64(body).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        username = payload.get("u")
        return username or None
    except Exception:
        return None


class AuthStore:
    """``data/auth/users.json`` 的读写门面。所有公开方法都能从多线程调用。"""

    def __init__(self, path=None, secret_path=None):
        base = Path(path).parent if path else DEFAULT_DIR
        self.path = Path(path) if path else DEFAULT_DIR / "users.json"
        self.secret_path = Path(secret_path) if secret_path else base / "secret.key"
        self._lock = threading.RLock()
        self._data = None
        self._secret = None

    # ── 读写 ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        data = {"version": 1, "require_login": True, "users": {}}
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    data.update(loaded)
                    data.setdefault("users", {})
        except Exception as exc:  # 坏文件不该把服务打死，但必须留下痕迹
            logger.error("读取账号文件失败，按空账号处理：%s", exc)
        self._data = data
        return data

    def _save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = str(self.path) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(self._load(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp_path, self.path)

    # ── 查询 ──────────────────────────────────────────────────────────

    @property
    def require_login(self) -> bool:
        return bool(self._load().get("require_login", True))

    def has_users(self) -> bool:
        return bool(self._load().get("users"))

    def usernames(self):
        return sorted(self._load().get("users", {}).keys())

    def has_user(self, username: str) -> bool:
        return _key(username) in self._load().get("users", {})

    def display_name(self, username: str) -> str:
        record = self._load().get("users", {}).get(_key(username)) or {}
        return record.get("display_name") or _key(username)

    # ── 变更 ──────────────────────────────────────────────────────────

    def set_require_login(self, value: bool):
        with self._lock:
            self._load()["require_login"] = bool(value)
            self._save()

    def create_user(self, username, password, display_name=None, require_login=None):
        username = (username or "").strip()
        if not USERNAME_RE.match(username):
            raise AuthError("用户名只能使用 1–32 位字母、数字、下划线、横线或点")
        password = password or ""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise AuthError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")

        key = _key(username)
        with self._lock:
            users = self._load().setdefault("users", {})
            if key in users:
                raise AuthError("该用户名已存在")

            salt = secrets.token_bytes(SALT_BYTES)
            users[key] = {
                "display_name": (display_name or username).strip() or username,
                "salt": salt.hex(),
                "iterations": PBKDF2_ITERATIONS,
                "hash": hash_password(password, salt),
                "created_at": int(time.time()),
            }
            if require_login is not None:
                self._load()["require_login"] = bool(require_login)
            self._save()
            return users[key]

    def verify(self, username, password) -> bool:
        record = self._load().get("users", {}).get(_key(username))
        if not record:
            # 用户不存在时也走一遍哈希，避免用响应时间判断账号是否存在
            hash_password(password or "", b"\x00" * SALT_BYTES)
            return False
        try:
            expected = record["hash"]
            actual = hash_password(
                password or "",
                bytes.fromhex(record["salt"]),
                int(record.get("iterations", PBKDF2_ITERATIONS)),
            )
        except Exception:
            return False
        return hmac.compare_digest(expected, actual)

    def change_password(self, username, old_password, new_password):
        if not self.verify(username, old_password):
            raise AuthError("旧密码不正确")
        self.reset_password(username, new_password)

    def reset_password(self, username, new_password):
        """不需要旧密码的重置：改密接口与命令行脚本共用。改完轮换签名密钥。"""
        key = _key(username)
        new_password = new_password or ""
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise AuthError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
        with self._lock:
            record = self._load().get("users", {}).get(key)
            if not record:
                raise AuthError("账号不存在")
            salt = secrets.token_bytes(SALT_BYTES)
            record.update({
                "salt": salt.hex(),
                "iterations": PBKDF2_ITERATIONS,
                "hash": hash_password(new_password, salt),
                "updated_at": int(time.time()),
            })
            self._save()
            self.rotate_secret()

    # ── 签名密钥 ──────────────────────────────────────────────────────

    def secret(self) -> bytes:
        with self._lock:
            if self._secret is not None:
                return self._secret
            try:
                if self.secret_path.exists():
                    raw = self.secret_path.read_text(encoding="utf-8").strip()
                    if raw:
                        self._secret = bytes.fromhex(raw)
                        return self._secret
            except Exception as exc:
                logger.error("读取会话密钥失败，将重新生成：%s", exc)
            return self.rotate_secret()

    def rotate_secret(self) -> bytes:
        with self._lock:
            value = secrets.token_bytes(32)
            try:
                self.secret_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = str(self.secret_path) + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as handle:
                    handle.write(value.hex())
                try:
                    os.chmod(tmp_path, 0o600)
                except Exception:
                    pass
                os.replace(tmp_path, self.secret_path)
            except Exception as exc:
                logger.error("写入会话密钥失败：%s", exc)
            self._secret = value
            return value


def _key(username) -> str:
    """用户名统一小写存储与查找——登录不区分大小写。"""
    return (username or "").strip().lower()


_store = None


def get_store() -> AuthStore:
    global _store
    if _store is None:
        _store = AuthStore()
    return _store
