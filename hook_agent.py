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


def _start_console_hide_watcher():
    if sys.platform != "win32":
        return None, None
    try:
        import os
        import threading
        import ctypes
    except Exception:
        return None, None
    stop_event = threading.Event()
    try:
        u32 = ctypes.windll.user32
    except Exception:
        return None, None
    try:
        k32 = ctypes.windll.kernel32
        import ctypes.wintypes as wt
    except Exception:
        k32 = None
        wt = None

    def _build_parent_pid_map() -> dict[int, int] | None:
        if k32 is None or wt is None:
            return None
        try:
            TH32CS_SNAPPROCESS = 0x00000002

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wt.DWORD),
                    ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", wt.ULONG_PTR),
                    ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", wt.LONG),
                    ("dwFlags", wt.DWORD),
                    ("szExeFile", wt.WCHAR * 260),
                ]

            k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
            k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
            k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            k32.Process32FirstW.restype = wt.BOOL
            k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            k32.Process32NextW.restype = wt.BOOL
            k32.CloseHandle.argtypes = [wt.HANDLE]
            k32.CloseHandle.restype = wt.BOOL

            snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if not snap or snap == wt.HANDLE(-1).value:
                return None

            parent_map: dict[int, int] = {}
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                if not k32.Process32FirstW(snap, ctypes.byref(entry)):
                    return None
                while True:
                    pid = int(entry.th32ProcessID or 0)
                    ppid = int(entry.th32ParentProcessID or 0)
                    if pid > 0:
                        parent_map[pid] = ppid
                    if not k32.Process32NextW(snap, ctypes.byref(entry)):
                        break
            finally:
                try:
                    k32.CloseHandle(snap)
                except Exception:
                    pass
            return parent_map
        except Exception:
            return None

    def _hide_console_windows_for_children() -> None:
        root_pid = os.getpid()
        parent_map: dict[int, int] | None = None
        psutil_mod = None

        def _is_descendant(pid: int) -> bool:
            nonlocal parent_map, psutil_mod
            if pid <= 0:
                return False
            if pid == root_pid:
                return True

            if parent_map is None:
                parent_map = _build_parent_pid_map()
                if parent_map is None:
                    parent_map = {}
                    try:
                        import psutil as _psutil  # type: ignore
                        psutil_mod = _psutil
                    except Exception:
                        psutil_mod = None

            if parent_map:
                cur = pid
                for _ in range(64):
                    if cur == root_pid:
                        return True
                    cur = int(parent_map.get(int(cur), 0) or 0)
                    if cur <= 0:
                        return False
                return False

            if psutil_mod is None:
                return False
            try:
                p = psutil_mod.Process(int(pid))
            except Exception:
                return False
            for _ in range(32):
                try:
                    if p.pid == root_pid:
                        return True
                    p = p.parent()
                except Exception:
                    break
                if p is None:
                    break
            return False

        def _enum_proc(hwnd, _lparam):
            try:
                class_name = ctypes.create_unicode_buffer(256)
                if u32.GetClassNameW(hwnd, class_name, 256) == 0:
                    return True
                if class_name.value != "ConsoleWindowClass":
                    return True
                pid = ctypes.c_ulong()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if not _is_descendant(int(pid.value or 0)):
                    return True
                try:
                    u32.ShowWindow(hwnd, 0)
                except Exception:
                    pass
            except Exception:
                pass
            return True

        try:
            cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_enum_proc)
            u32.EnumWindows(cb, 0)
        except Exception:
            pass

    def _loop():
        while not stop_event.is_set():
            try:
                _hide_console_windows_for_children()
            except Exception:
                pass
            stop_event.wait(0.05)

    th = threading.Thread(target=_loop, daemon=True)
    th.start()
    return stop_event, th

def _send_payload(host: str, port: int, payload: dict) -> None:
    global _last_error_time
    try:
        import json
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

def _send_text(host: str, port: int, text_or_packet) -> None:
    global _msg_count, _msg_timer
    payload: dict | None = None
    if isinstance(text_or_packet, dict):
        text_payload = str(text_or_packet.get("text") or "").strip()
        if not text_payload:
            return
        payload = {"text": text_payload}
        for src_key, dst_key in (
            ("source", "source"),
            ("label", "label"),
            ("thread_id", "threadId"),
            ("threadId", "threadId"),
            ("pid", "pid"),
        ):
            value = text_or_packet.get(src_key)
            if value is None or value == "":
                continue
            payload[dst_key] = value
    else:
        text_payload = str(text_or_packet or "").strip()
        if not text_payload:
            return
        payload = {"text": text_payload}

    # Rate limiting: keep some protection, but do not choke fast text updates.
    now = time.time()
    if now - _msg_timer > 1.0:
        _msg_timer = now
        _msg_count = 0
    
    if _msg_count >= 120:
        return
    _msg_count += 1

    _send_payload(host, port, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="ScreenTranslator Hook Agent")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--port", type=int, default=37123)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--prefer-frida-only", action="store_true")
    args = parser.parse_args()

    try:
        _console_hide_stop, _console_hide_thread = _start_console_hide_watcher()
    except Exception:
        _console_hide_stop, _console_hide_thread = None, None

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

    def _on_packet(packet) -> None:
        _send_text(args.host, args.port, packet)

    def _on_text(t: str) -> None:
        _send_text(args.host, args.port, t)

    th.status.connect(_on_status)
    if hasattr(th, "packet_received"):
        th.packet_received.connect(_on_packet)
    else:
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
    try:
        if _console_hide_stop is not None:
            _console_hide_stop.set()
    except Exception:
        pass
    time.sleep(0.05)
    return int(rc or 0)


if __name__ == "__main__":
    raise SystemExit(main())

