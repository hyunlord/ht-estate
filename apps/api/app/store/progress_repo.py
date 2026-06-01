"""적재 진행 원장 (C20) — 멀티데이 재개용 (stage, region, month) 완료 추적.

전국 적재는 data.go.kr 일일캡(개발계정 1,000건/day)으로 수일에 걸친다. 매 run이 처음부터
재fetch하면 캡만 태우므로, 완료한 region×월을 기록해 재개 시 skip한다. 0행 월도 기록 →
빈 월(거래 없음)과 미적재를 구분(데이터 추론은 둘을 못 가른다). 코어 적재 테이블 불변(회귀 0).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


def completed_months(conn: sqlite3.Connection, stage: str, region: str) -> set[str]:
    """(stage, region)에 대해 이미 적재 완료한 월(YYYYMM) 집합. 재개 skip 판정용."""
    rows = conn.execute(
        "SELECT month FROM ingest_progress WHERE stage = ? AND region = ?",
        (stage, region),
    ).fetchall()
    return {r[0] for r in rows}


def record_month(
    conn: sqlite3.Connection,
    stage: str,
    region: str,
    month: str,
    rows: int,
    *,
    fetched_at: datetime | None = None,
) -> None:
    """(stage, region, month) 완료를 원장에 기록(멱등 upsert). rows=0도 기록(빈 월 재fetch 방지)."""
    when = (fetched_at or datetime.now(UTC)).isoformat()
    conn.execute(
        "INSERT INTO ingest_progress (stage, region, month, rows, fetched_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(stage, region, month) DO UPDATE SET rows = excluded.rows, "
        "fetched_at = excluded.fetched_at",
        (stage, region, month, rows, when),
    )
    conn.commit()


def region_has_complex(conn: sqlite3.Connection, region: str) -> bool:
    """시군구(앞5)에 단지가 이미 적재됐는지 — complex 스테이지 재개 skip 판정용."""
    row = conn.execute(
        "SELECT 1 FROM complex WHERE substr(bjd_code, 1, 5) = ? LIMIT 1",
        (region,),
    ).fetchone()
    return row is not None
