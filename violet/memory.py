"""Short-lived conversation memory kept in RAM only (never written to disk)."""
from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Hashable

from .utils import truncate


class ConversationMemory:
    def __init__(self, turns: int, ttl_seconds: int, *, max_keys: int = 300, clock=time.monotonic) -> None:
        self._turns = turns
        self._ttl = ttl_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._data: OrderedDict[Hashable, deque[tuple[float, str, str]]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self._turns > 0

    def add(self, key: Hashable, role: str, content: str) -> None:
        if not self.enabled:
            return
        buf = self._data.get(key)
        if buf is None:
            buf = deque(maxlen=self._turns * 2)
            self._data[key] = buf
            while len(self._data) > self._max_keys:
                self._data.popitem(last=False)  # forget the least recently used conversation
        self._data.move_to_end(key)
        buf.append((self._clock(), role, truncate(content, 1200)))

    def history(self, key: Hashable) -> list[dict[str, str]]:
        buf = self._data.get(key)
        if not buf:
            return []
        now = self._clock()
        while buf and now - buf[0][0] > self._ttl:
            buf.popleft()
        if not buf:
            self._data.pop(key, None)
            return []
        self._data.move_to_end(key)
        return [{"role": role, "content": text} for _, role, text in buf]

    def clear(self, key: Hashable) -> bool:
        return self._data.pop(key, None) is not None
