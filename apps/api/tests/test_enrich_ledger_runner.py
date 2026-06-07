"""enrich 러너 run_enrich — HTTP 429(쿼터/레이트리밋) 우아한 중단. (enrich-1c fix)

enrich-1c 실측: 건축HUB 일일쿼터가 **HTTP 429**로 와서 `_http` 8회 재시도 소진 후 전파 →
러너가 크래시(exit 1). 픽스: PublicDataError와 동일하게 httpx.HTTPError를 잡아 우아하게 중단
(완료분 커밋·레저로 재개). 키리스(enrich_one 몽키패치, 라이브 호출 0).
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import enrich_building_ledger as runner  # noqa: E402


@contextmanager
def _noop_lock() -> Iterator[bool]:
    yield True  # 항상 획득(테스트는 락 경합 무관)


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, property_type) VALUES (?, ?, 'rowhouse')",
        [("ro:11680:대치동:1:가", "가"), ("ro:11680:대치동:2:나", "나"),
         ("ro:11680:대치동:3:다", "다")],
    )
    conn.commit()
    return conn


def test_http_429_stops_gracefully(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    # enrich_one: 1건 성공 후 2건째에 HTTP 429 → 러너가 크래시 없이 중단·완료분 보존
    calls = {"n": 0}

    def fake_enrich_one(conn, complex_id, name, *, api_key, client=None):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            return "enriched"
        req = httpx.Request("GET", "https://apis.data.go.kr/x")
        raise httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, request=req))

    monkeypatch.setattr(runner, "enrich_one", fake_enrich_one)

    # 크래시(예외 전파) 없이 Counter 반환해야 함
    counts = runner.run_enrich(
        db, api_key="x", lock=_noop_lock, throttle=None, batch_size=10, limit=0,
        inter_batch_sleep=0, log=None,
    )
    assert counts["enriched"] == 1  # 429 직전 1건만
    # 완료분(1건)은 레저에 커밋 — 재개 시 멱등 이어받기
    done = db.execute(
        "SELECT COUNT(*) FROM ingest_progress WHERE stage = ?", (runner.ENRICH_STAGE,)
    ).fetchone()[0]
    assert done == 1


def test_transport_error_also_graceful(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 네트워크 전송오류(재시도 소진)도 httpx.HTTPError → 우아한 중단(크래시 아님)
    def fake_enrich_one(conn, complex_id, name, *, api_key, client=None):  # type: ignore[no-untyped-def]
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(runner, "enrich_one", fake_enrich_one)
    counts = runner.run_enrich(
        db, api_key="x", lock=_noop_lock, throttle=None, batch_size=10, limit=0,
        inter_batch_sleep=0, log=None,
    )
    assert sum(counts.values()) == 0  # 첫 건부터 실패 → 0건, 크래시 없음
