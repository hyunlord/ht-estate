"""후기/평판 코퍼스 build CLI (E3-2 + rag-corpus-quality) — 단건 스모크 + bulk 적재 드라이버.

단건: complex_id/name → Naver blog/cafe fetch → 건물검증+노이즈필터 → 청킹 → :8092 embed →
review_chunk(+vec) write-back. C47 공유 `.ingest.lock`으로 직렬화. graceful(소스/embed defer).

bulk(--bulk): on-demand-only 탈출. 거래 많은(=조회 가능성 큰) 단지부터 멀티데이 누적 적재.
  · 멱등: is_fresh skip(재fetch 0) + ingest_progress(stage='corpus_bulk') 처리완료 skip(resume).
  · 쿼터-aware: Naver 429 → fetcher.quota_blocked 폴링 → 우아중단(다음 run resume·완료분 보존).
  · 건물검증+노이즈 룰 + (env에 ENRICH_LLM 있으면) gemma doc 분류기로 경계 precision.
  · review_chunk/_vec만 write(좌표/canonical 무접촉 → 지문/counts 불변). 재적재=그 단지 청크만 교체.
키 필요(.env NAVER_*·EMBED_BASE_URL[·ENRICH_LLM_*])라 키리스 게이트 밖. 코어/테스트는 app/corpus/*.

    uv run python scripts/build_corpus.py --complex-id A12345 --force    # 단건 스모크
    uv run python scripts/build_corpus.py --bulk --limit 300 --min-txn 5  # bulk 적재
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
from refill_kapt_fields import DEFAULT_INTER_BATCH_SLEEP, ShlockBatch, _chunks

import app.settings  # noqa: F401  (.env 로딩)
from app.corpus.builder import (
    BUILT,
    EMBED_DEFERRED,
    FRESH,
    LOCK_YIELD,
    NO_RELEVANT,
    NO_SOURCE,
    build_corpus,
)
from app.corpus.relevance import make_doc_classifier
from app.corpus.vec import ensure_vec_table
from app.embed.client import embed_client_from_env
from app.enrich.fetcher import naver_fetcher_from_env
from app.enrich.provider import LLMProvider, provider_from_env
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.store.pipeline_state import bootstrap_pipeline_state_safe
from app.store.progress_repo import record_month

CORPUS_STAGE = "corpus_bulk"
_CORPUS_MONTH = "-"
# 재fetch 안 하는 terminal(처리완료 → resume skip). EMBED_DEFERRED/LOCK_YIELD/quota는 재시도.
_TERMINAL = frozenset({BUILT, FRESH, NO_SOURCE, NO_RELEVANT})
_DEFER = frozenset({EMBED_DEFERRED, LOCK_YIELD})


def bulk_targets(
    conn: sqlite3.Connection, *, min_txn: int
) -> list[tuple[str, str, str]]:
    """조회 가능성 큰 단지부터 — 거래수 ≥ min_txn·이름 보유. (cid, name, region_label)."""
    rows = conn.execute(
        """
        SELECT c.complex_id, c.name,
               COALESCE(c.sigungu,'') || ' ' || COALESCE(c.dong,'') AS region,
               (SELECT COUNT(*) FROM "transaction" t WHERE t.complex_id = c.complex_id)
               + (SELECT COUNT(*) FROM rent_transaction r WHERE r.complex_id = c.complex_id)
                 AS deals
        FROM complex c
        WHERE c.name IS NOT NULL AND c.name != ''
        GROUP BY c.complex_id
        HAVING deals >= ?
        ORDER BY deals DESC
        """,
        (min_txn,),
    ).fetchall()
    return [(r["complex_id"], r["name"], (r["region"] or "").strip()) for r in rows]


def done_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT region FROM ingest_progress WHERE stage = ?", (CORPUS_STAGE,)
    ).fetchall()
    return {r[0] for r in rows}


def reset_stale_progress(conn: sqlite3.Connection) -> int:
    """게이트 변경 후 재시도용 — '기록은 됐으나 청크 0'(NO_RELEVANT/NO_SOURCE)인 corpus_bulk
    진입분만 ledger서 삭제 → 다음 bulk서 새 게이트로 재시도. **청크 있는(BUILT) 단지는 보존**
    (재fetch 0·쿼터 절약). ingest_progress만 삭제(review_chunk/_vec·canonical 무접촉). 삭제 수 반환.
    """
    cur = conn.execute(
        "DELETE FROM ingest_progress WHERE stage = ? "
        "AND region NOT IN (SELECT DISTINCT complex_id FROM review_chunk)",
        (CORPUS_STAGE,),
    )
    conn.commit()
    return cur.rowcount


def run_bulk(
    conn: sqlite3.Connection,
    *,
    fetcher,  # type: ignore[no-untyped-def]
    embed_client,  # type: ignore[no-untyped-def]
    provider: LLMProvider | None,
    lock: Callable[[], object],
    limit: int,
    min_txn: int,
    batch_size: int,
    ttl: timedelta,
    max_chunks: int,
    interval: float = 0.2,
    inter_batch_sleep: float = DEFAULT_INTER_BATCH_SLEEP,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
) -> Counter[str]:
    """거래순 단지 배치 build — 건물검증+노이즈+gemma 필터·멱등·쿼터 우아중단·resume."""
    done = done_ids(conn)
    targets = [(c, n, r) for (c, n, r) in bulk_targets(conn, min_txn=min_txn) if c not in done]
    if limit > 0:
        targets = targets[:limit]
    if log is not None:
        log(f"corpus-bulk 대상 {len(targets)}건 (완료 {len(done)} skip, min_txn={min_txn})")
    now = datetime.now(UTC)
    counts: Counter[str] = Counter()
    for batch in _chunks(targets, batch_size):
        with lock() as acquired:  # type: ignore[attr-defined]
            if not acquired:
                if log is not None:
                    log("공유락 점유 중(cron) — 이번 run 양보, 다음 run 재개")
                break
            for cid, name, region in batch:
                if interval > 0:
                    sleep(interval)
                clf = make_doc_classifier(provider, name, region)
                r = build_corpus(
                    conn, cid, name, fetcher=fetcher, embed_client=embed_client,
                    now=now, ttl=ttl, max_chunks=max_chunks, classifier=clf,
                )
                counts[r.status] += 1
                if getattr(fetcher, "quota_blocked", False):
                    if log is not None:
                        log("Naver 쿼터 차단(429) — 우아중단(다음 run resume·완료분 보존)")
                    conn.commit()
                    return counts
                if r.status in _TERMINAL:
                    record_month(conn, CORPUS_STAGE, cid, _CORPUS_MONTH, 1)  # resume skip
                # _DEFER(embed down/락)·기타는 미기록 → 다음 run 재시도
            conn.commit()
        if inter_batch_sleep > 0:
            sleep(inter_batch_sleep)
        if log is not None and counts:
            log(f"  …처리 {sum(counts.values())}건 (built={counts[BUILT]} "
                f"no_relevant={counts[NO_RELEVANT]} no_source={counts[NO_SOURCE]})")
    if log is not None:
        log(f"corpus-bulk 완료 — 이번 run {sum(counts.values())}건: "
            f"built={counts[BUILT]} fresh={counts[FRESH]} "
            f"no_relevant={counts[NO_RELEVANT]} no_source={counts[NO_SOURCE]} "
            f"embed_deferred={counts[EMBED_DEFERRED]}")
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="build_corpus")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--complex-id", default=None, help="단건: 대상 단지 id(미지정이면 --name 필요)")
    p.add_argument("--name", default=None, help="단건: 소스 쿼리 단지명(미지정이면 DB서 조회)")
    p.add_argument("--bulk", action="store_true", help="bulk 적재(거래순 단지·멀티데이 누적)")
    p.add_argument("--limit", type=int, default=0, help="bulk: 이번 run 최대 단지수(0=무제한)")
    p.add_argument("--min-txn", type=int, default=3, help="bulk: 최소 거래수(매매+전월세) 필터")
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--interval", type=float, default=0.2, help="bulk: 콜 간 sleep(쿼터 throttle)")
    p.add_argument("--inter-batch-sleep", type=float, default=DEFAULT_INTER_BATCH_SLEEP)
    p.add_argument("--ttl-weeks", type=int, default=3)
    p.add_argument("--max-chunks", type=int, default=40)
    p.add_argument("--force", action="store_true", help="단건: 신선해도 재build")
    p.add_argument("--reset-stale", action="store_true",
                   help="bulk: 청크 0인 corpus_bulk ledger 삭제(게이트 변경 후 재시도)")
    p.add_argument("--lock", default=None, help="공유 락(기본 <db디렉토리>/.ingest.lock)")
    p.add_argument("--max-spin", type=float, default=60.0)
    args = p.parse_args(argv)

    fetcher = naver_fetcher_from_env()
    if fetcher is None:
        print("✗ NAVER_CLIENT_ID/SECRET 미설정 — 중단")
        return 1
    client = embed_client_from_env()
    provider = provider_from_env()  # ENRICH_LLM_* 있으면 gemma 분류기, 없으면 룰만

    conn = get_connection(args.db)
    init_db(conn)
    ensure_vec_table(conn)
    lock_path = args.lock or str(Path(args.db).resolve().parent / ".ingest.lock")
    lock = ShlockBatch(lock_path, max_spin=args.max_spin)
    ttl = timedelta(weeks=args.ttl_weeks)

    if args.bulk:
        if args.reset_stale:
            n = reset_stale_progress(conn)
            print(f"[reset-stale] 청크 0인 corpus_bulk ledger {n}건 삭제 → 새 게이트로 재시도")
        run_bulk(
            conn, fetcher=fetcher, embed_client=client, provider=provider, lock=lock,
            limit=args.limit, min_txn=args.min_txn, batch_size=args.batch_size,
            ttl=ttl, max_chunks=args.max_chunks, interval=args.interval,
            inter_batch_sleep=args.inter_batch_sleep, log=print,
        )
        bootstrap_pipeline_state_safe(conn)
        return 0

    # ── 단건 스모크 ──
    cid = args.complex_id
    name = args.name
    if cid and not name:
        row = conn.execute("SELECT name FROM complex WHERE complex_id = ?", (cid,)).fetchone()
        if row is None:
            print(f"✗ complex_id={cid} 없음")
            return 1
        name = row["name"]
    if not cid or not name:
        print("✗ --complex-id(또는 --name 동반) 또는 --bulk 필요")
        return 1

    region_row = conn.execute(
        "SELECT COALESCE(sigungu,'')||' '||COALESCE(dong,'') AS region FROM complex "
        "WHERE complex_id = ?", (cid,)).fetchone()
    region = (region_row["region"] or "").strip() if region_row else ""
    r = build_corpus(
        conn, cid, name, fetcher=fetcher, embed_client=client,
        now=datetime.now(UTC), ttl=ttl, lock=lock, max_chunks=args.max_chunks,
        force=args.force, classifier=make_doc_classifier(provider, name, region),
    )
    print(f"[{r.status}] complex={cid} name={name!r} "
          f"docs={r.docs_fetched} chunks={r.chunks_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
