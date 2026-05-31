"""적재 오케스트레이션 — 스테이지 mock으로 순서·부분선택·요약·월파싱·경고(키리스)."""

from __future__ import annotations

import sqlite3

import pytest

import app.ingest as ingest_mod
from app.ingest import STAGE_ORDER, main, parse_months, run_ingest
from app.store.db import get_connection


@pytest.fixture
def conn() -> sqlite3.Connection:
    return get_connection(":memory:")


@pytest.fixture
def stage_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_complexes(conn, **kw):  # type: ignore[no-untyped-def]
        calls.append("complex")
        return 5

    def fake_months(conn, region, months, **kw):  # type: ignore[no-untyped-def]
        calls.append("transaction")
        return 113

    def fake_join(conn, **kw):  # type: ignore[no-untyped-def]
        calls.append("join")
        return {"matched": 50, "unmatched": 63, "total": 113}

    def fake_geo(conn, geocode, **kw):  # type: ignore[no-untyped-def]
        calls.append("geocode")
        return {"matched": 4, "unmatched": 0, "total": 4}

    monkeypatch.setattr(ingest_mod, "ingest_complexes", fake_complexes)
    monkeypatch.setattr(ingest_mod, "ingest_months", fake_months)
    monkeypatch.setattr(ingest_mod, "backfill_matches", fake_join)
    monkeypatch.setattr(ingest_mod, "backfill_coords", fake_geo)
    return calls


def test_runs_all_stages_in_canonical_order(conn, stage_calls: list[str]) -> None:
    summary = run_ingest(
        conn, region="11680", months=["202504"], stages=list(STAGE_ORDER),
        api_key="d", kakao_key="d", log=lambda _m: None,
    )
    assert stage_calls == ["complex", "transaction", "join", "geocode"]
    assert summary.complexes == 5
    assert summary.transactions == 113
    assert summary.matched == 50 and summary.join_total == 113
    assert summary.geocoded == 4 and summary.geocode_total == 4


def test_canonical_order_regardless_of_input_order(conn, stage_calls: list[str]) -> None:
    run_ingest(
        conn, region="11680", months=[], stages=["geocode", "complex"],
        api_key="d", kakao_key="d", log=lambda _m: None,
    )
    assert stage_calls == ["complex", "geocode"]  # 입력 역순이어도 canonical


def test_partial_stage_runs_only_selected(conn, stage_calls: list[str]) -> None:
    run_ingest(conn, region="11680", months=[], stages=["join"], log=lambda _m: None)
    assert stage_calls == ["join"]


def test_prereq_warning_logged(conn, stage_calls: list[str]) -> None:
    logs: list[str] = []
    run_ingest(
        conn, region="11680", months=[], stages=["geocode"], kakao_key="d", log=logs.append
    )
    # geocode는 complex 선행 — 미선택이라 경고
    assert any("선행 미선택" in line and "geocode" in line for line in logs)


def test_summary_str() -> None:
    from app.ingest import IngestSummary

    s = IngestSummary(
        complexes=5, transactions=113, matched=50, join_total=113, geocoded=4, geocode_total=4
    )
    text = str(s)
    assert "단지 5" in text and "거래 113" in text and "50/113" in text and "4/4" in text


def test_parse_months_range() -> None:
    assert parse_months("202412-202503") == ["202412", "202501", "202502", "202503"]


def test_parse_months_list_and_empty() -> None:
    assert parse_months("202401,202403") == ["202401", "202403"]
    assert parse_months("") == []


def test_main_join_only_is_keyless(stage_calls: list[str]) -> None:
    # join은 키 불필요 → 키리스로 main 동작
    rc = main(["--region", "11680", "--stages", "join", "--db", ":memory:"])
    assert rc == 0
    assert stage_calls == ["join"]


def test_main_rejects_unknown_stage() -> None:
    with pytest.raises(SystemExit):
        main(["--region", "11680", "--stages", "bogus", "--db", ":memory:"])
