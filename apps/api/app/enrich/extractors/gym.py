"""gym 추출기 — 첫 실 enrichment 속성 (R1 정합).

파이프라인: cid → 단지(name·road_addr) 조회 → 웹검색(주입 search_fn) → 공개페이지
fetch(주입 fetch_fn) → LLM 추출(주입 llm_fn, 단지내 vs 인근 상업 헬스장 구별) →
EnrichmentFact[]. 의존성 전부 주입형이라 mock으로 키리스 게이트.

R1 핵심:
- **단지 내 입주민 피트니스 vs 인근 상업 헬스장 구별**(LLM 프롬프트가 명시, in_complex 플래그).
- no-signal = UNKNOWN(demote-not-exclude), NO 아님. unknown도 저confidence fact로 write(음수 캐싱).
- IP/legal: 공개페이지만·본문 재현 금지(요지만)·네이버/호갱노노/아실 미스크레이프 금지
  (search.is_blocked).
- confidence = 출처품질 × LLM confidence.
"""

from __future__ import annotations

import json
import sqlite3

from app.enrich.llm import LlmFn
from app.enrich.search import FetchFn, SearchFn, is_blocked, source_type_and_weight
from app.enrich.store import EnrichmentFact

MAX_SOURCES = 3  # 후보 페이지 상한(레이트·비용)

_SYSTEM = """\
너는 한국 아파트 단지의 '단지 내 입주민 전용 헬스장/피트니스/주민운동시설' 유무만 판정한다.

⚠️ 핵심 구별 (R1):
- 판정 대상은 **단지 내부에 입주민이 쓰는 헬스/피트니스 시설**이다.
- **인근 상업 헬스장·구민체육센터·다른 단지 시설은 NO로 본다**(단지 내부가 아니므로).
- 본문이 단지 내부 시설인지 인근 상업시설인지 모호하면 in_complex=false로 보수적으로.

출력 규칙:
- has_gym: 단지 내 피트니스가 있으면 'yes', 없다고 명확하면 'no',
  신호가 없거나 불확실하면 'unknown'.
- in_complex: 근거가 단지 내부 시설을 가리키면 true,
  인근 상업/타단지/불명확이면 false.
- evidence: 근거의 **요지만** 한 문장으로(원문 복붙 금지).
- confidence: 0~1. 단정 어려우면 낮게.
신호 없음은 'no'가 아니라 'unknown'이다."""


class GymExtractor:
    """P1-1 Extractor — (complex_id, 'gym') → EnrichmentFact[]. 의존성 주입형."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        search_fn: SearchFn,
        fetch_fn: FetchFn,
        llm_fn: LlmFn,
        *,
        max_sources: int = MAX_SOURCES,
    ) -> None:
        self._conn = conn
        self._search = search_fn
        self._fetch = fetch_fn
        self._llm = llm_fn
        self._max_sources = max_sources

    def __call__(self, complex_id: str, attribute: str, /) -> list[EnrichmentFact]:
        if attribute != "gym":
            return []
        row = self._conn.execute(
            "SELECT name, road_addr FROM complex WHERE complex_id = ?", (complex_id,)
        ).fetchone()
        if row is None or not row["name"]:
            return []
        name = row["name"]
        query = f"{name} 단지 내 헬스장 피트니스 운동시설"

        facts: list[EnrichmentFact] = []
        for result in self._search(query)[: self._max_sources]:
            if is_blocked(result.url):  # R1 legal — 네이버/호갱노노/아실 미스크레이프
                continue
            body = self._fetch(result.url)
            if not body:
                continue
            user = f"단지명: {name}\n주소: {row['road_addr'] or '미상'}\n\n페이지 본문:\n{body}"
            try:
                extracted = self._llm(_SYSTEM, user)
            except Exception:
                continue  # LLM 실패는 그 출처만 skip(graceful)
            fact = self._to_fact(extracted, result)
            if fact is not None:
                facts.append(fact)

        # 신호가 하나도 없으면 UNKNOWN sentinel(음수 캐싱 — demote-not-exclude, R1)
        if not facts:
            facts.append(
                EnrichmentFact(
                    value=json.dumps({"has_gym": "unknown", "evidence": "신호 없음"}),
                    confidence=0.1,
                    source_type="none",
                    source_url=f"urn:ht-estate:gym:no-signal:{complex_id}",
                )
            )
        return facts

    def _to_fact(self, extracted: dict[str, object], result: object) -> EnrichmentFact | None:
        has_gym = extracted.get("has_gym")
        if has_gym not in ("yes", "no", "unknown"):
            return None
        in_complex = bool(extracted.get("in_complex"))
        # 단지 내부 시설이 아니면(인근 상업 등) gym 신호로 인정하지 않고 unknown으로 강등
        verdict = has_gym if (has_gym != "yes" or in_complex) else "unknown"
        raw_conf = extracted.get("confidence", 0.0)
        try:
            llm_conf = float(raw_conf) if isinstance(raw_conf, (int, float, str)) else 0.0
        except ValueError:
            llm_conf = 0.0
        kind = getattr(result, "source_kind", "web")
        source_type, weight = source_type_and_weight(kind)
        return EnrichmentFact(
            value=json.dumps(
                {"has_gym": verdict, "evidence": str(extracted.get("evidence", ""))[:300]},
                ensure_ascii=False,
            ),
            confidence=round(weight * llm_conf, 3),  # 출처품질 × LLM
            source_type=source_type,
            source_url=getattr(result, "url", ""),
        )
