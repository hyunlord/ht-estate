"""도로명주소 파싱 + 매칭 키 — K-apt doroJuso와 좌표DB를 같은 키로 잇는다.

K-apt는 도로명코드를 안 주므로 (시도|시군구|도로명|본번-부번)로 매칭한다.
양쪽(주소 파싱·좌표DB 적재)이 같은 addr_key를 써야 룩업이 성립한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 도로명 뒤 [지하] 본번[-부번]. 도로명은 ~로/~길/~대로(모두 '로'/'길'로 끝남).
_NUM = re.compile(r"(\d+)(?:-(\d+))?")


@dataclass(frozen=True)
class RoadAddr:
    sido: str
    sigungu: str
    road: str
    bonbun: int
    bubun: int
    underground: bool


def parse_road_addr(addr: str | None) -> RoadAddr | None:
    """ "서울특별시 강남구 언주로 420" → RoadAddr. 파싱 실패면 None(graceful)."""
    if not addr:
        return None
    tokens = addr.split()
    if len(tokens) < 3:
        return None
    sido = tokens[0]
    idx = 1
    sigungu_parts: list[str] = []
    while idx < len(tokens) and tokens[idx][-1] in "시군구":
        sigungu_parts.append(tokens[idx])
        idx += 1
    if not sigungu_parts:
        return None
    sigungu = "".join(sigungu_parts)

    rest = tokens[idx:]
    for j, tok in enumerate(rest):
        if not tok.endswith(("로", "길")):
            continue
        underground = False
        k = j + 1
        if k < len(rest) and rest[k].startswith("지하"):
            underground = True
            k += 1
        if k >= len(rest):
            return None
        match = _NUM.match(rest[k])
        if not match:
            return None
        bonbun = int(match.group(1))
        bubun = int(match.group(2) or 0)
        return RoadAddr(sido, sigungu, tok, bonbun, bubun, underground)
    return None


def addr_key(sido: str, sigungu: str, road: str, bonbun: int, bubun: int) -> str:
    """매칭 키. 공백 제거로 표기 흔들림 흡수. 시도/시군구는 공식명이라 양쪽 동일."""
    return f"{sido}|{sigungu}|{road}|{bonbun}-{bubun}".replace(" ", "")


def key_of(parsed: RoadAddr) -> str:
    return addr_key(parsed.sido, parsed.sigungu, parsed.road, parsed.bonbun, parsed.bubun)
