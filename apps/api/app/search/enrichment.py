"""enrichment 뷰 공용 코어 — soft 속성(gym·pet…)을 후보에 부착하는 공통 부분.

설계 §7 카드: Tier-2 사실은 hard filter(repo) 밖, 후보 산출 **후** 부착하는 표시 관심사다.
query-time은 `enrich(stub_extractor)`로 **읽기 전용** read-through(hit=시드, miss=무결과→DB 불변).

속성별로 다른 것(Summary 모델·합성 규칙·카드 Row)은 각 모듈(gym.py·pet.py)이 갖고,
여기서는 공통(출처 pair · enrich+합성 루프)만 둔다. `_seedlib`의 일반화 선례와 동형 —
과추상 금지(caveats·confirm 같은 속성 고유 필드는 공유 코어로 끌어올리지 않는다).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.enrich.runner import Extractor, enrich, stub_extractor
from app.enrich.store import EnrichmentFact


class EnrichSource(BaseModel):
    """soft 사실 한 출처의 딥링크(출처 이동). http면 클릭, urn sentinel이면 비링크 라벨."""

    source_type: str
    source_url: str


def read_through_synth[T](
    conn: sqlite3.Connection,
    ids: Sequence[str],
    attribute: str,
    synthesize: Callable[[list[EnrichmentFact]], T],
    *,
    now: datetime,
    ttl: timedelta,
    extractor: Extractor = stub_extractor,
) -> dict[str, T]:
    """후보 id × 속성 → {id: Summary}. enrich(stub) read-through(읽기 전용) 후 속성별 합성.

    extractor 주입형 — 기본 stub(읽기 전용). live(키)에서 실추출기 주입 시 같은 경로가
    miss→lazy 실추출로 전환된다(API 불변). 무사실 id는 synthesize([])로 'none' 합성.
    """
    if not ids:
        return {}
    facts_by_id = enrich(conn, ids, attribute, extractor, ttl=ttl, now=now)
    return {cid: synthesize(facts_by_id.get(cid, [])) for cid in ids}
