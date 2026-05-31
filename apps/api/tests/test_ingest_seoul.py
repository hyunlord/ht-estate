"""서울 다지역 배치 — 지역 루프·순서·공유 throttle·월계산·region 선택(키리스, run_ingest mock)."""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from app.ingest import IngestSummary
from app.store.db import get_connection
from app.throttle import Throttle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_seoul  # noqa: E402
from ingest_seoul import SEOUL_25, coverage_table, main, recent_months, run_batch  # noqa: E402


@pytest.fixture
def conn() -> sqlite3.Connection:
    return get_connection(":memory:")


def test_seoul_25_has_25_distinct_valid_codes() -> None:
    codes = [c for c, _ in SEOUL_25]
    assert len(codes) == 25
    assert len(set(codes)) == 25  # 중복 없음
    assert all(c.startswith("11") and len(c) == 5 for c in codes)  # 서울 시군구코드


def test_recent_months_returns_12_ascending_ending_prev_month() -> None:
    months = recent_months(date(2026, 5, 31))
    assert len(months) == 12
    assert months == sorted(months)  # 오름차순
    assert months[-1] == "202604"  # 마지막 완료월(전월)
    assert months[0] == "202505"


def test_run_batch_loops_regions_in_order_sharing_throttle(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, object]] = []
    shared = Throttle(0.0)

    def fake_run_ingest(c, *, region, months, stages, api_key, kakao_key, throttle, log):  # type: ignore[no-untyped-def]
        seen.append((region, throttle))
        return IngestSummary(complexes=3, transactions=10)

    monkeypatch.setattr(ingest_seoul, "run_ingest", fake_run_ingest)
    regions = [("11110", "종로구"), ("11140", "중구")]
    results = run_batch(
        conn, regions, months=["202604"], stages=["complex", "transaction"],
        api_key="d", kakao_key=None, throttle=shared,
    )
    assert [r.code for r in results] == ["11110", "11140"]  # 입력 순서 유지
    assert [region for region, _ in seen] == ["11110", "11140"]
    assert all(t is shared for _, t in seen)  # 전 지역 동일 throttle 공유
    assert results[0].summary.complexes == 3


def test_main_rejects_unknown_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest_seoul, "run_batch", lambda *a, **k: [])
    with pytest.raises(SystemExit):
        main(["--regions", "99999", "--stages", "complex", "--db", ":memory:"])


def test_main_selects_subset_and_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_regions: list[tuple[str, str]] = []
    captured_stages: list[str] = []

    def fake_batch(conn, regions, **kw):  # type: ignore[no-untyped-def]
        captured_regions.extend(regions)
        captured_stages.extend(kw["stages"])
        return []

    monkeypatch.setattr(ingest_seoul, "run_batch", fake_batch)
    # 키 게터 우회(테스트 환경에 키 없을 수 있음 — complex는 DATA 키 경로를 탄다).
    monkeypatch.setattr(ingest_seoul, "get_api_key", lambda: "dummy")
    monkeypatch.setattr(ingest_seoul, "get_kakao_key", lambda: "dummy")
    rc = main(["--regions", "11110,11710", "--stages", "complex", "--months", "202604",
               "--db", ":memory:"])
    assert rc == 0
    assert [c for c, _ in captured_regions] == ["11110", "11710"]  # SEOUL_25 순서 보존
    assert captured_stages == ["complex"]


def test_coverage_table_sums_totals() -> None:
    results = [
        ingest_seoul.RegionResult("11110", "종로구",
                                  IngestSummary(complexes=2, transactions=5,
                                                geocoded=2, geocode_total=2)),
        ingest_seoul.RegionResult("11140", "중구",
                                  IngestSummary(complexes=3, transactions=7,
                                                geocoded=1, geocode_total=3)),
    ]
    table = coverage_table(results)
    assert "단지 5" in table  # 2+3
    assert "거래 12" in table  # 5+7
    assert "3/5" in table  # geocode 합계
