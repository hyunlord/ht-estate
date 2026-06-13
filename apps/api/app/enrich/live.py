"""lazy 실추출 라이브 와이어링 seam (E1) — config(provider)에서 gym/pet 실추출기 구성.

**활성화 지점**: `_run_search`가 후보 산출 후 `live_extractors(conn, ids)`를 받아 None이 아니면
`attach_gym/attach_pet(..., extractor=...)`로 주입하면 lazy 실추출이 켜진다. provider 미구성
(env 없음)이면 None → 기존 stub(읽기전용) 유지 → **키리스 게이트·현 동작 불변**.

스레드 안전: 후보 단지명을 **메인 스레드에서 미리 해소**(name_resolver)해 추출기 클로저에 넘긴다
(runner.enrich가 추출기를 병렬 호출 — 스레드에서 conn 미접근). live 활성화(env 세팅·실 fetcher
주입·추출 run)는 ops/config 소관(E1 범위 밖) — 이 모듈은 그 seam만 제공한다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence

from app.corpus.relevance import region_tokens
from app.enrich.extractors.doc_verify import DocTarget
from app.enrich.extractors.gym import make_gym_extractor
from app.enrich.extractors.gym_verify import make_gym_verify_extractor
from app.enrich.extractors.pet import make_pet_extractor
from app.enrich.extractors.pet_verify import make_pet_verify_extractor
from app.enrich.fetcher import NullFetcher, SourceFetcher
from app.enrich.provider import LLMProvider, provider_from_env
from app.enrich.runner import Extractor


def name_resolver(conn: sqlite3.Connection, ids: Sequence[str]) -> Callable[[str], str | None]:
    """후보 complex_id → 단지명 사전해소(메인 스레드 1회 read). 추출기 클로저가 스레드안전 사용."""
    if not ids:
        return lambda _cid: None
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT complex_id, name FROM complex WHERE complex_id IN ({placeholders})", list(ids)
    ).fetchall()
    names = {r["complex_id"]: r["name"] for r in rows}
    return names.get


def doc_target_resolver(
    conn: sqlite3.Connection, ids: Sequence[str]
) -> Callable[[str], DocTarget]:
    """doc 검증 추출기 공용 사전해소 — cid → (name, region_label, region_tokens). C86 게이트 입력.

    메인 스레드 1회 read(스레드안전). region은 sigungu/dong(건물검증 게이트 + gemma 검증 프롬프트).
    gym_verified·pet_verified 등 구조화 검증 인스턴스가 공유(thin config는 쿼리/프롬프트만 다름).
    """
    if not ids:
        return lambda _cid: (None, "", [])
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT complex_id, name, sigungu, dong FROM complex WHERE complex_id IN ({placeholders})",
        list(ids),
    ).fetchall()
    by_id = {
        r["complex_id"]: (
            r["name"],
            f"{r['sigungu'] or ''} {r['dong'] or ''}".strip(),
            region_tokens(r["sigungu"], r["dong"]),
        )
        for r in rows
    }
    return lambda cid: by_id.get(cid, (None, "", []))


def live_extractors(
    conn: sqlite3.Connection,
    ids: Sequence[str],
    *,
    provider: LLMProvider | None = None,
    fetcher: SourceFetcher | None = None,
) -> dict[str, Extractor] | None:
    """후보 한정 live 추출기 {'gym':…, 'pet':…} 또는 None(provider 미구성 → stub 유지).

    provider 기본=env(provider_from_env). fetcher 기본=NullFetcher(라이브 검색은 flagged config).
    테스트는 provider·fetcher mock 주입. **후보 한정**(ids만) — 대량 추출 아님.
    """
    provider = provider or provider_from_env()
    if provider is None:
        return None  # 미구성 → 호출부가 stub 유지(현 동작·게이트 불변)
    fetcher = fetcher or NullFetcher()
    name_of = name_resolver(conn, ids)
    resolve = doc_target_resolver(conn, ids)  # gym_verified·pet_verified 공용(name+region)
    return {
        "gym": make_gym_extractor(provider, fetcher, name_of),
        # 구조화 검증(gym-evidence/pet-evidence): doc 교차검증(C86 게이트+gemma) → '<attr>_verified'
        # (web_verified). 별도 속성이라 Kakao/시드가 'gym'/'pet'에 있어도 doc 검증을 단락 안 함.
        "gym_verified": make_gym_verify_extractor(provider, fetcher, resolve),
        "pet": make_pet_extractor(provider, fetcher, name_of),
        "pet_verified": make_pet_verify_extractor(provider, fetcher, resolve),
    }
