#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import sys
import time
import errno

from src.core.hook_client import HookTextThread, hook_log


_last_error_time = 0
_msg_count = 0
_msg_timer = 0

def _send_payload(host: str, port: int, payload: dict) -> None:
    global _last_error_time
    try:
        import json

        data = json.dumps(payload, ensure_ascii=False)
    except Exception:
        return
    try:
        with socket.create_connection((host, int(port)), timeout=3.0) as s:
            s.sendall((data + "\n").encode("utf-8", errors="ignore"))
    except Exception as e:
        now = time.time()
        # Suppress repeated connection errors (log once every 5 seconds)
        is_conn_err = isinstance(e, ConnectionRefusedError) or (hasattr(e, 'errno') and e.errno == errno.ECONNREFUSED)
        if is_conn_err:
             if now - _last_error_time > 5.0:
                 try:
                     hook_log(f"HookAgent发送失败: 目标端口未开启或拒绝连接 host={host} port={port}")
                 except Exception:
                     pass
                 _last_error_time = now
        else:
             try:
                 hook_log(f"HookAgent发送失败: {e} host={host} port={port}")
             except Exception:
                 pass

def _send_text(host: str, port: int, text: str) -> None:
    global _msg_count, _msg_timer
    payload = (text or "").strip()
    if not payload:
        return
    
    # Rate Limiting (20 msg/sec)
    now = time.time()
    if now - _msg_timer > 1.0:
        _msg_timer = now
        _msg_count = 0
    
    if _msg_count >= 20:
        return
    _msg_count += 1
    
    _send_payload(host, port, {"text": payload})


def main() -> int:
    parser = argparse.ArgumentParser(description="ScreenTranslator Hook Agent")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--port", type=int, default=37123)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--prefer-frida-only", action="store_true")
    args = parser.parse_args()

    try:
        import struct
        arch = struct.calcsize("P") * 8
        hook_log(f"HookAgent自身架构: {arch}位 executable={sys.executable}")
    except Exception as e:
        try:
            hook_log(f"HookAgent架构检测失败: {e}")
        except Exception:
            pass

    hook_log(f"HookAgent启动: pid={args.pid} port={args.port}")

    th = HookTextThread(
        pid=int(args.pid),
        min_chars=1,
        listen_port=int(args.port),
        enable_win_event=not bool(args.prefer_frida_only),
        enable_socket=False,
        enable_uia=not bool(args.prefer_frida_only),
        enable_frida=True,
        prefer_frida_only=bool(args.prefer_frida_only),
    )

    def _on_status(s: str) -> None:
        try:
            hook_log(f"HookAgent状态: {s}")
        except Exception:
            pass
        try:
            _send_payload(args.host, args.port, {"status": str(s or "").strip(), "pid": int(args.pid)})
        except Exception:
            pass

    def _on_text(t: str) -> None:
        _send_text(args.host, args.port, t)

    th.status.connect(_on_status)
    th.text_received.connect(_on_text)
    th.start()

    rc = 0
    app = None
    try:
        from PyQt6.QtCore import QCoreApplication

        app = QCoreApplication(sys.argv)
    except Exception:
        app = None
    if app is not None:
        rc = app.exec()
    else:
        try:
            while th.is_alive():
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
    try:
        th.requestInterruption()
        th.wait(1500)
    except Exception:
        pass
    time.sleep(0.05)
    return int(rc or 0)


if __name__ == "__main__":
    raise SystemExit(main())

