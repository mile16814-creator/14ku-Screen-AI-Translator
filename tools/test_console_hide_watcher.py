import os
import sys
import time
import subprocess
import ctypes
import ctypes.wintypes as wt


def _find_console_hwnd_by_pid(target_pid: int) -> int | None:
    u32 = ctypes.windll.user32
    matches: list[int] = []

    def _enum_proc(hwnd, _lparam):
        try:
            class_name = ctypes.create_unicode_buffer(256)
            if u32.GetClassNameW(hwnd, class_name, 256) == 0:
                return True
            if class_name.value != "ConsoleWindowClass":
                return True
            pid = wt.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value or 0) == int(target_pid):
                matches.append(int(hwnd))
        except Exception:
            pass
        return True

    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(_enum_proc)
    u32.EnumWindows(cb, 0)
    return matches[0] if matches else None


def main() -> int:
    if sys.platform != "win32":
        print("skip: not win32")
        return 0

    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from main import _start_console_hide_watcher

    stop_event, _th = _start_console_hide_watcher()
    p = subprocess.Popen(["cmd.exe", "/k", "title ST_TEST_CONSOLE & timeout /t 10 >nul"], cwd=os.getcwd())

    try:
        t0 = time.time()
        hwnd = None
        while time.time() - t0 < 2.0:
            hwnd = _find_console_hwnd_by_pid(p.pid)
            if hwnd:
                break
            time.sleep(0.02)

        if not hwnd:
            print("skip: console hwnd not found (可能未创建独立控制台窗口)")
            return 0

        u32 = ctypes.windll.user32
        time.sleep(0.25)
        visible = bool(u32.IsWindowVisible(wt.HWND(hwnd)))
        print(f"pid={p.pid} hwnd={hwnd} visible={visible}")
        return 0 if not visible else 1
    finally:
        try:
            p.kill()
        except Exception:
            pass
        try:
            if stop_event is not None:
                stop_event.set()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
