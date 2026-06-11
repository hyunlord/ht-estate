"""배정 초등 통학구역 (school-2) — point-in-polygon → 학구ID → 연계 조인 → 배정 초등.

초등 통학구역 SHP(EPSG:5186 네이티브) polygon에 단지 좌표(WGS84→5186 reproject)가 들어가는지
shapely STRtree+covers로 판정 → HAKGUDO_ID → 연계 CSV(초등 필터) 조인 → 배정 초등(공동통학구역은
복수). **초등 ONLY**(중/고는 평준화 추첨이라 배정 개념 없음). 좌표 read·school_assignment write만 →
지문/counts 불변. advisory(열람용·교육청 확인 — schoolzone 법적효력 없음, pet 패턴 동형).

graceful: SHP/CSV/geometry 파싱오류·결손 → skip(크래시 0). reproject는 점만(폴리곤 왜곡 0).
"""

from __future__ import annotations

import csv
import glob
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import shapefile  # pyshp
from pydantic import BaseModel
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

EPSG_WGS84 = "EPSG:4326"
EPSG_ZONE = "EPSG:5186"  # Korea 2000 / Central Belt 2010 (.prj 실측)
SOURCE = "schoolzone_elem_zone"
SOURCE_URL = "https://schoolzone.emac.kr/"
SHARED_CLASS = "1"  # HAKGUDO_GB '1' = 공동통학구역


@dataclass(frozen=True)
class Assignment:
    """단지 배정 1건 — 학구ID·분류·배정 초등."""

    zone_id: str
    zone_class: str
    school_id: str
    school_name: str
    is_shared: bool


class AssignmentRow(BaseModel):
    """카드용 배정 행(advisory). sentinel(배정 없음)은 read에서 제외 → 빈 리스트=dash."""

    zone_id: str
    zone_class: str | None
    school_id: str
    school_name: str | None
    is_shared: bool


def load_elem_links(link_path: str) -> dict[str, list[tuple[str, str]]]:
    """연계 CSV → {학구ID: [(학교ID, 학교명)...]} (초등만). 공동은 학구당 복수."""
    out: dict[str, list[tuple[str, str]]] = {}
    with open(link_path, encoding="cp949", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("학교급구분") or "").strip() != "초등학교":
                continue
            zid = (r.get("학구ID") or "").strip()
            sid = (r.get("학교ID") or "").strip()
            nm = (r.get("학교명") or "").strip()
            if zid and sid:
                out.setdefault(zid, []).append((sid, nm))
    return out


class ZoneIndex:
    """초등 통학구역 STRtree(5186) + 학구→초등 매핑. assign(lat,lng)=배정 초등(들)·폴리곤 밖=[]."""

    def __init__(
        self,
        polys: list[tuple[str, str, BaseGeometry]],
        zone_schools: dict[str, list[tuple[str, str]]],
    ) -> None:
        self._geoms = [g for _, _, g in polys]
        self._meta = [(zid, zcls) for zid, zcls, _ in polys]  # _geoms와 인덱스 정렬
        self._tree = STRtree(self._geoms)
        self._zone_schools = zone_schools
        self._tf = Transformer.from_crs(EPSG_WGS84, EPSG_ZONE, always_xy=True)

    def assign(self, lat: float, lng: float) -> list[Assignment]:
        x, y = self._tf.transform(lng, lat)  # always_xy: (lng,lat)→(x,y) meters(5186)
        pt = Point(x, y)
        out: list[Assignment] = []
        seen: set[tuple[str, str]] = set()
        for idx in self._tree.query(pt):  # bbox 후보
            if not self._geoms[idx].covers(pt):  # 경계 포함 containment
                continue
            zid, zcls = self._meta[idx]
            for sid, nm in self._zone_schools.get(zid, []):
                if (zid, sid) in seen:
                    continue
                seen.add((zid, sid))
                out.append(Assignment(zid, zcls, sid, nm, is_shared=False))
        shared = zcls_shared(out)
        return [
            Assignment(a.zone_id, a.zone_class, a.school_id, a.school_name, is_shared=shared)
            for a in out
        ]


def zcls_shared(assigns: list[Assignment]) -> bool:
    """공동 여부 — 배정 학교가 복수이거나 학구분류='1'이면 공동."""
    return len(assigns) > 1 or any(a.zone_class == SHARED_CLASS for a in assigns)


