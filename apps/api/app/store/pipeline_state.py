"""pipeline-state: 적재기 자기서술 원장 — META 기록(canonical 무접촉·read-only 진행 유도).

각 파이프라인의 (출생·목표·진행·마지막 실행·정상 여부)를 pipeline_state 1행으로 UPSERT한다.
"한 쿼리로 자기서술" → git 고고학·메모리 불요. 이번 오진(정상 신규적재를 wipe로)의 구조적 해소:
- **introduced_at(출생)을 provenance(MIN fetched_at 등)서 유도·write-once** → birth-vs-wipe 구분.
- **metric**으로 current/target가 rows인지 distinct complex인지 자가문서 → rows-vs-distinct 해소.

불변식: **유일 write 대상은 pipeline_state**. canonical(complex/txn/rent/poi/…)은 COUNT/MIN으로만
**읽는다**(좌표/canonical 컬럼 write 0) → 지문 163df7cd7e6a3cc2·counts 불변. UPSERT는 멱등(단일 행).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

_FILL_COMPLETE_PCT = 0.99  # bounded 파이프라인이 이 비율 이상이면 'complete'(잔여=구조적 미달분)


def _ts(value: object) -> str | None:
    """저장용 타임스탬프 문자열. datetime이면 ISO, 문자열이면 그대로, None이면 None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_ts(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _estimate_eta(
    introduced_at: object, target: int | None, current: int | None,
    status: str, now: datetime,
) -> str | None:
    """filling 상태서 평균 적재율(current / 출생후 경과일)로 풀커버 ETA. 산출 불가면 None."""
    if status != "filling" or not target or not current or current <= 0 or current >= target:
        return None
    intro = _parse_ts(introduced_at)
    if intro is None:
        return None
    if intro.tzinfo is None:
        intro = intro.replace(tzinfo=UTC)
    days = (now - intro).total_seconds() / 86400.0
    if days < 0.5:  # 출생 직후엔 rate 신뢰 불가
        return None
    rate = current / days  # 일별 평균
    if rate <= 0:
        return None
    return (now + timedelta(days=(target - current) / rate)).isoformat()


def record_pipeline_state(
    conn: sqlite3.Connection,
    name: str,
    *,
    target: int | None,
    current: int | None,
    added: int | None,
    status: str,
    metric: str,
    introduced_at_default: object = None,
    now: datetime | None = None,
) -> None:
    """파이프라인 상태 UPSERT(멱등·단일 행). introduced_at은 **write-once**(기존값 보존).

    canonical은 호출부가 COUNT/MIN으로 읽어 넘긴다 — 이 함수는 pipeline_state만 write.
    """
    now = now or datetime.now(UTC)
    row = conn.execute(
        "SELECT introduced_at FROM pipeline_state WHERE name = ?", (name,)
    ).fetchone()
    existing_intro = row[0] if row else None
    introduced_at = existing_intro if existing_intro else _ts(introduced_at_default)
    eta = _estimate_eta(introduced_at, target, current, status, now)
    conn.execute(
        "INSERT INTO pipeline_state "
        "(name, introduced_at, target_count, current_count, metric, last_run_at, "
        " last_run_added, status, expected_complete_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET "
        # introduced_at write-once — 기존값 있으면 절대 덮지 않음(출생일 보존·드리프트 0)
        "  introduced_at = COALESCE(pipeline_state.introduced_at, excluded.introduced_at), "
        "  target_count = excluded.target_count, "
        "  current_count = excluded.current_count, "
        "  metric = excluded.metric, "
        "  last_run_at = excluded.last_run_at, "
        "  last_run_added = excluded.last_run_added, "
        "  status = excluded.status, "
        "  expected_complete_at = excluded.expected_complete_at, "
        "  updated_at = excluded.updated_at",
        (name, introduced_at, target, current, metric, _ts(now),
         added, status, eta, _ts(now)),
    )
    conn.commit()


def _scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def _bounded_status(current: int, target: int | None) -> str:
    if not target:
        return "idle"
    return "complete" if current >= target * _FILL_COMPLETE_PCT else "filling"


# 분모 — 지오코딩된 단지(좌표 보유: POI/학교 적재 가능 모수)·전체 단지.
_GEOCODED = "SELECT COUNT(*) FROM complex WHERE lat IS NOT NULL AND lng IS NOT NULL"
_TOTAL = "SELECT COUNT(*) FROM complex"
_ADDR = "SELECT COUNT(*) FROM complex WHERE COALESCE(NULLIF(road_addr,''),legal_addr) IS NOT NULL"


def bootstrap_pipeline_state(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    """모든 파이프라인 상태를 canonical **provenance서 유도**해 UPSERT(read-only COUNT/MIN).

    introduced_at은 각 데이터의 MIN(fetched_at 등)서 정확히 유도(poi≈최초 적재일=출생). 백필처럼
    행 타임스탬프 없는 건 default=now(첫 기록 1회). added=직전 기록 대비 증분. canonical write 0.
    """
    now = now or datetime.now(UTC)
    geocoded = int(_scalar(conn, _GEOCODED))
    total = int(_scalar(conn, _TOTAL))
    addr = int(_scalar(conn, _ADDR))
    prev = {
        r[0]: r[1]
        for r in conn.execute("SELECT name, current_count FROM pipeline_state").fetchall()
    }

    def rec(name: str, *, target: int | None, current: int, status: str, metric: str,
            intro_sql: str | None) -> None:
        intro = _scalar(conn, intro_sql) if intro_sql else None
        before = prev.get(name)
        added = (current - int(before)) if before is not None else 0
        record_pipeline_state(
            conn, name, target=target, current=current, added=added, status=status,
            metric=metric, introduced_at_default=(intro if intro else now), now=now,
        )

    # ── 거래 적재(무경계 — 목표 없음·idle) ──
    txn = int(_scalar(conn, 'SELECT COUNT(*) FROM "transaction"'))
    rec("ingest_txn", target=None, current=txn, status="idle", metric="rows (매매 실거래)",
        intro_sql='SELECT MIN(updated_at) FROM "transaction"')
    rent = int(_scalar(conn, "SELECT COUNT(*) FROM rent_transaction"))
    rec("ingest_rent", target=None, current=rent, status="idle", metric="rows (전월세 실거래)",
        intro_sql="SELECT MIN(updated_at) FROM rent_transaction")

    # ── POI 근접(지오코딩 모수·distinct complex) — 이번 혼동의 당사자(출생기록 핵심) ──
    poi = int(_scalar(conn, "SELECT COUNT(DISTINCT complex_id) FROM poi_proximity"))
    rec("poi_proximity", target=geocoded, current=poi, status=_bounded_status(poi, geocoded),
        metric="distinct complex_id with POI",
        intro_sql="SELECT MIN(fetched_at) FROM poi_proximity")

    # ── 건축물대장 enrich(전체 모수·complex 컬럼) ──
    ledger = int(_scalar(
        conn, "SELECT COUNT(*) FROM complex WHERE ledger_pk IS NOT NULL AND ledger_pk != ''"))
    rec("ledger_enrich", target=total, current=ledger, status=_bounded_status(ledger, total),
        metric="complex with building-ledger",
        intro_sql="SELECT MIN(ledger_fetched_at) FROM complex")

    # ── 학교(거리·배정) ──
    sdist = int(_scalar(conn, "SELECT COUNT(DISTINCT complex_id) FROM school_proximity"))
    rec("school_distance", target=geocoded, current=sdist,
        status=_bounded_status(sdist, geocoded),
        metric="distinct complex_id with school distance(초/중/고)",
        intro_sql="SELECT MIN(fetched_at) FROM school_proximity")
    sasg = int(_scalar(conn, "SELECT COUNT(DISTINCT complex_id) FROM school_assignment"))
    rec("school_assignment", target=geocoded, current=sasg,
        status=_bounded_status(sasg, geocoded),
        metric="distinct complex_id with assignment zone(sentinel 포함)",
        intro_sql="SELECT MIN(fetched_at) FROM school_assignment")

    # ── 백필(주소 모수·행 타임스탬프 없음→intro=now 1회) ──
    sgg = int(_scalar(
        conn, "SELECT COUNT(*) FROM complex WHERE sigungu IS NOT NULL AND sigungu != ''"))
    rec("sigungu_backfill", target=addr, current=sgg, status=_bounded_status(sgg, addr),
        metric="complex with sigungu(주소 파싱→컬럼)", intro_sql=None)
    dong = int(_scalar(conn, "SELECT COUNT(*) FROM complex WHERE dong IS NOT NULL AND dong!=''"))
    rec("dong_backfill", target=addr, current=dong, status=_bounded_status(dong, addr),
        metric="complex with dong(extract_dong)", intro_sql=None)

    # ── 온디맨드(후보-한정 lazy·목표 없음) ──
    gp = int(_scalar(
        conn,
        "SELECT COUNT(DISTINCT complex_id) FROM enrichment "
        "WHERE attribute IN ('gym','pet','pet_allowed')"))
    rec("gym_pet", target=None, current=gp, status="on_demand",
        metric="distinct complex_id (lazy/detail-triggered·후보만)",
        intro_sql="SELECT MIN(fetched_at) FROM enrichment "
                  "WHERE attribute IN ('gym','pet','pet_allowed')")
    e3 = int(_scalar(conn, "SELECT COUNT(*) FROM review_chunk"))
    rec("e3_rag_corpus", target=None, current=e3, status="on_demand",
        metric="review_chunk rows (on-demand RAG 코퍼스)",
        intro_sql="SELECT MIN(fetched_at) FROM review_chunk")


def bootstrap_pipeline_state_safe(conn: sqlite3.Connection) -> None:
    """run-end/init_db용 — 실패해도 호출부(canonical 작업)를 깨지 않음(META 기록은 비치명)."""
    try:
        bootstrap_pipeline_state(conn)
    except Exception:  # noqa: BLE001 — 메타 기록 실패는 무시(파이프라인 본업 보호)
        pass


def read_pipeline_state(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """pipeline_state 전 행 → dict 리스트(엔드포인트·스크립트 read-only)."""
    cur = conn.execute(
        "SELECT name, introduced_at, target_count, current_count, metric, last_run_at, "
        "last_run_added, status, expected_complete_at, updated_at "
        "FROM pipeline_state ORDER BY name"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
