from __future__ import annotations

import logging
import threading
import time
import os
import sys
import re
from array import array
from collections import deque

try:
    from PyQt6.QtCore import QThread, pyqtSignal
except Exception:
    class _FallbackSignal:
        def __init__(self):
            self._handlers = []

        def connect(self, fn):
            try:
                self._handlers.append(fn)
            except Exception:
                pass

        def emit(self, *args, **kwargs):
            for fn in list(self._handlers):
                try:
                    fn(*args, **kwargs)
                except Exception:
                    pass

    def pyqtSignal(*_args, **_kwargs):
        return _FallbackSignal()

    class QThread(threading.Thread):
        def __init__(self):
            super().__init__(daemon=True)
            self._interrupt = threading.Event()

        def requestInterruption(self):
            self._interrupt.set()

        def isInterruptionRequested(self):
            return self._interrupt.is_set()

        def wait(self, timeout_ms: int | None = None):
            timeout = None if timeout_ms is None else max(0.0, float(timeout_ms) / 1000.0)
            try:
                self.join(timeout)
            except Exception:
                pass
            return True

        def isRunning(self):
            try:
                return self.is_alive()
            except Exception:
                return False


_HOOK_LOGGER: logging.Logger | None = None


def _get_hook_logger() -> logging.Logger | None:
    global _HOOK_LOGGER
    if _HOOK_LOGGER is not None:
        return _HOOK_LOGGER
    try:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "hook.log"
    except Exception:
        return None

    try:
        logger = logging.getLogger("hook_logger")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for h in list(logger.handlers):
            logger.removeHandler(h)
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        _HOOK_LOGGER = logger
        return logger
    except Exception:
        return None


def hook_log(message: str) -> None:
    logger = _get_hook_logger()
    if logger is None:
        return
    try:
        logger.info(str(message))
    except Exception:
        pass


def _ensure_hidden_console_for_console_children() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
    except Exception:
        return
    try:
        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32
    except Exception:
        return
    try:
        hwnd = k32.GetConsoleWindow()
        if int(hwnd or 0) == 0:
            try:
                k32.AllocConsole()
            except Exception:
                return
            hwnd = k32.GetConsoleWindow()
        if int(hwnd or 0) != 0:
            try:
                u32.ShowWindow(hwnd, 0)
            except Exception:
                pass
    except Exception:
        return


