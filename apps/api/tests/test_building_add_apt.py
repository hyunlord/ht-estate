"""거래-도출 아파트 건물(#6-③B-2a) — 도출/링크·K-apt 구분·도출불가 NULL·fuzzy 무접촉·서브셋 지문.

gated 백필: --apply 없이 write 0, --apply만 생성. 테스트는 인메모리(기존 K-apt/거래 무접촉).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

from app.store.db import get_connection, init_db
from app.store.nonapt_repo import (
    DerivedAptTrade,
    building_key,
    is_derivable_apt,
    upsert_apartment_building,
)

# scripts/를 import 경로에 — backfill·geocode_fingerprint(스크립트) 재사용.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from backfill_derived_apartments import run_backfill  # noqa: E402
from geocode_fingerprint import geocode_fingerprint  # noqa: E402


def _lock():
    """shlock 바이너리 없이 항상 획득하는 가짜 락(테스트용)."""
    from refill_kapt_fields import ShlockBatch

    return ShlockBatch(
        "/tmp/_test_bld_add.lock",
        runner=lambda *a, **k: SimpleNamespace(returncode=0),  # type: ignore[arg-type,return-value]
    )


def _trade(**kw) -> DerivedAptTrade:
    base = dict(name="래미안", legal_dong="역삼동", jibun="711-1", sgg_cd="11680", build_year=2010)
    base.update(kw)
    return DerivedAptTrade(**base)  # type: ignore[arg-type]


def _ins(conn, table, txn_id, *, name: str = "래미안", bjd: str | None = "1168010100",
         dong: str = "역삼동", jibun: str | None = "711-1",
         cid: str | None = None, conf: float | None = None):
    conn.execute(
        f'INSERT INTO "{table}" (txn_id, complex_id, match_confidence, apt_name_raw, '
        "bjd_code, legal_dong, jibun, build_year) VALUES (?,?,?,?,?,?,?,2010)",
        (txn_id, cid, conf, name, bjd, dong, jibun),
    )
    conn.commit()


def _row(conn, table, txn_id) -> sqlite3.Row:
    return conn.execute(
        f'SELECT apt_name_raw, bjd_code, legal_dong, jibun, build_year FROM "{table}" '
        "WHERE txn_id = ?",
        (txn_id,),
    ).fetchone()


def _cid(conn, table, txn_id):
    return conn.execute(
        f'SELECT complex_id, match_confidence FROM "{table}" WHERE txn_id = ?', (txn_id,)
    ).fetchone()


def test_building_key_apartment_prefix_distinguishes_from_kapt() -> None:
    # 도출 아파트 = 'ap:' 접두(K-apt 단지코드[콜론 없음]·ro:/of:와 구분).
    key = building_key(_trade())
    assert key.startswith("ap:11680:역삼동:")


def test_derive_idempotent_same_building() -> None:
    # 같은 건물 여러 거래(접미·연식 무관) → 같은 key.
    a = _trade(name="래미안아파트", build_year=2010)
    b = _trade(name="래미안", build_year=2011)
    assert building_key(a) == building_key(b)


def test_is_derivable_apt_requires_jibun_name_bjd() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _ins(conn, "transaction", "T_ok")
    _ins(conn, "transaction", "T_nojibun", jibun=None)
    _ins(conn, "transaction", "T_noname", name="")
    _ins(conn, "transaction", "T_nobjd", bjd=None)
    assert is_derivable_apt(_row(conn, "transaction", "T_ok")) is True
    assert is_derivable_apt(_row(conn, "transaction", "T_nojibun")) is False
    assert is_derivable_apt(_row(conn, "transaction", "T_noname")) is False
    assert is_derivable_apt(_row(conn, "transaction", "T_nobjd")) is False


def test_upsert_apartment_building_thin_geo_preserved() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    key = upsert_apartment_building(conn, _trade())
    row = conn.execute(
        "SELECT property_type, lat, lng, legal_addr FROM complex WHERE complex_id = ?", (key,)
    ).fetchone()
    assert row["property_type"] == "apartment"
    assert row["lat"] is None and row["lng"] is None  # geo 미포함 → NULL(B-2b 지오코딩 대상)
    assert "역삼동" in row["legal_addr"]
    assert upsert_apartment_building(conn, _trade()) == key  # 멱등
    n = conn.execute("SELECT count(*) FROM complex WHERE complex_id = ?", (key,)).fetchone()[0]
    assert n == 1


def test_backfill_links_orphans_skips_fuzzy_and_nonderivable() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _ins(conn, "transaction", "O1")  # orphan 도출가능
    _ins(conn, "transaction", "O2")  # 같은 건물(같은 키)
    conn.execute("INSERT INTO complex (complex_id, property_type) VALUES ('A12345','apartment')")
    _ins(conn, "transaction", "F1", name="은마", dong="대치동", jibun="316", cid="A12345", conf=0.9)
    _ins(conn, "transaction", "N1", name="모름", jibun=None)  # 도출불가

    res = run_backfill(conn, table="transaction", apply=True, limit=0, batch_size=10, lock=_lock())
    assert res["buildings"] == 1 and res["derivable"] == 2 and res["linked"] == 2
    assert res["nonderivable"] == 1
    o1, o2 = _cid(conn, "transaction", "O1"), _cid(conn, "transaction", "O2")
    assert o1["complex_id"].startswith("ap:") and o1["match_confidence"] == 1.0
    assert o1["complex_id"] == o2["complex_id"]  # 같은 건물
    f1 = _cid(conn, "transaction", "F1")
    assert f1["complex_id"] == "A12345" and f1["match_confidence"] == 0.9  # fuzzy 무접촉
    assert _cid(conn, "transaction", "N1")["complex_id"] is None  # 도출불가 NULL


def test_backfill_dry_run_writes_nothing() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _ins(conn, "transaction", "O1")
    before = conn.execute("SELECT count(*) FROM complex").fetchone()[0]
    res = run_backfill(conn, table="transaction", apply=False, limit=0, batch_size=10, lock=_lock())
    assert res["derivable"] == 1 and res["buildings"] == 1 and res["linked"] == 0
    after = conn.execute("SELECT count(*) FROM complex").fetchone()[0]
    assert after == before  # write 0
    assert _cid(conn, "transaction", "O1")["complex_id"] is None


def test_fingerprint_kapt_only_excludes_derived() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, property_type, lat, lng) VALUES "
        "('A10022731','apartment',37.5,127.0), "
        "('ap:11680:역삼동:711-1:래미안','apartment',NULL,NULL)"
    )
    conn.commit()
    _, n_all = geocode_fingerprint(conn)
    _, n_kapt = geocode_fingerprint(conn, kapt_only=True)
    assert n_all == 2 and n_kapt == 1  # kapt-only는 도출 'ap:' 제외