def load_zone_index(shp_glob: str, link_path: str) -> ZoneIndex:
    """초등 통학구역 SHP(폴리곤) + 연계 CSV → ZoneIndex. 손상 geometry는 skip(graceful)."""
    shp_path = glob.glob(shp_glob)[0]
    reader = shapefile.Reader(shp_path, encoding="cp949")
    fields = [f[0] for f in reader.fields[1:]]
    id_i, gb_i = fields.index("HAKGUDO_ID"), fields.index("HAKGUDO_GB")
    polys: list[tuple[str, str, BaseGeometry]] = []
    for sr in reader.iterShapeRecords():
        sh, rec = sr.shape, sr.record
        if sh is None or rec is None:
            continue
        try:
            geom = shape(sh.__geo_interface__)  # type: ignore[arg-type]
            if geom.is_empty:
                continue
            polys.append((str(rec[id_i]), str(rec[gb_i]), geom))
        except Exception:  # noqa: BLE001 — 손상 geometry/레코드 skip(크래시 0)
            continue
    return ZoneIndex(polys, load_elem_links(link_path))


# ── store ──
def write_assignment(
    conn: sqlite3.Connection, complex_id: str, assigns: list[Assignment], *, now: datetime
) -> None:
    """단지의 배정(들) write. 배정 없으면 sentinel(zone_id=''). 멱등(기존 삭제 후 삽입)."""
    conn.execute("DELETE FROM school_assignment WHERE complex_id = ?", (complex_id,))
    rows = (
        [(complex_id, a.zone_id, a.zone_class, a.school_id, a.school_name, a.is_shared,
          SOURCE, SOURCE_URL, now.isoformat()) for a in assigns]
        if assigns
        else [(complex_id, "", None, "", None, False, SOURCE, SOURCE_URL, now.isoformat())]
    )
    conn.executemany(
        "INSERT INTO school_assignment (complex_id, zone_id, zone_class, school_id, school_name, "
        "is_shared, source, source_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )


class _Target(Protocol):
    complex_id: str
    assignment: list[AssignmentRow] | None


def attach_assignment(conn: sqlite3.Connection, candidates: Sequence[_Target]) -> None:
    """후보에 배정 초등 in-place 부착(읽기 전용·sentinel 제외 → 빈 리스트=dash)."""
    if not candidates:
        return
    summaries = read_assignment(conn, [c.complex_id for c in candidates])
    for cand in candidates:
        cand.assignment = summaries.get(cand.complex_id, [])


def read_assignment(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, list[AssignmentRow]]:
    """후보 id → {id: [AssignmentRow...]}. sentinel(zone_id='')·미계산은 빈 리스트(dash)."""
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT complex_id, zone_id, zone_class, school_id, school_name, is_shared "
        f"FROM school_assignment WHERE complex_id IN ({ph}) AND zone_id != '' "
        "ORDER BY complex_id, school_name",
        list(ids),
    ).fetchall()
    out: dict[str, list[AssignmentRow]] = {cid: [] for cid in ids}
    for r in rows:
        out[r["complex_id"]].append(
            AssignmentRow(
                zone_id=r["zone_id"], zone_class=r["zone_class"], school_id=r["school_id"],
                school_name=r["school_name"], is_shared=bool(r["is_shared"]),
            )
        )
    return out


def resolve_assigned_schools(conn: sqlite3.Connection, query: str) -> list[str]:
    """질의 학교명 → 매칭 stored school_name 리스트(fuzzy·app/match 재사용). positive-match 필터용.

    "잠원초"·"서울잠원초"·"잠원초등학교" → "서울잠원초등학교"(접미통일+포함부스트). 지역 접두 다르면
    제외(부산잠원). 매칭 0이면 빈 리스트 → 호출부가 무결과 처리(없는 학교=무결과).
    DISTINCT school_name(~5k)만 read → 지문/counts 불변.
    """
    from app.match.fuzzy import DEFAULT_THRESHOLD, school_similarity

    q = (query or "").strip()
    if not q:
        return []
    rows = conn.execute(
        "SELECT DISTINCT school_name FROM school_assignment "
        "WHERE school_name IS NOT NULL AND school_name != '' AND zone_id != ''"
    ).fetchall()
    return [
        r["school_name"]
        for r in rows
        if school_similarity(q, r["school_name"]) >= DEFAULT_THRESHOLD
    ]


def enrich_assignment(
    conn: sqlite3.Connection, index: ZoneIndex, *, now: datetime, limit: int
) -> dict[str, int]:
    """미계산 단지(좌표보유) point-in-polygon 배정 적재. 멱등 resume·단지커밋. 외부 호출 0."""
    rows = conn.execute(
        "SELECT c.complex_id, c.lat, c.lng FROM complex c "
        "WHERE c.lat IS NOT NULL AND c.lng IS NOT NULL AND "
        "NOT EXISTS (SELECT 1 FROM school_assignment s WHERE s.complex_id = c.complex_id) "
        "ORDER BY c.complex_id LIMIT ?",
        (limit,),
    ).fetchall()
    n_assigned = n_none = 0
    for row in rows:
        assigns = index.assign(row["lat"], row["lng"])
        write_assignment(conn, row["complex_id"], assigns, now=now)
        if assigns:
            n_assigned += 1
        else:
            n_none += 1
        conn.commit()
    return {"assigned": n_assigned, "none": n_none, "processed": len(rows)}
