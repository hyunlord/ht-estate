"""sqlite-vec(vec0) 로드 + review_chunk_vec 가상테이블 + KNN. (E3-2)

벡터 컬럼은 확장(vec0) 로드가 선행돼야 해 schema.sql(executescript) 밖에서 런타임 생성한다.
이 모듈을 통해서만 review_chunk_vec를 만들고/질의 → 확장 의존을 한 곳에 격리. sqlite_vec은
경량(torch-free) dep이라 apps/api 게이트 무오염. vec0는 카논 DB 기존 스키마와 무충돌(별 가상테이블).
"""

from __future__ import annotations

import sqlite3

import sqlite_vec

from app.embed.client import EMBED_DIM

VEC_TABLE = "review_chunk_vec"


def load_vec(conn: sqlite3.Connection) -> None:
    """conn에 sqlite-vec(vec0) 확장 로드(멱등). 로드 후 곧장 load_extension 재차단(보안)."""
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def ensure_vec_table(conn: sqlite3.Connection) -> None:
    """vec0 로드 + review_chunk_vec(chunk_id TEXT PK, emb float[DIM]) 생성(멱등)."""
    load_vec(conn)
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_TABLE} USING vec0("
        f"chunk_id TEXT PRIMARY KEY, emb float[{EMBED_DIM}])"
    )


def serialize(vector: list[float]) -> bytes:
    """float 리스트 → vec0 입력 바이트(little-endian float32)."""
    return sqlite_vec.serialize_float32(vector)


def knn(conn: sqlite3.Connection, query_vec: list[float], k: int) -> list[tuple[str, float]]:
    """쿼리 벡터 KNN → [(chunk_id, distance)] 거리 오름차순. conn은 vec 로드 선행 필요."""
    rows = conn.execute(
        f"SELECT chunk_id, distance FROM {VEC_TABLE} "
        "WHERE emb MATCH ? AND k = ? ORDER BY distance",
        (serialize(query_vec), k),
    ).fetchall()
    return [(r[0], float(r[1])) for r in rows]
