"""
Windows 硬件指纹（设备ID）

规则：设备ID = 主板序列号 + 硬盘序列号
说明：
- 尽量使用 PowerShell 的 CIM 查询（Win10/11 通用）
- 失败时降级到 wmic（部分系统可能禁用/移除）
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional, List


def _run_cmd(cmd: List[str]) -> str:
    """运行命令并返回 stdout（失败返回空字符串）"""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if p.returncode != 0:
            return ""
        return (p.stdout or "").strip()
    except Exception:
        return ""


def _clean_serial(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    # 去掉常见的空白/无意义值
    s = re.sub(r"\s+", "", s)
    if s.lower() in {"none", "null", "unknown", "n/a", "na"}:
        return ""
    return s


def _first_nonempty(lines: List[str]) -> str:
    for line in lines:
        v = _clean_serial(line)
        if v:
            return v
    return ""


def get_motherboard_serial() -> str:
    """获取主板序列号（Win32_BaseBoard.SerialNumber）"""
    if os.name != "nt":
        return ""

    # PowerShell CIM（推荐）
    out = _run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "(Get-CimInstance Win32_BaseBoard | Select-Object -First 1 -ExpandProperty SerialNumber)",
        ]
    )
    v = _clean_serial(out)
    if v:
        return v

    # wmic 降级
    out = _run_cmd(["wmic", "baseboard", "get", "serialnumber"])
    if out:
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        # 第一行通常是列名
        if len(lines) >= 2:
            return _first_nonempty(lines[1:])
        return _first_nonempty(lines)

    return ""


def get_disk_serial() -> str:
    """
    获取硬盘序列号
    - 优先 Win32_PhysicalMedia.SerialNumber（可能为空/带空格）
    - 再尝试 Win32_DiskDrive.SerialNumber
    """
    if os.name != "nt":
        return ""

    # PowerShell CIM（PhysicalMedia）
    out = _run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "(Get-CimInstance Win32_PhysicalMedia | Select-Object -First 1 -ExpandProperty SerialNumber)",
        ]
    )
    v = _clean_serial(out)
    if v:
        return v

    # PowerShell CIM（DiskDrive）
    out = _run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "(Get-CimInstance Win32_DiskDrive | Select-Object -First 1 -ExpandProperty SerialNumber)",
        ]
    )
    v = _clean_serial(out)
    if v:
        return v

    # wmic 降级
    out = _run_cmd(["wmic", "diskdrive", "get", "serialnumber"])
    if out:
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if len(lines) >= 2:
            return _first_nonempty(lines[1:])
        return _first_nonempty(lines)

    return ""


def get_hardware_id(fallback: Optional[str] = None) -> str:
    """
    生成设备ID：主板序列号 + 硬盘序列号
    - 若某项取不到，会用空字符串拼接；两者都取不到则返回 fallback 或空。
    """
    mb = get_motherboard_serial()
    disk = get_disk_serial()
    hwid = _clean_serial(mb + disk)
    if hwid:
        return hwid
    return fallback or ""


