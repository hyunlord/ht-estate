"""source-fetcher 추상화 — 웹검색·지도 POI·관리규약을 mock 가능한 인터페이스 뒤로. (E1)

추출기는 단지명으로 소스(웹/카페/블로그/지도 POI)를 가져와 LLM에 넘긴다. 구체 검색 provider
(웹검색 API·지도 POI)는 **flagged config 결정**(블로커 아님) — 이 모듈은 **프로토콜 + Null/mock**
이 산출물이다. graceful: fetch 실패는 빈 결과(추출기가 defer).

키리스: live 검색은 config로 주입. 테스트는 `SourceFetcher`를 mock(고정 문서)으로 주입.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class SourceDoc(BaseModel):
    """가져온 소스 한 건 — provenance(source_type·url) + LLM에 넣을 본문 스니펫."""

    source_type: str  # 'web'|'cafe'|'blog'|'youtube'|'map' — provenance source_type
    source_url: str  # 딥링크(출처 이동)
    text: str  # 추출 LLM 입력 본문(스니펫)


class SourceFetcher(Protocol):
    """주입형 소스 페처 — (query, kind) → 문서 리스트. 무결과/실패는 빈 리스트(추출기 defer)."""

    def fetch(self, query: str, *, kind: str) -> list[SourceDoc]: ...


class NullFetcher:
    """기본 페처 — 항상 빈 결과(라이브 검색 미구성). 구체 provider는 flagged config로 대체.

    골격이 라이브 활성화 없이도 안전하게 돌게 한다(추출기 → miss → DB 불변).
    """

    def fetch(self, query: str, *, kind: str) -> list[SourceDoc]:
        return []
