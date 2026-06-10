"""recon: YouTube 소스 viability (E3-2 PART0) — 결론: **DEFER**(blog/cafe-only ship).

실측(2026-06-10, Spark 데이터센터 IP)으로 단지명→영상→한국어 자막 스크립트 경로를 프로브:

  1) 검색: YOUTUBE_API_KEY 미설정 → Data API 불가. 검색결과 스크랩(youtube.com/results)은
     videoId는 뽑히나(예: 43개) **취약**(무공식·레이아웃 변동·ToS 리스크).
  2) 자막: legacy timedtext(api/timedtext?lang=ko&v=...)가 **status 200·len 0**(빈 응답) —
     데이터센터 IP에서 자막 미반환(innertube 경로 필요·차단 추정).

판정: 키리스·견고한 스크립트 경로 부재 → **YouTube defer**(별도 recon/티켓·평면도 동형의
recon-first 규율). 파이프라인은 source-agnostic(SourceDoc.source_type·span_ref) — 차후 YouTube
fetcher + 타임스탬프 청커(span_ref='t{a}-{b}')를 **스키마 무변경**으로 끼운다.

재실행(키/IP 바뀌면 재판정):  uv run python scripts/spikes/recon_youtube_transcript.py
"""

from __future__ import annotations

import re

import httpx

UA = {"User-Agent": "Mozilla/5.0"}


def probe(query: str = "헬리오시티 아파트 후기") -> None:
    try:
        r = httpx.get("https://www.youtube.com/results", params={"search_query": query},
                      headers=UA, timeout=10, follow_redirects=True)
        vids = list(dict.fromkeys(re.findall(r'"videoId":"([\w-]{11})"', r.text)))
        print(f"[search-scrape] status={r.status_code} uniq_videoIds={len(vids)} sample={vids[:3]}")
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[search-scrape] FAIL {type(exc).__name__}: {exc}")
        vids = []
    vid = vids[0] if vids else "dQw4w9WgXcQ"
    try:
        t = httpx.get("https://www.youtube.com/api/timedtext", params={"lang": "ko", "v": vid},
                      headers=UA, timeout=10)
        print(f"[timedtext ko v={vid}] status={t.status_code} len={len(t.text)} (len 0 = 자막없음/차단)")
    except httpx.HTTPError as exc:
        print(f"[timedtext] FAIL {type(exc).__name__}: {exc}")
    print("=> 판정: 자막 빈응답/검색취약 → DEFER(blog/cafe-only ship). source-agnostic이라 차후 끼움.")


if __name__ == "__main__":
    probe()
