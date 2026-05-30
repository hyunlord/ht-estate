"""throttle — 순수함수 + 주입형 Throttle을 가짜 시계로 결정론 검증."""

from __future__ import annotations

from app.throttle import Throttle, seconds_until_next


def test_seconds_until_next_first_call_is_zero() -> None:
    assert seconds_until_next(None, 100.0, 2.0) == 0.0


def test_seconds_until_next_waits_remaining() -> None:
    # last=100, now=100.5, 간격 2초 → 1.5초 더 대기
    assert seconds_until_next(100.0, 100.5, 2.0) == 1.5


def test_seconds_until_next_no_wait_when_elapsed() -> None:
    # 이미 3초 경과(간격 2초) → 대기 없음
    assert seconds_until_next(100.0, 103.0, 2.0) == 0.0


def test_throttle_sleeps_between_calls_with_fake_clock() -> None:
    times = iter([0.0, 0.0, 0.5, 0.5, 5.0, 5.0])  # clock() 호출 순서대로
    slept: list[float] = []
    throttle = Throttle(2.0, sleep=slept.append, clock=lambda: next(times))

    throttle.wait()  # 첫 호출: clock=0.0(delay 계산) → sleep 없음 → clock=0.0(last 갱신)
    throttle.wait()  # clock=0.5 → 0.5 경과, 1.5 더 자야 함 → sleep(1.5) → clock=0.5
    throttle.wait()  # clock=5.0 → 충분 경과 → sleep 없음

    assert slept == [1.5]
