"""적재 자동 재개 루프 (C22) — 완료까지 반복·일시오류 백오프 재시도·영구오류 중단·max_runs 가드."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from app.sources.errors import PublicDataError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ingest_loop import default_is_permanent, loop_until_done  # noqa: E402


def test_loops_until_remaining_zero() -> None:
    runs = {"n": 0}
    # remaining: 시작 2 → run 후 1 → run 후 0
    remaining_seq = iter([2, 1, 0])

    def run_once() -> None:
        runs["n"] += 1

    ok = loop_until_done(
        run_once, lambda: next(remaining_seq), max_runs=5,
        sleep=lambda _d: None, log=lambda _m: None,
    )
    assert ok is True
    assert runs["n"] == 2  # 2회 run으로 완료


def test_already_done_runs_nothing() -> None:
    runs = {"n": 0}
    ok = loop_until_done(
        lambda: runs.__setitem__("n", runs["n"] + 1), lambda: 0,
        max_runs=5, sleep=lambda _d: None, log=lambda _m: None,
    )
    assert ok is True and runs["n"] == 0


def test_transient_error_backs_off_and_retries() -> None:
    calls = {"n": 0}
    slept: list[float] = []

    def run_once() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("network down")  # 일시 → 재시도

    # remaining: 첫 호출은 run 전(2), 성공 후 0
    remaining_seq = iter([2, 0])
    ok = loop_until_done(
        run_once, lambda: next(remaining_seq), max_runs=5,
        sleep=slept.append, retry_backoff=30.0, log=lambda _m: None,
    )
    assert ok is True
    assert calls["n"] == 2  # 1실패 + 1성공
    assert slept and slept[0] == 30.0  # 일시 오류 후 백오프 대기


def test_permanent_error_stops() -> None:
    def run_once() -> None:
        raise PublicDataError("22", "일일 트래픽 초과")  # 일일캡 — 영구(루프로 못 품)

    with pytest.raises(PublicDataError):
        loop_until_done(
            run_once, lambda: 5, max_runs=5, sleep=lambda _d: None, log=lambda _m: None
        )


def test_max_runs_guard_returns_false() -> None:
    # remaining이 계속 >0이면 max_runs에서 멈추고 False(미완).
    ok = loop_until_done(
        lambda: None, lambda: 3, max_runs=3, sleep=lambda _d: None, log=lambda _m: None
    )
    assert ok is False


def test_backoff_grows_then_resets_on_success() -> None:
    slept: list[float] = []
    seq = iter([
        httpx.ConnectError("x"), httpx.ConnectError("x"), None,  # 2 실패 후 성공
    ])

    def run_once() -> None:
        exc = next(seq)
        if exc is not None:
            raise exc

    remaining_seq = iter([5, 0])  # 성공 후 0
    loop_until_done(
        run_once, lambda: next(remaining_seq), max_runs=5,
        sleep=slept.append, retry_backoff=10.0, max_backoff=1000.0, log=lambda _m: None,
    )
    assert slept == [10.0, 20.0]  # 지수 증가(10→20), 성공 시 리셋


def test_default_is_permanent_classification() -> None:
    assert default_is_permanent(PublicDataError("30", "키 미등록")) is True
    assert default_is_permanent(PublicDataError("22", "캡 초과")) is True
    req = httpx.Request("GET", "http://x")
    err403 = httpx.HTTPStatusError("f", request=req, response=httpx.Response(403, request=req))
    err503 = httpx.HTTPStatusError("e", request=req, response=httpx.Response(503, request=req))
    assert default_is_permanent(err403) is True
    assert default_is_permanent(err503) is False
    assert default_is_permanent(httpx.ConnectError("down")) is False
