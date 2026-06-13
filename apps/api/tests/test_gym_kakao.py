"""gym-kakao — Kakao 헬스장 근접 신호: 반경 정밀(건물내 yes·먼곳 no)·병합(kakao>저신뢰 web→✓)·
missing=keep·enrich가 gym 사실만 write(좌표/canonical 무접촉·지문/counts 불변)·pipeline_state."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from app.enrich.store import EnrichmentFact, read_facts, write_facts
from app.search.gym import ATTRIBUTE as GYM_ATTRIBUTE
from app.search.gym import synthesize_gym
from app.search.gym_kakao import GYM_RADIUS_M, gym_fact_value, nearest_gym
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 13, tzinfo=UTC)


class _FakeKakao:
    """keyword_docs를 흉내내는 가짜 Kakao 클라(거리 필터는 nearest_gym이·여긴 unfiltered 반환)."""

    def __init__(self, docs: dict[str, list[dict]]) -> None:
        self.docs = docs

    def keyword_docs(self, keyword: str, x: float, y: float, *, radius: int, size: int = 5):  # type: ignore[no-untyped-def]
        return self.docs.get(keyword, [])


def _doc(name: str, dist: int, url: str = "http://place.map.kakao.com/1") -> dict:
    return {"place_name": name, "distance": str(dist), "place_url": url}


def _db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


# ── 반경 정밀: 건물내(≤50m) yes · 먼곳(>50m) 무매치 ──
def test_nearest_gym_in_building() -> None:
    client = _FakeKakao({"헬스장": [_doc("바디러너스", 2), _doc("위스테이", 99)], "피트니스": []})
    m = nearest_gym(client, 37.5, 127.1)  # type: ignore[arg-type]
    assert m is not None and m["place_name"] == "바디러너스" and m["distance_m"] == 2


def test_nearest_gym_far_is_none() -> None:
    # 근육발전소 135m·이웃 187m = 단지 amenity 아님 → None(precision·오탐 0).
    client = _FakeKakao(
        {"헬스장": [_doc("근육발전소PT", 135), _doc("나무트레이닝", 187)], "피트니스": []})
    assert nearest_gym(client, 37.5, 127.1) is None  # type: ignore[arg-type]
    assert GYM_RADIUS_M == 50  # 프로파일 결정 반경


def test_nearest_gym_picks_closest_across_keywords() -> None:
    client = _FakeKakao({"헬스장": [_doc("A", 48)], "피트니스": [_doc("스포짐", 12)]})
    m = nearest_gym(client, 37.5, 127.1)  # type: ignore[arg-type]
    assert m is not None and m["place_name"] == "스포짐" and m["distance_m"] == 12


def test_gym_fact_value_shape() -> None:
    v = json.loads(gym_fact_value({"place_name": "바디러너스", "distance_m": 2}))
    assert v["has_gym"] == "yes" and "바디러너스" in v["evidence"] and "Kakao" in v["evidence"]


# ── 병합: Kakao(고신뢰) > 저신뢰 web → synthesize yes(✓ 게이트) ──
def test_kakao_signal_beats_low_conf_web() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type) VALUES ('C','C','officetel')")
    conn.commit()
    # 저신뢰 web "no" 먼저(C80 advisory 원인) + Kakao "yes" 고신뢰.
    write_facts(conn, "C", GYM_ATTRIBUTE, [EnrichmentFact(
        value=json.dumps({"has_gym": "no", "evidence": "단지 시설 없음"}, ensure_ascii=False),
        confidence=0.31, source_type="agent_research", source_url="urn:a:1")],
        ttl=timedelta(days=30), now=NOW)
    write_facts(conn, "C", GYM_ATTRIBUTE, [EnrichmentFact(
        value=gym_fact_value({"place_name": "바디러너스", "distance_m": 2}),
        confidence=0.88, source_type="kakao_local", source_url="http://place.map.kakao.com/1")],
        ttl=timedelta(days=365), now=NOW)
    summary = synthesize_gym(read_facts(conn, "C", GYM_ATTRIBUTE, now=NOW))
    assert summary.has_gym == "yes" and summary.confidence == 0.88  # Kakao primary
    assert len(summary.sources) == 2  # provenance 다출처 보존
    assert any(s.source_type == "kakao_local" for s in summary.sources)


# ── missing=keep: 무매치 → write 0(단정 "없음" 아님) ──
def test_missing_keep_no_match_no_write() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type) VALUES ('C','C','officetel')")
    conn.commit()
    # nearest_gym None → 러너가 write_facts 안 함. gym 사실 0 → synthesize none(advisory).
    assert read_facts(conn, "C", GYM_ATTRIBUTE, now=NOW) == []
    assert synthesize_gym([]).has_gym == "none"  # 단정 "no" 아님


# ── ★ enrich가 gym 사실만 write — canonical/좌표 무접촉 ──
def test_write_does_not_touch_canonical() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng) "
        "VALUES ('C','C','officetel', 37.5, 127.0)")
    conn.execute('INSERT INTO "transaction" (txn_id, complex_id, deal_date) VALUES (?,?,?)',
                 ("t1", "C", "2026-05-01"))
    conn.commit()
    before = (
        conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0],
        conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0],
        conn.execute("SELECT lat||','||lng FROM complex WHERE complex_id='C'").fetchone()[0],
    )
    write_facts(conn, "C", GYM_ATTRIBUTE, [EnrichmentFact(
        value=gym_fact_value({"place_name": "바디러너스", "distance_m": 2}),
        confidence=0.88, source_type="kakao_local", source_url="u")],
        ttl=timedelta(days=365), now=NOW)
    after = (
        conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0],
        conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0],
        conn.execute("SELECT lat||','||lng FROM complex WHERE complex_id='C'").fetchone()[0],
    )
    assert before == after  # complex/txn 카운트·좌표 불변(gym 사실만 write)


# ── pipeline_state 등록 ──
def test_pipeline_state_registers_gym_kakao() -> None:
    from app.store.pipeline_state import bootstrap_pipeline_state, read_pipeline_state
    conn = _db()
    bootstrap_pipeline_state(conn, now=NOW)
    assert "gym_kakao" in {r["name"] for r in read_pipeline_state(conn)}
