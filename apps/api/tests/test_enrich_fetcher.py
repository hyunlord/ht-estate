"""Naver SourceFetcher (E1-live) — 키리스(httpx.MockTransport). 실 HTTP 0.

vertical 묶음 질의·source_type 매핑·HTML 스트립·throughput 캡·graceful(부분/빈)·env 팩토리.
"""

from __future__ import annotations

import httpx
import pytest

from app.enrich.fetcher import NaverSourceFetcher, SourceDoc, naver_fetcher_from_env


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def _item(link: str, title: str, desc: str) -> dict:
    return {"link": link, "title": title, "description": desc}


def _ok_handler(req: httpx.Request) -> httpx.Response:
    path = req.url.path  # /v1/search/<vertical>.json
    if "/blog." in path:
        blog = _item("http://blog/1", "<b>역삼</b>자이 헬스장", "피트니스 &amp; 사우나")
        return httpx.Response(200, json={"items": [blog]})
    if "/cafearticle." in path:
        return httpx.Response(200, json={"items": [_item("http://cafe/1", "카페글", "강아지")]})
    if "/webkr." in path:
        return httpx.Response(200, json={"items": [_item("http://web/1", "웹문서", "규약")]})
    if "/news." in path:
        return httpx.Response(500, json={})  # graceful: 이 vertical만 실패
    return httpx.Response(404, json={})


def test_fetch_aggregates_verticals_with_source_type() -> None:
    f = NaverSourceFetcher("id", "sec", client=_client(_ok_handler))
    docs = f.fetch("역삼자이", kind="web")
    assert [d.source_type for d in docs] == ["blog", "cafe", "web"]  # news 실패는 흡수(부분)
    assert all(isinstance(d, SourceDoc) for d in docs)
    assert docs[0].source_url == "http://blog/1"


def test_html_tags_and_entities_stripped() -> None:
    f = NaverSourceFetcher("id", "sec", client=_client(_ok_handler))
    text = f.fetch("역삼자이", kind="web")[0].text
    assert "<b>" not in text and "</b>" not in text
    assert "&amp;" not in text and "&" in text  # 엔티티 unescape


def test_max_docs_cap() -> None:
    def many(req: httpx.Request) -> httpx.Response:
        items = [{"link": f"http://x/{i}", "title": f"t{i}", "description": "d"} for i in range(3)]
        return httpx.Response(200, json={"items": items})

    f = NaverSourceFetcher("id", "sec", display=3, max_docs=4, client=_client(many))
    docs = f.fetch("q", kind="web")
    assert len(docs) == 4  # blog(3) + cafe(3)=6 → cap 4


def test_graceful_total_failure_returns_empty() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    f = NaverSourceFetcher("id", "sec", client=_client(boom))
    assert f.fetch("q", kind="web") == []  # 전 vertical 실패 → 빈(crash 금지)


def test_blank_link_or_text_skipped() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "/blog." not in req.url.path:
            return httpx.Response(200, json={"items": []})  # blog vertical만 응답
        return httpx.Response(
            200,
            json={"items": [
                {"link": "", "title": "링크없음", "description": "x"},          # link 없음 → skip
                {"link": "http://ok/1", "title": "", "description": ""},        # 내용 없음 → skip
                {"link": "http://ok/2", "title": "유효", "description": "내용"},  # 유지
            ]},
        )

    f = NaverSourceFetcher("id", "sec", client=_client(handler))
    docs = f.fetch("q", kind="web")
    assert [d.source_url for d in docs] == ["http://ok/2"]


def test_env_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    assert naver_fetcher_from_env() is None  # 미구성 → None(NullFetcher 폴백)
    monkeypatch.setenv("NAVER_CLIENT_ID", "cid")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "csec")
    f = naver_fetcher_from_env()
    assert isinstance(f, NaverSourceFetcher) and f.client_id == "cid"
