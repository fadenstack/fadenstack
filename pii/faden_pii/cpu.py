"""CPU thread limits for direct runs and constrained containers."""

import os
from pathlib import Path


def available_threads() -> int:
    available = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    try:
        available = min(available, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        pass
    for quota_path, period_path in (
        (Path("/sys/fs/cgroup/cpu.max"), None),
        (
            Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
            Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us"),
        ),
    ):
        try:
            if period_path is None:
                quota, period = quota_path.read_text().split()
            else:
                quota, period = quota_path.read_text(), period_path.read_text()
            if quota.strip() != "max" and int(quota) > 0:
                available = min(available, max(1, int(quota) // int(period)))
        except (OSError, ValueError, ZeroDivisionError):
            continue
    return max(1, available)
