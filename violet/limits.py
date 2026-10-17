"""Fair-use protection: per-user cooldown, per-user hourly cap and a global daily budget.

Everything lives in memory (resets on restart), which is exactly right for a small free-hosted bot.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Verdict:
    ok: bool
    kind: str = ""  # "cooldown" | "hourly" | "daily"
    retry_after: float = 0.0


class UsageGuard:
    def __init__(self, cooldown: float, hourly_limit: int, daily_limit: int, *, clock=time.monotonic, utc_day=None) -> None:
        self._cooldown = cooldown
        self._hourly = hourly_limit
        self._daily = daily_limit
        self._clock = clock
        self._utc_day = utc_day or (lambda: datetime.now(timezone.utc).date())
        self._last: dict[int, float] = {}
        self._hour: dict[int, deque[float]] = {}
        self._day = self._utc_day()
        self.daily_used = 0
        self._ops = 0

    @property
    def daily_limit(self) -> int:
        return self._daily

    def _roll_day(self) -> None:
        today = self._utc_day()
        if today != self._day:
            self._day, self.daily_used = today, 0

    def check(self, user_id: int) -> Verdict:
        """Ask whether this user may make a request right now (does not count it)."""
        self._roll_day()
        now = self._clock()
        if self.daily_used >= self._daily:
            return Verdict(False, "daily")
        last = self._last.get(user_id)
        if last is not None and now - last < self._cooldown:
            return Verdict(False, "cooldown", self._cooldown - (now - last))
        window = self._hour.get(user_id)
        if window:
            while window and now - window[0] >= 3600:
                window.popleft()
            if len(window) >= self._hourly:
                return Verdict(False, "hourly", 3600 - (now - window[0]))
        return Verdict(True)

    def commit(self, user_id: int) -> None:
        """Count a request that is about to be made."""
        now = self._clock()
        self._last[user_id] = now
        self._hour.setdefault(user_id, deque()).append(now)
        self.daily_used += 1
        self._ops += 1
        if self._ops % 200 == 0:
            self._prune(now)

    def refund(self, user_id: int) -> None:
        """Undo a commit when the request failed through no fault of the user."""
        window = self._hour.get(user_id)
        if window:
            window.pop()
        self._last.pop(user_id, None)
        self.daily_used = max(0, self.daily_used - 1)

    def begin(self, user_id: int) -> Verdict:
        """check() + commit() in one step."""
        verdict = self.check(user_id)
        if verdict.ok:
            self.commit(user_id)
        return verdict

    def _prune(self, now: float) -> None:
        for uid in [u for u, t in self._last.items() if now - t > 3600]:
            self._last.pop(uid, None)
            self._hour.pop(uid, None)
