from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class TokenBucket:
    rate: float
    capacity: float
    tokens: float
    last_ts: float

    @classmethod
    def create(cls, rate: int, capacity: int) -> "TokenBucket":
        now = time.monotonic()
        return cls(rate=float(rate), capacity=float(capacity), tokens=float(capacity), last_ts=now)

    def take(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_ts
        self.last_ts = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        if self.tokens < cost:
            return False
        self.tokens -= cost
        return True

