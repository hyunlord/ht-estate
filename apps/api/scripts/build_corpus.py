"""후기/평판 코퍼스 build CLI (E3-2) — 후보-한정 lazy build의 ops/스모크 드라이버.

단지 1건(complex_id 또는 name) → Naver blog/cafe fetch → 청킹 → :8092 embed → review_chunk(+vec)
write-back. C47 공유 `.ingest.lock`으로 cron과 직렬화. graceful(소스/embed 실패 defer).
키 필요(.env NAVER_*·EMBED_BASE_URL)라 키리스 게이트 밖(ops). 코어/테스트는 app/corpus/*.

    uv run python scripts/build_corpus.py --complex-id A12345 --force
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
from refill_kapt_fields import ShlockBatch

import app.settings  # noqa: F401  (.env 로딩)
from app.corpus.builder import build_corpus
from app.corpus.vec import ensure_vec_table
from app.embed.client import embed_client_from_env
from app.enrich.fetcher import naver_fetcher_from_env
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="build_corpus")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--complex-id", default=None, help="대상 단지 id(미지정이면 --name 필요)")
    p.add_argument("--name", default=None, help="소스 쿼리 단지명(미지정이면 DB서 조회)")
    p.add_argument("--ttl-weeks", type=int, default=3)
    p.add_argument("--max-chunks", type=int, default=40)
    p.add_argument("--force", action="store_true", help="신선해도 재build")
    p.add_argument("--lock", default=None, help="공유 락(기본 <db디렉토리>/.ingest.lock)")
    p.add_argument("--max-spin", type=float, default=60.0)
    args = p.parse_args(argv)

    fetcher = naver_fetcher_from_env()
    if fetcher is None:
        print("✗ NAVER_CLIENT_ID/SECRET 미설정 — 중단")
        return 1
    client = embed_client_from_env()

    conn = get_connection(args.db)
    init_db(conn)
    ensure_vec_table(conn)

    cid = args.complex_id
    name = args.name
    if cid and not name:
        row = conn.execute("SELECT name FROM complex WHERE complex_id = ?", (cid,)).fetchone()
        if row is None:
            print(f"✗ complex_id={cid} 없음")
            return 1
        name = row["name"]
    if not cid or not name:
        print("✗ --complex-id(또는 --name 동반) 필요")
        return 1

    lock_path = args.lock or str(Path(args.db).resolve().parent / ".ingest.lock")
    lock = ShlockBatch(lock_path, max_spin=args.max_spin)
    r = build_corpus(
        conn, cid, name, fetcher=fetcher, embed_client=client,
        now=datetime.now(UTC), ttl=timedelta(weeks=args.ttl_weeks),
        lock=lock, max_chunks=args.max_chunks, force=args.force,
    )
    print(f"[{r.status}] complex={cid} name={name!r} "
          f"docs={r.docs_fetched} chunks={r.chunks_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
