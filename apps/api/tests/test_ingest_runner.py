"""적재 오케스트레이션 — 스테이지 mock으로 순서·부분선택·요약·월파싱·경고(키리스)."""

from __future__ import annotations

import sqlite3

import pytest

import app.ingest as ingest_mod
from app.ingest import (
    API_KEY_STAGES,
    DEFAULT_STAGES,
    STAGE_ORDER,
    main,
    parse_months,
    run_ingest,
)
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

    def fake_rent(conn, region, months, **kw):  # type: ignore[no-untyped-def]
        calls.append("rent")
        return 9

    def fake_rent_bjd(conn, **kw):  # type: ignore[no-untyped-def]
        calls.append("rent_bjd")
        return {"filled": 7, "pending": 9}

    def fake_join(conn, **kw):  # type: ignore[no-untyped-def]
        # 매매 join(table 미지정=transaction) vs 전월세 join(table=rent_transaction) 구분.
        table = kw.get("table", "transaction")
        if table == "transaction":
            calls.append("join")
            return {"matched": 50, "unmatched": 63, "total": 113}
        calls.append("rent_join")
        return {"matched": 4, "unmatched": 5, "total": 9}

    def fake_geo(conn, geocode, **kw):  # type: ignore[no-untyped-def]
        calls.append("geocode")
        return {"matched": 4, "unmatched": 0, "total": 4}

    def fake_nonapt(conn, region, month, **kw):  # type: ignore[no-untyped-def]
        # P5-1b: 비-아파트 전월세 stage(kind별 호출 — rowhouse·officetel). 거래에서 건물 도출.
        calls.append("nonapt_rent")
        return 6

    def fake_nonapt_sale(conn, region, month, **kw):  # type: ignore[no-untyped-def]
        # P5-1b-3: 비-아파트 매매 stage(kind별 — rowhouse·officetel). 같은 건물에 매매축 합류.
        calls.append("nonapt_sale")
        return 4

    monkeypatch.setattr(ingest_mod, "ingest_complexes", fake_complexes)
    monkeypatch.setattr(ingest_mod, "ingest_months", fake_months)
    monkeypatch.setattr(ingest_mod, "ingest_rent_months", fake_rent)
    monkeypatch.setattr(ingest_mod, "backfill_rent_bjd", fake_rent_bjd)
    monkeypatch.setattr(ingest_mod, "backfill_matches", fake_join)
    monkeypatch.setattr(ingest_mod, "backfill_coords", fake_geo)
    monkeypatch.setattr(ingest_mod, "ingest_nonapt_rent_month", fake_nonapt)
    monkeypatch.setattr(ingest_mod, "ingest_nonapt_sale_month", fake_nonapt_sale)
    return calls


def test_runs_all_stages_in_canonical_order(conn, stage_calls: list[str]) -> None:
    summary = run_ingest(
        conn, region="11680", months=["202504"], stages=list(STAGE_ORDER),
        api_key="d", kakao_key="d", log=lambda _m: None,
    )
    # rent: 적재→bjd룩업→자체조인. nonapt_rent/sale: 연립·오피스텔 2 kind씩. 매매 "join" 별도.
    assert stage_calls == [
        "complex", "transaction", "rent", "rent_bjd", "rent_join",
        "nonapt_rent", "nonapt_rent", "nonapt_sale", "nonapt_sale", "join", "geocode",
    ]
    assert summary.nonapt_sale_transactions == 8  # 4 × 2 kind
    # 매매 필드 불변(회귀 0)
    assert summary.complexes == 5
    assert summary.transactions == 113
    assert summary.matched == 50 and summary.join_total == 113
    assert summary.geocoded == 4 and summary.geocode_total == 4
    # 전월세 필드 추가
    assert summary.rent_transactions == 9
    assert summary.rent_matched == 4 and summary.rent_join_total == 9
    # P5-1b 비-아파트 전월세 (2 kind × 6)
    assert summary.nonapt_rent_transactions == 12


