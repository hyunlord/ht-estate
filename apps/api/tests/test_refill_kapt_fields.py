"""K-apt 풀필드 재적재(P0) — 죽은 criterion 깨우기.

검증 핵심(키리스·mock):
- 풀필드(elevator/cctv/subway/heat …) 채움 + **lat/lng·geo provenance 보존**(geocode 불변 1순위).
- resume(레저 완료분 skip) · 멱등(2회=1회·재fetch 0) · None/실패 미기록(다음 패스 재시도).
- cron-safe 배치 공유락: 점유 중이면 굶지 않고 양보(이번 run 중단=다음 run 재개), 배치마다 release.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.sources.errors import PublicDataError
from app.sources.kapt import ComplexInfo, parse_complex_info
from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refill_kapt_fields  # noqa: E402
from refill_kapt_fields import (  # noqa: E402
    DEFAULT_INTER_BATCH_SLEEP,
    ShlockBatch,
    all_complex_ids,
    main,
    mark_refilled,
    refilled_ids,
    run_refill,
)


class _OkResult:
    """shlock 결과 stub — `.returncode`만 노출(_CompletedLike 충족)."""

    returncode = 0


def _ok_runner(argv: list[str], **kwargs: object) -> _OkResult:
    """shlock 대체 — 항상 획득(단일 프로세스 main 테스트용·실 바이너리 불필요로 hermetic)."""
    return _OkResult()

FixtureLoader = Callable[[str], str]


def _full_info(load_fixture: FixtureLoader) -> ComplexInfo:
    """풀필드가 채워진 실캡처 ComplexInfo (A10027474)."""
    info = parse_complex_info(load_fixture("kapt_basis.json"), load_fixture("kapt_detail.json"))
    assert info is not None
    assert info.elevator_count is not None  # fixture가 풀필드 보유 전제
    return info


@contextmanager
def _always_acquired():
    yield True


def _acquire_factory():
    """run_refill에 주입할 lock — 항상 획득(테스트 기본)."""
    return _always_acquired()


def _seed_complex(conn, complex_id: str, *, lat=None, lng=None, geo_source=None) -> None:
    """풀필드 이전 상태(컬럼 NULL) + geocode 좌표를 가진 단지 한 행 — UPDATE 경로 보장."""
    conn.execute(
        "INSERT INTO complex (complex_id, name, bjd_code, lat, lng, geo_source, "
        "elevator_count, cctv_count, subway_time, heat_type) "
        "VALUES (?, '구명', '1168010100', ?, ?, ?, NULL, NULL, NULL, NULL)",
        (complex_id, lat, lng, geo_source),
    )
    conn.commit()


def test_refill_fills_fields_and_preserves_geocode(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    info = _full_info(load_fixture)
    _seed_complex(conn, info.kapt_code, lat=37.5, lng=127.04, geo_source="Kakao Local 주소검색")

    processed = run_refill(
        conn,
        fetch_info=lambda cid: info,
        lock=_acquire_factory,
        throttle=None,
        batch_size=10,
        limit=0,
    )

    assert processed == 1
    row = conn.execute(
        "SELECT lat, lng, geo_source, elevator_count, cctv_count, subway_time, heat_type "
        "FROM complex WHERE complex_id=?",
        (info.kapt_code,),
    ).fetchone()
    # geocode 보존 — 재적재가 절대 안 건드림(1순위 규율)
    assert row["lat"] == 37.5
    assert row["lng"] == 127.04
    assert row["geo_source"] == "Kakao Local 주소검색"
    # 깨어난 풀필드 — 0%였던 컬럼이 채워짐
    assert row["elevator_count"] == info.elevator_count
    assert row["cctv_count"] == info.cctv_count
    assert row["subway_time"] == info.subway_time
    assert row["heat_type"] == info.heat_type
    # 레저 기록(재개 skip 대상)
    assert refilled_ids(conn) == {info.kapt_code}


def test_refill_resumes_skipping_ledgered(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    info = _full_info(load_fixture)
    _seed_complex(conn, "C_DONE", lat=1.0, lng=2.0)
    _seed_complex(conn, "C_TODO", lat=3.0, lng=4.0)
    mark_refilled(conn, "C_DONE", 1)  # 이미 완료 표시

    fetched: list[str] = []

    def fake_fetch(cid: str) -> ComplexInfo:
        fetched.append(cid)
        return info.model_copy(update={"kapt_code": cid})

    processed = run_refill(
        conn, fetch_info=fake_fetch, lock=_acquire_factory, throttle=None, batch_size=10, limit=0
    )

    assert processed == 1
    assert fetched == ["C_TODO"]  # 완료분 재fetch 0(일일캡 보존)


def test_refill_idempotent_second_run_is_noop(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    info = _full_info(load_fixture)
    _seed_complex(conn, info.kapt_code, lat=37.5, lng=127.04)

    calls: list[str] = []

    def fake_fetch(cid: str) -> ComplexInfo:
        calls.append(cid)
        return info

    run_refill(conn, fetch_info=fake_fetch, lock=_acquire_factory, throttle=None,
               batch_size=10, limit=0)
    processed2 = run_refill(conn, fetch_info=fake_fetch, lock=_acquire_factory, throttle=None,
                            batch_size=10, limit=0)

    assert calls == [info.kapt_code]  # 2회차는 fetch 0
    assert processed2 == 0


def test_refill_limit_bounds_run(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    info = _full_info(load_fixture)
    for i in range(5):
        _seed_complex(conn, f"C{i}", lat=float(i), lng=float(i))

    def fake_fetch(cid: str) -> ComplexInfo:
        return info.model_copy(update={"kapt_code": cid})

    processed = run_refill(conn, fetch_info=fake_fetch, lock=_acquire_factory, throttle=None,
                           batch_size=2, limit=3)
    assert processed == 3  # limit이 run 바운드
    assert len(refilled_ids(conn)) == 3


def test_refill_skips_none_for_next_pass(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed_complex(conn, "C_OK", lat=1.0, lng=1.0)
    _seed_complex(conn, "C_NONE", lat=2.0, lng=2.0)
    info = _full_info(load_fixture)

    def fake_fetch(cid: str) -> ComplexInfo | None:
        return None if cid == "C_NONE" else info.model_copy(update={"kapt_code": cid})

    processed = run_refill(conn, fetch_info=fake_fetch, lock=_acquire_factory, throttle=None,
                           batch_size=10, limit=0)
    assert processed == 1
    assert refilled_ids(conn) == {"C_OK"}  # None 단지는 미기록 → 다음 패스 재시도


def test_refill_yields_when_lock_held(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed_complex(conn, "C0", lat=1.0, lng=1.0)
    info = _full_info(load_fixture)

    @contextmanager
    def _held():
        yield False  # cron이 점유 중

    processed = run_refill(conn, fetch_info=lambda cid: info, lock=lambda: _held(),
                           throttle=None, batch_size=10, limit=0)
    assert processed == 0  # 굶지 않고 양보(이번 run 중단=다음 run 재개)
    assert refilled_ids(conn) == set()


def test_refill_releases_lock_each_batch(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    for i in range(4):
        _seed_complex(conn, f"C{i}", lat=float(i), lng=float(i))
    info = _full_info(load_fixture)

    events: list[str] = []

    @contextmanager
    def _tracking():
        events.append("acquire")
        try:
            yield True
        finally:
            events.append("release")

    run_refill(conn, fetch_info=lambda cid: info.model_copy(update={"kapt_code": cid}),
               lock=lambda: _tracking(), throttle=None, batch_size=2, limit=0)
    # 4단지 / batch=2 → 배치 2개 → acquire/release 쌍 2회(배치마다 release로 cron 양보)
    assert events == ["acquire", "release", "acquire", "release"]


def test_all_complex_ids_stable_order() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    for cid in ("C3", "C1", "C2"):
        conn.execute("INSERT INTO complex (complex_id) VALUES (?)", (cid,))
    conn.commit()
    assert all_complex_ids(conn) == ["C1", "C2", "C3"]  # 결정론(resume+limit 안정)


def test_shlock_batch_spin_acquire_then_release(tmp_path: Path) -> None:
    lock = tmp_path / ".ingest.lock"
    calls: list[list[str]] = []
    sleeps: list[float] = []

    class _Result:
        def __init__(self, rc: int) -> None:
            self.returncode = rc

    # 첫 시도 점유(rc=1), 둘째 시도 획득(rc=0) → spin이 cron에 양보 후 획득
    rcs = iter([1, 0])

    def fake_run(argv, **kw):  # type: ignore[no-untyped-def]
        calls.append(argv)
        return _Result(next(rcs))

    batch = ShlockBatch(
        str(lock), runner=fake_run, pid=4242, sleep=sleeps.append,
        clock=iter([0.0, 1.0, 2.0]).__next__, spin_interval=1.0, max_spin=10.0,
    )
    lock.write_text("stub")  # release가 지울 대상
    with batch() as acquired:
        assert acquired is True
    assert sleeps == [1.0]  # 1회 양보 후 획득
    assert all(a[:1] == ["/usr/bin/shlock"] for a in calls)
    assert not lock.exists()  # release가 락 파일 제거


def test_shlock_batch_gives_up_when_persistently_held(tmp_path: Path) -> None:
    lock = tmp_path / ".ingest.lock"

    class _Result:
        returncode = 1  # 계속 점유

    batch = ShlockBatch(
        str(lock), runner=lambda *a, **k: _Result(), pid=1, sleep=lambda s: None,
        clock=iter([0.0, 1.0, 2.0, 3.0]).__next__, spin_interval=1.0, max_spin=2.0,
    )
    with batch() as acquired:
        assert acquired is False  # max_spin 초과 → 포기(이번 run 양보)


def test_main_keyless_runs_with_injected_fetch(
    tmp_path: Path, load_fixture: FixtureLoader, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "t.db"
    conn = get_connection(str(db))
    init_db(conn)
    info = _full_info(load_fixture)
    _seed_complex(conn, info.kapt_code, lat=37.5, lng=127.04)
    conn.close()

    monkeypatch.setattr(refill_kapt_fields, "get_api_key", lambda: "dummy")
    monkeypatch.setattr(
        refill_kapt_fields, "fetch_complex_info", lambda cid, **kw: info
    )
    rc = main(
        ["--db", str(db), "--lock", str(tmp_path / ".ingest.lock"), "--interval", "0"],
        runner=_ok_runner,  # 실 /usr/bin/shlock 불필요 — hermetic(샌드박스/CI 이식)
    )
    assert rc == 0

    conn = get_connection(str(db))
    row = conn.execute(
        "SELECT lat, elevator_count FROM complex WHERE complex_id=?", (info.kapt_code,)
    ).fetchone()
    assert row["lat"] == 37.5  # 보존
    assert row["elevator_count"] == info.elevator_count  # 채움


def _seed_with_bjd(conn, complex_id: str, bjd_code: str) -> None:
    conn.execute(
        "INSERT INTO complex (complex_id, bjd_code, lat, lng) VALUES (?, ?, 1.0, 1.0)",
        (complex_id, bjd_code),
    )
    conn.commit()


def test_all_complex_ids_sido_filter_and_order() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed_with_bjd(conn, "S2", "1168010100")  # 서울 11
    _seed_with_bjd(conn, "B1", "2611010100")  # 부산 26
    _seed_with_bjd(conn, "S1", "1111010100")  # 서울 11
    _seed_with_bjd(conn, "G1", "4113510300")  # 경기 41 (도심 아님)
    # 도심(11,26)만, 시도 오름차순(서울11→부산26) + 시도 내 complex_id 정렬
    assert all_complex_ids(conn, ["11", "26"]) == ["S1", "S2", "B1"]
    assert all_complex_ids(conn) == ["B1", "G1", "S1", "S2"]  # 무필터=전체


def test_refill_sido_prefix_only_processes_urban(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed_with_bjd(conn, "S1", "1111010100")  # 서울
    _seed_with_bjd(conn, "G1", "4113510300")  # 경기(보류)
    info = _full_info(load_fixture)
    fetched: list[str] = []

    def fake(cid: str) -> ComplexInfo:
        fetched.append(cid)
        return info.model_copy(update={"kapt_code": cid})

    processed = run_refill(conn, fetch_info=fake, lock=_acquire_factory, throttle=None,
                           batch_size=10, limit=0, sido_prefixes=["11"])
    assert processed == 1
    assert fetched == ["S1"]  # 경기(41)는 도심 필터에서 제외


def test_refill_stops_gracefully_on_public_data_error(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    for i in range(4):
        _seed_complex(conn, f"C{i}", lat=float(i), lng=float(i))
    info = _full_info(load_fixture)

    def fake(cid: str) -> ComplexInfo:
        if cid == "C2":  # 3번째에서 일일캡 초과 모사
            raise PublicDataError("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR")
        return info.model_copy(update={"kapt_code": cid})

    processed = run_refill(conn, fetch_info=fake, lock=_acquire_factory, throttle=None,
                           batch_size=10, limit=0)
    # 캡 전 2건만 처리·기록, 크래시 없이 중단(레저로 다음 run 재개)
    assert processed == 2
    assert refilled_ids(conn) == {"C0", "C1"}


def test_refill_inter_batch_sleep_yields_lock_between_batches(load_fixture: FixtureLoader) -> None:
    """양보 검증 — 배치 release 후·재acquire 전 양보 창에 경합 acquirer(가짜 cron)가 락 획득.

    starve 회귀 방어: release 직후 즉시 재acquire(microsecond 창)면 단발 cron이 락을 못 잡는다.
    이 테스트는 inter_batch_sleep이 *락을 놓은 뒤* 호출되어 그 사이 락이 비고(=경합자 획득 가능),
    마지막 배치 뒤엔 양보하지 않음을 결정론으로 단언한다.
    """
    conn = get_connection(":memory:")
    init_db(conn)
    for i in range(4):
        _seed_complex(conn, f"C{i}", lat=float(i), lng=float(i))
    info = _full_info(load_fixture)

    holder: dict[str, str | None] = {"who": None}  # shlock 파일과 동형: 한 번에 한 보유자

    @contextmanager
    def refill_lock():
        assert holder["who"] is None  # release 후 재진입이라 항상 비어있어야
        holder["who"] = "refill"
        try:
            yield True
        finally:
            holder["who"] = None

    cron_wins: list[float] = []

    def fake_cron_tick(seconds: float) -> None:
        # 양보 창에 단발 cron이 끼어든다. refill이 *정말* 락을 놓고 sleep했다면 holder는 비어
        # 획득 성공. 보유 중 sleep이면 holder=="refill"이라 이 단언이 깨져 회귀를 잡는다.
        assert holder["who"] is None
        holder["who"] = "cron"
        cron_wins.append(seconds)
        holder["who"] = None  # cron tick 종료 → release

    run_refill(
        conn,
        fetch_info=lambda cid: info.model_copy(update={"kapt_code": cid}),
        lock=lambda: refill_lock(),
        throttle=None,
        batch_size=2,
        limit=0,
        inter_batch_sleep=2.0,
        sleep=fake_cron_tick,
    )
    # 4단지/batch=2 → 배치 2개 → 사이 양보 1회(마지막 뒤엔 없음) → cron 1회 획득
    assert cron_wins == [2.0]


def test_refill_inter_batch_sleep_zero_disables_yield(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    for i in range(4):
        _seed_complex(conn, f"C{i}", lat=float(i), lng=float(i))
    info = _full_info(load_fixture)
    sleeps: list[float] = []

    run_refill(
        conn,
        fetch_info=lambda cid: info.model_copy(update={"kapt_code": cid}),
        lock=_acquire_factory,
        throttle=None,
        batch_size=2,
        limit=0,
        inter_batch_sleep=0.0,
        sleep=sleeps.append,
    )
    assert sleeps == []  # 0=무양보(known-idle 전용)


def test_main_forwards_inter_batch_sleep_default_and_override(
    tmp_path: Path, load_fixture: FixtureLoader, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI 기본값(활성-cron 안전 양수)이 run_refill로 전달되고, --inter-batch-sleep로 덮인다."""
    db = tmp_path / "t.db"
    conn = get_connection(str(db))
    init_db(conn)
    info = _full_info(load_fixture)
    _seed_complex(conn, info.kapt_code, lat=37.5, lng=127.04)
    conn.close()

    captured: list[float] = []

    def spy_run_refill(*_a, inter_batch_sleep: float, **_kw):  # type: ignore[no-untyped-def]
        captured.append(inter_batch_sleep)
        return 0

    monkeypatch.setattr(refill_kapt_fields, "get_api_key", lambda: "dummy")
    monkeypatch.setattr(refill_kapt_fields, "fetch_complex_info", lambda cid, **kw: info)
    monkeypatch.setattr(refill_kapt_fields, "run_refill", spy_run_refill)

    base = ["--db", str(db), "--lock", str(tmp_path / ".ingest.lock"), "--interval", "0"]
    assert main(base, runner=_ok_runner) == 0
    assert main([*base, "--inter-batch-sleep", "0"], runner=_ok_runner) == 0

    assert captured == [DEFAULT_INTER_BATCH_SLEEP, 0.0]
    assert DEFAULT_INTER_BATCH_SLEEP > 0  # 기본은 활성-cron 안전 양수
