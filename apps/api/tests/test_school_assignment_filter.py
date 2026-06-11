"""특정 초등 배정(통학구역) 필터 (school-assignment) — 학교명 매칭·positive-match repo·NL. 키리스.

★ positive-selection(missing≠keep): 다른학교·무배정(sentinel)=제외(거리 필터의 미적재=keep과 반대).
school_assignment READ만(필터는 READ·write 0) → 지문/counts 불변. app/match fuzzy 재사용.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.match.fuzzy import school_similarity
from app.match.normalize import normalize_school_name
from app.school.assignment import resolve_assigned_schools
from app.search.nl_parse import parse_query
from app.search.repo import search_complexes
from app.search.spec import HardFilterSpec
from app.store.db import get_connection, init_db


# ── 학교명 매칭 (app/match 재사용) ──
def test_normalize_school_name_suffix() -> None:
    # '초등학교'/'초' 접미 통일 → 같은 base, 지역 접두 보존.
    assert normalize_school_name("서울잠원초등학교") == normalize_school_name("서울잠원초")
    assert normalize_school_name("잠원초등학교") == normalize_school_name("잠원초")


def test_school_similarity_variants_match() -> None:
    stored = "서울잠원초등학교"
    for q in ("서울잠원초", "잠원초", "잠원초등학교", "서울잠원초등학교", "잠원"):
        assert school_similarity(q, stored) >= 0.85, q


def test_school_similarity_rejects_other_and_region() -> None:
    # 다른 학교·지역접두 다름 → 비매치(positive-match 정밀·오염 방지).
    assert school_similarity("잠원초", "서울반원초등학교") < 0.85
    assert school_similarity("서울잠원초", "부산잠원초등학교") < 0.85  # 지역 정체성 보존


# ── 시드 DB ──
def _seed() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    for cid, name in [("C1", "잠원단지"), ("C2", "반원단지"), ("C3", "공동단지"),
                      ("C4", "무배정단지"), ("C5", "미계산단지")]:
        conn.execute(
            "INSERT INTO complex (complex_id, name, property_type) VALUES (?,?, 'apartment')",
            (cid, name),
        )

    def asn(cid, zid, sid, sname, shared):  # type: ignore[no-untyped-def]
        conn.execute(
            "INSERT INTO school_assignment (complex_id, zone_id, zone_class, school_id, "
            "school_name, is_shared, source, source_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, zid, "1" if shared else "0", sid, sname, 1 if shared else 0, "s", "u", "t"),
        )

    asn("C1", "Z1", "S1", "서울잠원초등학교", False)         # 잠원초 배정
    asn("C2", "Z2", "S2", "서울반원초등학교", False)         # 다른 학교 → 제외 기대
    asn("C3", "Z3", "S1", "서울잠원초등학교", True)          # 공동: 잠원 + 반원
    asn("C3", "Z3", "S2", "서울반원초등학교", True)
    asn("C4", "", "", None, False)                          # sentinel(무배정) → 제외 기대
    # C5: school_assignment 행 0 → 미계산 → 제외 기대
    conn.commit()
    return conn


@pytest.fixture
def db() -> sqlite3.Connection:
    return _seed()


# ── resolve ──
def test_resolve_assigned_schools(db: sqlite3.Connection) -> None:
    assert resolve_assigned_schools(db, "잠원초") == ["서울잠원초등학교"]
    assert resolve_assigned_schools(db, "서울반원초등학교") == ["서울반원초등학교"]
    assert resolve_assigned_schools(db, "없는학교") == []  # 매칭 0
    assert resolve_assigned_schools(db, "") == []


# ── repo positive-match ──
def _ids(cands) -> set[str]:  # type: ignore[no-untyped-def]
    return {c.complex_id for c in cands}


def test_filter_positive_match_excludes_other_and_missing(db: sqlite3.Connection) -> None:
    cands = search_complexes(db, HardFilterSpec(assigned_school="잠원초", limit=50))
    ids = _ids(cands)
    assert "C1" in ids          # 잠원초 배정 → 포함
    assert "C3" in ids          # 공동통학구역(잠원 포함) → 포함(여럿 중 하나 매치)
    assert "C2" not in ids      # 다른 학교(반원)만 → 제외(★missing≠keep)
    assert "C4" not in ids      # 무배정(sentinel) → 제외
    assert "C5" not in ids      # 미계산 → 제외


def test_filter_unknown_school_empty(db: sqlite3.Connection) -> None:
    # 없는 학교 → positive-match 무결과(제외가 의도·전체 반환 아님).
    cands = search_complexes(db, HardFilterSpec(assigned_school="없는초등학교", limit=50))
    assert cands == []


def test_filter_no_assigned_returns_all(db: sqlite3.Connection) -> None:
    # assigned_school 미지정 → 배정 필터 무영향(전 단지·무회귀).
    cands = search_complexes(db, HardFilterSpec(limit=50))
    assert _ids(cands) == {"C1", "C2", "C3", "C4", "C5"}


def test_filter_region_qualified_disambiguates(db: sqlite3.Connection) -> None:
    # "서울잠원초"는 서울잠원초등학교만. 시드엔 잠원초 1종뿐이라 C1·C3 매치(부산잠원 없음).
    ids = _ids(search_complexes(db, HardFilterSpec(assigned_school="서울잠원초", limit=50)))
    assert "C1" in ids and "C3" in ids and "C2" not in ids


# ── NL 파싱 (배정 의도 → assigned_school) ──
def _runner(payload: dict):  # type: ignore[no-untyped-def]
    def run(prompt: str, max_turns: int) -> str:
        return json.dumps(payload)
    return run


def test_nl_assigned_school_extracted() -> None:
    payload = {"hard": {"assigned_school": "서울잠원초"}, "soft": {},
               "detected": [{"phrase": "서울잠원초 배정", "criterion_key": "assigned_school",
                             "mode": "hard"}], "unsupported": []}
    parsed = parse_query("서울잠원초 배정 단지", runner=_runner(payload))
    assert parsed.spec.assigned_school == "서울잠원초"
    assert any(d.criterion_key == "assigned_school" and d.mode == "hard" for d in parsed.detected)


def test_nl_assigned_school_with_net_area() -> None:
    payload = {"hard": {"assigned_school": "잠원초", "net_area_min": 84}, "soft": {},
               "detected": [], "unsupported": []}
    parsed = parse_query("잠원초 통학구역 신축 84", runner=_runner(payload))
    assert parsed.spec.assigned_school == "잠원초" and parsed.spec.net_area_min == 84


def test_nl_non_assignment_query_unaffected() -> None:
    # 배정 의도 없는 쿼리 → assigned_school None(거리 등 기존 동작 무회귀).
    payload = {"hard": {}, "soft": {"criteria": [{"key": "elem_dist", "weight": 1.0}]},
               "detected": [], "unsupported": []}
    parsed = parse_query("초등학교 가까운 단지", runner=_runner(payload))
    assert parsed.spec.assigned_school is None
