"""Tiny in-process sliding-window limiter for the paid-LLM extract endpoint.

Process-local state is sufficient while the API runs single-instance
(Cloud Run max-instances=1); if that changes, swap the store behind the
same allow() contract.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

_HOUR = 3600.0
_DAY = 86400.0
_MAX_TRACKED_IPS = 10_000


class SlidingWindowLimiter:
    def __init__(self, per_ip_per_hour: int, global_per_day: int) -> None:
        self.per_ip_per_hour = per_ip_per_hour
        self.global_per_day = global_per_day
        self._by_ip: dict[str, deque[float]] = defaultdict(deque)
        self._all: deque[float] = deque()

    def allow(self, ip: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        window = self._by_ip[ip]
        while window and now - window[0] > _HOUR:
            window.popleft()
        while self._all and now - self._all[0] > _DAY:
            self._all.popleft()
        if len(window) >= self.per_ip_per_hour:
            return False
        if len(self._all) >= self.global_per_day:
            return False
        window.append(now)
        self._all.append(now)
        if len(self._by_ip) > _MAX_TRACKED_IPS:
            self._by_ip = defaultdict(deque, {k: v for k, v in self._by_ip.items() if v})
        return True
