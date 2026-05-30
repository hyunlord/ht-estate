"""레이트 throttle — 다월/다지역 증분 적재 시 개발계정 일일한도 대비 호출 간 최소 간격.

핵심 로직은 순수함수 `seconds_until_next`로 분리해 시계 없이 결정론적으로 검증한다.
`Throttle`은 sleep/clock을 주입받아 테스트에서 가짜 시계로 돌릴 수 있다.
"""

from __future__ import annotations

import time
from collections.abc import Callable


def seconds_until_next(last: float | None, now: float, min_interval: float) -> float:
    """직전 호출(last) 이후 min_interval을 채우기 위해 더 기다릴 초. 첫 호출/충분경과면 0."""
    if last is None:
        return 0.0
    remaining = min_interval - (now - last)
    return remaining if remaining > 0 else 0.0


class Throttle:
    """호출 사이 최소 간격을 보장. `wait()`를 매 호출 직전에 부른다."""

    def __init__(
        self,
        min_interval: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        delay = seconds_until_next(self._last, self._clock(), self._min_interval)
        if delay > 0:
            self._sleep(delay)
        self._last = self._clock()
