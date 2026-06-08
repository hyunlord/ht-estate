"""실추출 공용 파이프라인 (E1) — fetch → provider-LLM → 규율강제 parse → facts.

graceful-degrade(설계 §6·crash 금지): fetch 무결과/실패 → [](miss·defer) · provider 다운
(ProviderError) → [](defer) · LLM malformed → [](defer). 모두 다음 호출 재시도(429 패턴 동형).

규율(auto_enrich 선례): **환각 출처 drop**(LLM이 낸 source_url이 실제 fetch 문서에 없으면 버림)·
차단도메인 drop·상태 도메인 강제·confidence clamp[0,1]. 속성 고유(상태셋·value 빌더)는 호출부.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from app.enrich.fetcher import SourceDoc, SourceFetcher
from app.enrich.provider import LLMProvider, ProviderError
from app.enrich.store import EnrichmentFact

# 차단 도메인 — 집계/스팸/비신뢰 소스(출처로 부적합). 보수적 최소셋(확장은 config 후속).
_BLOCKED_DOMAINS = ("translate.google", "webcache.googleusercontent", "facebook.com/tr")


def _blocked(url: str) -> bool:
    return any(b in url for b in _BLOCKED_DOMAINS)


def clamp_confidence(raw: object) -> float:
    """confidence를 [0,1]로. 파싱불가/누락은 0.5(중립 보수)."""
    try:
        return max(0.0, min(1.0, float(raw)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5


def build_user_prompt(name: str, docs: Sequence[SourceDoc]) -> str:
    """단지명 + 소스(출처별 url+본문)를 LLM 입력으로. 출처별 JSON 판정 요구(다출처 보존)."""
    blocks = [f"[{i}] source_url={d.source_url}\n{d.text}" for i, d in enumerate(docs)]
    sources = "\n\n".join(blocks)
    return (
        f"단지명: {name}\n\n아래 소스들을 근거로 판정하라. **소스에 없는 내용은 추측 금지**.\n"
        f"각 소스(source_url)별로 한 항목씩, 위 시스템 지시의 JSON 배열로만 답하라.\n\n{sources}"
    )


def run_extraction(
    name: str | None,
    *,
    queries: Sequence[str],
    kind: str,
    fetcher: SourceFetcher,
    provider: LLMProvider,
    system: str,
    parse: Callable[[str, dict[str, SourceDoc]], list[EnrichmentFact]],
) -> list[EnrichmentFact]:
    """공용 추출: 소스 fetch → provider-LLM → parse(규율). 어느 단계든 실패/무결과면 [](defer)."""
    if not name:
        return []
    docs: list[SourceDoc] = []
    for q in queries:
        try:
            docs.extend(fetcher.fetch(q, kind=kind))
        except Exception:  # noqa: BLE001 — 페처 실패는 graceful(이 쿼리만 skip, defer)
            continue
    docs = [d for d in docs if d.source_url and not _blocked(d.source_url)]
    if not docs:
        return []  # 소스 없음 → miss(다음 호출 재시도)
    by_url = {d.source_url: d for d in docs}
    try:
        raw = provider.complete(system, build_user_prompt(name, docs))
    except ProviderError:
        return []  # provider 다운/레이트리밋 → defer(crash 금지)
    return parse(raw, by_url)


def parse_items(raw: str, by_url: dict[str, SourceDoc]) -> list[dict]:
    """LLM 응답(JSON 배열/객체) → 항목 리스트 + **환각 출처 drop**. malformed면 []."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    out: list[dict] = []
    for it in items:
        if isinstance(it, dict) and it.get("source_url") in by_url:  # 환각 출처 drop
            out.append(it)
    return out
