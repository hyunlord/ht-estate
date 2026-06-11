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
# 거래명 끝의 *건물* 동 번호/범위 — 단지 정체성이 아닌 노이즈라 제거한다(P2-4 라이브 보정).
#   "…101동~111동" / "…1동~8동" / "…1~6" (동 범위) · "…101동"(단동) → 제거.
# 범위 구분자는 물결(~/∼)만 — 하이픈은 지번·정상명에 흔해 범위로 오인하면 정체성을 깎는다.
# 단동은 반드시 '동'을 요구 → 끝자리 맨숫자(차/단지 신호: "한양4")는 보존(번호가드 근거).
_BLDG_SUFFIX = re.compile(r"\d+동?\s*[~∼]\s*\d+동?$|\d+동$")
# 선행 'LH' 운영사 접두 — LH 평면도 단지명("LH수서1단지")과 K-apt("수서1단지")가 갈리는 비대칭
# (P3-2 스파이크: sim 0.83<0.85, 포함부스트 미발동)을 메운다. **선행 LH만** 제거(운영사 접두) —
# '주공'·'휴먼시아'는 이름 중간 정체성 토큰("개포주공7단지"≠"개포7단지") → 제거하면 오매칭, 보존.
_LH_PREFIX = re.compile(r"^lh")
# 학교명 접미 통일 — "잠원초등학교"/"잠원초"를 한 base로(접미 제거 후 normalize_name).
# 지역 접두("서울"·"부산")는 **정체성**이라 보존(서울잠원초 ≠ 부산잠원초). '분교'도 보존(별개 학교).
_SCHOOL_SUFFIX = re.compile(r"(초등학교|초)$")


def normalize_name(raw: str) -> str:
    """단지명을 비교용 canonical 토큰으로. 번호(차/단지)는 보존, 끝 건물 동번호/범위는 제거.

    소문자화 뒤 선행 'LH' 운영사 접두 제거(LH 평면도↔K-apt 매칭, P3-2). 주공/휴먼시아는 미제거.
    """
    s = _PAREN.sub("", raw)
    s = _BLDG_SUFFIX.sub("", s)
    s = _SEP.sub("", s)
    s = _SUFFIX.sub("", s)
    s = s.strip().lower()
    return _LH_PREFIX.sub("", s)


def normalize_school_name(raw: str) -> str:
    """학교명 비교용 canonical — '초등학교'/'초' 접미 통일 후 normalize_name(괄호/구분자/소문자).

    "서울잠원초등학교"→"서울잠원" · "잠원초"→"잠원". 지역 접두는 보존(정체성).
    """
    return normalize_name(_SCHOOL_SUFFIX.sub("", raw or ""))


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
