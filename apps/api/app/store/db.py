"""스토어 경계 — SQLite 연결 + 스키마 초기화.

이 모듈이 저장소 교체 경계다(원칙2: SQLite→Postgres 승급을 막지 말 것).
호출부는 raw 커넥션이 아니라 이 팩토리를 통해서만 DB에 닿는다 →
승급 시 여기만 교체(psycopg/SQLAlchemy 등)하면 된다. 스키마 DDL은
`schema.sql`에 1:1로 두고 여기서 실행만 한다.

이 티켓(T0-1)은 `init_db()`로 테이블을 "생성"하는 것까지. 실제 row 적재는 T0-2+.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# 기본 저장소: 단일 파일 SQLite (설계 §2 기본값). 경로는 호출 시 주입 가능.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "ht-estate.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """커넥션 1개 반환. 외래키 강제 ON, row를 이름으로 접근 가능하게 설정.

    `:memory:`를 넘기면 인메모리 DB(테스트용).
    """
    # 파일 DB면 부모 디렉터리를 보장(러너가 data/ 없이 첫 적재할 때 대비). :memory:는 skip.
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI sync 엔드포인트는 스레드풀에서 도므로 커넥션이
    # 생성 스레드와 다른 스레드에서 쓰일 수 있다(요청당 단일 사용이라 안전).
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# P4-1 additive 마이그레이션 — 기존 DB의 complex 테이블에 풀필드 컬럼을 더한다.
# schema.sql의 CREATE TABLE은 `IF NOT EXISTS`라 **이미 있는** 테이블엔 새 컬럼이 적용되지 않는다.
# → PRAGMA introspection으로 빠진 컬럼만 ALTER ADD COLUMN(nullable). 신규 DB는 CREATE가 이미
# 만들어 no-op. 멱등(빠진 것만)·additive(기존 컬럼/인덱스/데이터 불변)·resume-safe
# (ADD COLUMN nullable은 SQLite 메타데이터 연산 — 행 재작성 없음 → 실행 중 적재 루프 디스럽트 최소).
_COMPLEX_ADD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("heat_type", "TEXT"), ("sale_type", "TEXT"), ("mgmt_type", "TEXT"),
    ("dong_count", "INTEGER"), ("top_floor", "INTEGER"),
    ("priv_area", "REAL"), ("mgmt_area", "REAL"),
    ("builder", "TEXT"), ("developer", "TEXT"),
    ("mgmt_staff", "INTEGER"),
    ("security_type", "TEXT"), ("security_staff", "INTEGER"),
    ("cleaning_type", "TEXT"), ("cleaning_staff", "INTEGER"),
    ("disinfection_type", "TEXT"), ("disinfection_staff", "INTEGER"),
    ("disinfection_method", "TEXT"),
    ("garbage_type", "TEXT"), ("water_supply", "TEXT"),
    ("electricity_contract", "TEXT"), ("fire_alarm", "TEXT"), ("internet", "TEXT"),
    ("elevator_count", "INTEGER"), ("cctv_count", "INTEGER"),
    ("subway_line", "TEXT"), ("subway_station", "TEXT"),
    ("subway_time", "TEXT"), ("bus_time", "TEXT"),
    ("convenient_facility_raw", "TEXT"), ("education_facility_raw", "TEXT"),
    ("has_daycare", "BOOLEAN"), ("has_playground", "BOOLEAN"),
    ("has_senior_center", "BOOLEAN"), ("has_library", "BOOLEAN"),
    ("property_type", "TEXT"),  # P5-1: 주택유형(비-아파트). 기존 K-apt 행은 init_db가 백필.
)


def _add_missing_columns(
    conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]
) -> None:
    """table에 없는 컬럼만 ALTER ADD COLUMN(nullable). 멱등 — 이미 있으면 skip."""
    existing = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    for name, decl in columns:
        if name not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {name} {decl}')


def init_db(conn: sqlite3.Connection) -> None:
    """`schema.sql`을 실행해 canonical 테이블 생성 + additive 컬럼 마이그레이션 적용.

    멱등(`CREATE TABLE IF NOT EXISTS` + 빠진 컬럼만 ADD). 반복 호출 안전. 기존 DB도
    init_db 호출만으로 P4-1 풀필드 컬럼이 backfill-ready(nullable) 상태가 된다.
    """
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(ddl)
    _add_missing_columns(conn, "complex", _COMPLEX_ADD_COLUMNS)  # P4-1: 기존 DB 풀필드 컬럼 보강
    # P5-1: 기존 complex(전부 K-apt 아파트)는 property_type NULL → apartment 백필. 멱등(NULL만).
    # 비-아파트 행은 적재 시 명시 type으로 들어와 NULL이 아니므로 영향 없음.
    conn.execute("UPDATE complex SET property_type = 'apartment' WHERE property_type IS NULL")
    conn.commit()