class HookTextThread(QThread):
    text_received = pyqtSignal(str)
    packet_received = pyqtSignal(object)
    status = pyqtSignal(str)

    def __init__(
        self,
        *,
        pid: int,
        min_chars: int = 1,
        max_chars: int = 200,
        debounce_ms: int = 120,
        listen_port: int | None = None,
        enable_win_event: bool = True,
        enable_socket: bool = True,
        enable_uia: bool = True,
        enable_frida: bool = True,
        prefer_frida_only: bool = False,
    ):
        super().__init__()
        try:
            self._pid = int(pid or 0)
        except Exception:
            self._pid = 0
        self._min_chars = max(1, int(min_chars))
        self._max_chars = max(self._min_chars, int(max_chars))
        self._debounce_ms = max(30, int(debounce_ms))
        try:
            self._listen_port = int(listen_port) if listen_port is not None else None
        except Exception:
            self._listen_port = None
        self._enable_win_event = bool(enable_win_event)
        self._enable_socket = bool(enable_socket)
        self._enable_uia = bool(enable_uia)
        self._enable_frida = bool(enable_frida)
        self._prefer_frida_only = bool(prefer_frida_only)
        self._last_emit_ts = 0.0
        self._last_text = ""
        self._seen = deque(maxlen=300)
        self._seen_set: set[int] = set()
        self._packet_seen = deque(maxlen=800)
        self._packet_seen_set: set[int] = set()
        self._packet_last_emit_ts: dict[tuple[str, str, int, str], float] = {}
        self._win_event_proc = None
        self._hooks = []
        self._server_thread = None
        self._server_stop = threading.Event()
        self._server_sock = None
        self._uia_thread = None
        self._uia_stop = threading.Event()
        self._frida_thread = None
        self._frida_stop = threading.Event()
        self._agent_process = None
        self._enable_renpy_injection = self._resolve_renpy_injection_enabled()

    @staticmethod
    def _parse_bool(raw, default: bool = False) -> bool:
        try:
            if isinstance(raw, bool):
                return bool(raw)
            s = str(raw or "").strip().lower()
        except Exception:
            return bool(default)
        if not s:
            return bool(default)
        if s in ("1", "true", "yes", "on", "enabled"):
            return True
        if s in ("0", "false", "no", "off", "disabled"):
            return False
        return bool(default)

    def _resolve_renpy_injection_enabled(self) -> bool:
        # Default ON: the injected Ren'Py poller is read-only and is the most reliable
        # way to get dialogue from Ren'Py titles like DDLC without depending on render hooks.
        enabled = True
        try:
            import configparser
            from pathlib import Path

            cfg = configparser.ConfigParser()
            ini_paths = [
                Path(os.getcwd()) / "config" / "settings.ini",
                Path(__file__).resolve().parents[2] / "config" / "settings.ini",
            ]
            for p in ini_paths:
                if not p.exists():
                    continue
                cfg.read(str(p), encoding="utf-8")
                if cfg.has_option("hook", "renpy_injection"):
                    enabled = self._parse_bool(cfg.get("hook", "renpy_injection"), False)
                    break
        except Exception:
            pass
        try:
            env = os.environ.get("SCREEN_TRANSLATOR_RENPY_INJECTION")
            if env is not None and str(env).strip() != "":
                enabled = self._parse_bool(env, enabled)
        except Exception:
            pass
        return bool(enabled)

    def _find_32bit_agent(self) -> str | None:
        # Base candidates relative to CWD
        candidates = [
            # 1. dist/ScreenTranslator-x86/HookAgent/HookAgent.exe
            os.path.abspath(os.path.join(os.getcwd(), "dist", "ScreenTranslator-x86", "HookAgent", "HookAgent.exe")),
            # 2. ScreenTranslator-x86/HookAgent/HookAgent.exe
            os.path.abspath(os.path.join(os.getcwd(), "ScreenTranslator-x86", "HookAgent", "HookAgent.exe")),
            # 2b. ScreenTranslator-x86/HookAgent.exe (Directly in x86 folder)
            os.path.abspath(os.path.join(os.getcwd(), "ScreenTranslator-x86", "HookAgent.exe")),
             # 3. ../ScreenTranslator-x86/HookAgent/HookAgent.exe
            os.path.abspath(os.path.join(os.getcwd(), "..", "ScreenTranslator-x86", "HookAgent", "HookAgent.exe")),
            # 4. HookAgent-x86/HookAgent.exe
            os.path.abspath(os.path.join(os.getcwd(), "HookAgent-x86", "HookAgent.exe")),
            # 5. HookAgent/HookAgent.exe (Simple subdirectory)
            os.path.abspath(os.path.join(os.getcwd(), "HookAgent", "HookAgent.exe")),
            # 6. Dev path check
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "dist", "ScreenTranslator-x86", "HookAgent", "HookAgent.exe")),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    def _is_32bit_python_cmd(self, cmd: list[str]) -> bool:
        try:
            import subprocess
        except Exception:
            return False
        try:
            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                try:
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0
                except Exception:
                    startupinfo = None
                try:
                    creationflags |= subprocess.CREATE_NO_WINDOW
                except Exception:
                    pass
            result = subprocess.run(
                cmd + ["-c", "import struct,sys;sys.exit(0 if struct.calcsize('P')==4 else 1)"],
                capture_output=True,
                timeout=3,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            return int(result.returncode or 1) == 0
        except Exception:
            return False

    def _resolve_py32_cmd(self) -> list[str] | None:
        try:
            import subprocess
        except Exception:
            return None
        candidates: list[list[str]] = []

        # 1) settings.ini [hook] py32
        try:
            import configparser
            from pathlib import Path
            cfg = configparser.ConfigParser()
            ini_paths = [
                Path(os.getcwd()) / "config" / "settings.ini",
                Path(__file__).resolve().parents[2] / "config" / "settings.ini",
            ]
            for p in ini_paths:
                if not p.exists():
                    continue
                cfg.read(str(p), encoding="utf-8")
                if cfg.has_option("hook", "py32"):
                    py32 = str(cfg.get("hook", "py32") or "").strip()
                    if py32:
                        candidates.append([py32])
                    break
        except Exception:
            pass

        # 2) env
        for k in ("SCREEN_TRANSLATOR_PY32", "PY32"):
            v = str(os.environ.get(k) or "").strip()
            if v:
                candidates.append([v])

        # 3) py launcher
        try:
            if subprocess.run(["py", "--version"], capture_output=True, timeout=2).returncode == 0:
                candidates.append(["py", "-3-32"])
        except Exception:
            pass

        # 4) common install locations
        try:
            from pathlib import Path
            bases = [
                os.environ.get("LOCALAPPDATA"),
                os.environ.get("ProgramFiles(x86)"),
                os.environ.get("ProgramFiles"),
            ]
            for base in [b for b in bases if b]:
                bp = Path(base)
                for p in bp.glob("Programs/Python/Python3*-32/python.exe"):
                    candidates.append([str(p)])
                for p in bp.glob("Python3*-32/python.exe"):
                    candidates.append([str(p)])
        except Exception:
            pass

        seen: set[tuple[str, ...]] = set()
        for cmd in candidates:
            key = tuple(cmd)
            if key in seen:
                continue
            seen.add(key)
            if self._is_32bit_python_cmd(cmd):
                return cmd
        return None

    def _find_source_hook_agent(self) -> str | None:
        try:
            from pathlib import Path
            candidates = [
                Path(os.getcwd()) / "hook_agent.py",
                Path(__file__).resolve().parents[2] / "hook_agent.py",
                Path(os.getcwd()) / "screen-translator" / "hook_agent.py",
            ]
            for c in candidates:
                if c.exists():
                    return str(c)
        except Exception:
            pass
        return None

    def request_learn(self, *_args) -> None:
        return

    def _seen_add(self, h: int) -> bool:
        if h in self._seen_set:
            return False
        if len(self._seen) >= int(self._seen.maxlen or 0):
            try:
                old = self._seen.popleft()
                try:
                    self._seen_set.discard(int(old))
                except Exception:
                    pass
            except Exception:
                pass
        self._seen.append(h)
        self._seen_set.add(h)
        return True

    def _packet_seen_add(self, h: int) -> bool:
        if h in self._packet_seen_set:
            return False
        if len(self._packet_seen) >= int(self._packet_seen.maxlen or 0):
            try:
                old = self._packet_seen.popleft()
                try:
                    self._packet_seen_set.discard(int(old))
                except Exception:
                    pass
            except Exception:
                pass
        self._packet_seen.append(h)
        self._packet_seen_set.add(h)
        return True

    @staticmethod
    def _coerce_int(value) -> int | None:
        try:
            if value is None:
                return None
            s = str(value).strip()
            if not s:
                return None
            return int(s)
        except Exception:
            return None

    @staticmethod
    def _normalize_hook_text(text: str) -> str:
        try:
            payload = str(text or "")
        except Exception:
            return ""
        if not payload:
            return ""
        payload = payload.replace("\x00", "")
        payload = payload.replace("\r\n", "\n").replace("\r", "\n")
        payload = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", payload)
        payload = re.sub(r"\s+", " ", payload).strip()
        return payload

    def _build_text_packet(
        self,
        text: str,
        source: str,
        *,
        label: str = "",
        thread_id: int | None = None,
        pid: int | None = None,
        transport: str = "",
    ) -> dict[str, object] | None:
        payload = self._normalize_hook_text(text)
        if not payload:
            return None
        if len(payload) > int(self._max_chars):
            payload = payload[: int(self._max_chars)]
        src = str(source or "unknown").strip().lower() or "unknown"
        lbl = str(label or src or "unknown").strip() or src
        tid = self._coerce_int(thread_id)
        pid_i = self._coerce_int(pid)
        packet: dict[str, object] = {
            "text": payload,
            "source": src,
            "label": lbl,
            "thread_id": tid,
            "pid": pid_i,
            "signature": f"{src}|{lbl}|{tid if tid is not None else 0}",
        }
        if transport:
            packet["transport"] = str(transport).strip().lower()
        return packet

    def _should_emit_packet(self, packet: dict[str, object]) -> bool:
        src = str(packet.get("source") or "")
        lbl = str(packet.get("label") or "")
        tid = int(packet.get("thread_id") or 0)
        txt = str(packet.get("text") or "")
        key = (src, lbl, tid, txt)
        now = float(time.time())
        try:
            last = float(self._packet_last_emit_ts.get(key, 0.0) or 0.0)
        except Exception:
            last = 0.0
        if last > 0.0 and (now - last) < 0.45:
            return False
        self._packet_last_emit_ts[key] = now
        try:
            if len(self._packet_last_emit_ts) > 2048:
                cutoff = now - 20.0
                stale = [k for k, v in self._packet_last_emit_ts.items() if float(v or 0.0) < cutoff]
                for k in stale:
                    self._packet_last_emit_ts.pop(k, None)
                while len(self._packet_last_emit_ts) > 1536:
                    try:
                        first_key = next(iter(self._packet_last_emit_ts))
                    except Exception:
                        break
                    self._packet_last_emit_ts.pop(first_key, None)
        except Exception:
            pass
        return True

    def _should_emit(self, text: str) -> bool:
        now = time.time()
        if text == self._last_text and (now - self._last_emit_ts) * 1000.0 < self._debounce_ms:
            return False
        self._last_text = text
        self._last_emit_ts = now
        h = hash(text)
        return self._seen_add(h)

    def _emit_text(self, text: str) -> bool:
        payload = str(text or "").strip()
        if not payload:
            return False
        # if len(payload) < int(self._min_chars):
        #    return
        if len(payload) > int(self._max_chars):
            payload = payload[: int(self._max_chars)]
        if not self._should_emit(payload):
            return False
        try:
            self.text_received.emit(payload)
            return True
        except Exception:
            return False

    def _emit_text_with_source(
        self,
        text: str,
        source: str,
        *,
        label: str = "",
        thread_id: int | None = None,
        pid: int | None = None,
        transport: str = "",
    ) -> None:
        packet = self._build_text_packet(
            text,
            source,
            label=label,
            thread_id=thread_id,
            pid=pid,
            transport=transport,
        )
        if packet is None:
            return
        try:
            if self._prefer_frida_only and str(packet.get("source") or "") in ("uia", "win_event"):
                return
        except Exception:
            pass
        if not self._should_emit_packet(packet):
            return
        try:
            self.packet_received.emit(dict(packet))
        except Exception:
            pass
        try:
            self._emit_text(str(packet.get("text") or ""))
        except Exception:
            pass
        try:
            ll = str(packet.get("label") or "").strip().lower()
            if ll.startswith("pythonapi:pystring_fromstring") or ll.startswith("multibytetowidechar"):
                return
            hook_log(
                "TEXT_SRC: "
                f"{packet.get('source')} label={packet.get('label')} tid={packet.get('thread_id')}"
            )
        except Exception:
            pass

    def _parse_hook_line(self, line: str) -> dict[str, object]:
        result: dict[str, object] = {
            "pid": None,
            "text": "",
            "status": "",
            "label": "",
            "source": "",
            "thread_id": None,
        }
        payload = str(line or "").strip()
        if not payload:
            return result
        if payload.startswith("[HOOK_ERR]"):
            err = payload[len("[HOOK_ERR]") :].strip()
            err = err.replace("\\r", "").replace("\\n", " | ").strip()
            result["status"] = f"Hook Ren'Py runtime error: {err}" if err else "Hook Ren'Py runtime error"
            return result
        if payload.startswith("{") and payload.endswith("}"):
            try:
                import json

                data = json.loads(payload)
                text = self._normalize_hook_text(data.get("text") or "")
                status = str(data.get("status") or "").strip()
                label = str(data.get("label") or "").strip()
                source = str(data.get("source") or "").strip().lower()
                pid = data.get("pid", None)
                thread_id = data.get("threadId", data.get("thread_id"))
                result.update(
                    {
                        "pid": self._coerce_int(pid),
                        "text": text,
                        "status": status,
                        "label": label,
                        "source": source,
                        "thread_id": self._coerce_int(thread_id),
                    }
                )
                return result
            except Exception:
                pass
        if payload.lower().startswith("pid=") and "|" in payload:
            head, body = payload.split("|", 1)
            pid_str = head.split("=", 1)[-1].strip()
            result["pid"] = self._coerce_int(pid_str)
            result["text"] = self._normalize_hook_text(body)
            return result
        if payload.lower().startswith("pid:") and "|" in payload:
            head, body = payload.split("|", 1)
            pid_str = head.split(":", 1)[-1].strip()
            result["pid"] = self._coerce_int(pid_str)
            result["text"] = self._normalize_hook_text(body)
            return result
        result["text"] = self._normalize_hook_text(payload)
        return result

    def _server_loop(self) -> None:
        try:
            import socket
        except Exception as e:
            try:
                self.status.emit(f"Hook外部端口不可用: {e}")
            except Exception:
                pass
            return

        port = int(self._listen_port or 0)
        if port <= 0:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.listen(5)
            sock.settimeout(0.5)
        except Exception as e:
            try:
                self.status.emit(f"Hook外部端口监听失败: {e}")
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
            return

        try:
            self.status.emit(f"Hook外部端口监听: 127.0.0.1:{port}")
        except Exception:
            pass

        while not self._server_stop.is_set() and not self.isInterruptionRequested():
            try:
                conn, _addr = sock.accept()
            except Exception:
                continue
            try:
                conn.settimeout(0.5)
            except Exception:
                pass

            buf = b""
            try:
                while not self._server_stop.is_set() and not self.isInterruptionRequested():
                    try:
                        data = conn.recv(4096)
                    except Exception:
                        data = b""
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            s = line.decode("utf-8", errors="ignore").strip()
                        except Exception:
                            s = ""
                        if not s:
                            continue
                        packet = self._parse_hook_line(s)
                        pid = self._coerce_int(packet.get("pid"))
                        text = str(packet.get("text") or "")
                        status = str(packet.get("status") or "")
                        label = str(packet.get("label") or "").strip()
                        source = str(packet.get("source") or "").strip().lower() or "socket"
                        thread_id = self._coerce_int(packet.get("thread_id"))
                        if pid is not None and int(pid) != int(self._pid):
                            continue
                        if status:
                            try:
                                self.status.emit(str(status))
                            except Exception:
                                pass
                            try:
                                hook_log(f"STATUS(EXT): {status}")
                            except Exception:
                                pass
                        self._emit_text_with_source(
                            text,
                            source,
                            label=label,
                            thread_id=thread_id,
                            pid=pid,
                            transport="socket",
                        )
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        try:
            sock.close()
        except Exception:
            pass

    def _uia_loop(self) -> None:
        try:
            import comtypes
            import comtypes.client
        except Exception as e:
            try:
                self.status.emit(f"Hook UIA 不可用: {e}")
            except Exception:
                pass
            return

        try:
            comtypes.CoInitialize()
        except Exception:
            pass

        try:
            uia = comtypes.client.CreateObject("UIAutomationClient.CUIAutomation")
        except Exception:
            uia = None
        if uia is None:
            try:
                comtypes.client.GetModule("UIAutomationCore.dll")
                from comtypes.gen import UIAutomationClient as _UIA

                uia = comtypes.client.CreateObject(_UIA.CUIAutomation, interface=_UIA.IUIAutomation)
            except Exception as e:
                try:
                    self.status.emit(f"Hook UIA 初始化失败: {e}")
                except Exception:
                    pass
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass
                return

        try:
            from comtypes.gen.UIAutomationClient import (
                IUIAutomationTextPattern,
                IUIAutomationValuePattern,
                IUIAutomationLegacyIAccessiblePattern,
            )
        except Exception:
            IUIAutomationTextPattern = None
            IUIAutomationValuePattern = None
            IUIAutomationLegacyIAccessiblePattern = None

        UIA_TextPatternId = 10014
        UIA_ValuePatternId = 10002
        UIA_LegacyIAccessiblePatternId = 10018
        UIA_WindowControlTypeId = 50032

        def _uia_extract_text(elem) -> str:
            text = ""
            try:
                ctrl_type = int(elem.CurrentControlType or 0)
            except Exception:
                ctrl_type = 0
            try:
                name = str(elem.CurrentName or "").strip()
            except Exception:
                name = ""
            try:
                val_pat = elem.GetCurrentPattern(UIA_ValuePatternId)
                if val_pat is not None and IUIAutomationValuePattern is not None:
                    vp = val_pat.QueryInterface(IUIAutomationValuePattern)
                    v = str(vp.CurrentValue or "").strip()
                    if v:
                        return v
            except Exception:
                pass
            try:
                txt_pat = elem.GetCurrentPattern(UIA_TextPatternId)
                if txt_pat is not None and IUIAutomationTextPattern is not None:
                    tp = txt_pat.QueryInterface(IUIAutomationTextPattern)
                    doc = tp.DocumentRange
                    if doc is not None:
                        t = str(doc.GetText(-1) or "").strip()
                        if t:
                            return t
            except Exception:
                pass
            try:
                leg_pat = elem.GetCurrentPattern(UIA_LegacyIAccessiblePatternId)
                if leg_pat is not None and IUIAutomationLegacyIAccessiblePattern is not None:
                    lp = leg_pat.QueryInterface(IUIAutomationLegacyIAccessiblePattern)
                    v = str(lp.CurrentValue or "").strip()
                    if v:
                        return v
                    n = str(lp.CurrentName or "").strip()
                    if n:
                        return n
            except Exception:
                pass
            if ctrl_type != UIA_WindowControlTypeId and name:
                return name
            return text

        try:
            self.status.emit("Hook UIA 轮询已启动")
        except Exception:
            pass

        last_elem = None
        while not self._uia_stop.is_set() and not self.isInterruptionRequested():
            try:
                elem = uia.GetFocusedElement()
            except Exception:
                elem = None
            if elem is not None:
                try:
                    if int(elem.CurrentProcessId or 0) == int(self._pid):
                        if elem != last_elem:
                            last_elem = elem
                        text = _uia_extract_text(elem)
                        if text:
                            self._emit_text_with_source(text, "uia")
                except Exception:
                    pass
            time.sleep(0.2)

        try:
            comtypes.CoUninitialize()
        except Exception:
            pass

    def _frida_loop(self) -> None:
        try:
            self.status.emit("Hook Frida 线程运行中")
        except Exception:
            pass
        try:
            _ensure_hidden_console_for_console_children()
        except Exception:
            pass
        try:
            import frida
        except Exception as e:
            try:
                self.status.emit(f"Hook Frida 不可用: {e}")
            except Exception:
                pass
            return

        try:
            session = frida.attach(int(self._pid))
        except Exception as e:
            try:
                self.status.emit(f"Hook Frida 附加失败: {e}")
            except Exception:
                pass
            return

        out_port = int(self._listen_port or 37123)
        out_host = "127.0.0.1"
        try:
            mode = "ENABLED" if bool(self._enable_renpy_injection) else "DISABLED"
            self.status.emit(f"Hook Ren'Py injection mode: {mode}")
        except Exception:
            pass

        script_src = r"""
        const MAX_LEN = 500;
        const HOOK_HOST = "__HOOK_HOST__";
        const HOOK_PORT = __HOOK_PORT__;
        const ENABLE_RENPY_INJECTION = (__ENABLE_RENPY_INJECTION__ === 1);
        
        // Helper to find exports robustly
        function findExport(lib, name) {
          try {
            if (Module.findExportByName) return Module.findExportByName(lib, name);
          } catch(e) {}
          try {
             if (Module.getExportByName) return Module.getExportByName(lib, name);
          } catch(e) {}
          try {
             const mod = Process.getModuleByName(lib);
             if (mod) {
                if (mod.findExportByName) return mod.findExportByName(name);
                if (mod.getExportByName) return mod.getExportByName(name);
                const exps = mod.enumerateExports();
                for (let i = 0; i < exps.length; i++) {
                   if (exps[i].name === name) return exps[i].address;
                }
             }
          } catch(e) {}
          try {
             const mod = Process.findModuleByName(lib);
             if (mod) {
                if (mod.findExportByName) return mod.findExportByName(name);
                const exps = mod.enumerateExports();
                for (let i = 0; i < exps.length; i++) {
                   if (exps[i].name === name) return exps[i].address;
                }
             }
          } catch(e) {}
          return null;
        }

        function enumerateExports(modName) {
             try {
                 if (Module.enumerateExports) return Module.enumerateExports(modName);
             } catch(e) {}
             try {
                 const mod = Process.findModuleByName(modName);
                 if (mod && mod.enumerateExports) return mod.enumerateExports();
             } catch(e) {}
             return [];
        }

        send({ status: "frida_script_loaded" });
        try {
          send({ status: "debug_env: Interceptor=" + typeof Interceptor + 
                 ", NativeFunction=" + typeof NativeFunction + 
                 ", Module=" + typeof Module +
                 ", Memory=" + typeof Memory +
                 ", File=" + typeof File });
        } catch(e) {
          send({ status: "debug_env_fail: " + e });
        }
        try {
          send({ status: "debug_step_1" });
        } catch(e) {}
        function readW(ptr, len) {
          try {
            if (ptr.isNull()) return "";
            var safeLen = MAX_LEN;
            if (len !== undefined && len !== null) {
                var parsedLen = parseInt(len);
                if (parsedLen > 0) {
                    safeLen = Math.min(parsedLen, MAX_LEN);
                }
            }
            // Always provide a length to prevent reading until infinity (crash on non-null-terminated)
             var ret = ptr.readUtf16String(safeLen);
             if (ret) return ret.split('\0')[0];
             return "";
           } catch (e) { return ""; }
         }
        function readA(ptr, len) {
          try {
            if (ptr.isNull()) return "";
            var safeLen = MAX_LEN;
            if (len !== undefined && len !== null) {
                var parsedLen = parseInt(len);
                if (parsedLen > 0) {
                    safeLen = Math.min(parsedLen, MAX_LEN);
                }
            }
            var ret = ptr.readAnsiString(safeLen);
            if (ret) return ret.split('\0')[0];
            return "";
           } catch (e) { return ""; }
         }
        const BAD_STRINGS = {
            "voice": 1, "movie": 1, "overlay": 1, "transient": 1, "None": 1, "master": 1, 
            "splash_message": 1, "transform": 1, "image_placement": 1, "default": 1, 
            "bytecode": 1, "none": 1, "unicode": 1, "tex": 1, "suppress_overlay": 1, 
            "music": 1, "from": 1, "to": 1, "loop": 1, "True": 1, "python": 1, "label": 1, 
            "screens": 1, "main_menu": 1, "jump": 1, "if": 1, "call": 1, "audio": 1, 
            "t1": 1, "return": 1, "pass": 1, "False": 1, "gui": 1, "vbox": 1, "hbox": 1,
            "null": 1, "solid": 1, "frame": 1, "window": 1, "text": 1, "button": 1, "bar": 1,
            "viewport": 1, "imagemap": 1, "timer": 1, "key": 1, "input": 1, "grid": 1,
            "style_prefix": 1, "navigation_xpos": 1, "navigation_spacing": 1,
            "narrator": 1, "say": 1, "who": 1, "what": 1, "id": 1, "style": 1, "self": 1,
            "child": 1, "replaces": 1, "scope": 1, "function": 1, "focus": 1, "xalign": 1,
            "yalign": 1, "spacing": 1, "layout": 1, "clicked": 1, "text_style": 1,
            "substitute": 1, "text_": 1, "button_text": 1, "hovered": 1, "unhovered": 1,
            "action": 1, "say_window": 1, "title": 1, "main_menu_background": 1,
            "subpixel": 1, "ease_cubic": 1, "activate_sound": 1, "game_menu_background": 1,
            "scroll": 1, "context": 1, "vpfunc": 1, "scrollbars": 1, "vscrollbar": 1,
            "side_": 1, "positions": 1, "child_size": 1, "offsets": 1, "xadjustment": 1,
            "yadjustment": 1, "set_adjustments": 1, "mousewheel": 1, "draggable": 1,
            "edgescroll": 1, "xinitial": 1, "yinitial": 1, "role": 1, "time_policy": 1,
            "keymap": 1, "alternate": 1, "selected": 1, "sensitive": 1, "keysym": 1,
            "alternate_keysym": 1, "page_name_value": 1, "length": 1, "allow": 1,
            "exclude": 1, "prefix": 1, "suffix": 1, "ground": 1, "idle": 1, "hover": 1,
            "insensitive": 1, "selected_idle": 1, "selected_hover": 1, "st": 1, "at": 1,
            "range": 1, "value": 1, "changed": 1, "adjustment": 1, "step": 1, "page": 1,
            "xpos": 1, "ypos": 1, "xanchor": 1, "yanchor": 1, "xoffset": 1, "yoffset": 1,
            "xmaximum": 1, "ymaximum": 1, "xminimum": 1, "yminimum": 1, "xfill": 1, "yfill": 1,
            "top_padding": 1, "bottom_padding": 1, "left_padding": 1, "right_padding": 1,
            "top_margin": 1, "bottom_margin": 1, "left_margin": 1, "right_margin": 1,
            "size_group": 1, "events": 1, "trans": 1, "show": 1, "hide": 1, "scene": 1,
            "config": 1, "store": 1, "persistent": 1, "name": 1, "screen": 1
        };

        var _lastText = "";
        var _lastTime = 0;
        // Once Ren'Py injection succeeds, prefer injected Ren'Py socket text only.
        // This avoids PythonAPI/SDL/GDI noise flood on DDLC-like games.
        var _renpyTextOnly = false;
        
        // --- Rate Limiting & Safety ---
        var _globalMsgCount = 0;
        var _globalMsgTimer = 0;
        var _glyphDebugCount = 0; // Dedicated counter for glyph index debug sampling
        const MAX_MSG_PER_SEC = 120; // Keep up with fast VN text without dropping current lines
        var _startTime = Date.now();
        const STARTUP_DELAY_MS = 1000; // Wait 1s before sending text to avoid startup freeze
        const TYPEWRITER_SETTLE_MS = 120;

        // Universal Typewriter Buffer
        var _uCharBuf = "";
        var _uCharTimer = null;
        var _uCharLabel = "";

        // Growing Text Buffer (for "H", "He", "Hel"...)
        var _growBuf = "";
        var _growTimer = null;
        var _growLabel = "";
        var _growSent = false;

        function normalizePrefixBuf(s) {
            try {
                s = (s || "").toString();
                if (!s) return "";
                var allSame = true;
                for (var i = 1; i < s.length; i++) {
                    if (s[i] !== s[0]) { allSame = false; break; }
                }
                if (allSame) return s[0];
                if (s.length >= 4 && (s.length % 2) === 0) {
                    var isPair = true;
                    for (var i = 0; i < s.length; i += 2) {
                        if (s[i] !== s[i+1]) { isPair = false; break; }
                    }
                    if (isPair) {
                        var fixed = "";
                        for (var i = 0; i < s.length; i += 2) fixed += s[i];
                        return fixed;
                    }
                }
                return s;
            } catch(e) { return (s || "").toString(); }
        }

        function looksCompleteSentence(t, trimmed) {
            try {
                t = (t || "").toString();
                trimmed = (trimmed || "").toString();
                if (!t) return false;
                if (/[.!?\u3002\uff01\uff1f\u2026\u300d\u300f"']$/.test(trimmed)) return true;
                if (/[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]/.test(t)) return true;
                if (t.indexOf(" ") !== -1) return true;
                if (t.length >= 12) return true;
            } catch(e) {}
            return false;
        }

        function shouldSendImmediately(t, trimmed) {
            try {
                t = (t || "").toString();
                trimmed = (trimmed || "").toString();
                if (!t || !trimmed) return false;
                if (t.length >= 2) return true;
            } catch(e) {}
            return false;
        }

        function sendText(t, label) {
            try {
                if (_renpyTextOnly) return;
                // Startup Delay
                if (Date.now() - _startTime < STARTUP_DELAY_MS) return;
                
                t = (t || "").toString();
                var trimmed = t.trim();
                if (t.length === 0) return;

                var now = Date.now();
                if (_lastText && _lastText.length === 1 && (now - _lastTime) < 1200 && t.length >= 2) {
                    var last = _lastText;
                    if (/^[A-Za-z]$/.test(last) && /^[a-z]/.test(t) && !t.startsWith(last) && t[0] !== " " && t[0] !== "\u3000") {
                        t = last + t;
                        trimmed = t.trim();
                    }
                }

                if (t.length === 1 && _growBuf && !_growSent) {
                    if (/^[A-Za-z]$/.test(t) && /^[a-z]/.test(_growBuf) && !_growBuf.startsWith(t) && _growBuf[0] !== " " && _growBuf[0] !== "\u3000") {
                        _growBuf = t + _growBuf;
                        _growLabel = label || _growLabel;
                        if (_growTimer) clearTimeout(_growTimer);
                        _growTimer = setTimeout(function() {
                            if (_growBuf && !_growSent) {
                                sendTextInternal(_growBuf, _growLabel);
                                _growSent = true;
                            }
                        }, TYPEWRITER_SETTLE_MS);
                        return;
                    }
                }

                if (_lastText && _lastText.length >= 2 && _lastText.length <= 4 && t.length >= 3) {
                    var c = _lastText[0];
                    var allSame = true;
                    for (var i = 1; i < _lastText.length; i++) {
                        if (_lastText[i] !== c) { allSame = false; break; }
                    }
                    if (allSame) {
                        var cc = c.charCodeAt(0);
                        var t0 = t.charCodeAt(0);
                        if (cc >= 65 && cc <= 90 && t0 >= 97 && t0 <= 122 && !t.startsWith(c)) {
                            t = c + t;
                            trimmed = t.trim();
                        }
                    }
                }
                
                // --- Garbage / Hex Filters ---
                if (trimmed.startsWith("0x") || trimmed.startsWith("0X")) return; // Hex pointer
                if (trimmed.indexOf("\\u") !== -1) return; // Literal unicode escape
                if (/^[0-9A-Fa-f]{8,}$/.test(trimmed)) return; // Hex dump
                if (trimmed.length > 50 && trimmed.indexOf(" ") === -1 && !/[\u3000-\u9fff]/.test(trimmed)) return; // Long string no spaces/CJK
                
                // --- Growing Text Buffer (Typewriter Sentence) ---
                if (_growBuf) {
                    // Case A: Identical to buffer
                    if (t === _growBuf) {
                        return; // Ignore. If not sent, timer will send it. If sent, we ignore it (prevent duplicates).
                    }
                    
                    // Case B: Extension (Typewriter growth)
                    // Check if t starts with _growBuf
                    if (t.startsWith(_growBuf) && t.length > _growBuf.length) {
                        // Relaxed jump limit: < 50 chars (to allow for chunked updates but prevent merging unrelated text)
                        if (t.length - _growBuf.length < 50) {
                            _growBuf = t;
                            _growLabel = label || _growLabel;
                            _growSent = false; // Mark as unsent since it grew

                            if (shouldSendImmediately(_growBuf, _growBuf.trim())) {
                                sendTextInternal(_growBuf, _growLabel);
                                _growSent = true;
                            }
                            if (_growTimer) clearTimeout(_growTimer);
                            _growTimer = setTimeout(function() {
                                if (_growBuf && !_growSent) {
                                    sendTextInternal(_growBuf, _growLabel);
                                    _growSent = true;
                                    // We keep _growBuf to prevent re-sending if game keeps redrawing it
                                }
                            }, TYPEWRITER_SETTLE_MS); // short settle window for typewriter growth
                            return;
                        }
                    }
                }
                
                // --- Single Char Logic Integration ---
                if (t.length == 1) {
                    // Filter invalid single chars
                    if (!trimmed && t !== " " && t !== "\u3000") return;
                    
                    // Append to single char buffer
                    _uCharBuf += t;
                    _uCharLabel = label || "Typewriter";
                    
                    if (_uCharTimer) clearTimeout(_uCharTimer);
                    _uCharTimer = setTimeout(function() {
                        if (_uCharBuf && _uCharBuf.length > 0) {
                            sendTextInternal(_uCharBuf, _uCharLabel);
                            _uCharBuf = "";
                        }
                    }, TYPEWRITER_SETTLE_MS);
                    return;
                }
                
                // --- New Sentence / Flush ---
                
                // If we have unsent grow buffer, flush it now (because we are starting a new sentence/jump)
                if (_growBuf && !_growSent) {
                    sendTextInternal(_growBuf, _growLabel);
                }
                
                // Check if this new text supersedes the pending single char buffer
                var _uNorm = _uCharBuf ? normalizePrefixBuf(_uCharBuf) : "";
                if (_uNorm && t.startsWith(_uNorm)) {
                    _uCharBuf = ""; // Promote single char buffer to growing buffer
                    if (_uCharTimer) clearTimeout(_uCharTimer);
                } else if (_uNorm && _uNorm.length <= 6 && t.length > 1 && t[0] !== " " && t[0] !== "\u3000" && _uNorm[_uNorm.length - 1] !== " " && _uNorm[_uNorm.length - 1] !== "\u3000") {
                    _uCharBuf = "";
                    if (_uCharTimer) clearTimeout(_uCharTimer);
                    var joined = _uNorm + t;
                    _growBuf = joined;
                    _growLabel = label || _uCharLabel || _growLabel;
                    _growSent = false;
                    if (shouldSendImmediately(_growBuf, _growBuf.trim())) {
                        sendTextInternal(_growBuf, _growLabel);
                        _growSent = true;
                    }
                    if (_growTimer) clearTimeout(_growTimer);
                    _growTimer = setTimeout(function() {
                        if (_growBuf && !_growSent) {
                            sendTextInternal(_growBuf, _growLabel);
                            _growSent = true;
                        }
                    }, TYPEWRITER_SETTLE_MS);
                    return;
                } else if (_uNorm) {
                    // Flush single char buffer if not superseded
                    sendTextInternal(_uNorm, _uCharLabel);
                    _uCharBuf = "";
                    if (_uCharTimer) clearTimeout(_uCharTimer);
                }

                // Start new growing buffer
                _growBuf = t;
                _growLabel = label;
                _growSent = false;

                if (shouldSendImmediately(t, trimmed)) {
                    sendTextInternal(_growBuf, _growLabel);
                    _growSent = true;
                    if (_growTimer) clearTimeout(_growTimer);
                    _growTimer = null;
                }

                if (_growTimer) clearTimeout(_growTimer);
                _growTimer = setTimeout(function() {
                    if (_growBuf && !_growSent) {
                        sendTextInternal(_growBuf, _growLabel);
                        _growSent = true;
                    }
                }, TYPEWRITER_SETTLE_MS);
                
            } catch(e) {}
        }

        function sendTextInternal(t, label) {
             try {
                if (_renpyTextOnly) return;
                // Global Dedup for Shadow Rendering (same string sent twice within 200ms)
                var now = Date.now();
                if (t === _lastText && (now - _lastTime) < 200) {
                    _lastTime = now; // Update time to keep suppressing rapid fire
                    return;
                }
                
                // Aggressive Dedup for "11--22" style double text
                // If text is "11--22", convert to "1-2"
                if (t.length >= 4 && t.length % 2 === 0) {
                    var isDouble = true;
                    for (var i = 0; i < t.length - 1; i += 2) {
                        if (t[i] !== t[i+1]) {
                            isDouble = false;
                            break;
                        }
                    }
                    if (isDouble) {
                        var fixed = "";
                        for (var i = 0; i < t.length; i += 2) fixed += t[i];
                        t = fixed;
                        // After fixing, check dedup again just in case
                        if (t === _lastText && (now - _lastTime) < 200) return;
                    }
                }

                _lastText = t;
                _lastTime = now;

                // Allow single CJK characters to pass (range 0x4E00 - 0x9FFF)
                var hasCJK = false;
                for (var i = 0; i < t.length; i++) {
                    if (t.charCodeAt(i) >= 0x4E00 && t.charCodeAt(i) <= 0x9FFF) {
                        hasCJK = true;
                        break;
                    }
                }

                if (t.length < 2 && !hasCJK && !/^[a-zA-Z0-9]$/.test(t)) return;
                if (BAD_STRINGS[t]) return;
                
                // Substring Blacklist for UI Noise
                var tl = t.toLowerCase();
                var BAD_SUBSTRINGS = [
                    "test 1:", "test 2:", "test 3:", "test 4:", "test 5:", 
                    "pid:", "run screentranslator", "select this window", "click buttons below",
                    "ready...", "gettextextentpoint32w",
                    "running typewriter", "typewriter done",
                    "must be unicode", "expected a character buffer object",
                    "string index out of range", "bytearray index out of range",
                    "window was restored", "primary display bounds",
                    "windowed mode", "screen sizes:", "persistent.",
                    "main_menu", "py_repr"
                ];
                for (var i = 0; i < BAD_SUBSTRINGS.length; i++) {
                    if (tl.indexOf(BAD_SUBSTRINGS[i]) !== -1) return;
                }

                if (t.endsWith("$")) return;
                if (t.startsWith("_")) return;
                if (t.startsWith("%")) return;
                if (t.startsWith("<") && t.endsWith(">")) return;
                if (t.startsWith("[") && t.endsWith("]")) return;
                if (/^\d+\s*[-_/.:]\s*\d+$/.test(t)) return;
                if (/^[\{\}\[\]\(\)<>\-_=+*\/\\|~`!@#$%^&:;,.?\d\s]+$/.test(t)) return;
                
                // Block raw function names from being sent as text content if they slip through
                if (t === "GetTextExtentPoint32W" || t === "GetTextExtentExPointW" || t === "TextOutW" || t === "ExtTextOutW") return;

                if (t.startsWith("{") && t.endsWith("}")) return;
                
                // Ignore typical variable names (only alphanumeric+underscore, starts with lower case, no spaces)
                if (/^[a-z][a-z0-9_]*$/.test(t)) {
                    return;
                }
                
                // Also ignore strings that are ALL CAPS and underscores (constants) like "KC_RETURN"
                if (/^[A-Z][A-Z0-9_]*$/.test(t) && t.indexOf("_") > 0) return;

                if (t.indexOf("/") >= 0 || t.indexOf("\\") >= 0) {
                    if (t.indexOf(".rpy") > 0 || t.indexOf(".png") > 0 || t.indexOf(".jpg") > 0 || t.indexOf(".ogg") > 0) return;
                }
                
                // Filter Windows Menu items like "File(&F)", "Open(&O)..."
                if (/\(&[A-Z0-9]\)(\.\.\.)?$/.test(t)) return;

                const tid = Process.getCurrentThreadId();
                _globalMsgCount++;
                send({ text: t, threadId: tid, label: label || "unknown", source: "frida" });
            } catch(e) {}
        }
        function hookGdi(name, lib, handler) {
          try {
            // send({ status: "debug_hook_gdi_start: " + name });
            const addr = findExport(lib, name);
            // send({ status: "debug_hook_gdi_addr: " + name + " = " + addr });
            if (!addr) {
                // send({ status: "debug_miss: " + name });
                return false;
            }
            Interceptor.attach(addr, handler);
            send({ status: "debug_attach: " + name });
            return true;
          } catch(e) {
            send({ status: "debug_hook_gdi_inner_fail: " + name + " " + e });
            throw e;
          }
        }
        function findSymbolAny(targetName) {
          try {
            const addr = findExport(null, targetName);
            if (addr && !addr.isNull()) return addr;
          } catch (e) {}
          try {
            const sym = DebugSymbol.fromName(targetName);
            if (sym && sym.address && !sym.address.isNull()) return sym.address;
          } catch (e) {}
          try {
            const mods = Process.enumerateModules();
            for (let i = 0; i < mods.length; i++) {
              const exps = enumerateExports(mods[i].name);
              for (let j = 0; j < exps.length; j++) {
                if (exps[j].name === targetName) return exps[j].address;
              }
            }
          } catch (e) {}
          return null;
        }
        let pMultiByteToWideChar = null;
        let MB2WC = null;
        try {
          send({ status: "debug_mb2wc_step1" });
          pMultiByteToWideChar = findExport("kernel32.dll", "MultiByteToWideChar");
          send({ status: "debug_mb2wc_step2: " + pMultiByteToWideChar });
          
          if (pMultiByteToWideChar) {
              MB2WC = new NativeFunction(pMultiByteToWideChar, "int", ["uint", "uint", "pointer", "int", "pointer", "int"]);
          }
          send({ status: "debug_mb2wc_ok" });
        } catch(e) {
          send({ status: "debug_mb2wc_fail: " + e });
        }
        const seenMods = {};
        function reportModulesOnce() {
          try {
            const mods = Process.enumerateModules();
            for (let i = 0; i < mods.length; i++) {
              const name = mods[i].name.toLowerCase();
              if (seenMods[name]) continue;
              if (name.indexOf("renpy") >= 0 || name.indexOf("python") >= 0 || name.indexOf("sdl2") >= 0 || name.indexOf("ttf") >= 0) {
                seenMods[name] = true;
                send({ status: "module_seen", name: mods[i].name });
              }
            }
          } catch (e) {}
        }
        function tryInjectRenpy() {
          const pyrunSimple = findSymbolAny("PyRun_SimpleString");
          const pyrunString = findSymbolAny("PyRun_StringFlags") || findSymbolAny("PyRun_String");
          const pyrunFlags = pyrunSimple ? null : findSymbolAny("PyRun_SimpleStringFlags");
          const pImportAddModule = findSymbolAny("PyImport_AddModule");
          const pModuleGetDict = findSymbolAny("PyModule_GetDict");
          if (!pyrunSimple && !pyrunFlags && !pyrunString) {
            // send({ status: "renpy_no_pyrun" });
            return false;
          }
          const ensure = findSymbolAny("PyGILState_Ensure");
          const release = findSymbolAny("PyGILState_Release");
          const initThreads = findSymbolAny("PyEval_InitThreads");
          let PyRunFlags = null;
          let PyRunSimple = null;
          let PyRunStringFlags = null;
          let pyFlagsPtr = ptr(0);
          try {
            if (pyrunFlags) {
              PyRunFlags = new NativeFunction(pyrunFlags, "int", ["pointer", "pointer"]);
              pyFlagsPtr = Memory.alloc(16);
              Memory.writeU32(pyFlagsPtr, 0);
            }
          } catch (e) {
            send({ status: "renpy_pyrun_ctor_failed", error: "flags: " + e });
          }
          try {
            if (pyrunSimple) PyRunSimple = new NativeFunction(pyrunSimple, "int", ["pointer"]);
          } catch (e) {
            send({ status: "renpy_pyrun_ctor_failed", error: "simple: " + e });
          }
          try {
            if (pyrunString) PyRunStringFlags = new NativeFunction(pyrunString, "pointer", ["pointer", "int", "pointer", "pointer", "pointer"]);
          } catch (e) {
            send({ status: "renpy_pyrun_ctor_failed", error: "string: " + e });
          }
          const PyImport_AddModule = pImportAddModule ? new NativeFunction(pImportAddModule, "pointer", ["pointer"]) : null;
          const PyModule_GetDict = pModuleGetDict ? new NativeFunction(pModuleGetDict, "pointer", ["pointer"]) : null;
          if (!PyRunFlags && !PyRunSimple && !PyRunStringFlags) {
            return false;
          }
          const PyGIL_Ensure = ensure ? new NativeFunction(ensure, "int", []) : null;
          const PyGIL_Release = release ? new NativeFunction(release, "void", ["int"]) : null;
          const PyEval_InitThreads = initThreads ? new NativeFunction(initThreads, "void", []) : null;
          const pPyErrOccurred = findSymbolAny("PyErr_Occurred");
          const pPyErrFetch = findSymbolAny("PyErr_Fetch");
          const pPyErrNormalize = findSymbolAny("PyErr_NormalizeException");
          const pPyObjectStr = findSymbolAny("PyObject_Str");
          const pPyObjectRepr = findSymbolAny("PyObject_Repr");
          const pPyStringAsString = findSymbolAny("PyString_AsString");
          const pPyUnicodeAsUTF8String = findSymbolAny("PyUnicodeUCS2_AsUTF8String") || findSymbolAny("PyUnicode_AsUTF8String");
          const pPyDecRef = findSymbolAny("Py_DecRef");
          const pPyEvalGetBuiltins = findSymbolAny("PyEval_GetBuiltins");
          const pPyDictSetItemString = findSymbolAny("PyDict_SetItemString");
          const PyErr_Occurred = pPyErrOccurred ? new NativeFunction(pPyErrOccurred, "pointer", []) : null;
          const PyErr_Fetch = pPyErrFetch ? new NativeFunction(pPyErrFetch, "void", ["pointer", "pointer", "pointer"]) : null;
          const PyErr_NormalizeException = pPyErrNormalize ? new NativeFunction(pPyErrNormalize, "void", ["pointer", "pointer", "pointer"]) : null;
          const PyObject_Str = pPyObjectStr ? new NativeFunction(pPyObjectStr, "pointer", ["pointer"]) : null;
          const PyObject_Repr = pPyObjectRepr ? new NativeFunction(pPyObjectRepr, "pointer", ["pointer"]) : null;
          const PyString_AsString = pPyStringAsString ? new NativeFunction(pPyStringAsString, "pointer", ["pointer"]) : null;
          const PyUnicode_AsUTF8String = pPyUnicodeAsUTF8String ? new NativeFunction(pPyUnicodeAsUTF8String, "pointer", ["pointer"]) : null;
          const Py_DecRef = pPyDecRef ? new NativeFunction(pPyDecRef, "void", ["pointer"]) : null;
          const PyEval_GetBuiltins = pPyEvalGetBuiltins ? new NativeFunction(pPyEvalGetBuiltins, "pointer", []) : null;
          const PyDict_SetItemString = pPyDictSetItemString ? new NativeFunction(pPyDictSetItemString, "int", ["pointer", "pointer", "pointer"]) : null;
          send({ status: "renpy_pyrun_found" });
          function pyObjToUtf8(obj) {
            if (!obj || obj.isNull()) return "";
            function readStringObj(strObj) {
              if (!strObj || strObj.isNull() || !PyString_AsString) return "";
              try {
                const p = PyString_AsString(strObj);
                if (p && !p.isNull()) return readA(p, MAX_LEN);
              } catch (e) {}
              return "";
            }
            let tmp = ptr(0);
            try {
              let direct = readStringObj(obj);
              if (direct) return direct;
              if (PyUnicode_AsUTF8String) {
                try {
                  tmp = PyUnicode_AsUTF8String(obj);
                  const text = readStringObj(tmp);
                  if (text) return text;
                } catch (e) {}
                if (tmp && !tmp.isNull() && Py_DecRef) {
                  try { Py_DecRef(tmp); } catch (e) {}
                }
                tmp = ptr(0);
              }
              if (PyObject_Str) {
                try {
                  tmp = PyObject_Str(obj);
                  const text = readStringObj(tmp);
                  if (text) return text;
                } catch (e) {}
                if (tmp && !tmp.isNull() && Py_DecRef) {
                  try { Py_DecRef(tmp); } catch (e) {}
                }
                tmp = ptr(0);
              }
              if (PyObject_Repr) {
                try {
                  tmp = PyObject_Repr(obj);
                  const text = readStringObj(tmp);
                  if (text) return text;
                } catch (e) {}
              }
            } catch (e) {}
            finally {
              if (tmp && !tmp.isNull() && Py_DecRef) {
                try { Py_DecRef(tmp); } catch (e) {}
              }
            }
            return "";
          }
          function readPyErrorDetail() {
            try {
              if (!PyErr_Occurred || !PyErr_Fetch) return "";
              const current = PyErr_Occurred();
              if (!current || current.isNull()) return "";
              const ptype = Memory.alloc(Process.pointerSize);
              const pvalue = Memory.alloc(Process.pointerSize);
              const ptb = Memory.alloc(Process.pointerSize);
              Memory.writePointer(ptype, ptr(0));
              Memory.writePointer(pvalue, ptr(0));
              Memory.writePointer(ptb, ptr(0));
              PyErr_Fetch(ptype, pvalue, ptb);
              if (PyErr_NormalizeException) {
                try { PyErr_NormalizeException(ptype, pvalue, ptb); } catch (e) {}
              }
              const typeObj = Memory.readPointer(ptype);
              const valueObj = Memory.readPointer(pvalue);
              const tbObj = Memory.readPointer(ptb);
              const parts = [];
              const typeText = pyObjToUtf8(typeObj);
              const valueText = pyObjToUtf8(valueObj);
              if (typeText) parts.push(typeText);
              if (valueText && valueText !== typeText) parts.push(valueText);
              [typeObj, valueObj, tbObj].forEach(function (obj) {
                if (obj && !obj.isNull() && Py_DecRef) {
                  try { Py_DecRef(obj); } catch (e) {}
                }
              });
              return parts.join(": ");
            } catch (e) {
              return "";
            }
          }
          function getMainDict() {
            try {
              if (!PyImport_AddModule || !PyModule_GetDict) return ptr(0);
              const mainName = Memory.allocUtf8String("__main__");
              const mod = PyImport_AddModule(mainName);
              if (!mod || mod.isNull()) return ptr(0);
              const d = PyModule_GetDict(mod);
              if (!d || d.isNull()) return ptr(0);
              if (PyEval_GetBuiltins && PyDict_SetItemString) {
                try {
                  const builtinsDict = PyEval_GetBuiltins();
                  if (builtinsDict && !builtinsDict.isNull()) {
                    const builtinsName = Memory.allocUtf8String("__builtins__");
                    PyDict_SetItemString(d, builtinsName, builtinsDict);
                  }
                } catch (e) {}
              }
              return d;
            } catch (e) {
              return ptr(0);
            }
          }
          function runPyCode(codePtr) {
            if (PyRunSimple) {
              try {
                return PyRunSimple(codePtr) === 0;
              } catch (e) {}
            }
            if (PyRunFlags) {
              try {
                return PyRunFlags(codePtr, pyFlagsPtr) === 0;
              } catch (e) {}
            }
            if (PyRunStringFlags) {
              try {
                const mainDict = getMainDict();
                if (mainDict && !mainDict.isNull()) {
                  const result = PyRunStringFlags(codePtr, 257, mainDict, mainDict, pyFlagsPtr);
                  if (!result || result.isNull()) return false;
                  if (Py_DecRef) {
                    try { Py_DecRef(result); } catch (e) {}
                  }
                  return true;
                }
              } catch (e) {}
            }
            return false;
          }
          const codeLines = [
            "import threading, time, socket, re, json",
            "BAD_STRINGS = {",
            "    'voice', 'movie', 'overlay', 'transient', 'None', 'master',",
            "    'splash_message', 'transform', 'image_placement', 'default',",
            "    'bytecode', 'none', 'unicode', 'tex', 'suppress_overlay',",
            "    'music', 'from', 'to', 'loop', 'True', 'python', 'label',",
            "    'screens', 'main_menu', 'jump', 'if', 'call', 'audio',",
            "    't1', 'return', 'pass', 'False', 'gui', 'vbox', 'hbox',",
            "    'null', 'solid', 'frame', 'window', 'text', 'button', 'bar',",
            "    'viewport', 'imagemap', 'timer', 'key', 'input', 'grid',",
            "    'style_prefix', 'navigation_xpos', 'navigation_spacing',",
            "    'narrator', 'say', 'who', 'what', 'id', 'style', 'self',",
            "    'child', 'replaces', 'scope', 'function', 'focus', 'xalign',",
            "    'yalign', 'spacing', 'layout', 'clicked', 'text_style',",
            "    'substitute', 'text_', 'button_text', 'hovered', 'unhovered',",
            "    'action', 'say_window', 'title', 'main_menu_background',",
            "    'subpixel', 'ease_cubic', 'activate_sound', 'game_menu_background',",
            "    'scroll', 'context', 'vpfunc', 'scrollbars', 'vscrollbar',",
            "    'side_', 'positions', 'child_size', 'offsets', 'xadjustment',",
            "    'yadjustment', 'set_adjustments', 'mousewheel', 'draggable',",
            "    'edgescroll', 'xinitial', 'yinitial', 'role', 'time_policy',",
            "    'keymap', 'alternate', 'selected', 'sensitive', 'keysym',",
            "    'alternate_keysym', 'page_name_value', 'length', 'allow',",
            "    'exclude', 'prefix', 'suffix', 'ground', 'idle', 'hover',",
            "    'insensitive', 'selected_idle', 'selected_hover', 'st', 'at',",
            "    'range', 'value', 'changed', 'adjustment', 'step', 'page',",
            "    'xpos', 'ypos', 'xanchor', 'yanchor', 'xoffset', 'yoffset',",
            "    'xmaximum', 'ymaximum', 'xminimum', 'yminimum', 'xfill', 'yfill',",
            "    'top_padding', 'bottom_padding', 'left_padding', 'right_padding',",
            "    'top_margin', 'bottom_margin', 'left_margin', 'right_margin',",
            "    'size_group', 'events', 'trans', 'show', 'hide', 'scene',",
            "    'config', 'store', 'persistent', 'name', 'screen'",
            "}",
            "try:",
            "    _st_text_type = unicode",
            "    _st_basestring = basestring",
            "except NameError:",
            "    _st_text_type = str",
            "    _st_basestring = str",
            "_st_last = ''",
            "_st_last_ts = 0.0",
            "_st_last_live_ts = 0.0",
            "_st_live_source_seen = False",
            "def _st_to_text(v, depth=0):",
            "    try:",
            "        if depth > 5:",
            "            return ''",
            "        if v is None:",
            "            return ''",
            "        if isinstance(v, _st_text_type):",
            "            return v",
            "        if isinstance(v, _st_basestring):",
            "            try:",
            "                return v.decode('utf-8', 'ignore')",
            "            except Exception:",
            "                try: return _st_text_type(v)",
            "                except Exception: return ''",
            "        if isinstance(v, (list, tuple)):",
            "            out = []",
            "            for it in v:",
            "                s = _st_to_text(it, depth + 1)",
            "                if s: out.append(s)",
            "            return u' '.join(out)",
            "        if isinstance(v, dict):",
            "            out = []",
            "            for k in ('what', 'text', 'say', 'content', 'value'):",
            "                if k in v:",
            "                    s = _st_to_text(v.get(k), depth + 1)",
            "                    if s: out.append(s)",
            "            if out: return u' '.join(out)",
            "        for attr in ('what', 'text', 'string', 'contents', 'content'):",
            "            try:",
            "                if hasattr(v, attr):",
            "                    s = _st_to_text(getattr(v, attr), depth + 1)",
            "                    if s: return s",
            "            except Exception:",
            "                pass",
            "        try:",
            "            return _st_text_type(v)",
            "        except Exception:",
            "            return ''",
            "    except Exception:",
            "        return ''",
            "def _st_clean(s):",
            "    try:",
            "        s = _st_to_text(s)",
            "        if not s:",
            "            return ''",
            "        # remove Ren'Py text tags like {w}, {i}... while keeping plain text",
            "        s = re.sub(r'\\{[^{}]{0,80}\\}', '', s)",
            "        s = ' '.join(s.splitlines())",
            "        s = re.sub(r'\\s+', ' ', s).strip()",
            "        return s",
            "    except Exception:",
            "        return ''",
            "def _st_is_live_label(label):",
            "    try:",
            "        ll = _st_text_type(label or '').strip().lower()",
            "        if not ll: return False",
            "        if ll.startswith('renpy:patch:say_menu_text_filter'):",
            "            return False",
            "        if ll.startswith('renpy:patch:') or ll.startswith('renpy:text:') or ll.startswith('renpy:dtext:') or ll.startswith('renpy:character:'):",
            "            return True",
            "        if ll.startswith('renpy:interact:'):",
            "            return True",
            "        if ll.startswith('renpy:poll:screen:') or ll.startswith('renpy:poll:last_say_what') or ll.startswith('renpy:poll:last_say'):",
            "            return True",
            "        return False",
            "    except Exception:",
            "        return False",
            "def _st_send_raw(msg, label='renpy'):",
            "    c = None",
            "    try:",
            "        if msg is None: return",
            "        payload = {'text': _st_clean(msg), 'source': 'socket', 'label': (label or 'renpy')}",
            "        if not payload['text']: return",
            "        data = (json.dumps(payload, ensure_ascii=False) + '\\n').encode('utf-8', 'ignore')",
            "        c = socket.socket()",
            "        c.connect(('__HOOK_HOST__', __HOOK_PORT__))",
            "        c.send(data)",
            "    except Exception:",
            "        pass",
            "    try:",
            "        if c is not None: c.close()",
            "    except Exception:",
            "        pass",
            "def _st_send(t, label='renpy'):",
            "    try:",
            "        if t is None: return",
            "        global _st_last, _st_last_ts, _st_last_live_ts, _st_live_source_seen",
            "        s = _st_clean(t)",
            "        if not s: return",
            "        now_ts = time.time()",
            "        if s == _st_last and (now_ts - float(_st_last_ts or 0.0)) < 0.65: return",
            "        ll = _st_text_type(label or 'renpy').strip().lower()",
            "        if ll.startswith('renpy:patch:say_menu_text_filter'):",
            "            return",
            "        if ll.startswith('renpy:poll:history') and _st_live_source_seen:",
            "            return",
            "        if len(s) < 2:",
            "            keep_short = False",
            "            try:",
            "                for _ch in s:",
            "                    _cp = ord(_ch)",
            "                    if (0x3040 <= _cp <= 0x30FF) or (0x4E00 <= _cp <= 0x9FFF) or (0xAC00 <= _cp <= 0xD7AF):",
            "                        keep_short = True",
            "                        break",
            "            except Exception:",
            "                keep_short = False",
            "            if not keep_short: return",
            "        if s == '[HOOK_READY]':",
            "            _st_last = s",
            "            _st_last_ts = now_ts",
            "            _st_send_raw(s, label or 'renpy:ready')",
            "            return",
            "        if s in BAD_STRINGS: return",
            "        if s.startswith('_'): return",
            "        if s.startswith('<') and s.endswith('>'): return",
            "        if s.startswith('[') and s.endswith(']'): return",
            "        if s.startswith('{') and s.endswith('}'): return",
            "        if s.endswith('$'): return",
            "        if s.startswith('%'): return",
            "        low = s.lower()",
            "        if 'must be unicode, not str' in low: return",
            "        if 'expected a character buffer object' in low: return",
            "        if 'string index out of range' in low: return",
            "        if 'argument 1 must be unicode, not str' in low: return",
            "        if re.match(r'^\\d+\\s*[-_/.:]\\s*\\d+$', s): return",
            "        if re.match(r'^[\\{\\}\\[\\]\\(\\)<>\\-_=+*/\\\\|~`!@#$%^&:;,.?\\d\\s]+$', s):",
            "            if not ll.startswith('renpy:patch:') and not ll.startswith('renpy:interact:') and not ll.startswith('renpy:poll:'):",
            "                return",
            "            if len(s) <= 2:",
            "                return",
            "        # Ignore snake_case variables (lowercase start, alphanumeric+underscore)",
            "        if re.match(r'^[a-z][a-z0-9_]*$', s): return",
            "        # Ignore path-like strings",
            "        if '/' in s or '\\\\' in s:",
            "             if '.rpy' in s or '.png' in s or '.jpg' in s or '.ogg' in s: return",
            "        if not _st_is_dialogue_like(s):",
            "            if not _st_is_live_label(ll):",
            "                return",
            "            if len(s) > 220:",
            "                return",
            "        if _st_is_live_label(ll):",
            "            _st_live_source_seen = True",
            "            _st_last_live_ts = time.time()",
            "        _st_last = s",
            "        _st_last_ts = now_ts",
            "        _st_send_raw(s, label)",
            "    except Exception:",
            "        pass",
            "def _st_is_dialogue_like(s):",
            "    try:",
            "        if not s: return False",
            "        if len(s) < 2: return False",
            "        low = s.lower()",
            "        bad_sub = [",
            "            '.png', '.jpg', '.ogg', '.rpy', '.rpa', 'persistent.', 'menu_', 'screen ',",
            "            'transform', 'viewport', 'imagemap', 'dissolve(', 'return ', 'python:', 'label '",
            "        ]",
            "        for b in bad_sub:",
            "            if b in low: return False",
            "        if low.startswith('c:') or low.startswith('pk'):",
            "            return False",
            "        # too code-like",
            "        sym = 0",
            "        tot = 0",
            "        for ch in s:",
            "            if ch.isspace():",
            "                continue",
            "            tot += 1",
            "            if ch in '{}[]()<>|\\\\/*=:+`~$%^&_#@':",
            "                sym += 1",
            "        if tot > 0 and (float(sym) / float(tot)) > 0.35:",
            "            return False",
            "        return True",
            "    except Exception:",
            "        return False",
            "def _st_get_screen_what(prefix):",
            "    try:",
            "        import renpy",
            "    except Exception:",
            "        return '', ''",
            "    try:",
            "        gv = getattr(renpy, 'get_screen_variable', None)",
            "        if not gv:",
            "            ex = getattr(renpy, 'exports', None)",
            "            gv = getattr(ex, 'get_screen_variable', None) if ex is not None else None",
            "        if not gv:",
            "            return '', ''",
            "        for screen_name in ('say', 'nvl', 'bubble'):",
            "            try:",
            "                v = gv('what', screen=screen_name)",
            "                s = _st_clean(v)",
            "                if s:",
            "                    return s, (prefix + ':' + screen_name)",
            "            except Exception:",
            "                pass",
            "    except Exception:",
            "        pass",
            "    return '', ''",
            "def _st_poll_dialog(rev):",
            "    while True:",
            "        try:",
            "            try:",
            "                import renpy",
            "                if getattr(renpy, '_st_poll_rev', 0) != rev:",
            "                    return",
            "            except Exception:",
            "                pass",
            "            try:",
            "                if _st_live_source_seen and (time.time() - float(_st_last_live_ts or 0.0)) < 1.25:",
            "                    time.sleep(0.08)",
            "                    continue",
            "            except Exception:",
            "                pass",
            "            t = ''",
            "            label = 'renpy:poll'",
            "            try:",
            "                t, label = _st_get_screen_what('renpy:poll:screen')",
            "            except Exception:",
            "                t, label = '', 'renpy:poll'",
            "            try:",
            "                if not t:",
            "                    import renpy",
            "                    t = renpy.exports.last_say_what() or ''",
            "                    if t: label = 'renpy:poll:last_say_what'",
            "            except Exception:",
            "                t = ''",
            "            if not t:",
            "                try:",
            "                    import renpy",
            "                    ls = renpy.exports.last_say()",
            "                    if isinstance(ls, (list, tuple)) and len(ls) >= 2:",
            "                        t = ls[1] or ''",
            "                        if t: label = 'renpy:poll:last_say'",
            "                except Exception:",
            "                    t = ''",
            "            if not t:",
            "                try:",
            "                    import renpy",
            "                    h = getattr(renpy.store, '_history_list', None)",
            "                    if h and len(h) > 0:",
            "                        for x in reversed(h):",
            "                            k = _st_text_type(getattr(x, 'kind', '') or '').lower()",
            "                            if k != 'current':",
            "                                continue",
            "                            t = getattr(x, 'what', '') or ''",
            "                            if t:",
            "                                label = 'renpy:poll:history_current'",
            "                                break",
            "                except Exception:",
            "                    t = ''",
            "            if t:",
            "                _st_send(t, label)",
            "        except Exception:",
            "            pass",
            "        time.sleep(0.08)",
            "def _st_patch():",
            "    try:",
            "        import renpy",
            "    except Exception:",
            "        return False",
            "    try:",
            "        if getattr(renpy, '_st_patch_rev', 0) >= 10:",
            "            return True",
            "    except Exception:",
            "        pass",
            "    ok = False",
            "    try:",
            "        old_say = renpy.exports.say",
            "        def say(*args, **kwargs):",
            "            try:",
            "                _w = kwargs.get('what', None)",
            "                if _w is None and len(args) >= 2: _w = args[1]",
            "                if _w is None and len(args) >= 1: _w = args[-1]",
            "                _st_send(_w, 'renpy:patch:say')",
            "            except Exception: pass",
            "            return old_say(*args, **kwargs)",
            "        renpy.exports.say = say",
            "        ok = True",
            "    except Exception:",
            "        pass",
            "    try:",
            "        old_ds = getattr(renpy.exports, 'display_say', None)",
            "        if old_ds:",
            "            def display_say(*args, **kwargs):",
            "                try:",
            "                    _w = kwargs.get('what', None)",
            "                    if _w is None and len(args) >= 2: _w = args[1]",
            "                    if _w is None and len(args) >= 1: _w = args[-1]",
            "                    _st_send(_w, 'renpy:patch:display_say')",
            "                except Exception: pass",
            "                return old_ds(*args, **kwargs)",
            "            renpy.exports.display_say = display_say",
            "            ok = True",
            "    except Exception:",
            "        pass",
            "    try:",
            "        old_rpy_ds = getattr(renpy, 'display_say', None)",
            "        if old_rpy_ds:",
            "            def rpy_display_say(*args, **kwargs):",
            "                try:",
            "                    _w = kwargs.get('what', None)",
            "                    if _w is None and len(args) >= 2: _w = args[1]",
            "                    if _w is None and len(args) >= 1: _w = args[-1]",
            "                    _st_send(_w, 'renpy:patch:renpy_display_say')",
            "                except Exception: pass",
            "                return old_rpy_ds(*args, **kwargs)",
            "            renpy.display_say = rpy_display_say",
            "            ok = True",
            "    except Exception:",
            "        pass",
            "    try:",
            "        old_rpy_say = getattr(renpy, 'say', None)",
            "        if old_rpy_say:",
            "            def rpy_say(*args, **kwargs):",
            "                try:",
            "                    _w = kwargs.get('what', None)",
            "                    if _w is None and len(args) >= 2: _w = args[1]",
            "                    if _w is None and len(args) >= 1: _w = args[-1]",
            "                    _st_send(_w, 'renpy:patch:renpy_say')",
            "                except Exception: pass",
            "                return old_rpy_say(*args, **kwargs)",
            "            renpy.say = rpy_say",
            "            ok = True",
            "    except Exception:",
            "        pass",
            "    try:",
            "        old_utter = renpy.exports.utter",
            "        def utter(what, *args, **kwargs):",
            "            try: _st_send(what, 'renpy:patch:utter')",
            "            except Exception: pass",
            "            return old_utter(what, *args, **kwargs)",
            "        renpy.exports.utter = utter",
            "        ok = True",
            "    except Exception:",
            "        pass",
            "    try:",
            "        # Keep original filter untouched: this hook is noisy and often out-of-order.",
            "        _ = getattr(renpy.config, 'say_menu_text_filter', None)",
            "    except Exception:",
            "        pass",
            "    try:",
            "        import renpy.character as _st_char",
            "        old_ccall = _st_char.Character.__call__",
            "        def ccall(self, what, *args, **kwargs):",
            "            try: _st_send(what, 'renpy:character:call')",
            "            except Exception: pass",
            "            return old_ccall(self, what, *args, **kwargs)",
            "        _st_char.Character.__call__ = ccall",
            "        ok = True",
            "    except Exception:",
            "        pass",
            "    try:",
            "        import renpy.character as _st_char",
            "        adv = getattr(_st_char, 'ADVCharacter', None)",
            "        if adv and hasattr(adv, '__call__'):",
            "            old_adv_call = adv.__call__",
            "            def adv_call(self, what, *args, **kwargs):",
            "                try: _st_send(what, 'renpy:character:adv_call')",
            "                except Exception: pass",
            "                return old_adv_call(self, what, *args, **kwargs)",
            "            adv.__call__ = adv_call",
            "            ok = True",
            "    except Exception:",
            "        pass",
            "    try:",
            "        import renpy.text.text as _st_text",
            "        old_init = _st_text.Text.__init__",
            "        def tinit(self, text, *args, **kwargs):",
            "            try:",
            "                style = kwargs.get('style', '') if kwargs else ''",
            "                style_s = _st_text_type(style or '').lower()",
            "                if ('say_dialogue' in style_s) or ('nvl_dialogue' in style_s) or ('say' == style_s):",
            "                    s = _st_clean(text)",
            "                    if _st_is_dialogue_like(s):",
            "                        _st_send(s, 'renpy:text:init')",
            "            except Exception:",
            "                pass",
            "            return old_init(self, text, *args, **kwargs)",
            "        _st_text.Text.__init__ = tinit",
            "        ok = True",
            "    except Exception:",
            "        pass",
            "    try:",
            "        import renpy.text.text as _st_text",
            "        old_set = getattr(_st_text.Text, 'set_text', None)",
            "        if old_set:",
            "            def tset(self, text, *args, **kwargs):",
            "                try:",
            "                    style = getattr(self, 'style', '')",
            "                    style_s = _st_text_type(style or '').lower()",
            "                    if ('say_dialogue' in style_s) or ('nvl_dialogue' in style_s) or ('say' == style_s):",
            "                        s = _st_clean(text)",
            "                        if _st_is_dialogue_like(s):",
            "                            _st_send(s, 'renpy:text:set_text')",
            "                except Exception:",
            "                    pass",
            "                return old_set(self, text, *args, **kwargs)",
            "            _st_text.Text.set_text = tset",
            "            ok = True",
            "    except Exception:",
            "        pass",
            "    try:",
            "        import renpy.display.text as _st_dtext",
            "        dtext = getattr(_st_dtext, 'Text', None)",
            "        if dtext and hasattr(dtext, '__init__'):",
            "            old_dinit = dtext.__init__",
            "            def dinit(self, text, *args, **kwargs):",
            "                try:",
            "                    s = _st_clean(text)",
            "                    if _st_is_dialogue_like(s):",
            "                        _st_send(s, 'renpy:dtext:init')",
            "                except Exception:",
            "                    pass",
            "                return old_dinit(self, text, *args, **kwargs)",
            "            dtext.__init__ = dinit",
            "            ok = True",
            "        old_dset = getattr(dtext, 'set_text', None) if dtext else None",
            "        if old_dset:",
            "            def dset(self, text, *args, **kwargs):",
            "                try:",
            "                    s = _st_clean(text)",
            "                    if _st_is_dialogue_like(s):",
            "                        _st_send(s, 'renpy:dtext:set_text')",
            "                except Exception:",
            "                    pass",
            "                return old_dset(self, text, *args, **kwargs)",
            "            dtext.set_text = dset",
            "            ok = True",
            "    except Exception:",
            "        pass",
            "    try:",
            "        import renpy.display.core as _st_core",
            "        old_interact = getattr(_st_core.Interface, 'interact', None)",
            "        if old_interact:",
            "            def _st_interact(self, *args, **kwargs):",
            "                rv = old_interact(self, *args, **kwargs)",
            "                try:",
            "                    import renpy",
            "                    t = ''",
            "                    label = 'renpy:interact'",
            "                    try:",
            "                        t, label = _st_get_screen_what('renpy:interact:screen')",
            "                    except Exception:",
            "                        t, label = '', 'renpy:interact'",
            "                    try:",
            "                        if not t:",
            "                            t = renpy.exports.last_say_what() or ''",
            "                            if t: label = 'renpy:interact:last_say_what'",
            "                    except Exception:",
            "                        t = ''",
            "                    if not t:",
            "                        try:",
            "                            ls = renpy.exports.last_say()",
            "                            if isinstance(ls, (list, tuple)) and len(ls) >= 2:",
            "                                t = ls[1] or ''",
            "                                if t: label = 'renpy:interact:last_say'",
            "                        except Exception:",
            "                            t = ''",
            "                    if not t:",
            "                        try:",
            "                            h = getattr(renpy.store, '_history_list', None)",
            "                            if h and len(h) > 0:",
            "                                for x in reversed(h):",
            "                                    k = _st_text_type(getattr(x, 'kind', '') or '').lower()",
            "                                    if k != 'current':",
            "                                        continue",
            "                                    t = getattr(x, 'what', '') or ''",
            "                                    if t:",
            "                                        label = 'renpy:interact:history_current'",
            "                                        break",
            "                        except Exception:",
            "                            t = ''",
            "                    if t:",
            "                        _st_send(t, label)",
                "                except Exception:",
                "                    pass",
                "                return rv",
            "            _st_core.Interface.interact = _st_interact",
            "            ok = True",
            "    except Exception:",
            "        pass",
            "    if not ok:",
            "        try:",
            "            ex = getattr(renpy, 'exports', None)",
            "            if ex and (hasattr(ex, 'last_say_what') or hasattr(ex, 'last_say')):",
            "                ok = True",
            "        except Exception:",
            "            pass",
            "    if ok:",
            "        try:",
            "            renpy._st_patched = True",
            "            renpy._st_patch_rev = 10",
            "        except Exception: pass",
            "    # Avoid patching high-frequency text internals to reduce noise/instability on Ren'Py 6/7",
            "    return ok",
            "def _st_install():",
            "    while True:",
            "        try:",
            "            import renpy",
            "        except Exception:",
            "            time.sleep(0.5)",
            "            continue",
            "        try:",
            "            if _st_patch():",
            "                try:",
            "                    import renpy",
            "                    if getattr(renpy, '_st_poll_rev', 0) < 10:",
            "                        renpy._st_poll_started = True",
            "                        renpy._st_poll_rev = 10",
            "                        _th_poll = threading.Thread(target=_st_poll_dialog, args=(10,))",
            "                        try: _th_poll.setDaemon(True)",
            "                        except Exception:",
            "                            try: _th_poll.daemon = True",
            "                            except Exception: pass",
            "                        _th_poll.start()",
            "                except Exception:",
            "                    pass",
            "                _st_send('[HOOK_READY]', 'renpy:ready')",
            "                break",
            "        except Exception:",
            "            pass",
            "        time.sleep(0.5)",
            "_th_install = threading.Thread(target=_st_install)",
            "try: _th_install.setDaemon(True)",
            "except Exception:",
            "    try: _th_install.daemon = True",
            "    except Exception: pass",
            "_th_install.start()",
          ];
          const minimalCodeLines = [
            "import time, json",
            "try:",
            "    import thread as _st_thread",
            "except Exception:",
            "    _st_thread = None",
            "try:",
            "    import _socket as _st_sockmod",
            "except Exception:",
            "    try:",
            "        import socket as _st_sockmod",
            "    except Exception:",
            "        _st_sockmod = None",
            "try:",
            "    _st_text_type = unicode",
            "    _st_basestring = basestring",
            "except NameError:",
            "    _st_text_type = str",
            "    _st_basestring = str",
            "_st_last = u''",
            "_st_last_ts = 0.0",
            "def _st_to_text(v, depth=0):",
            "    try:",
            "        if depth > 4 or v is None: return u''",
            "        if isinstance(v, _st_text_type): return v",
            "        if isinstance(v, _st_basestring):",
            "            try: return v.decode('utf-8', 'ignore')",
            "            except Exception:",
            "                try: return _st_text_type(v)",
            "                except Exception: return u''",
            "        if isinstance(v, (list, tuple)):",
            "            out = []",
            "            for it in v:",
            "                s = _st_to_text(it, depth + 1)",
            "                if s: out.append(s)",
            "            return u' '.join(out)",
            "        if isinstance(v, dict):",
            "            out = []",
            "            for k in ('what', 'text', 'say', 'content', 'value'):",
            "                if k in v:",
            "                    s = _st_to_text(v.get(k), depth + 1)",
            "                    if s: out.append(s)",
            "            if out: return u' '.join(out)",
            "        for attr in ('what', 'text', 'string', 'content', 'contents'):",
            "            try:",
            "                if hasattr(v, attr):",
            "                    s = _st_to_text(getattr(v, attr), depth + 1)",
            "                    if s: return s",
            "            except Exception:",
            "                pass",
            "        try: return _st_text_type(v)",
            "        except Exception: return u''",
            "    except Exception:",
            "        return u''",
            "def _st_strip_tags(s):",
            "    try:",
            "        out = []",
            "        depth = 0",
            "        for ch in s:",
            "            if ch == '{':",
            "                depth += 1",
            "                continue",
            "            if depth > 0:",
            "                if ch == '}':",
            "                    depth -= 1",
            "                continue",
            "            out.append(ch)",
            "        return u''.join(out)",
            "    except Exception:",
            "        return s",
            "def _st_clean(s):",
            "    try:",
            "        s = _st_to_text(s)",
            "        if not s: return u''",
            "        s = _st_strip_tags(s)",
            "        s = s.replace(u'\\r', u' ').replace(u'\\n', u' ').replace(u'\\t', u' ')",
            "        s = u' '.join(s.split())",
            "        return s.strip()",
            "    except Exception:",
            "        return u''",
            "def _st_is_dialogue_like(s):",
            "    try:",
            "        if not s or len(s) < 2: return False",
            "        low = s.lower()",
            "        for bad in ('.png', '.jpg', '.ogg', '.rpy', '.rpa', '.save', 'persistent.', 'main_menu', 'menu_', 'viewport', 'screen ', 'transform', 'python:', 'label ', 'style_prefix', 'button_text', 'say_window', 'keymap', 'xalign', 'yalign', 'child_size'):",
            "            if bad in low: return False",
            "        for bad in ('==', '!=', '<=', '>=', '::', 'return ', 'call ', 'jump ', ' if ', ' elif ', ' while ', ' for ', 'lambda ', ' not ', ' and ', ' or '):",
            "            if bad in low: return False",
            "        if s.startswith('_') or s.startswith('%') or s.endswith('$'): return False",
            "        if s.startswith('<') and s.endswith('>'): return False",
            "        if s.startswith('[') and s.endswith(']'): return False",
            "        if s.startswith('{') and s.endswith('}'): return False",
            "        if len(s) >= 2 and s[1:2] == ':' and s[0:1].isalpha(): return False",
            "        only_word = True",
            "        has_us = False",
            "        for ch in s:",
            "            if not (ch.isalnum() or ch == '_'):",
            "                only_word = False",
            "                break",
            "            if ch == '_': has_us = True",
            "        if only_word and has_us and s[:1].islower(): return False",
            "        total = 0",
            "        sym = 0",
            "        for ch in s:",
            "            if ch.isspace(): continue",
            "            total += 1",
            "            if ch in u'{}[]()<>|\\\\/*=:+`~$%^&_#@':",
            "                sym += 1",
            "        if total > 0 and (float(sym) / float(total)) > 0.30: return False",
            "        return True",
            "    except Exception:",
            "        return False",
            "def _st_send_raw(msg, label=u'renpy'):",
            "    if _st_sockmod is None or not msg: return",
            "    c = None",
            "    try:",
            "        payload = {u'text': _st_clean(msg), u'source': u'socket', u'label': _st_to_text(label or u'renpy')}",
            "        if not payload[u'text']: return",
            "        data = json.dumps(payload, ensure_ascii=False)",
            "        c = _st_sockmod.socket(_st_sockmod.AF_INET, _st_sockmod.SOCK_STREAM)",
            "        c.connect(('__HOOK_HOST__', __HOOK_PORT__))",
            "        if isinstance(data, _st_text_type):",
            "            data = (data + u'\\n').encode('utf-8', 'ignore')",
            "        else:",
            "            data = (str(data) + '\\n').encode('utf-8', 'ignore')",
            "        c.send(data)",
            "    except Exception:",
            "        pass",
            "    try:",
            "        if c is not None: c.close()",
            "    except Exception:",
            "        pass",
            "def _st_send(t, label=u'renpy'):",
            "    global _st_last, _st_last_ts",
            "    try:",
            "        s = _st_clean(t)",
            "        if not s: return",
            "        _now = time.time()",
            "        if s == _st_last and (_now - float(_st_last_ts or 0.0)) < 0.65: return",
            "        if len(s) < 2:",
            "            _keep_short = False",
            "            try:",
            "                for _ch in s:",
            "                    _cp = ord(_ch)",
            "                    if (0x3040 <= _cp <= 0x30FF) or (0x4E00 <= _cp <= 0x9FFF) or (0xAC00 <= _cp <= 0xD7AF):",
            "                        _keep_short = True",
            "                        break",
            "            except Exception:",
            "                _keep_short = False",
            "            if not _keep_short: return",
            "        if s == u'[HOOK_READY]':",
            "            _st_last = s",
            "            _st_last_ts = _now",
            "            _st_send_raw(s, label or u'renpy:ready')",
            "            return",
            "        if not _st_is_dialogue_like(s): return",
            "        _st_last = s",
            "        _st_last_ts = _now",
            "        _st_send_raw(s, label)",
            "    except Exception:",
            "        pass",
            "def _st_get_screen_what(prefix):",
            "    try:",
            "        import renpy",
            "    except Exception:",
            "        return u'', u''",
            "    try:",
            "        gv = getattr(renpy, 'get_screen_variable', None)",
            "        if not gv:",
            "            ex = getattr(renpy, 'exports', None)",
            "            gv = getattr(ex, 'get_screen_variable', None) if ex is not None else None",
            "        if not gv:",
            "            return u'', u''",
            "        for screen_name in (u'say', u'nvl', u'bubble'):",
            "            try:",
            "                v = gv('what', screen=screen_name)",
            "                s = _st_clean(v)",
            "                if s:",
            "                    return s, (_st_to_text(prefix) + u':' + screen_name)",
            "            except Exception:",
            "                pass",
            "    except Exception:",
            "        pass",
            "    return u'', u''",
            "def _st_probe():",
            "    t = u''",
            "    label = u'renpy:poll'",
            "    try:",
            "        import renpy",
            "    except Exception:",
            "        return u'', label",
            "    try:",
            "        try:",
            "            t, label = _st_get_screen_what(u'renpy:poll:screen')",
            "        except Exception:",
            "            t, label = u'', u'renpy:poll'",
            "        ex = getattr(renpy, 'exports', None)",
            "        if (not t) and ex is not None:",
            "            try:",
            "                t = ex.last_say_what() or u''",
            "                if t: label = u'renpy:poll:last_say_what'",
            "            except Exception: t = u''",
            "            if not t:",
            "                try:",
            "                    ls = ex.last_say()",
            "                    if isinstance(ls, (list, tuple)) and len(ls) >= 2:",
            "                        t = ls[1] or u''",
            "                        if t: label = u'renpy:poll:last_say'",
            "                except Exception:",
            "                    t = u''",
            "        if not t:",
            "            try:",
            "                st = getattr(renpy, 'store', None)",
            "                h = getattr(st, '_history_list', None) if st is not None else None",
            "                if h and len(h) > 0:",
            "                    for x in reversed(h):",
            "                        k = _st_to_text(getattr(x, 'kind', u'') or u'').lower()",
            "                        if k != u'current':",
            "                            continue",
            "                        t = getattr(x, 'what', u'') or u''",
            "                        if t:",
            "                            label = u'renpy:poll:history_current'",
            "                            break",
            "            except Exception:",
            "                t = u''",
            "    except Exception:",
            "        t = u''",
            "    return t, label",
            "def _st_loop(rev):",
            "    while True:",
            "        try:",
            "            try:",
            "                import renpy",
            "                if getattr(renpy, '_st_poll_rev', 0) != rev: return",
            "            except Exception:",
            "                pass",
            "            t, label = _st_probe()",
            "            _st_send(t, label)",
            "        except Exception:",
            "            pass",
            "        time.sleep(0.08)",
            "try:",
            "    import renpy",
            "    if getattr(renpy, '_st_poll_rev', 0) < 10:",
            "        renpy._st_poll_started = True",
            "        renpy._st_poll_rev = 10",
            "        if _st_thread is not None:",
            "            _st_thread.start_new_thread(_st_loop, (10,))",
            "        _st_send_raw('[HOOK_READY]', u'renpy:ready')",
            "except Exception:",
            "    pass",
          ];
          const fullBody = codeLines.join("\n");
          const minimalBody = minimalCodeLines.join("\n");
          function buildWrapped(scriptBody) {
            return [
              "_st_src = " + JSON.stringify(scriptBody),
              "try:",
              "    _st_co = compile(_st_src, '<st_hook>', 'exec')",
              "    eval(_st_co, globals(), globals())",
              "except Exception as e:",
              "    try:",
              "        import _socket as _st_sockmod",
              "        _st_msg = '[HOOK_ERR] ' + e.__class__.__name__ + ': ' + str(e)",
              "        _st_c = _st_sockmod.socket(_st_sockmod.AF_INET, _st_sockmod.SOCK_STREAM)",
              "        _st_c.connect(('__HOOK_HOST__', __HOOK_PORT__))",
              "        try:",
              "            _st_data = (_st_msg + '\\n').encode('utf-8', 'ignore')",
              "        except Exception:",
              "            _st_data = (_st_msg + '\\n')",
              "        _st_c.send(_st_data)",
              "        _st_c.close()",
              "    except Exception:",
              "        pass",
            ].join("\n");
          }
          function pyQuoteSingle(text) {
            try {
              return "'" + String(text || "").replace(/\\/g, "\\\\").replace(/'/g, "\\'") + "'";
            } catch (e) {
              return "''";
            }
          }
          function writeRenpyTempScript(scriptText) {
            try {
              if (typeof File === "undefined") {
                send({ status: "debug_renpy_file_unavailable" });
                return null;
              }
              let basePath = "";
              try {
                if (Process.mainModule && Process.mainModule.path) {
                  basePath = String(Process.mainModule.path || "");
                }
              } catch (e) {}
              if (!basePath) {
                send({ status: "debug_renpy_file_nopath" });
                return null;
              }
              const norm = basePath.replace(/\\/g, "/");
              const slash = norm.lastIndexOf("/");
              const dir = slash >= 0 ? norm.substring(0, slash) : ".";
              const path = dir + "/st_hook_" + HOOK_PORT + ".py";
              const file = new File(path, "wb");
              try {
                file.write(scriptText);
                file.flush();
              } finally {
                try { file.close(); } catch (e) {}
              }
              send({ status: "debug_renpy_file_written: " + path });
              return path;
            } catch (e) {
              send({ status: "debug_renpy_file_write_failed: " + e });
              return null;
            }
          }
          function runRenpyScript(scriptBody) {
            const result = {
              ok: false,
              pyOk: false,
              fileOk: false,
              pyDetail: "",
              fileDetail: "",
              scriptPath: "",
            };
            try {
              const wrappedText = buildWrapped(scriptBody);
              const buf = Memory.allocUtf8String(wrappedText);
              try {
                result.pyOk = runPyCode(buf);
              } catch (e) {
                result.pyDetail = "PyRun exception: " + e;
                return result;
              }
              if (!result.pyOk) {
                result.pyDetail = readPyErrorDetail() || "";
                result.scriptPath = writeRenpyTempScript(scriptBody) || "";
                if (result.scriptPath) {
                  try {
                    const execCode = "execfile(" + pyQuoteSingle(result.scriptPath.replace(/\//g, "\\\\")) + ")";
                    const execBuf = Memory.allocUtf8String(execCode);
                    send({ status: "debug_renpy_execfile_try: " + result.scriptPath });
                    result.fileOk = runPyCode(execBuf);
                  } catch (e) {
                    result.fileDetail = "execfile exception: " + e;
                  }
                  if (!result.fileOk && !result.fileDetail) {
                    result.fileDetail = readPyErrorDetail() || "";
                  }
                  if (!result.fileOk) {
                    send({ status: "debug_renpy_execfile_failed: " + (result.fileDetail || "unknown") });
                  }
                }
              }
              result.ok = !!(result.pyOk || result.fileOk);
            } catch (e) {
              result.fileDetail = (e && e.toString) ? e.toString() : "";
            }
            return result;
          }
          const smoke = Memory.allocUtf8String("x=1");
          try {
            let state = 0;
            if (PyEval_InitThreads) PyEval_InitThreads();
            if (PyGIL_Ensure) state = PyGIL_Ensure();
            let smokeOk = false;
            try {
              smokeOk = runPyCode(smoke);
            } catch (e) {
              send({ status: "renpy_inject_failed", error: "PyRun smoke exception: " + e });
              if (PyGIL_Release) PyGIL_Release(state);
              return false;
            }
            if (!smokeOk) {
              const detail = readPyErrorDetail();
              if (PyGIL_Release) PyGIL_Release(state);
              send({ status: "renpy_inject_failed", error: detail ? ("PyRun smoke failed " + detail) : "PyRun smoke failed" });
              return false;
            }
            const fullResult = runRenpyScript(fullBody);
            if (fullResult.ok) {
              if (PyGIL_Release) PyGIL_Release(state);
              _renpyTextOnly = false;
              send({ status: "renpy_injected" });
              return true;
            }
            let fullDetail = fullResult.pyDetail || "";
            if (fullResult.fileDetail) {
              fullDetail = fullDetail ? (fullDetail + " | execfile " + fullResult.fileDetail) : ("execfile " + fullResult.fileDetail);
            }
            if (fullDetail) {
              send({ status: "debug_renpy_full_failed: " + fullDetail });
            }
            const minimalResult = runRenpyScript(minimalBody);
            if (PyGIL_Release) PyGIL_Release(state);
            if (!minimalResult.ok) {
              let detail = fullDetail || "";
              let minimalDetail = minimalResult.pyDetail || "";
              if (minimalResult.fileDetail) {
                minimalDetail = minimalDetail ? (minimalDetail + " | execfile " + minimalResult.fileDetail) : ("execfile " + minimalResult.fileDetail);
              }
              if (minimalDetail) {
                detail = detail ? (detail + " | minimal " + minimalDetail) : ("minimal " + minimalDetail);
              }
              send({ status: "renpy_inject_failed", error: detail ? ("PyRun failed " + detail) : "PyRun failed" });
              return false;
            }
            _renpyTextOnly = true;
            send({ status: "renpy_injected_minimal" });
            return true;
          } catch (e) {
            send({ status: "renpy_inject_failed", error: (e && e.toString) ? e.toString() : "" });
            return false;
          }
        }
        function hookMono() {
          const mono = Process.findModuleByName("mono-2.0-bdwgc.dll") || Process.findModuleByName("mono.dll");
          if (!mono) return false;
          function hookExport(name, handler) {
            const addr = findExport(mono.name, name);
            if (!addr) return false;
            Interceptor.attach(addr, handler);
            return true;
          }
          let ok = false;
          ok = hookExport("mono_string_new", {
            onEnter(args) {
              const text = readA(args[1]);
              sendText(text);
            }
          }) || ok;
          ok = hookExport("mono_string_new_len", {
            onEnter(args) {
              const text = readA(args[1], args[2]);
              sendText(text);
            }
          }) || ok;
          ok = hookExport("mono_string_new_utf16", {
            onEnter(args) {
              const text = readW(args[1], args[2]);
              sendText(text);
            }
          }) || ok;
          
          return ok;
        }
        function hookIl2cpp() {
          const mod = Process.findModuleByName("GameAssembly.dll") || Process.findModuleByName("il2cpp.dll");
          if (!mod) return false;
          function hookExport(name, handler) {
            const addr = findExport(mod.name, name);
            if (!addr) return false;
            Interceptor.attach(addr, handler);
            return true;
          }
          let ok = false;
          ok = hookExport("il2cpp_string_new", {
            onEnter(args) {
              const text = readA(args[0]);
              sendText(text);
            }
          }) || ok;
          ok = hookExport("il2cpp_string_new_len", {
            onEnter(args) {
              const text = readA(args[0], args[1]);
              sendText(text);
            }
          }) || ok;
          ok = hookExport("il2cpp_string_new_utf16", {
            onEnter(args) {
              const text = readW(args[0], args[1]);
              sendText(text);
            }
          }) || ok;
          ok = hookExport("il2cpp_string_new_utf8", {
            onEnter(args) {
              const text = readA(args[0]);
              sendText(text);
            }
          }) || ok;
          return ok;
        }
        function hookD3DPresent() {
          let ok = false;
          const d3d9 = findExport("d3d9.dll", "Direct3DCreate9");
          if (d3d9) ok = true;
          const d3d11 = findExport("d3d11.dll", "D3D11CreateDevice");
          if (d3d11) ok = true;
          if (ok) send({ status: "d3d_detected" });
          return ok;
        }
        function hookGdiExtras() {
          const gdi32 = "gdi32.dll";
          let ok = false;
          
          // Hook GetGlyphIndices (converts string to indices - catch it here!)
          const ggiW = findExport(gdi32, "GetGlyphIndicesW");
          if (ggiW) {
              Interceptor.attach(ggiW, {
                  onEnter(args) {
                      const count = args[2].toInt32();
                      if (count > 0) {
                          const text = readW(args[1], count);
                          sendText(text, "GetGlyphIndicesW");
                      }
                  }
              });
              ok = true;
          }
          const ggiA = findExport(gdi32, "GetGlyphIndicesA");
          if (ggiA) {
              Interceptor.attach(ggiA, {
                  onEnter(args) {
                      const count = args[2].toInt32();
                      if (count > 0) {
                          const text = readA(args[1], count);
                          sendText(text, "GetGlyphIndicesA");
                      }
                  }
              });
              ok = true;
          }
          
          // Hook GetCharacterPlacement (another text prep function)
          const gcpW = findExport(gdi32, "GetCharacterPlacementW");
          if (gcpW) {
              Interceptor.attach(gcpW, {
                  onEnter(args) {
                      const count = args[2].toInt32();
                      if (count > 0) {
                          const text = readW(args[1], count);
                          sendText(text, "GetCharacterPlacementW");
                      }
                  }
              });
              ok = true;
          }
          
          if (ok) send({ status: "debug_gdi_extras_hooked" });
          return ok;
        }
        function hookGetGlyphOutline() {
          send({ status: "debug_hook_glyph_start" });
          
          // Buffer for character accumulation (GDI draws char by char)
          var _glBuf = "";
          var _glTimer = null;
          
          function cleanDoubles(s) {
              if (!s || s.length < 2) return s;
              
              // Heuristic: if > 40% of chars are duplicates of previous char, treat as double stream
              // e.g. "bbooookk" (4/8=0.5), "11--22" (0.5), "77 55" (2/5=0.4)
              // "book" (1/4=0.25), "committee" (3/9=0.33)
              var dupCount = 0;
              for (var i = 0; i < s.length - 1; i++) {
                  if (s[i] === s[i+1]) dupCount++;
              }
              
              if (dupCount / s.length >= 0.40) {
                  var res = "";
                  for (var i = 0; i < s.length; i++) {
                      if (i < s.length - 1 && s[i] === s[i+1]) {
                          res += s[i];
                          i++; // Skip next
                      } else {
                          res += s[i];
                      }
                  }
                  return res;
              }
              return s;
          }

          function sendGl(t) {
              if (_glTimer) clearTimeout(_glTimer);
              _glBuf += t;
              _glTimer = setTimeout(function() {
                  if (_glBuf) {
                       var finalT = cleanDoubles(_glBuf);
                       sendText(finalT, "GetGlyphOutline");
                       _glBuf = "";
                   }
              }, 150);
          }

          const gdi32 = "gdi32.dll";
          let ok = false;
          const addrW = findExport(gdi32, "GetGlyphOutlineW");
          // send({ status: "debug_hook_glyph_addrW: " + addrW });
          if (addrW) {
            Interceptor.attach(addrW, {
              onEnter(args) {
                try {
                  const uChar = args[1].toInt32();
                  // send({ status: "GLYPH_HIT: " + uChar });
                  if (uChar > 0 && uChar < 0x10000) {
                     sendGl(String.fromCharCode(uChar));
                  }
                } catch (e) {}
              }
            });
            send({ status: "debug_glyph_w_attached" });
            ok = true;
          }
          const addrA = findExport(gdi32, "GetGlyphOutlineA");
          // send({ status: "debug_hook_glyph_addrA: " + addrA });
          if (addrA) {
             Interceptor.attach(addrA, {
              onEnter(args) {
                try {
                  const uChar = args[1].toInt32();
                  // send({ status: "GLYPH_HIT_A: " + uChar });
                  if (uChar > 0) {
                     if (uChar < 128) {
                        sendGl(String.fromCharCode(uChar));
                     } else if (MB2WC) {
                        const mem = Memory.alloc(8);
                        if (uChar > 0xFF) {
                            const high = (uChar >> 8) & 0xFF;
                            const low = uChar & 0xFF;
                            mem.writeU8(high);
                            mem.add(1).writeU8(low);
                            mem.add(2).writeU8(0);
                        } else {
                            mem.writeU8(uChar);
                            mem.add(1).writeU8(0);
                        }
                        const outBuf = Memory.alloc(16);
                        // CP_ACP = 0
                        const ret = MB2WC(0, 0, mem, -1, outBuf, 8);
                        if (ret > 0) {
                           sendGl(outBuf.readUtf16String());
                        }
                     }
                  }
                } catch (e) {}
              }
            });
            send({ status: "debug_glyph_a_attached" });
            ok = true;
          }
          return ok;
        }
        function hookSDLTTF() {
          let modName = null;
          try {
            const mods = Process.enumerateModules();
            for (let i = 0; i < mods.length; i++) {
              const name = mods[i].name.toLowerCase();
              if (name.indexOf("sdl2_ttf") >= 0 || name.indexOf("sdl2ttf") >= 0 || name.indexOf("ttf") >= 0) {
                modName = mods[i].name;
                break;
              }
            }
          } catch (e) {}
          const utf8Fns = [
            "TTF_RenderUTF8_Blended",
            "TTF_RenderUTF8_Shaded",
            "TTF_RenderUTF8_Solid",
            "TTF_RenderUTF8_Blended_Wrapped",
            "TTF_RenderUTF8_Shaded_Wrapped",
            "TTF_RenderUTF8_Solid_Wrapped",
            "TTF_RenderText_Blended",
            "TTF_RenderText_Shaded",
            "TTF_RenderText_Solid",
            "TTF_RenderText_Blended_Wrapped",
            "TTF_RenderText_Shaded_Wrapped",
            "TTF_RenderText_Solid_Wrapped"
          ];
          const uniFns = [
            "TTF_RenderUNICODE_Blended",
            "TTF_RenderUNICODE_Shaded",
            "TTF_RenderUNICODE_Solid",
            "TTF_RenderUNICODE_Blended_Wrapped",
            "TTF_RenderUNICODE_Shaded_Wrapped",
            "TTF_RenderUNICODE_Solid_Wrapped"
          ];
          const glyphFns = [
            "TTF_RenderGlyph_Solid",
            "TTF_RenderGlyph_Shaded",
            "TTF_RenderGlyph_Blended",
            "TTF_RenderGlyph_LCD",
            "TTF_RenderGlyph_LCD_V",
            "TTF_RenderGlyph32_Solid",
            "TTF_RenderGlyph32_Shaded",
            "TTF_RenderGlyph32_Blended",
            "TTF_RenderGlyph32_LCD",
            "TTF_RenderGlyph32_LCD_V"
          ];
          function hookAddr(addr, handler) {
            try {
              if (!addr || addr.isNull()) return false;
              Interceptor.attach(addr, handler);
              return true;
            } catch (e) {
              return false;
            }
          }
          function hookExport(name, handler) {
            try {
              const addr = findExport(modName, name);
              if (!addr || addr.isNull()) return false;
              Interceptor.attach(addr, handler);
              return true;
            } catch (e) {
              return false;
            }
          }
          let ok = false;
          const utf8Handler = {
            onEnter(args) {
              const text = readA(args[1]);
              sendText(text, "SDL_TTF_UTF8");
            }
          };
          const uniHandler = {
            onEnter(args) {
              const text = readW(args[1]);
              sendText(text, "SDL_TTF_UNICODE");
            }
          };
          const glyphHandler = {
            onEnter(args) {
              try {
                const cp = parseInt(args[1]) || 0;
                if (!cp) return;
                const ch = String.fromCodePoint(cp);
                sendText(ch, "SDL_TTF_GLYPH");
              } catch (e) {}
            }
          };
          if (modName) {
            utf8Fns.forEach(fn => {
              ok = hookExport(fn, utf8Handler) || ok;
            });
            uniFns.forEach(fn => {
              ok = hookExport(fn, uniHandler) || ok;
            });
            glyphFns.forEach(fn => {
              ok = hookExport(fn, glyphHandler) || ok;
            });
          } else {
            utf8Fns.forEach(fn => {
              const addr = findSymbolAny(fn);
              ok = hookAddr(addr, utf8Handler) || ok;
            });
            uniFns.forEach(fn => {
              const addr = findSymbolAny(fn);
              ok = hookAddr(addr, uniHandler) || ok;
            });
            glyphFns.forEach(fn => {
              const addr = findSymbolAny(fn);
              ok = hookAddr(addr, glyphHandler) || ok;
            });
          }
          return ok;
        }
        function hookPythonAPI() {
          const targets = [
            "PyUnicode_FromString",
            "PyUnicode_FromStringAndSize",
            "PyUnicode_FromWideChar",
            "PyUnicode_DecodeUTF8",
            "PyUnicode_Decode",
            "PyString_FromString",
            "PyString_FromStringAndSize"
          ];
          const hooked = new Set();
          function hookAddr(addr, name) {
            if (!addr || addr.isNull()) return false;
            if (hooked.has(name + "@" + addr)) return false;
            hooked.add(name + "@" + addr);
            Interceptor.attach(addr, {
              onEnter(args) {
                try {
                    let text = "";
                    if (name.indexOf("WideChar") >= 0) {
                      text = readW(args[0], args[1]);
                    } else if (name.indexOf("FromStringAndSize") >= 0 || name.indexOf("DecodeUTF8") >= 0) {
                      text = readA(args[0], args[1]);
                    } else if (name.indexOf("FromString") >= 0 || name.indexOf("Decode") >= 0) {
                      text = readA(args[0]);
                    }
                    // Optimized filter for high-frequency calls
                    if (text && text.length > 1) {
                         const s = text.trim();
                         const sl = s.toLowerCase();
                         const isPyString = name.indexOf("PyString_") === 0;
                         const likelyDialogue =
                           /[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]/.test(s) ||
                           (/[A-Za-z]/.test(s) && (/\s/.test(s) || /[.!?,"']$/.test(s) || s.length >= 12));
                         if (!s) return;
                         if (s.indexOf(".py") !== -1 || s.indexOf("/") !== -1 || s.indexOf("\\") !== -1 || s.indexOf("<") !== -1) return;
                         if (/^[a-z]:$/i.test(sl) || /^\.[a-z0-9]{2,5}$/.test(sl)) return;
                         if (/\.(png|jpe?g|ogg|mp3|wav|dll|exe|rpy|rpa|save)\b/i.test(sl)) return;
                         if (/(==|!=|<=|>=|::|->|\{#|\bpersistent\.|\bmain_menu\b|\bviewport\b|\bstyle_prefix\b|\bbutton_text\b|\bsay_window\b|\bkeymap\b|\bxalign\b|\byalign\b|\bchild_size\b|\bpy_repr\b)/i.test(sl)) return;
                         if (/^\([^)]*\)$/.test(s) && /\b(not|and|or|if|elif|else)\b/i.test(sl)) return;
                         if (/^[a-z_][a-z0-9_]*\([^)]*\)$/i.test(sl)) return;
                         if (/\b[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*\b/i.test(sl)) return;
                         if (isPyString && !likelyDialogue) return;
                         sendText(s, "PythonAPI:" + name);
                    }
                } catch(e) {}
              }
            });
            return true;
          }
          function hookModuleExports(mod) {
            let ok = false;
            try {
              const exps = enumerateExports(mod.name);
              for (let i = 0; i < exps.length; i++) {
                const e = exps[i];
                if (targets.indexOf(e.name) >= 0) {
                  ok = hookAddr(e.address, e.name) || ok;
                }
              }
            } catch (e) {}
            return ok;
          }
          let ok = false;
          targets.forEach(name => {
            try {
              const addr = findSymbolAny(name);
              ok = hookAddr(addr, name) || ok;
            } catch (e) {}
          });
          try {
            const mods = Process.enumerateModules();
            for (let i = 0; i < mods.length; i++) {
              const name = mods[i].name.toLowerCase();
              if (name.indexOf("python") >= 0 || name.indexOf("renpy") >= 0 || name.indexOf("libpy") >= 0) {
                ok = hookModuleExports(mods[i]) || ok;
              }
            }
            if (!ok) {
              for (let i = 0; i < mods.length; i++) {
                ok = hookModuleExports(mods[i]) || ok;
              }
            }
          } catch (e) {}
          return ok;
        }
        let sdlHooked = false;
        let pyHooked = false;
        let renpyInjected = false;
        let renpyDisabled = !ENABLE_RENPY_INJECTION;
        let renpyDisabledNotified = false;
        const renpyLightMode = !!ENABLE_RENPY_INJECTION;
        let sdlAttempts = 0;
        let pyAttempts = 0;
        let renpyAttempts = 0;
        let renpyForceAttempts = 0;
        let retryStarted = false;
        function tryHookSDLTTF() {
          if (sdlHooked) return true;
          if (sdlAttempts === 0) send({ status: "sdl_ttf_trying" });
          const ok = hookSDLTTF();
          if (ok) {
            sdlHooked = true;
            send({ status: "sdl_ttf_hooked" });
          }
          return ok;
        }
        function tryHookPython() {
          if (pyHooked) return true;
          if (pyAttempts === 0) send({ status: "python_trying" });
          const ok = hookPythonAPI();
          if (ok) {
            pyHooked = true;
            send({ status: "python_hooked" });
          }
          return ok;
        }
        function tryInjectRenpyOnce() {
          if (!ENABLE_RENPY_INJECTION || renpyDisabled) {
            if (!renpyDisabledNotified) {
              renpyDisabledNotified = true;
              send({ status: "renpy_disabled", reason: "stability_mode" });
            }
            return false;
          }
          if (renpyInjected) return true;
          if (renpyAttempts === 0) send({ status: "renpy_trying" });
          const ok = tryInjectRenpy();
          if (ok) {
            renpyInjected = true;
          } else {
            renpyDisabled = true;
            if (!renpyDisabledNotified) {
              renpyDisabledNotified = true;
              send({ status: "renpy_disabled", reason: "inject_failed" });
            }
          }
          return ok;
        }
        function forceRenpyTick() {
          if (!ENABLE_RENPY_INJECTION || renpyDisabled) return;
          if (renpyInjected) return;
          renpyForceAttempts += 1;
          if (renpyForceAttempts === 1) send({ status: "renpy_force_start" });
          const ok = tryInjectRenpy();
          if (ok) {
            renpyInjected = true;
            return;
          }
          renpyDisabled = true;
          if (!renpyDisabledNotified) {
            renpyDisabledNotified = true;
            send({ status: "renpy_disabled", reason: "force_failed" });
          }
          return;
          if (renpyForceAttempts >= 80) {
            send({ status: "renpy_force_failed" });
            return;
          }
          setTimeout(forceRenpyTick, 500);
        }
        function retryHooks() {
          try {
            if (!retryStarted) {
              retryStarted = true;
              send({ status: "retry_started" });
            }
            reportModulesOnce();
            if (!renpyLightMode && !sdlHooked) {
              sdlAttempts += 1;
              tryHookSDLTTF();
              if (!sdlHooked && sdlAttempts === 1) send({ status: "sdl_ttf_retrying" });
              if (!sdlHooked && sdlAttempts === 40) send({ status: "sdl_ttf_not_found" });
            }
            if (!renpyLightMode && !pyHooked) {
              pyAttempts += 1;
              tryHookPython();
              if (!pyHooked && pyAttempts === 1) send({ status: "python_retrying" });
              if (!pyHooked && pyAttempts === 40) send({ status: "python_not_found" });
            }
            if (ENABLE_RENPY_INJECTION && !renpyDisabled && !renpyInjected) {
              renpyAttempts += 1;
              tryInjectRenpyOnce();
              if (!renpyInjected && renpyAttempts === 1) send({ status: "renpy_retrying" });
              if (!renpyInjected && renpyAttempts === 40) send({ status: "renpy_inject_failed" });
            }
            const needRenpy = ENABLE_RENPY_INJECTION && !renpyDisabled;
            let needRetry = (needRenpy && !renpyInjected);
            if (!renpyLightMode) {
              needRetry = needRetry || !sdlHooked || !pyHooked;
            }
            if (needRetry) {
              setTimeout(retryHooks, 2000);
            }
          } catch (e) {
            send({ status: "retry_failed" });
          }
        }
        function hookKernel32() {
          const k32 = "kernel32.dll";
          const mb2wc = findExport(k32, "MultiByteToWideChar");
          if (mb2wc) {
              Interceptor.attach(mb2wc, {
                  onEnter(args) {
                      this.cp = args[0].toInt32();
                      this.dst = args[4];
                  },
                  onLeave(retval) {
                      const len = retval.toInt32();
                      if (len > 1 && !this.dst.isNull()) {
                           try {
                               const str = this.dst.readUtf16String(len);
                               if (str && str.length > 1) {
                                   // Stronger filter for MultiByteToWideChar to prevent crash/spam
                                   if (str.indexOf("\\") !== -1 || str.indexOf("/") !== -1) return;
                                   if (str.indexOf(".dll") !== -1 || str.indexOf(".exe") !== -1) return;
                                   if (str.indexOf(".txt") !== -1 || str.indexOf(".ini") !== -1) return;
                                   if (str.indexOf(".rpa") !== -1 || str.indexOf(".rpy") !== -1) return;
                                   if (str.indexOf(".py") !== -1 || str.indexOf(".xml") !== -1) return;
                                   if (str.indexOf(".png") !== -1 || str.indexOf(".jpg") !== -1) return;
                                   if (str.indexOf(".ogg") !== -1 || str.indexOf(".mp3") !== -1) return;
                                   if (str.indexOf(".wav") !== -1 || str.indexOf(".mid") !== -1) return;
                                   if (str.indexOf(".mod") !== -1 || str.indexOf(".xm") !== -1) return;
                                   if (str.indexOf("Couldn't find") !== -1) return;
                                   if (str.indexOf("Executing ATL") !== -1) return;
                                   if (str.indexOf("Compiling ATL") !== -1) {
                                       tryInjectRenpyOnce(); // Trigger Ren'Py injection if we see this!
                                       return;
                                   }
                                   if (str.indexOf("Image '") !== -1) return;
                                   if (str.indexOf("prefix_") !== -1) return;
                                   if (str.indexOf("end translate") !== -1) return;
                                   if (str.indexOf("*+?{") !== -1) return;
                                   
                                   // Stronger C++ / Engine internals filter
                                   if (str.indexOf("::") !== -1) return; // Block C++ scope resolution
                                   if (str.indexOf("tTJS") !== -1) return; // Block Kirikiri TVP engine classes
                                   if (str.indexOf("operator") !== -1) return; // Block operator overloading
                                   if (str.indexOf("const ") !== -1) return;
                                   if (str.indexOf("void ") !== -1) return;
                                   if (str.indexOf("unsigned ") !== -1) return;
                                   if (str.indexOf("std::") !== -1) return;
                                   
                                   sendText(str, "MultiByteToWideChar");
                               }
                           } catch(e) {}
                      }
                  }
              });
              send({ status: "debug_mb2wc_hooked" });
          }
        }
        function hookGdiMeasure() {
             const gdi32 = "gdi32.dll";
             const gtep32w = findExport(gdi32, "GetTextExtentPoint32W");
             if (gtep32w) {
                 Interceptor.attach(gtep32w, {
                     onEnter(args) {
                         const len = args[2].toInt32();
                         if (len > 0) {
                             const str = readW(args[1], len);
                             sendText(str, "GetTextExtentPoint32W");
                         }
                     }
                 });
                 send({ status: "debug_attach: GetTextExtentPoint32W" });
             }
             const gteepw = findExport(gdi32, "GetTextExtentExPointW");
             if (gteepw) {
                 Interceptor.attach(gteepw, {
                     onEnter(args) {
                         const len = args[2].toInt32();
                         if (len > 0) {
                             const str = readW(args[1], len);
                             sendText(str, "GetTextExtentExPointW");
                         }
                     }
                 });
                 send({ status: "debug_attach: GetTextExtentExPointW" });
             }
        }
        function hookLoadLibrary() {
          const k32 = "kernel32.dll";
          const fns = [
            { name: "LoadLibraryA", wide: false },
            { name: "LoadLibraryW", wide: true },
            { name: "LoadLibraryExA", wide: false },
            { name: "LoadLibraryExW", wide: true }
          ];
          fns.forEach(item => {
            const addr = findExport(k32, item.name);
            if (!addr) return;
            Interceptor.attach(addr, {
              onEnter(args) {
                this._path = item.wide ? readW(args[0]) : readA(args[0]);
              },
              onLeave(_ret) {
                const p = (this._path || "").toLowerCase();
                if (!renpyLightMode && p.indexOf("ttf") >= 0) {
                  tryHookSDLTTF();
                }
                if (!renpyLightMode && p.indexOf("python") >= 0 && p.indexOf(".dll") >= 0) {
                  tryHookPython();
                  tryInjectRenpyOnce();
                }
                if (p.indexOf("renpy") >= 0) {
                  tryInjectRenpyOnce();
                }
                if (p.indexOf("dwrite") >= 0) {
                   hookDirectWrite();
                }
                if (p.indexOf("d2d1") >= 0) {
                   hookDirect2D();
                }
                if (p.indexOf("gdiplus") >= 0) {
                   hookGdiPlus();
                }
                if (p.indexOf("d3dx9") >= 0 || p.indexOf("d3dx1") >= 0) {
                   hookD3DX();
                }
              }
            });
          });
        }
        function hookDirectWrite() {
          const dwrite = findExport("dwrite.dll", "DWriteCreateFactory");
          if (!dwrite) return false;
          // Avoid re-hooking
          if (dwrite.hooked) return true;
          dwrite.hooked = true;
          
          send({ status: "debug_hook_dwrite_found" });
          
          Interceptor.attach(dwrite, {
            onLeave(retval) {
              try {
                const factory = retval;
                if (factory.isNull()) return;
                const vtbl = Memory.readPointer(factory);
                const ptrSize = Process.pointerSize;
                // IDWriteFactory::CreateTextLayout (index 18)
                const idxCreateTextLayout = 18;
                // IDWriteFactory::CreateGdiCompatibleTextLayout (index 19)
                const idxCreateGdiCompatibleTextLayout = 19;
                
                const pCreateTextLayout = Memory.readPointer(vtbl.add(ptrSize * idxCreateTextLayout));
                const pCreateGdiLayout = Memory.readPointer(vtbl.add(ptrSize * idxCreateGdiCompatibleTextLayout));
                
                if (pCreateTextLayout && !pCreateTextLayout.isNull()) {
                  Interceptor.attach(pCreateTextLayout, {
                    onEnter(args) {
                      const text = readW(args[1], args[2]);
                      sendText(text);
                    }
                  });
                }
                if (pCreateGdiLayout && !pCreateGdiLayout.isNull()) {
                  Interceptor.attach(pCreateGdiLayout, {
                    onEnter(args) {
                      const text = readW(args[1], args[2]);
                      sendText(text);
                    }
                  });
                }
              } catch (e) {}
            }
          });
          return true;
        }
        function hookDirect2D() {
           const d2d1 = findExport("d2d1.dll", "D2D1CreateFactory");
           if (!d2d1) return false;
           if (d2d1.hooked) return true;
           d2d1.hooked = true;
           
           send({ status: "debug_hook_d2d1_found" });

           Interceptor.attach(d2d1, {
               onLeave(retval) {
                   // retval is HRESULT, args[1] is IID, args[2] is ppFactory
                   // But we are in onLeave, we can't access args easily.
                   // Actually D2D1CreateFactory(type, iid, pFactory)
                   // We need to hook onEnter to get the pointer to pointer? 
                   // No, let's just use a simpler approach: check exports of d2d1?
                   // D2D1 is interface based.
                   // Let's rely on DWrite usually being used with D2D.
                   send({ status: "debug_d2d1_create" });
               }
           });
           return true;
        }
        function hookGdiPlus() {
          const gdiplus = Process.findModuleByName("gdiplus.dll");
          if (!gdiplus) return false;
          let ok = false;
          
          function hookGdipFunc(name, handler) {
             const addr = findExport(gdiplus.name, name);
             if (addr) {
                 Interceptor.attach(addr, handler);
                 return true;
             }
             return false;
          }

          if (hookGdipFunc("GdipDrawString", {
              onEnter(args) {
                // GdipDrawString(graphics, string, length, font, rect, format, brush)
                try {
                    const len = args[2].toInt32();
                    const text = readW(args[1], len === -1 ? null : len);
                    sendText(text);
                } catch(e) {}
              }
          })) {
             ok = true;
          }

          if (hookGdipFunc("GdipDrawDriverString", {
              onEnter(args) {
                // GdipDrawDriverString(graphics, text, length, font, positions, flags, matrix, brush)
                try {
                    const len = args[2].toInt32();
                    const ptr = args[1];
                    if (!ptr.isNull() && len > 0) {
                        // Try reading as UTF-16 string first
                        const text = ptr.readUtf16String(len);
                        sendText(text);
                    }
                } catch(e) {}
              }
          })) {
             ok = true;
          }

          if (ok) send({ status: "debug_gdiplus_hooked" });
          return ok;
        }
        function hookD3DX() {
            let ok = false;
            const mods = Process.enumerateModules();
            
            function hookFontObj(pFont) {
                 if (pFont.isNull()) return;
                 try {
                     const vtbl = pFont.readPointer();
                     // ID3DXFont::DrawTextW is index 16
                     // DrawTextW(pSprite, pString, Count, pRect, Format, Color)
                     const idxDrawTextW = 16; 
                     const pDrawTextW = vtbl.add(Process.pointerSize * idxDrawTextW).readPointer();
                     
                     Interceptor.attach(pDrawTextW, {
                         onEnter(args) {
                             try {
                                 const count = args[3].toInt32();
                                 const text = readW(args[2], count === -1 ? null : count);
                                 sendText(text, "D3DXFontW");
                             } catch(e) {}
                         }
                     });
                     
                     // ID3DXFont::DrawTextA is index 15
                     const idxDrawTextA = 15;
                     const pDrawTextA = vtbl.add(Process.pointerSize * idxDrawTextA).readPointer();
                     
                     Interceptor.attach(pDrawTextA, {
                         onEnter(args) {
                             try {
                                 const count = args[3].toInt32();
                                 const text = readA(args[2], count === -1 ? null : count);
                                 sendText(text, "D3DXFontA");
                             } catch(e) {}
                         }
                     });
                     
                     send({ status: "debug_d3dx_font_hooked" });
                 } catch(e) {}
            }

            for (let i = 0; i < mods.length; i++) {
                const name = mods[i].name.toLowerCase();
                if (name.indexOf("d3dx9") >= 0 || name.indexOf("d3dx1") >= 0) {
                    // D3DXCreateFontW(pDevice, Height, ..., ppFont)
                    const createFontW = findExport(mods[i].name, "D3DXCreateFontW");
                    if (createFontW) {
                        Interceptor.attach(createFontW, {
                           onEnter(args) {
                               this.ppFont = args[11];
                           },
                           onLeave(retval) {
                               if (retval.toInt32() === 0 && this.ppFont && !this.ppFont.isNull()) { 
                                   const pFont = this.ppFont.readPointer();
                                   hookFontObj(pFont);
                                   send({ status: "debug_d3dx_createfont" });
                               }
                           }
                        });
                        ok = true;
                    }
                    
                    // D3DXCreateFontIndirectW(pDevice, pDesc, ppFont)
                    const createFontIndW = findExport(mods[i].name, "D3DXCreateFontIndirectW");
                    if (createFontIndW) {
                        Interceptor.attach(createFontIndW, {
                           onEnter(args) {
                               this.ppFont = args[2];
                           },
                           onLeave(retval) {
                               if (retval.toInt32() === 0 && this.ppFont && !this.ppFont.isNull()) { 
                                   const pFont = this.ppFont.readPointer();
                                   hookFontObj(pFont);
                                   send({ status: "debug_d3dx_createfont_indirect" });
                               }
                           }
                        });
                        ok = true;
                    }
                }
            }
            if (ok) send({ status: "debug_d3dx_detected" });
            return ok;
        }
        send({ status: "debug_main_start" });
        let ok = false;
        try {
            if (!renpyLightMode) {
            ok = hookGdi("TextOutW", "gdi32.dll", {
              onEnter(args) {
                const text = readW(args[3], args[4]);
                // TextOutW safety check
                if (text && text.length > 2000) return;
                sendText(text, "TextOutW");
              }
            }) || ok;
            ok = hookGdi("TextOutA", "gdi32.dll", {
              onEnter(args) {
                const text = readA(args[3], args[4]);
                if (text && text.length > 2000) return;
                sendText(text, "TextOutA");
              }
            }) || ok;
            ok = hookGdi("ExtTextOutW", "gdi32.dll", {
              onEnter(args) {
                const options = args[3].toInt32();
                if ((options & 0x10) !== 0) { // ETO_GLYPH_INDEX
                     // Glyph index mode - cannot read as string
                     // Reduced debug spam to prevent crash
                     _glyphDebugCount++;
                     if (_glyphDebugCount % 50 === 0) {
                        send({ status: "debug_glyph_index_detected_sampled" });
                     }
                     return;
                }
                const ptr = args[5];
                const count = args[6];
                let text = readW(ptr, count);
                if (text && text.length > 2000) return;
                try {
                    if (text && text.length >= 2 && /^[a-z]/.test(text)) {
                        const prev = readW(ptr.sub(2), 1);
                        if (prev && /^[A-Za-z]$/.test(prev) && !text.startsWith(prev)) {
                            text = prev + text;
                        }
                    }
                } catch(e) {}
                sendText(text, "ExtTextOutW");
              }
            }) || ok;
            ok = hookGdi("ExtTextOutA", "gdi32.dll", {
              onEnter(args) {
                const text = readA(args[5], args[6]);
                if (text && text.length > 2000) return;
                sendText(text, "ExtTextOutA");
              }
            }) || ok;
            ok = hookGdi("DrawTextW", "user32.dll", {
              onEnter(args) {
                const text = readW(args[1], args[2]);
                if (text && text.length > 2000) return;
                sendText(text, "DrawTextW");
              }
            }) || ok;
            ok = hookGdi("DrawTextA", "user32.dll", {
              onEnter(args) {
                const text = readA(args[1], args[2]);
                if (text && text.length > 2000) return;
                sendText(text, "DrawTextA");
              }
            }) || ok;
            ok = hookGdi("DrawTextExW", "user32.dll", {
              onEnter(args) {
                const text = readW(args[1], args[2]);
                if (text && text.length > 2000) return;
                sendText(text, "DrawTextExW");
              }
            }) || ok;
            ok = hookGdi("DrawTextExA", "user32.dll", {
              onEnter(args) {
                const text = readA(args[1], args[2]);
                if (text && text.length > 2000) return;
                sendText(text, "DrawTextExA");
              }
            }) || ok;
            
            // Debug font creation
            hookGdi("CreateFontW", "gdi32.dll", { onEnter(args) {} });
            hookGdi("CreateFontIndirectW", "gdi32.dll", { onEnter(args) {} });
            hookGdi("CreateFontA", "gdi32.dll", { onEnter(args) {} });
            hookGdi("CreateFontIndirectA", "gdi32.dll", { onEnter(args) {} });
            }
        } catch(e) {
            send({ status: "debug_gdi_fail: " + e });
        }
        send({ status: "debug_main_gdi_done" });
        if (!renpyLightMode) {
          ok = hookDirectWrite() || ok;
          ok = hookGdiPlus() || ok;
          ok = hookD3DX() || ok;
          ok = hookMono() || ok;
          ok = hookIl2cpp() || ok;
          ok = hookD3DPresent() || ok;
          ok = hookGetGlyphOutline() || ok;
          ok = hookGdiExtras() || ok;
          hookKernel32(); 
          // hookGdiMeasure(); // Disabled to prevent spam/crash
          ok = tryHookSDLTTF() || ok;
          ok = tryHookPython() || ok;
        } else {
          send({ status: "renpy_light_mode" });
        }
        tryInjectRenpyOnce();
        hookLoadLibrary();
        retryHooks();
        if (!ok && !renpyLightMode) {
          send({ status: "no_hook" });
        }
        send({ status: "debug_main_end" });
        setTimeout(function() { send({ status: "hook_ready_delayed" }); }, STARTUP_DELAY_MS);
        """
        script_src = (
            script_src
            .replace("__HOOK_HOST__", out_host)
            .replace("__HOOK_PORT__", str(out_port))
            .replace("__ENABLE_RENPY_INJECTION__", "1" if self._enable_renpy_injection else "0")
        )

        def _on_message(msg, _data):
            try:
                mtype = str(msg.get("type") or "").strip().lower()
                if mtype == "error":
                    try:
                        desc = str(msg.get("description") or "").strip()
                        stack = str(msg.get("stack") or "").strip()
                        if stack:
                            self.status.emit(f"Hook Frida 脚本错误: {desc}\n{stack}")
                        else:
                            self.status.emit(f"Hook Frida 脚本错误: {desc}")
                    except Exception:
                        pass
                    try:
                        hook_log(f"FRIDA_ERROR: {msg}")
                    except Exception:
                        pass
                    return
                if mtype != "send":
                    return
                payload = msg.get("payload") or {}
                status = payload.get("status")
                if status == "frida_script_loaded":
                    try:
                        self.status.emit("Hook Frida 脚本已加载")
                    except Exception:
                        pass
                    return
                if status == "retry_started":
                    try:
                        self.status.emit("Hook 后台持续搜索已启动")
                    except Exception:
                        pass
                    return
                if status == "module_seen":
                    try:
                        name = str(payload.get("name") or "").strip()
                        if name:
                            self.status.emit(f"Hook 模块已加载: {name}")
                    except Exception:
                        pass
                    return
                if status == "retry_failed":
                    try:
                        self.status.emit("Hook 后台搜索异常")
                    except Exception:
                        pass
                    return
                if status == "sdl_ttf_trying":
                    try:
                        self.status.emit("Hook SDL_ttf 初次尝试…")
                    except Exception:
                        pass
                    return
                if status == "python_trying":
                    try:
                        self.status.emit("Hook Python API 初次尝试…")
                    except Exception:
                        pass
                    return
                if status == "renpy_trying":
                    try:
                        self.status.emit("Hook Ren'Py 初次注入…")
                    except Exception:
                        pass
                    return
                if status == "renpy_pyrun_found":
                    try:
                        self.status.emit("Hook Ren'Py 已找到 PyRun 符号")
                    except Exception:
                        pass
                    return
                if status == "sdl_ttf_retrying":
                    try:
                        self.status.emit("Hook SDL_ttf 搜索中…")
                    except Exception:
                        pass
                    return
                if status == "sdl_ttf_not_found":
                    return
                if status == "python_retrying":
                    try:
                        self.status.emit("Hook Python API 搜索中…")
                    except Exception:
                        pass
                    return
                if status == "python_not_found":
                    return
                if status == "renpy_retrying":
                    try:
                        self.status.emit("Hook Ren'Py 注入尝试中…")
                    except Exception:
                        pass
                    return
                if status == "renpy_force_start":
                    try:
                        self.status.emit("Hook Ren'Py 强制注入开始…")
                    except Exception:
                        pass
                    return
                if status == "renpy_force_failed":
                    return
                if status == "renpy_disabled":
                    try:
                        reason = str(payload.get("reason") or "").strip()
                    except Exception:
                        reason = ""
                    try:
                        if reason:
                            self.status.emit(f"Hook Ren'Py injection disabled: {reason}")
                        else:
                            self.status.emit("Hook Ren'Py injection disabled")
                    except Exception:
                        pass
                    return
                if status == "renpy_injected":
                    try:
                        self.status.emit("Hook Ren'Py 注入已完成")
                    except Exception:
                        pass
                    return
                if status == "renpy_injected_minimal":
                    try:
                        self.status.emit("Hook Ren'Py fallback enabled")
                    except Exception:
                        pass
                    return
                if status == "renpy_light_mode":
                    try:
                        self.status.emit("Hook Ren'Py 轻量模式（已禁用高频渲染钩子）")
                    except Exception:
                        pass
                    return
                if status == "renpy_no_pyrun":
                    try:
                        self.status.emit("Hook Ren'Py inject failed: PyRun symbol not found")
                    except Exception:
                        pass
                    return
                if status == "renpy_inject_failed":
                    try:
                        err = str(payload.get("error") or "").strip()
                    except Exception:
                        err = ""
                    try:
                        if err:
                            self.status.emit(f"Hook Ren'Py inject failed: {err}")
                        else:
                            self.status.emit("Hook Ren'Py inject failed")
                    except Exception:
                        pass
                    return
                if status == "python_hooked":
                    try:
                        self.status.emit("Hook Python API 已启动")
                    except Exception:
                        pass
                    return
                if status == "sdl_ttf_hooked":
                    try:
                        self.status.emit("Hook SDL_ttf 已启动")
                    except Exception:
                        pass
                    return
                if status == "d3d_detected":
                    try:
                        self.status.emit("Hook 检测到 D3D 设备")
                    except Exception:
                        pass
                    return
                if status == "no_hook":
                    try:
                        self.status.emit("Hook Frida 未找到可钩子函数")
                    except Exception:
                        pass
                    return
                if status == "hook_ready_delayed":
                    try:
                        self.status.emit("Hook 准备就绪 (启动延迟结束)")
                    except Exception:
                        pass
                    return
                if status and str(status).startswith("debug_"):
                    try:
                        self.status.emit(f"Hook Debug: {status}")
                    except Exception:
                        pass
                    return
                text = payload.get("text", "")
                label = str(payload.get("label") or "").strip()
                source = str(payload.get("source") or "frida").strip().lower() or "frida"
                thread_id = self._coerce_int(payload.get("threadId", payload.get("thread_id")))
                if text:
                    self._emit_text_with_source(
                        text,
                        source,
                        label=label,
                        thread_id=thread_id,
                        pid=self._pid,
                    )
            except Exception:
                pass

        try:
            script = session.create_script(script_src)
            script.on("message", _on_message)
            script.load()
            try:
                self.status.emit("Hook Frida 已启动")
            except Exception:
                pass
        except Exception as e:
            try:
                self.status.emit(f"Hook Frida 启动失败: {e}")
            except Exception:
                pass
            try:
                session.detach()
            except Exception:
                pass
            return

        try:
            while not self._frida_stop.is_set() and not self.isInterruptionRequested():
                time.sleep(0.2)
        finally:
            try:
                script.unload()
            except Exception:
                pass
            try:
                session.detach()
            except Exception:
                pass

    def run(self) -> None:
        if self._pid <= 0:
            try:
                self.status.emit("Hook钩子：PID 无效")
            except Exception:
                pass
            return

        try:
            import sys
            import os
            import ctypes
            import ctypes.wintypes as wt
        except Exception:
            sys = None
            os = None
            ctypes = None
            wt = None

        def _current_py_arch() -> str:
            try:
                return "x86" if ctypes and ctypes.sizeof(ctypes.c_void_p) == 4 else "x64"
            except Exception:
                return "unknown"

        def _detect_process_arch(pid: int) -> str:
            if ctypes is None or wt is None or os.name != "nt":
                return "unknown"
            k32 = ctypes.windll.kernel32
            try:
                OpenProcess = k32.OpenProcess
                CloseHandle = k32.CloseHandle
                IsWow64Process = getattr(k32, "IsWow64Process", None)
                IsWow64Process2 = getattr(k32, "IsWow64Process2", None)
            except Exception:
                return "unknown"
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = None
            try:
                OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
                OpenProcess.restype = wt.HANDLE
                CloseHandle.argtypes = [wt.HANDLE]
                CloseHandle.restype = wt.BOOL
                h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wt.DWORD(int(pid)))
                h_val = int(getattr(h, "value", h) or 0)
                if not h_val:
                    return "unknown"
                if IsWow64Process2 is not None:
                    pm = wt.USHORT()
                    nm = wt.USHORT()
                    IsWow64Process2.argtypes = [wt.HANDLE, ctypes.POINTER(wt.USHORT), ctypes.POINTER(wt.USHORT)]
                    IsWow64Process2.restype = wt.BOOL
                    if bool(IsWow64Process2(h, ctypes.byref(pm), ctypes.byref(nm))):
                        pmv = int(pm.value or 0)
                        nmv = int(nm.value or 0)
                        if pmv != 0:
                            return "x86"
                        if nmv == 0x8664:
                            return "x64"
                        if nmv == 0x014C:
                            return "x86"
                if IsWow64Process is not None:
                    wow = wt.BOOL()
                    IsWow64Process.argtypes = [wt.HANDLE, ctypes.POINTER(wt.BOOL)]
                    IsWow64Process.restype = wt.BOOL
                    if bool(IsWow64Process(h, ctypes.byref(wow))):
                        if bool(wow.value):
                            return "x86"
                        return "x64"
            except Exception:
                return "unknown"
            finally:
                try:
                    if h:
                        CloseHandle(h)
                except Exception:
                    pass
            return "unknown"

        def _detect_process_arch_by_pe(pid: int) -> str:
            try:
                import struct
            except Exception:
                return "unknown"

            exe_path = ""
            try:
                import psutil

                try:
                    proc = psutil.Process(int(pid))
                    exe_path = proc.exe()
                except Exception:
                    exe_path = ""
            except Exception:
                exe_path = ""

            if not exe_path and ctypes is not None and wt is not None and os.name == "nt":
                try:
                    k32 = ctypes.windll.kernel32
                    OpenProcess = k32.OpenProcess
                    CloseHandle = k32.CloseHandle
                    QueryFullProcessImageNameW = getattr(k32, "QueryFullProcessImageNameW", None)
                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

                    OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
                    OpenProcess.restype = wt.HANDLE
                    CloseHandle.argtypes = [wt.HANDLE]
                    CloseHandle.restype = wt.BOOL

                    if QueryFullProcessImageNameW is not None:
                        QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
                        QueryFullProcessImageNameW.restype = wt.BOOL

                    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wt.DWORD(int(pid)))
                    h_val = int(getattr(h, "value", h) or 0)
                    if h_val:
                        try:
                            if QueryFullProcessImageNameW is not None:
                                size = wt.DWORD(32768)
                                buf = ctypes.create_unicode_buffer(size.value)
                                if bool(QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))):
                                    exe_path = str(buf.value or "")
                        finally:
                            CloseHandle(h)
                except Exception:
                    exe_path = ""

            if not exe_path:
                return "unknown"

            try:
                with open(exe_path, "rb") as f:
                    dos = f.read(64)
                    if dos[:2] != b"MZ":
                        return "unknown"
                    pe_offset = struct.unpack("<I", dos[60:64])[0]
                    f.seek(pe_offset)
                    if f.read(4) != b"PE\x00\x00":
                        return "unknown"
                    coff = f.read(20)
                    machine = struct.unpack("<H", coff[0:2])[0]
                if machine == 0x014C:
                    return "x86"
                if machine == 0x8664:
                    return "x64"
            except Exception:
                return "unknown"
            return "unknown"

        try:
            target_arch = _detect_process_arch(self._pid)
            if target_arch == "unknown":
                target_arch = _detect_process_arch_by_pe(self._pid)
            py_arch = _current_py_arch()
            if target_arch != "unknown" and py_arch != "unknown":
                msg = f"Hook架构检测: 目标进程 {target_arch} / Python {py_arch}"
                try:
                    self.status.emit(msg)
                except Exception:
                    pass
                try:
                    hook_log(msg)
                except Exception:
                    pass
                if target_arch != py_arch:
                    warn = "Hook架构不匹配，32位游戏请使用32位Python/Frida运行"
                    hint = f"Hook需要切换: {target_arch}"
                    try:
                        self.status.emit(warn)
                    except Exception:
                        pass
                    try:
                        hook_log(warn)
                    except Exception:
                        pass
                    try:
                        self.status.emit(hint)
                    except Exception:
                        pass
                    try:
                        hook_log(hint)
                    except Exception:
                        pass
                    try:
                        self._enable_win_event = False
                        self._enable_uia = False
                        self._enable_frida = False
                    except Exception:
                        pass
                    
                    # Prevent recursive launch: Check if I am already the agent
                    is_agent = False
                    try:
                        # Check if running from HookAgent.exe or with --pid argument
                        if '--pid' in sys.argv:
                            is_agent = True
                        if getattr(sys, 'frozen', False) and 'HookAgent' in os.path.basename(sys.executable):
                            is_agent = True
                    except Exception:
                        pass

                    if is_agent:
                        try:
                            self.status.emit(f"HookAgent ({py_arch}) 无法注入目标 ({target_arch}) - 架构不匹配")
                        except Exception:
                            pass
                        return

                    # Do not relaunch helper here; main window controls helper lifecycle.
                    # Keeping only socket listener avoids duplicate helper races/crash loops.
                    try:
                        self.status.emit("Hook 架构不匹配：等待主程序切换并启动对应位数辅助进程")
                    except Exception:
                        pass

                    # 仅保留外部端口监听，用于由32位注入助手回传文本
                    if not bool(self._enable_socket):
                        return
            else:
                try:
                    msg = f"Hook架构检测: 目标进程 {target_arch} / Python {py_arch}"
                    self.status.emit(msg)
                except Exception:
                    pass
                try:
                    hook_log(msg)
                except Exception:
                    pass
        except Exception:
            pass

        if self._enable_socket and self._listen_port:
            try:
                self._server_stop.clear()
                self._server_thread = threading.Thread(target=self._server_loop, daemon=True)
                self._server_thread.start()
            except Exception:
                self._server_thread = None

        if self._enable_uia:
            try:
                self._uia_stop.clear()
                self._uia_thread = threading.Thread(target=self._uia_loop, daemon=True)
                self._uia_thread.start()
            except Exception:
                self._uia_thread = None

        if self._enable_frida:
            try:
                self._frida_stop.clear()
                self._frida_thread = threading.Thread(target=self._frida_loop, daemon=True)
                self._frida_thread.start()
                try:
                    self.status.emit("Hook Frida 线程已启动")
                except Exception:
                    pass
            except Exception:
                self._frida_thread = None

        try:
            import os
            import ctypes
            import ctypes.wintypes as wt
        except Exception as e:
            if self._server_thread is not None:
                try:
                    self.status.emit(f"Hook钩子仅启动外部端口: {e}")
                except Exception:
                    pass
                while not self.isInterruptionRequested():
                    time.sleep(0.2)
                return
            try:
                self.status.emit(f"Hook钩子初始化失败: {e}")
            except Exception:
                pass
            return

        if os.name != "nt":
            if self._server_thread is not None:
                try:
                    self.status.emit("Hook钩子仅启动外部端口: 非 Windows")
                except Exception:
                    pass
                while not self.isInterruptionRequested():
                    time.sleep(0.2)
                return
            try:
                self.status.emit("Hook钩子仅支持 Windows")
            except Exception:
                pass
            return

        if not self._enable_win_event:
            try:
                self.status.emit("Hook钩子仅启动外部端口")
            except Exception:
                pass
            while not self.isInterruptionRequested():
                time.sleep(0.2)
            return

        if not hasattr(wt, "LRESULT"):
            wt.LRESULT = ctypes.c_ssize_t
        user32 = ctypes.windll.user32

        EVENT_SYSTEM_FOREGROUND = 0x0003
        EVENT_OBJECT_FOCUS = 0x8005
        EVENT_OBJECT_NAMECHANGE = 0x800C
        EVENT_OBJECT_VALUECHANGE = 0x800E
        OBJID_WINDOW = 0x00000000
        OBJID_CLIENT = -4
        WINEVENT_OUTOFCONTEXT = 0x0000
        WINEVENT_SKIPOWNPROCESS = 0x0002
        PM_REMOVE = 0x0001
        QS_ALLINPUT = 0x04FF
        MWMO_ALERTABLE = 0x0002
        WAIT_TIMEOUT = 0x00000102
        WM_GETTEXT = 0x000D
        WM_GETTEXTLENGTH = 0x000E

        WinEventProcType = ctypes.WINFUNCTYPE(
            None,
            wt.HANDLE,
            wt.DWORD,
            wt.HWND,
            wt.LONG,
            wt.LONG,
            wt.DWORD,
            wt.DWORD,
        )
        user32.SetWinEventHook.argtypes = [
            wt.DWORD,
            wt.DWORD,
            wt.HMODULE,
            WinEventProcType,
            wt.DWORD,
            wt.DWORD,
            wt.DWORD,
        ]
        user32.SetWinEventHook.restype = wt.HANDLE
        user32.UnhookWinEvent.argtypes = [wt.HANDLE]
        user32.UnhookWinEvent.restype = wt.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        user32.GetWindowThreadProcessId.restype = wt.DWORD
        user32.GetWindowTextLengthW.argtypes = [wt.HWND]
        user32.GetWindowTextLengthW.restype = wt.INT
        user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, wt.INT]
        user32.GetWindowTextW.restype = wt.INT
        user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.SendMessageW.restype = wt.LRESULT
        user32.IsWindow.argtypes = [wt.HWND]
        user32.IsWindow.restype = wt.BOOL
        user32.IsWindowVisible.argtypes = [wt.HWND]
        user32.IsWindowVisible.restype = wt.BOOL
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
        user32.PeekMessageW.restype = wt.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.TranslateMessage.restype = wt.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.DispatchMessageW.restype = wt.LRESULT
        user32.MsgWaitForMultipleObjectsEx.argtypes = [
            wt.DWORD,
            ctypes.POINTER(wt.HANDLE),
            wt.DWORD,
            wt.DWORD,
            wt.DWORD,
        ]
        user32.MsgWaitForMultipleObjectsEx.restype = wt.DWORD

        def _read_text_via_sendmessage(hwnd: int) -> str:
            try:
                length = int(user32.SendMessageW(wt.HWND(hwnd), WM_GETTEXTLENGTH, 0, 0) or 0)
            except Exception:
                length = 0
            if length <= 0:
                return ""
            length = min(length, int(self._max_chars))
            buf = ctypes.create_unicode_buffer(length + 1)
            try:
                user32.SendMessageW(wt.HWND(hwnd), WM_GETTEXT, wt.WPARAM(length + 1), ctypes.byref(buf))
            except Exception:
                return ""
            return str(buf.value or "")

        def _read_text_via_windowtext(hwnd: int) -> str:
            try:
                length = int(user32.GetWindowTextLengthW(wt.HWND(hwnd)) or 0)
            except Exception:
                length = 0
            length = min(max(length, 0), int(self._max_chars))
            buf = ctypes.create_unicode_buffer(length + 1 if length > 0 else int(self._max_chars) + 1)
            try:
                got = int(user32.GetWindowTextW(wt.HWND(hwnd), buf, len(buf)) or 0)
            except Exception:
                got = 0
            if got <= 0:
                return ""
            return str(buf.value or "")

        @WinEventProcType
        def _win_event_proc(_hook, event, hwnd, id_object, _id_child, _tid, _time_ms):
            try:
                if self.isInterruptionRequested():
                    return
            except Exception:
                return
            if not hwnd:
                return
            if int(id_object) != OBJID_CLIENT:
                return
            try:
                if not bool(user32.IsWindow(wt.HWND(hwnd))):
                    return
            except Exception:
                return
            try:
                if not bool(user32.IsWindowVisible(wt.HWND(hwnd))):
                    return
            except Exception:
                pass

            pid_out = wt.DWORD(0)
            try:
                user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid_out))
            except Exception:
                return
            if int(pid_out.value or 0) != int(self._pid):
                return

            if int(event) == EVENT_SYSTEM_FOREGROUND:
                return

            text = ""
            if int(event) in (EVENT_OBJECT_NAMECHANGE, EVENT_OBJECT_VALUECHANGE, EVENT_OBJECT_FOCUS):
                # 仅从控件读取文本，避免把窗口标题当成正文
                text = _read_text_via_sendmessage(int(hwnd)) or ""
            text = str(text or "").strip()
            if not text:
                return
            if len(text) < int(self._min_chars):
                return
            if len(text) > int(self._max_chars):
                text = text[: int(self._max_chars)]
            if not self._should_emit(text):
                return
            try:
                self._emit_text_with_source(text, "win_event")
            except Exception:
                return

        self._win_event_proc = _win_event_proc

        try:
            hook1 = user32.SetWinEventHook(
                wt.DWORD(EVENT_OBJECT_FOCUS),
                wt.DWORD(EVENT_OBJECT_FOCUS),
                wt.HMODULE(0),
                _win_event_proc,
                wt.DWORD(0),
                wt.DWORD(0),
                wt.DWORD(WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS),
            )
            if hook1:
                self._hooks.append(hook1)
            hook2 = user32.SetWinEventHook(
                wt.DWORD(EVENT_OBJECT_NAMECHANGE),
                wt.DWORD(EVENT_OBJECT_VALUECHANGE),
                wt.HMODULE(0),
                _win_event_proc,
                wt.DWORD(0),
                wt.DWORD(0),
                wt.DWORD(WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS),
            )
            if hook2:
                self._hooks.append(hook2)
        except Exception as e:
            try:
                self.status.emit(f"Hook钩子启动失败: {e}")
            except Exception:
                pass
            if self._server_thread is not None:
                while not self.isInterruptionRequested():
                    time.sleep(0.2)
                return
            return

        if not self._hooks:
            try:
                self.status.emit("Hook钩子启动失败: 无法安装系统钩子")
            except Exception:
                pass
            if self._server_thread is not None:
                while not self.isInterruptionRequested():
                    time.sleep(0.2)
                return
            return

        try:
            self.status.emit("Hook钩子已就绪")
        except Exception:
            pass

        msg = wt.MSG()
        try:
            while not self.isInterruptionRequested():
                rc = int(
                    user32.MsgWaitForMultipleObjectsEx(
                        wt.DWORD(0),
                        None,
                        wt.DWORD(200),
                        wt.DWORD(QS_ALLINPUT),
                        wt.DWORD(MWMO_ALERTABLE),
                    )
                )
                if rc == WAIT_TIMEOUT:
                    continue
                while bool(user32.PeekMessageW(ctypes.byref(msg), wt.HWND(0), 0, 0, PM_REMOVE)):
                    try:
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
                    except Exception:
                        pass
        finally:
            for h in list(self._hooks):
                try:
                    user32.UnhookWinEvent(h)
                except Exception:
                    pass
            self._hooks = []
            try:
                self._server_stop.set()
            except Exception:
                pass
            try:
                self._uia_stop.set()
            except Exception:
                pass
            try:
                self._frida_stop.set()
            except Exception:
                pass
            try:
                if self._agent_process is not None:
                    self._agent_process.terminate()
                    self._agent_process = None
            except Exception:
                pass
            try:
                if self._server_sock is not None:
                    try:
                        self._server_sock.close()
                    except Exception:
                        pass
            except Exception:
                pass

