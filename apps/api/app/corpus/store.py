"""review_chunk + review_chunk_vec write-back / freshness / read. (E3-2)

review_chunk(메타+레시피핀)와 review_chunk_vec(벡터)를 **한 트랜잭션**으로 단지 단위 교체
(delete-by-complex → insert) → 멱등·반쪽쓰기 0. embed는 호출부(builder)가 write 전에 끝내
(EmbedUnavailable이면 여기 안 옴) write 단계엔 DB 오류만 → rollback이 양쪽 되돌림. 좌표/complex/
transaction 무접촉 → 지문/counts 불변.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.corpus.vec import VEC_TABLE, ensure_vec_table
from app.embed.client import EmbedRecipe


class ReviewChunk(BaseModel):
    """코퍼스 청크 1건 — 본문 + provenance(source_type·url·span_ref). 벡터는 별도(vec0)."""

    chunk_id: str
    complex_id: str
    chunk_text: str
    source_type: str
    source_url: str
    span_ref: str | None = None


@dataclass(frozen=True)
class PendingChunk:
    """write 전 청크 — chunk_id 미정(make_chunk_id가 결정). builder가 embed 직전에 만든다."""

    source_type: str
    source_url: str
    span_ref: str
    text: str


def make_chunk_id(complex_id: str, source_url: str, span_ref: str) -> str:
    """결정론 chunk_id = sha256(complex_id|source_url|span_ref)[:24] — 위치 기반 멱등 upsert."""
    raw = f"{complex_id}|{source_url}|{span_ref}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def is_fresh(
    conn: sqlite3.Connection, complex_id: str, recipe: EmbedRecipe, *, now: datetime
) -> bool:
    """현 레시피로 TTL-유효 청크가 1건 이상 있나(=rebuild 불요). 레시피 불일치/만료=stale→rebuild.

    모델변경 감지(레시피핀)와 신선도(TTL)를 한 술어로 — 둘 중 하나라도 어긋나면 재수집 대상.
    """
    row = conn.execute(
        "SELECT 1 FROM review_chunk WHERE complex_id = ? AND ttl_expires_at > ? "
        "AND embed_model = ? AND embed_dim = ? AND embed_normalized = ? LIMIT 1",
        (complex_id, now.isoformat(), recipe.embed_model, recipe.dim, recipe.normalized),
    ).fetchone()
    return row is not None


def _delete_complex(conn: sqlite3.Connection, complex_id: str) -> None:
    """단지의 기존 청크를 양쪽 테이블서 제거(rebuild 교체용). vec0는 cascade 없어 명시 삭제."""
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT chunk_id FROM review_chunk WHERE complex_id = ?", (complex_id,)
        )
    ]
    if ids:
        ph = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM {VEC_TABLE} WHERE chunk_id IN ({ph})", ids)
    conn.execute("DELETE FROM review_chunk WHERE complex_id = ?", (complex_id,))


def write_chunks(
    conn: sqlite3.Connection,
    complex_id: str,
    pending: Sequence[PendingChunk],
    vectors: Sequence[Sequence[float]],
    recipe: EmbedRecipe,
    *,
    now: datetime,
    ttl: timedelta,
) -> int:
    """단지 청크 교체 적재(review_chunk + vec) — 한 트랜잭션·멱등. 적재 청크 수 반환.

    pending과 vectors는 동일 순서·길이. embed는 호출부서 이미 완료(여기서 실패원 없음). 단지 단위
    delete→insert라 재실행 dup 0(같은 위치=같은 chunk_id 덮어씀, 사라진 소스는 제거).
    """
    if len(pending) != len(vectors):
        raise ValueError("pending/vectors 길이 불일치")
    from app.corpus.vec import serialize  # 지역 import: vec 의존 격리

    ensure_vec_table(conn)
    _delete_complex(conn, complex_id)
    ttl_at = (now + ttl).isoformat()
    for pc, vec in zip(pending, vectors, strict=True):
        cid = make_chunk_id(complex_id, pc.source_url, pc.span_ref)
        conn.execute(
            "INSERT OR REPLACE INTO review_chunk "
            "(chunk_id, complex_id, chunk_text, source_type, source_url, span_ref, "
            " fetched_at, ttl_expires_at, embed_model, embed_dim, embed_normalized) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, complex_id, pc.text, pc.source_type, pc.source_url, pc.span_ref,
             now.isoformat(), ttl_at, recipe.embed_model, recipe.dim, recipe.normalized),
        )
        conn.execute(
            f"INSERT INTO {VEC_TABLE}(chunk_id, emb) VALUES (?, ?)", (cid, serialize(list(vec)))
        )
    conn.commit()  # review_chunk + vec0 동시 커밋(반쪽쓰기 0)
    return len(pending)


def read_chunks(conn: sqlite3.Connection, complex_id: str) -> list[ReviewChunk]:
    """단지 청크 메타 읽기(카드/디버그용). 벡터는 vec0 KNN으로 별도 질의."""
    rows = conn.execute(
        "SELECT chunk_id, complex_id, chunk_text, source_type, source_url, span_ref "
        "FROM review_chunk WHERE complex_id = ? ORDER BY source_url, span_ref",
        (complex_id,),
    ).fetchall()
    return [
        ReviewChunk(
            chunk_id=r["chunk_id"], complex_id=r["complex_id"], chunk_text=r["chunk_text"],
            source_type=r["source_type"], source_url=r["source_url"], span_ref=r["span_ref"],
        )
        for r in rows
    ]


def chunk_count(conn: sqlite3.Connection, complex_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM review_chunk WHERE complex_id = ?", (complex_id,)
    ).fetchone()
    return int(row[0])
