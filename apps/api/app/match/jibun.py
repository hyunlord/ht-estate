"""지번(번지) 추출·정규화 — 거래(MOLIT)와 단지(K-apt) 양측을 같은 캐논으로.

매칭은 "같은 법정동 + 같은 지번 = 같은 물리 필지 = 같은 단지일 강한 증거"를 쓴다(설계 §5.1).
그러려면 양측 지번을 비교 가능한 한 형태로 떨어뜨려야 한다:

- MOLIT: `bonbun`/`bubun`(0패딩 4자리, 예 0489-0000) 우선, 없으면 `jibun` 문자열(489 / 489-1).
- K-apt: `kaptAddr` 자유서술("…역삼동 711-1 역삼자이아파트")에서 동 뒤 첫 *숫자 토큰*만.

캐논 = 0패딩 제거한 (본번, 부번) 정수쌍. 부번 0이면 본번만 표기("711"), 아니면 "711-1".
**산 번지**는 정규 번지와 번호공간이 겹쳐(산1 ≠ 1) 매칭하면 오매칭이므로 None — jibun 회수를
포기하고 정밀도를 지킨다(아파트가 산 번지인 경우는 희박).
"""

from __future__ import annotations

import re

# 주소 토큰 하나가 번지인지: 선택적 산 + 본번(-부번) + 선택적 '번지' 접미사.
# 부번은 \d* — K-apt가 빈 부번을 "본번-"(예 "126-")로 렌더하는 케이스를 본번만으로 흡수(P2-4).
_LOT_TOKEN = re.compile(r"^(산)?(\d+)(?:-(\d*))?번?지?$")
# MOLIT jibun 문자열: 본번(-부번). 산/기타 표기는 매칭에서 제외(None).
_JIBUN_FIELD = re.compile(r"^(\d+)(?:-(\d+))?$")
_DONG = re.compile(r"[가-힣]+(?:동|가|읍|면|리)")

Jibun = tuple[int, int]  # (본번, 부번)


def _from_pair(bonbun: int, bubun: int) -> Jibun | None:
    """본번 0(무효 필지)이면 None, 아니면 (본번, 부번)."""
    return (bonbun, bubun) if bonbun > 0 else None


def from_molit(bonbun: str | None, bubun: str | None, jibun: str | None) -> Jibun | None:
    """MOLIT 한 거래의 지번 → (본번, 부번). bonbun/bubun(0패딩) 우선, 없으면 jibun 문자열.

    0패딩을 정수화로 흡수한다("0489"→489). 본번 0 또는 파싱 불가면 None(graceful).
    """
    if bonbun and bonbun.strip():
        try:
            return _from_pair(int(bonbun), int(bubun) if bubun and bubun.strip() else 0)
        except ValueError:
            return None
    if jibun and jibun.strip():
        m = _JIBUN_FIELD.match(jibun.strip())
        if m:
            return _from_pair(int(m.group(1)), int(m.group(2) or 0))
    return None


def from_kapt_address(addr: str | None) -> Jibun | None:
    """K-apt 자유서술 지번주소에서 (본번, 부번) 추출.

    동 토큰 이후의 첫 *숫자 토큰*만 지번으로 본다 — 단지명에 박힌 숫자("래미안3차")를
    피하려고 공백 단위 토큰 매칭을 쓴다. 산 번지는 None(번호공간 충돌 → 오매칭 방지).
    """
    if not addr:
        return None
    match = _DONG.search(addr)
    if not match:
        return None
    prev_san = False
    for tok in addr[match.end() :].split():
        if tok == "산":  # "산 1-2"처럼 떨어진 산 표기
            prev_san = True
            continue
        m = _LOT_TOKEN.match(tok)
        if m:
            if prev_san or m.group(1):  # 산 번지 → 매칭 불가
                return None
            return _from_pair(int(m.group(2)), int(m.group(3) or 0))
        prev_san = False  # 숫자도 산도 아닌 토큰은 산 플래그를 리셋
    return None


def to_canonical(jibun: Jibun | None) -> str | None:
    """(본번, 부번) → 비교/저장용 캐논 문자열. 부번 0은 생략. None은 그대로 None."""
    if jibun is None:
        return None
    bonbun, bubun = jibun
    return f"{bonbun}" if bubun == 0 else f"{bonbun}-{bubun}"
