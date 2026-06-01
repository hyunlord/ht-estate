"""전국 적재 — 시군구 도출·CSV 왕복·재개 skip·시도집계·루프(키리스, mock)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.ingest import IngestSummary
from app.sources.kapt import ComplexRef
from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_nationwide  # noqa: E402
from ingest_nationwide import (  # noqa: E402
    discover_sigungu,
    load_codes,
    loaded_sigungu,
    main,
    save_codes,
    sido_summary,
)
from ingest_seoul import RegionResult  # noqa: E402


def _ref(kapt_code: str, bjd: str, sido: str, sigungu: str) -> ComplexRef:
    return ComplexRef(kapt_code=kapt_code, name=kapt_code, bjd_code=bjd, sido=sido, sigungu=sigungu)


def test_discover_distinct_sigungu_from_bjd_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    refs = [
        _ref("A1", "1111011800", "서울특별시", "종로구"),
        _ref("A2", "1111012000", "서울특별시", "종로구"),  # 같은 시군구(11110) — 1개로
        _ref("A3", "4113510300", "경기도", "성남시분당구"),
        _ref("A4", "5011025000", "제주특별자치도", "제주시"),
    ]
    monkeypatch.setattr(ingest_nationwide, "list_complexes", lambda **kw: refs)
    codes = discover_sigungu("dummy")
    assert [c for c, _, _ in codes] == ["11110", "41135", "50110"]  # distinct·정렬
    assert codes[0] == ("11110", "서울특별시", "종로구")


def test_csv_round_trip(tmp_path: Path) -> None:
    rows = [("11110", "서울특별시", "종로구"), ("41135", "경기도", "성남시분당구")]
    p = tmp_path / "sigungu.csv"
    save_codes(p, rows)
    assert load_codes(p) == rows  # 헤더 skip + 왕복 동일


def test_loaded_sigungu_for_resume() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, bjd_code) VALUES (?, ?)",
        [("C1", "1111011800"), ("C2", "1111012000"), ("C3", "4113510300")],
    )
    conn.commit()
    assert loaded_sigungu(conn) == {"11110", "41135"}  # distinct 앞5


def test_sido_summary_groups_by_prefix() -> None:
    results = [
        RegionResult("11110", "서울 종로", IngestSummary(complexes=38, transactions=700)),
        RegionResult("11140", "서울 중구", IngestSummary(complexes=49, transactions=1200)),
        RegionResult("41135", "경기 분당", IngestSummary(complexes=100, transactions=3000)),
    ]
    out = sido_summary(results)
    assert "11  시군구  2 · 단지    87" in out  # 서울 2개 합산 38+49
    assert "41  시군구  1 · 단지   100" in out


def test_main_discover_writes_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest_nationwide, "get_api_key", lambda: "dummy")
    monkeypatch.setattr(
        ingest_nationwide, "list_complexes",
        lambda **kw: [_ref("A1", "2611010100", "부산광역시", "중구")],
    )
    out = tmp_path / "codes.csv"
    rc = main(["--discover", "--codes-file", str(out)])
    assert rc == 0
    assert load_codes(out) == [("26110", "부산광역시", "중구")]


def test_main_resume_skips_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 코드표 2개 중 1개(11110)는 이미 적재 → resume이 1개만 run_batch에 넘긴다.
    codes_csv = tmp_path / "codes.csv"
    save_codes(codes_csv, [("11110", "서울", "종로구"), ("41135", "경기", "분당구")])
    db = tmp_path / "t.db"
    conn = get_connection(str(db))
    init_db(conn)
    conn.execute("INSERT INTO complex (complex_id, bjd_code) VALUES ('C1','1111011800')")
    conn.commit()
    conn.close()

    passed: list[str] = []

    def fake_batch(conn, regions, **kw):  # type: ignore[no-untyped-def]
        passed.extend(c for c, _ in regions)
        return []

    monkeypatch.setattr(ingest_nationwide, "run_batch", fake_batch)
    monkeypatch.setattr(ingest_nationwide, "get_api_key", lambda: "dummy")
    rc = main(["--resume", "--stages", "complex", "--months", "202604",
               "--codes-file", str(codes_csv), "--db", str(db)])
    assert rc == 0
    assert passed == ["41135"]  # 11110 skip(이미 적재), 41135만


def test_resume_works_for_transaction_stage_without_complex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # C20: --resume가 더 이상 complex 전용 아님 — txn/rent 재개(월 단위 skip)도 지원(가드 제거).
    codes_csv = tmp_path / "codes.csv"
    save_codes(codes_csv, [("41135", "경기", "분당구")])
    monkeypatch.setattr(ingest_nationwide, "get_api_key", lambda: "dummy")
    captured: dict = {}

    def fake_batch(conn, regions, **kw):  # type: ignore[no-untyped-def]
        captured["regions"] = [c for c, _ in regions]
        captured["resume"] = kw.get("resume")
        return []

    monkeypatch.setattr(ingest_nationwide, "run_batch", fake_batch)
    rc = main(["--resume", "--stages", "transaction", "--months", "202604",
               "--codes-file", str(codes_csv), "--db", ":memory:"])
    assert rc == 0  # 에러 없음(가드 제거)
    assert captured["regions"] == ["41135"]  # 미적재 → pending → run_batch에 전달
    assert captured["resume"] is True  # run_batch로 resume 전달


def test_pending_regions_filters_by_completed_months(tmp_path: Path) -> None:
    # 재개: txn 월이 전부 적재된 시군구는 pending에서 빠지고, 일부만 된 곳은 남는다.
    from ingest_nationwide import pending_regions

    from app.store.progress_repo import record_month

    conn = get_connection(":memory:")
    init_db(conn)
    codes = [("11110", "서울", "종로구"), ("41135", "경기", "분당구")]
    months = ["202603", "202604"]
    # 11110: 두 달 다 적재(완료) / 41135: 한 달만(미완)
    record_month(conn, "transaction", "11110", "202603", 5)
    record_month(conn, "transaction", "11110", "202604", 7)
    record_month(conn, "transaction", "41135", "202603", 3)
    pending = pending_regions(conn, codes, ["transaction"], months)
    assert [c for c, _, _ in pending] == ["41135"]  # 11110 완료 → 제외
