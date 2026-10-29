# 进程内滑动窗口限流：同一来源（登录账号或 IP）在 window_s 秒内最多 limit 次（单进程部署，见 AGENTS.md「技术选择」）
from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """记一次并返回是否放行；超限的这次不计入。"""
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_s
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            # 顺手清掉早已过窗的来源，字典不会随访问者无限增长
            if len(self._hits) > 1000:
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                    del self._hits[k]
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
