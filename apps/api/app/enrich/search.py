"""웹검색·페이지 fetch 인터페이스 (주입형) + IP/legal 가드.

search_fn / fetch_fn은 주입형 — 게이트는 mock, 실 provider는 P1-2-live(키). R1 legal:
공식홈·입주공고가 가장 깨끗, 네이버·호갱노노·아실은 **자동 스크레이프 금지**(차단리스트).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# R1 legal: 자동 fetch 금지 도메인(다윈중개 판례·DB권·ToS). 공식홈/언론/블로그/카페만.
BLOCKED_DOMAINS: tuple[str, ...] = (
    "naver.com",
    "hogangnono.com",
    "asil.kr",
    "land.naver.com",
)

# 출처 도메인 → source_type + 품질 가중(공식홈 1.0 > 언론 0.85 > 블로그 0.6 > 카페 0.5).
# 공식홈 판정은 단지명 매칭이 어려워 known 포털만 분류, 나머지는 'web'(중립 0.7).
_SOURCE_WEIGHTS: dict[str, tuple[str, float]] = {
    "news": ("news", 0.85),
    "blog": ("blog", 0.6),
    "cafe": ("cafe", 0.5),
    "official": ("official", 1.0),
    "web": ("web", 0.7),
}


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    source_kind: str = "web"  # 'official'|'news'|'blog'|'cafe'|'web'


# 주입형 시그니처. search_fn: 질의 → 후보; fetch_fn: URL → 본문 텍스트(None=실패/차단).
SearchFn = Callable[[str], list[SearchResult]]
FetchFn = Callable[[str], str | None]


def is_blocked(url: str) -> bool:
    """R1 차단 도메인이면 True(자동 fetch 금지)."""
    lowered = url.lower()
    return any(domain in lowered for domain in BLOCKED_DOMAINS)


def source_type_and_weight(kind: str) -> tuple[str, float]:
    """source_kind → (source_type, 품질가중). 미지의 kind는 web(0.7)."""
    return _SOURCE_WEIGHTS.get(kind, _SOURCE_WEIGHTS["web"])
