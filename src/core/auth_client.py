"""
登录/注册/配额请求封装

调用方式（客户端线程里调用即可）：
- AuthClient.login(device_id)  -> POST base_url + login_path
- AuthClient.register(device_id) -> POST base_url + register_path

新增：
- AuthClient.quota(device_id, method="post"|"get")
- AuthClient.consume(device_id, words)
- AuthClient.recharge(device_id, tier, words)

请求 JSON：
{"id": 设备ID, "status": "登陆中", "action": "login"|"register"}

服务端建议返回：
{"ok": true/false, "reason": "..."}  （即使失败也尽量 200，便于客户端显示失败原因）
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Thread
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urljoin

import requests


@dataclass
class AuthResponse:
    ok: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class AuthClient:
    def __init__(
        self,
        base_url: str = "https://14ku.date",
        login_path: str = "/api/login",
        register_path: str = "/api/register",
        quota_path: str = "/api/quota",
        consume_path: str = "/api/consume",
        recharge_path: str = "/api/recharge",
        update_path: str = "/api/client_update",
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or "").strip() or "https://14ku.date"
        self.login_path = login_path or "/api/login"
        self.register_path = register_path or "/api/register"
        self.quota_path = quota_path or "/api/quota"
        self.consume_path = consume_path or "/api/consume"
        self.recharge_path = recharge_path or "/api/recharge"
        self.update_path = update_path or "/api/client_update"
        self.timeout = timeout
        self.session = requests.Session()
        # 登录/注册后台线程引用（用于防止重复请求 & 结束后释放引用）
        self._auth_thread: Optional[Thread] = None

    def _url(self, path: str) -> str:
        # urljoin 需要 base_url 以 / 结尾才可靠拼路径
        base = self.base_url if self.base_url.endswith("/") else (self.base_url + "/")
        return urljoin(base, (path or "").lstrip("/"))

    def _post_json(self, path: str, payload: Dict[str, Any]) -> AuthResponse:
        url = self._url(path)
        try:
            resp = self.session.post(url, json=payload, timeout=self.timeout)
        except requests.exceptions.Timeout:
            return AuthResponse(False, "请求超时")
        except requests.exceptions.ConnectionError:
            return AuthResponse(False, "网络连接失败")
        except Exception as e:
            return AuthResponse(False, f"请求异常: {e}")

        data: Optional[Dict[str, Any]] = None
        text = (resp.text or "").strip()
        try:
            if text:
                j = resp.json()
                if isinstance(j, dict):
                    data = j
        except Exception:
            data = None

        # 兼容新服务端：始终 200 + {"ok":false,"reason":"..."}
        if isinstance(data, dict) and "ok" in data:
            ok = bool(data.get("ok"))
            reason = str(data.get("reason") or data.get("message") or data.get("msg") or "")
            return AuthResponse(ok, reason or ("成功" if ok else "失败"), data=data)

        # 传统：按 HTTP 码判断
        if 200 <= resp.status_code < 300:
            msg = ""
            if isinstance(data, dict):
                msg = str(data.get("message") or data.get("msg") or "")
            return AuthResponse(True, msg or "成功", data=data)

        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("message") or data.get("msg") or "")
        if not msg:
            msg = f"HTTP {resp.status_code}"
            if text:
                msg += f": {text[:200]}"
        return AuthResponse(False, msg, data=data)

    def _get_json(self, path: str, params: Dict[str, Any]) -> AuthResponse:
        url = self._url(path)
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.exceptions.Timeout:
            return AuthResponse(False, "请求超时")
        except requests.exceptions.ConnectionError:
            return AuthResponse(False, "网络连接失败")
        except Exception as e:
            return AuthResponse(False, f"请求异常: {e}")

        data: Optional[Dict[str, Any]] = None
        text = (resp.text or "").strip()
        try:
            if text:
                j = resp.json()
                if isinstance(j, dict):
                    data = j
        except Exception:
            data = None

        # 兼容新服务端：始终 200 + {"ok":false,"reason":"..."}
        if isinstance(data, dict) and "ok" in data:
            ok = bool(data.get("ok"))
            reason = str(data.get("reason") or data.get("message") or data.get("msg") or "")
            return AuthResponse(ok, reason or ("成功" if ok else "失败"), data=data)

        if 200 <= resp.status_code < 300:
            msg = ""
            if isinstance(data, dict):
                msg = str(data.get("message") or data.get("msg") or "")
            return AuthResponse(True, msg or "成功", data=data)

        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("message") or data.get("msg") or "")
        if not msg:
            msg = f"HTTP {resp.status_code}"
            if text:
                msg += f": {text[:200]}"
        return AuthResponse(False, msg, data=data)

    def login(self, device_id: str) -> AuthResponse:
        payload = {
            "id": device_id,
            "status": "登陆中",
            "action": "login",
        }
        return self._post_json(self.login_path, payload)

    def register(self, device_id: str) -> AuthResponse:
        payload = {
            "id": device_id,
            "status": "登陆中",
            "action": "register",
        }
        return self._post_json(self.register_path, payload)

    def quota(self, device_id: str, method: str = "post") -> AuthResponse:
        """
        查询字数余额（免费/付费/顶级）及桶信息。
        - GET  /api/quota?id=xxx
        - POST /api/quota {"id":"xxx","action":"quota"}
        """
        m = (method or "post").strip().lower()
        if m == "get":
            return self._get_json(self.quota_path, {"id": device_id})
        payload = {"id": device_id, "action": "quota"}
        return self._post_json(self.quota_path, payload)

    def consume(self, device_id: str, words: int) -> AuthResponse:
        """扣除翻译字数（免费 -> 付费(30天) -> 顶级(90天)）"""
        payload = {"id": device_id, "action": "consume", "words": int(words or 0)}
        return self._post_json(self.consume_path, payload)

    def recharge(self, device_id: str, tier: str, words: int) -> AuthResponse:
        """
        充值字数（生成独立桶）
        tier: "paid" | "top"
        """
        payload = {
            "id": device_id,
            "action": "recharge",
            "tier": (tier or "").strip(),
            "words": int(words or 0),
        }
        return self._post_json(self.recharge_path, payload)

    @staticmethod
    def _parse_version(version: str) -> Tuple[int, ...]:
        """
        将版本字符串解析为可比较的整数元组。
        兼容：'1.2.3'、'v1.2.3'、'1.2.3-beta'、'1.2' 等。
        """
        v = (version or "").strip()
        if not v:
            return tuple()

        # 去掉常见前缀
        if v.lower().startswith("v"):
            v = v[1:]

        parts = []
        for seg in v.split("."):
            seg = seg.strip()
            if not seg:
                parts.append(0)
                continue
            num = ""
            for ch in seg:
                if ch.isdigit():
                    num += ch
                else:
                    break
            if num == "":
                parts.append(0)
            else:
                try:
                    parts.append(int(num))
                except Exception:
                    parts.append(0)
        # 去掉尾部多余 0，让比较更稳定（1.2 == 1.2.0）
        while parts and parts[-1] == 0:
            parts.pop()
        return tuple(parts)

    @classmethod
    def is_newer_version(cls, latest_version: str, current_version: str) -> bool:
        """latest_version 是否严格大于 current_version"""
        a = cls._parse_version(latest_version)
        b = cls._parse_version(current_version)
        if not a:
            return False
        if not b:
            # 客户端没版本时，保守起见认为需要更新
            return True
        # 对齐长度比较
        n = max(len(a), len(b))
        a2 = a + (0,) * (n - len(a))
        b2 = b + (0,) * (n - len(b))
        return a2 > b2

    def check_client_update(
        self,
        device_id: str,
        current_version: str,
        platform: str = "windows",
        app: str = "ScreenTranslator",
    ) -> AuthResponse:
        """
        向服务端查询是否存在新客户端版本。

        约定请求：POST update_path
        {
          "id": "...",
          "action": "client_update",
          "app": "ScreenTranslator",
          "platform": "windows",
          "current_version": "1.0.0"
        }

        约定返回（示例）：
        {
          "ok": true,
          "latest_version": "1.1.0",
          "download_url": "https://...",
          "force": true,
          "message": "..."
        }
        """
        payload = {
            "id": device_id,
            "action": "client_update",
            "app": (app or "").strip() or "ScreenTranslator",
            "platform": (platform or "").strip() or "windows",
            "current_version": (current_version or "").strip(),
        }
        return self._post_json(self.update_path, payload)

    def login_async(self, device_id: str, on_finished: Callable[[str, AuthResponse], None]) -> bool:
        """
        后台线程执行登录请求，结束后回调 on_finished(action, response)。
        注意：回调发生在后台线程里；若你在 UI 线程更新控件，请自行切回主线程。
        """
        return self._start_async("login", device_id, on_finished)

    def register_async(self, device_id: str, on_finished: Callable[[str, AuthResponse], None]) -> bool:
        """
        后台线程执行注册请求，结束后回调 on_finished(action, response)。
        注意：回调发生在后台线程里；若你在 UI 线程更新控件，请自行切回主线程。
        """
        return self._start_async("register", device_id, on_finished)

    def _start_async(self, action: str, device_id: str, on_finished: Callable[[str, AuthResponse], None]) -> bool:
        # 若已有进行中的请求，拒绝重复启动（由上层决定是否提示/排队）
        t = self._auth_thread
        if t is not None and t.is_alive():
            return False

        def _run() -> None:
            try:
                if action == "login":
                    resp = self.login(device_id)
                elif action == "register":
                    resp = self.register(device_id)
                else:
                    resp = AuthResponse(False, f"未知 action: {action}")
                on_finished(action, resp)
            except Exception as e:
                try:
                    on_finished(action, AuthResponse(False, f"请求异常: {e}"))
                except Exception:
                    # 避免回调异常导致线程无法释放引用
                    pass
            finally:
                # 请求结束后释放线程引用（登录/注册都一样）
                self._auth_thread = None

        self._auth_thread = Thread(target=_run, daemon=True)
        self._auth_thread.start()
        return True


