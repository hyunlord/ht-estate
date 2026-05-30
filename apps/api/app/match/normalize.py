"""이름·주소 정규화 — 거래(apt_name_raw)와 단지(name)에 동일 적용.

라이브 보정(T0-4, 강남 70단지)으로 확정한 규칙:
- 괄호 내용 제거: "현대6차(78~81,83,84동)" → "현대6차"
- 접미사 '아파트/apt' 제거, 구분자(-'·) 제거, 공백 제거, 소문자화
- **차/단지 번호는 보존**: 현대5차 ≠ 현대6차, 1단지 ≠ 2단지 (번호가드의 근거)
- K-apt는 이름 앞에 동/지역을 붙인다("압구정미성2차") → 포함관계 매칭은 fuzzy에서 처리
"""

from __future__ import annotations

import re

_PAREN = re.compile(r"[(（\[].*?[)）\]]")
_SUFFIX = re.compile(r"(아파트|apt)$", re.IGNORECASE)
_SEP = re.compile(r"[-'’·\s]+")
_NUM = re.compile(r"\d+")
_DONG = re.compile(r"[가-힣]+(?:동|가|읍|면|리)")


def normalize_name(raw: str) -> str:
    """단지명을 비교용 canonical 토큰으로. 번호(차/단지)는 보존한다."""
    s = _PAREN.sub("", raw)
    s = _SEP.sub("", s)
    s = _SUFFIX.sub("", s)
    return s.strip().lower()


def name_numbers(name: str) -> set[str]:
    """이름에 박힌 숫자 집합(차수·단지·동번호). 번호가드용 — 정규화된 문자열에 적용."""
    return set(_NUM.findall(name))


def extract_dong(addr: str | None) -> str | None:
    """지번주소에서 법정동 토큰 추출: "서울특별시 강남구 역삼동 711-1 …" → "역삼동".

    complex.dong 컬럼이 비어있을 때 legal_addr로 동을 얻는 fallback. 시군구(구/군/시)
    이후 첫 동/가/읍/면/리 토큰을 쓴다.
    """
    if not addr:
        return None
    # 시군구 토큰 이후로 한정해 단지명에 든 동 글자 오인을 줄임
    after = re.split(r"[가-힣]+(?:구|군|시)\s*", addr, maxsplit=1)
    target = after[-1] if len(after) > 1 else addr
    match = _DONG.search(target)
    return match.group(0) if match else None
