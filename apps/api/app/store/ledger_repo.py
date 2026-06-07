"""건축물대장 enrich 적재 — 비-아파트 건물의 빈 속성을 대장 표제부로 채움. (enrich-1)

**enrich-only** — 주소매칭된 *기존* complex 행을 UPDATE만 한다(INSERT 없음). 대장에 건물이 있어도
우리 set에 없으면 미삽입 → **건물 수 불변**. UPDATE 컬럼셋에 lat/lng·geo_*가 **없어** 좌표는 절대
안 건드린다(geocode 보존 1순위, nonapt_repo._BUILDING_COLS와 동일 구조적 강제).

- 공유 레거시 컬럼(building_type·household_count·top_floor·elevator_count·approval_date)은
  COALESCE로 **NULL일 때만** 채운다(기존 값 무손상·멱등).
- 대장 전용 컬럼(주용도·연면적·지상/지하층·건폐/용적률·높이·호수)은 직접 set(매칭 대장 반영).
- provenance: ledger_source_url(API 요청 식별)·ledger_fetched_at·ledger_pk·ledger_bld_nm.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.sources.building_ledger import BASE_URL, TITLE_OP, BuildingLedgerTitle

# enrich UPDATE 컬럼 — **lat/lng·geo_* 의도적 부재**(좌표 보존). 신규/삭제 없음(UPDATE only).
_ENRICH_SQL = """
UPDATE complex SET
  building_type           = COALESCE(building_type, :structure),
  household_count         = COALESCE(household_count, :household_count),
  top_floor               = COALESCE(top_floor, :ground_floor_count),
  elevator_count          = COALESCE(elevator_count, :elevator_count),
  approval_date           = COALESCE(approval_date, :approval_date),
  main_purpose            = :main_purpose,
  total_floor_area        = :total_floor_area,
  ground_floor_count      = :ground_floor_count,
  basement_floor_count    = :basement_floor_count,
  building_coverage_ratio = :building_coverage_ratio,
  floor_area_ratio        = :floor_area_ratio,
  building_height         = :building_height,
  ho_count                = :ho_count,
  ledger_source_url       = :ledger_source_url,
  ledger_fetched_at       = :fetched_at,
  ledger_pk               = :ledger_pk,
  ledger_bld_nm           = :bld_nm,
  updated_at              = :fetched_at
WHERE complex_id = :complex_id
"""


def ledger_source_url(sigungu_cd: str, bjdong_cd: str, bun: str, ji: str) -> str:
    """재현 가능한 대장 출처 포인터 — 표제부 조회 식별(provenance source_url)."""
    return (
        f"{BASE_URL}/{TITLE_OP}?sigunguCd={sigungu_cd}&bjdongCd={bjdong_cd}"
        f"&platGbCd=0&bun={bun}&ji={ji}"
    )


def enrich_building(
    conn: sqlite3.Connection,
    complex_id: str,
    ledger: BuildingLedgerTitle,
    *,
    source_url: str,
    fetched_at: datetime | None = None,
) -> bool:
    """기존 complex 행을 대장 표제부로 enrich(UPDATE only). 행이 갱신됐으면 True(매칭).

    INSERT 없음 → complex_id가 없으면 무변경 False(건물 수 불변). 좌표 컬럼 미포함 → 좌표 보존.
    """
    when = (fetched_at or datetime.now(UTC)).isoformat()
    params = {
        "complex_id": complex_id,
        "structure": ledger.structure,
        "household_count": ledger.household_count,
        "elevator_count": ledger.elevator_count,
        "approval_date": ledger.approval_date,
        "main_purpose": ledger.main_purpose,
        "total_floor_area": ledger.total_floor_area,
        "ground_floor_count": ledger.ground_floor_count,
        "basement_floor_count": ledger.basement_floor_count,
        "building_coverage_ratio": ledger.building_coverage_ratio,
        "floor_area_ratio": ledger.floor_area_ratio,
        "building_height": ledger.building_height,
        "ho_count": ledger.ho_count,
        "ledger_source_url": source_url,
        "fetched_at": when,
        "ledger_pk": ledger.ledger_pk,
        "bld_nm": ledger.bld_nm,
    }
    cur = conn.execute(_ENRICH_SQL, params)
    return cur.rowcount > 0


def pick_match(
    candidates: list[BuildingLedgerTitle], building_name: str | None
) -> BuildingLedgerTitle | None:
    """한 지번의 표제부 동들 중 우리 건물명과 매칭되는 1건 선택(다중 동 디스앰비그).

    1동이면 그것. 다동이면 bld_nm이 우리 건물명을 포함/피포함하는 것 우선, 없으면 None
    (억지 매칭 금지 — 모호하면 미enrich가 정직). 주거용 주용도만 고려(상가동 배제는 호출부 정책).
    """
    real = [c for c in candidates if c.structure or c.main_purpose or c.total_floor_area]
    if not real:
        return None
    if len(real) == 1:
        return real[0]
    if building_name:
        name = building_name.replace(" ", "")
        for c in real:
            bn = (c.bld_nm or "").replace(" ", "")
            if bn and (name in bn or bn in name):
                return c
    return None