def test_all_default_excludes_rent_opt_in(
    conn, stage_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # main의 "all"은 DEFAULT_STAGES(rent 미포함) — 기존 ops 동작 불변(매매 회귀 0).
    # 키리스: main이 complex/transaction에 get_api_key()를 부르므로 키 게터 우회(.env 없는 CI 가정).
    monkeypatch.setattr(ingest_mod, "get_api_key", lambda: "d")
    monkeypatch.setattr(ingest_mod, "get_kakao_key", lambda: "d")
    assert "rent" not in DEFAULT_STAGES
    rc = main(["--region", "11680", "--stages", "all", "--db", ":memory:",
               "--months", "202504"])
    assert rc == 0
    assert "rent" not in stage_calls  # all에 rent 없음
    assert stage_calls == ["complex", "transaction", "join", "geocode"]


def test_rent_stage_ingests_and_joins(conn, stage_calls: list[str]) -> None:
    # rent 명시 → 적재 + 자체 조인. 매매 stage는 안 돈다.
    summary = run_ingest(
        conn, region="11680", months=["202504"], stages=["rent"],
        api_key="d", log=lambda _m: None,
    )
    assert stage_calls == ["rent", "rent_bjd", "rent_join"]
    assert summary.rent_transactions == 9
    assert summary.rent_matched == 4
    assert summary.transactions == 0 and summary.matched == 0  # 매매 미실행


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


def test_nonapt_rent_in_api_key_stages() -> None:
    # 회귀 마커(P5-1b-run): #48이 nonapt_rent를 키게이팅에서 누락 → api_key=None → assert 크래시.
    assert "nonapt_rent" in API_KEY_STAGES


@pytest.mark.parametrize("stage", sorted(API_KEY_STAGES))
def test_main_supplies_api_key_for_every_key_stage(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """클래스 갭 차단: main은 data.go.kr 키 필요한 *모든* stage(nonapt_rent 포함)에 키 공급.

    #48은 run_ingest를 키와 함께 직접 테스트했을 뿐 main()의 stage→키 게이팅을 안 봐서 누락이 샜다.
    여기서 API_KEY_STAGES 전수를 돌려, 어느 키-필요 stage든 main이 None 아닌 키를 넘기는지 단언한다.
    """
    monkeypatch.setattr(ingest_mod, "get_api_key", lambda: "SENTINEL")
    monkeypatch.setattr(ingest_mod, "get_kakao_key", lambda: "K")
    captured: dict[str, object] = {}

    def fake_run_ingest(conn, **kw):  # type: ignore[no-untyped-def]
        captured["api_key"] = kw.get("api_key")
        return ingest_mod.IngestSummary()

    monkeypatch.setattr(ingest_mod, "run_ingest", fake_run_ingest)
    rc = main(["--region", "11680", "--stages", stage, "--db", ":memory:", "--months", "202504"])
    assert rc == 0
    assert captured["api_key"] == "SENTINEL"  # None이면 run_ingest assert로 런타임 크래시(회귀)


def test_main_rejects_unknown_stage() -> None:
    with pytest.raises(SystemExit):
        main(["--region", "11680", "--stages", "bogus", "--db", ":memory:"])


# ───────── C20 resume (멀티데이 재개) ─────────


def test_resume_transaction_skips_recorded_months_and_records_new(
    conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.store.db import init_db
    from app.store.progress_repo import completed_months, record_month

    init_db(conn)
    record_month(conn, "transaction", "11680", "202401", 50)  # 이미 적재된 월
    fetched: list[str] = []

    def fake_month(c, region, month, **kw):  # type: ignore[no-untyped-def]
        fetched.append(month)
        return 7

    monkeypatch.setattr(ingest_mod, "ingest_month", fake_month)
    summary = run_ingest(
        conn, region="11680", months=["202401", "202402", "202403"],
        stages=["transaction"], api_key="d", resume=True, log=lambda _m: None,
    )
    assert fetched == ["202402", "202403"]  # 기적재 202401 skip
    assert summary.transactions == 14  # 2개월 × 7
    # 새로 적재한 월도 원장 기록 → 다음 재개 시 skip
    assert completed_months(conn, "transaction", "11680") == {"202401", "202402", "202403"}


def test_resume_rent_skips_recorded_months(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.store.db import init_db
    from app.store.progress_repo import completed_months, record_month

    init_db(conn)
    record_month(conn, "rent", "11680", "202402", 9)
    fetched: list[str] = []
    monkeypatch.setattr(
        ingest_mod, "ingest_rent_month",
        lambda c, region, month, **kw: fetched.append(month) or 3,  # type: ignore[no-untyped-def]
    )
    run_ingest(
        conn, region="11680", months=["202401", "202402"], stages=["rent"],
        api_key="d", resume=True, log=lambda _m: None,
    )
    assert fetched == ["202401"]  # 202402 skip
    assert completed_months(conn, "rent", "11680") == {"202401", "202402"}


def test_resume_rent_transient_empty_not_recorded(
    conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 어드버서리얼(fix/rent-empty-ledger): 버스트 빈응답이 PublicDataError로 raise되면
    # 그 월은 ledger에 기록되면 안 됨 → pending 유지 → 재개 재시도. "완료 0건" 영구박힘 방지.
    from app.sources.errors import PublicDataError
    from app.store.db import init_db
    from app.store.progress_repo import completed_months

    init_db(conn)
    fetched: list[str] = []

    def flaky_rent(c, region, month, **kw):  # type: ignore[no-untyped-def]
        fetched.append(month)
        if month == "202402":
            raise PublicDataError(None, "빈 응답 미확정 — transient 의심")
        return 5

    monkeypatch.setattr(ingest_mod, "ingest_rent_month", flaky_rent)
    with pytest.raises(PublicDataError):
        run_ingest(
            conn, region="11680", months=["202401", "202402", "202403"],
            stages=["rent"], api_key="d", resume=True, log=lambda _m: None,
        )
    # 성공한 202401만 기록 · transient 202402와 이후 202403은 미기록(pending) → 재개 시 재시도
    assert completed_months(conn, "rent", "11680") == {"202401"}


def test_resume_records_each_month_so_crash_loses_at_most_one(
    conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # C22 체크포인트: region×월 단위 기록 → 3번째 월 크래시여도 앞 2개월은 원장에 남음(손실 ≤1월).
    from app.store.db import init_db
    from app.store.progress_repo import completed_months

    init_db(conn)

    def flaky_month(c, region, month, **kw):  # type: ignore[no-untyped-def]
        if month == "202403":
            raise ConnectionError("네트워크 끊김")  # 하드 실패(재시도 소진 가정)
        return 10

    monkeypatch.setattr(ingest_mod, "ingest_month", flaky_month)
    with pytest.raises(ConnectionError):
        run_ingest(
            conn, region="11680", months=["202401", "202402", "202403"],
            stages=["transaction"], api_key="d", resume=True, log=lambda _m: None,
        )
    # 크래시 전 완료한 월만 기록 → 재개 시 그 2개월 skip, 202403만 재fetch(손실 ≤1월)
    assert completed_months(conn, "transaction", "11680") == {"202401", "202402"}


def test_resume_complex_skips_loaded_region(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.store.db import init_db

    init_db(conn)
    conn.execute("INSERT INTO complex (complex_id, bjd_code) VALUES ('C1', '1168010100')")
    conn.commit()
    called: list[str] = []
    monkeypatch.setattr(
        ingest_mod, "ingest_complexes",
        lambda c, **kw: called.append("x") or 5,  # type: ignore[no-untyped-def]
    )
    run_ingest(
        conn, region="11680", months=[], stages=["complex"], api_key="d",
        resume=True, log=lambda _m: None,
    )
    assert called == []  # region에 complex 있으면 skip


def test_resume_complex_runs_when_region_empty(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.store.db import init_db

    init_db(conn)  # complex 없음
    called: list[str] = []
    monkeypatch.setattr(
        ingest_mod, "ingest_complexes",
        lambda c, **kw: called.append("x") or 5,  # type: ignore[no-untyped-def]
    )
    run_ingest(
        conn, region="11680", months=[], stages=["complex"], api_key="d",
        resume=True, log=lambda _m: None,
    )
    assert called == ["x"]  # 미적재 region은 정상 적재
