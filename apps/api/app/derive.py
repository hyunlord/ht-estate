"""파생 필드 — 원본(raw)에서 계산하는 결정론 값. 원본은 보존하고 여기서만 파생한다.

VLM/주관 점수 금지(원칙4). 여기 파생은 전부 객관 규칙(키워드·산술)이다.
"""

from __future__ import annotations

# gym 한정 키워드. '헬스'는 헬스장·헬스클럽을 substring으로 흡수.
# 골프연습장·탁구장·스쿼시·사우나는 gym이 아니므로 **제외**한다.
GYM_KEYWORDS: tuple[str, ...] = ("헬스", "휘트니스", "피트니스", "fitness", "gym")


def has_gym(amenities_raw: str | None) -> bool:
    """부대복리시설 원본 텍스트에 gym 키워드가 있으면 True.

    주의(T0-2 라이브 발견): K-apt 부대복리시설 데이터엔 헬스장이 거의 기록되지
    않는다(강남·송파·서초 ~180단지 표본 0건). 로직은 옳지만 실 K-apt에선 대부분
    False가 나오므로, 헬스장 신호는 Tier-2 enrichment 보강이 필요할 수 있다.
    """
    if not amenities_raw:
        return False
    text = amenities_raw.lower()
    return any(keyword.lower() in text for keyword in GYM_KEYWORDS)


# P4-1: welfareFacility(부대복리시설) raw에서 **패턴이 명확한** 시설만 boolean 파생(NL 토대).
# 모호한 토큰은 파생 안 함(과파생 금지·원칙4) — raw(amenities_raw)는 그대로 보존해 ticket #2가 활용.
_WELFARE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "daycare": ("보육시설", "어린이집"),
    "playground": ("놀이터",),
    "senior_center": ("노인정", "경로당"),
    "library": ("문고", "도서관"),
}


def _has_welfare(amenities_raw: str | None, kind: str) -> bool:
    """amenities_raw에 kind의 명확 키워드가 있으면 True(없거나 미지정 kind면 False)."""
    if not amenities_raw:
        return False
    return any(kw in amenities_raw for kw in _WELFARE_KEYWORDS.get(kind, ()))


def has_daycare(amenities_raw: str | None) -> bool:
    """단지 내 보육시설/어린이집 보유(명확 토큰)."""
    return _has_welfare(amenities_raw, "daycare")


def has_playground(amenities_raw: str | None) -> bool:
    """단지 내 (어린이)놀이터 보유."""
    return _has_welfare(amenities_raw, "playground")


def has_senior_center(amenities_raw: str | None) -> bool:
    """단지 내 노인정/경로당 보유."""
    return _has_welfare(amenities_raw, "senior_center")


def has_library(amenities_raw: str | None) -> bool:
    """단지 내 문고/도서관 보유."""
    return _has_welfare(amenities_raw, "library")


def parking_ratio(parking_total: int | None, household_count: int | None) -> float | None:
    """세대당 주차대수 = parking_total / household_count.

    household_count가 None/0이면(데이터 부실·분모 0) None — 0으로 나누지 않는다.
    """
    if parking_total is None or not household_count:
        return None
    return parking_total / household_count
