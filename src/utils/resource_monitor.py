"""
资源监控工具（进程内存/CPU、GPU 显存）

设计目标：
- 启动阶段不阻塞：依赖尽量延迟导入
- 可用性优先：GPU 显存优先使用 torch.cuda；无 GPU 时返回 None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class ProcessStats:
    rss_bytes: int
    cpu_percent: Optional[float]  # 进程 CPU%，psutil 采样法；不可用则 None


@dataclass
class GpuStats:
    available: bool
    device_name: Optional[str]
    total_bytes: Optional[int]
    allocated_bytes: Optional[int]
    reserved_bytes: Optional[int]


def format_bytes(num_bytes: Optional[float]) -> str:
    if num_bytes is None:
        return "-"
    try:
        n = float(num_bytes)
    except Exception:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    if i == 0:
        return f"{int(n)} {units[i]}"
    return f"{n:.1f} {units[i]}"


def _try_get_psutil_process():
    try:
        import psutil  # type: ignore
        return psutil.Process()
    except Exception:
        return None


def init_process_cpu_sampler() -> None:
    """
    psutil 的 cpu_percent 需要先调用一次“预热”，否则首次读数常为 0。
    """
    p = _try_get_psutil_process()
    if not p:
        return
    try:
        p.cpu_percent(interval=None)
    except Exception:
        pass


def get_process_stats() -> ProcessStats:
    p = _try_get_psutil_process()
    if not p:
        # 没有 psutil 时无法稳定获取 RSS，这里退化为 0
        return ProcessStats(rss_bytes=0, cpu_percent=None)
    try:
        rss = int(getattr(p.memory_info(), "rss", 0) or 0)
    except Exception:
        rss = 0
    try:
        cpu = p.cpu_percent(interval=None)
        cpu = float(cpu) if cpu is not None else None
    except Exception:
        cpu = None
    return ProcessStats(rss_bytes=rss, cpu_percent=cpu)


def get_gpu_stats() -> GpuStats:
    """
    优先 torch.cuda：
    - allocated: torch 已分配给张量/缓存的显存
    - reserved: torch CUDA caching allocator 保留的显存
    """
    try:
        import torch  # type: ignore
    except Exception:
        return GpuStats(
            available=False,
            device_name=None,
            total_bytes=None,
            allocated_bytes=None,
            reserved_bytes=None,
        )

    try:
        if not torch.cuda.is_available():
            return GpuStats(False, None, None, None, None)
        dev = 0
        name = None
        total = None
        try:
            name = torch.cuda.get_device_name(dev)
        except Exception:
            name = None
        try:
            total = int(torch.cuda.get_device_properties(dev).total_memory)
        except Exception:
            total = None
        try:
            allocated = int(torch.cuda.memory_allocated(dev))
        except Exception:
            allocated = None
        try:
            reserved = int(torch.cuda.memory_reserved(dev))
        except Exception:
            reserved = None
        return GpuStats(True, name, total, allocated, reserved)
    except Exception:
        return GpuStats(False, None, None, None, None)


def stats_to_dict(ps: ProcessStats, gs: GpuStats) -> Dict[str, Any]:
    return {
        "process": {"rss_bytes": ps.rss_bytes, "cpu_percent": ps.cpu_percent},
        "gpu": {
            "available": gs.available,
            "device_name": gs.device_name,
            "total_bytes": gs.total_bytes,
            "allocated_bytes": gs.allocated_bytes,
            "reserved_bytes": gs.reserved_bytes,
        },
    }


