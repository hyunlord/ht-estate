"""gym-kakao: Kakao Local 헬스장 근접 신호 — 비아파트 gym(네이버 없이 신뢰 소스).

단지 좌표 0~50m(건물내/동일지)에 "헬스장"/"피트니스" Kakao Local place가 있으면 = 신뢰 "이 단지
헬스장 있음" 신호(동일위치 물리 POI). gym enrichment 사실(source_type='kakao_local'·고신뢰 0.88)로
write → synthesize_gym이 최고-confidence를 primary로(저신뢰 web 추출 0.31 우선) → 디테일 ✓ 있음
(C80 신뢰 게이트)·soft gym 점수 포함. **missing=keep**: 매치 없으면 write 0(단정 "없음" 아님·
기존 advisory 폴백).

반경 정밀(프로파일): 송파KCC 바디러너스 2m·견지동 40m = 건물내/동일지 → 잡음. 용산KCC 77m·
KCC엠파이어 135m·이웃 99m+ = 근처 상업 헬스장 → **단지 amenity 오인 금지**(precision-first, 50m).
"""

from __future__ import annotations

import json

from app.poi.proximity import KakaoLocalClient

GYM_KEYWORDS = ("헬스장", "피트니스")
GYM_RADIUS_M = 50  # 건물내/동일지(프로파일: in-building ≤40m, 이웃은 77m+ → 50m이 정밀 컷)
GYM_CONFIDENCE = 0.88  # 동일위치 물리 POI = 고신뢰(C80 게이트 ≥0.7 통과 → ✓ 있음)
SOURCE_TYPE = "kakao_local"


def nearest_gym(
    client: KakaoLocalClient, lat: float, lng: float, *, radius: int = GYM_RADIUS_M
) -> dict | None:
    """반경 내 가장 가까운 헬스장/피트니스 place. {place_name, distance_m, url} 또는 None(무매치).

    Kakao keyword(헬스장·피트니스)를 radius로 검색해 최근접 1건. 반경 밖이면 None(missing=keep).
    """
    best: dict | None = None
    for kw in GYM_KEYWORDS:
        for d in client.keyword_docs(kw, lng, lat, radius=radius):
            raw = d.get("distance")
            dist = int(float(raw)) if raw not in (None, "") else radius + 1
            if dist <= radius and (best is None or dist < best["distance_m"]):
                best = {
                    "place_name": d.get("place_name", ""),
                    "distance_m": dist,
                    "url": d.get("place_url", ""),
                }
    return best


def gym_fact_value(match: dict) -> str:
    """Kakao 매치 → gym enrichment value(JSON{has_gym, evidence}). synthesize_gym._parse가 읽음."""
    name = match["place_name"]
    dist = match["distance_m"]
    return json.dumps(
        {"has_gym": "yes", "evidence": f"{name} ({dist}m·Kakao Local)"}, ensure_ascii=False
    )
