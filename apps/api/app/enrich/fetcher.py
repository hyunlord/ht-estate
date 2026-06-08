"""source-fetcher 추상화 — 웹검색·지도 POI·관리규약을 mock 가능한 인터페이스 뒤로. (E1)

추출기는 단지명으로 소스(웹/카페/블로그/지도 POI)를 가져와 LLM에 넘긴다. 구체 검색 provider
(웹검색 API·지도 POI)는 **flagged config 결정**(블로커 아님) — 이 모듈은 **프로토콜 + Null/mock**
이 산출물이다. graceful: fetch 실패는 빈 결과(추출기가 defer).

키리스: live 검색은 config로 주입. 테스트는 `SourceFetcher`를 mock(고정 문서)으로 주입.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx
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


# Naver 검색 API 환경변수(루트 .env, settings.py 자동 로딩).
NAVER_ID_ENV = "NAVER_CLIENT_ID"
NAVER_SECRET_ENV = "NAVER_CLIENT_SECRET"

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_snippet(s: str) -> str:
    """Naver 결과의 <b> 강조 태그·HTML 엔티티 제거 → LLM 입력 평문."""
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


@dataclass
class NaverSourceFetcher:
    """Naver 검색 API 페처 — 블로그·카페·웹·뉴스 vertical을 묶어 실 source_url을 낸다. (E1-live)

    `fetch(query, kind=...)` 한 번이 여러 vertical을 질의해 SourceDoc(source_type·실 link·스니펫)을
    반환한다. **차단도메인은 _common.run_extraction이 fetch 후 일괄 drop**(여기서 순환 import 회피).
    throughput: vertical당 `display`개·fetch당 `max_docs` 상한(프롬프트/출력 토큰 축소).
    graceful: vertical 실패(타임아웃·429·malformed)는 빈 결과로 흡수 → 부분/빈 반환(crash 금지).
    `kind`는 프로토콜 호환용으로 받되 Naver는 vertical 묶음으로 질의(현재 무시).
    키리스 테스트: `client`(httpx.MockTransport) 주입.
    """

    client_id: str
    client_secret: str
    display: int = 2  # vertical당 결과 수(throughput 캡)
    max_docs: int = 4  # fetch당 총 문서 상한
    timeout: float = 10.0
    client: httpx.Client | None = None
    # (endpoint, source_type) — news는 enum에 없어 'web'으로 매핑.
    verticals: tuple[tuple[str, str], ...] = field(
        default=(("blog", "blog"), ("cafearticle", "cafe"), ("webkr", "web"), ("news", "web"))
    )

    def fetch(self, query: str, *, kind: str) -> list[SourceDoc]:
        docs: list[SourceDoc] = []
        for endpoint, source_type in self.verticals:
            if len(docs) >= self.max_docs:
                break
            docs.extend(self._search(query, endpoint, source_type))
        return docs[: self.max_docs]

    def _search(self, query: str, endpoint: str, source_type: str) -> list[SourceDoc]:
        url = f"https://openapi.naver.com/v1/search/{endpoint}.json"
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }
        params = {"query": query, "display": self.display, "sort": "sim"}
        own = self.client is None
        cl = self.client or httpx.Client(timeout=self.timeout)
        try:
            resp = cl.get(url, headers=headers, params=params)
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return []  # graceful: 이 vertical 실패 → 빈(부분 결과) — 다음 vertical 계속
        finally:
            if own:
                cl.close()
        out: list[SourceDoc] = []
        for it in items:
            link = (it.get("link") or "").strip()
            text = _clean_snippet(f"{it.get('title', '')} {it.get('description', '')}")
            if not link or not text:
                continue  # 출처 이동 불가/내용 없음 → skip
            out.append(SourceDoc(source_type=source_type, source_url=link, text=text))
        return out


def naver_fetcher_from_env() -> SourceFetcher | None:
    """env(NAVER_CLIENT_ID/SECRET)에서 Naver 페처 구성. 미설정이면 None(NullFetcher 폴백 호출부)."""
    cid = os.environ.get(NAVER_ID_ENV, "").strip()
    secret = os.environ.get(NAVER_SECRET_ENV, "").strip()
    if not cid or not secret:
        return None
    return NaverSourceFetcher(client_id=cid, client_secret=secret)
