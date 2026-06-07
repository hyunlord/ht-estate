"""법정동코드 정적 참조 생성기 (enrich-1, **빌드 도구** — 런타임 아님).

건축물대장(BldRgstHubService)은 `bjdongCd`(법정동 5자리)로 조회하는데, 우리 비-아파트 건물은
법정동 *이름*(umdNm)만 있고 코드가 없다. 이 일회성 스크립트가 비-아파트 건물의 distinct
(sgg_cd, 법정동명) 쌍을 Kakao 주소검색의 `b_code`(법정동코드 10자리)로 해소해
`data/regions/bjdong_kr.csv`(sgg_cd, legal_dong, bjdong_cd)로 굳힌다.

규율:
- **빌드 타임만 Kakao** — 산출 CSV는 런타임에 키 없이 읽는 정적 참조(키리스 게이트 안전).
- **읽기전용** — 메인 DB에서 쌍만 읽고, 건물/거래/좌표 무접촉. CSV만 쓴다.
- 결정론 — 쌍을 정렬해 처리(재실행 시 안정적 순서). rate-bound(--interval).

    uv run python scripts/gen_bjdong_ref.py            # 전체(메인 DB)
    uv run python scripts/gen_bjdong_ref.py --limit 50 # 스모크
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import time
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
import httpx

from app.settings import get_kakao_key
from app.store.db import DEFAULT_DB_PATH, get_connection
from app.store.regions import sigungu_label

OUT_CSV = Path(__file__).resolve().parents[1] / "data" / "regions" / "bjdong_kr.csv"
KAKAO_ADDR_URL = "https://dapi.kakao.com/v2/local/search/address.json"


def distinct_pairs(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """비-아파트 건물의 distinct (sgg_cd, 법정동명) — complex_id(PNU식)에서 파싱, 정렬."""
    rows = conn.execute(
        "SELECT DISTINCT complex_id FROM complex WHERE property_type IN ('rowhouse','officetel')"
    ).fetchall()
    pairs: set[tuple[str, str]] = set()
    for (cid,) in rows:
        parts = cid.split(":")
        if len(parts) >= 5 and parts[1] and parts[1] != "?" and parts[2]:
            pairs.add((parts[1], parts[2]))
    return sorted(pairs)


def resolve_bjdong(
    sgg_cd: str, legal_dong: str, key: str, *, client: httpx.Client
) -> str | None:
    """(sgg_cd, 법정동명) → bjdongCd(5자리). Kakao b_code[5:10]. 무결과면 None(부분커버리지)."""
    label = sigungu_label(sgg_cd) or ""
    query = f"{label} {legal_dong}".strip()
    resp = client.get(
        KAKAO_ADDR_URL, params={"query": query}, headers={"Authorization": f"KakaoAK {key}"}
    )
    resp.raise_for_status()
    docs = resp.json().get("documents", [])
    for doc in docs:
        addr = doc.get("address") or {}
        b_code = addr.get("b_code")
        # sgg 일치(앞 5자리) 확인 — 동명 전국중복 오해소 방지
        if b_code and len(b_code) == 10 and b_code[:5] == sgg_cd:
            return b_code[5:10]
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--interval", type=float, default=0.05)
    args = ap.parse_args()

    key = get_kakao_key()
    conn = get_connection(args.db)
    pairs = distinct_pairs(conn)
    if args.limit:
        pairs = pairs[: args.limit]
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    resolved = 0
    with httpx.Client(timeout=15) as client, OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sgg_cd", "legal_dong", "bjdong_cd"])
        for i, (sgg_cd, legal_dong) in enumerate(pairs):
            try:
                bjdong = resolve_bjdong(sgg_cd, legal_dong, key, client=client)
            except (httpx.HTTPError, ValueError):
                bjdong = None
            if bjdong:
                writer.writerow([sgg_cd, legal_dong, bjdong])
                resolved += 1
            if (i + 1) % 200 == 0:
                fh.flush()
                print(f"  {i + 1}/{len(pairs)} 처리 · {resolved} 해소", flush=True)
            time.sleep(args.interval)
    print(f"[done] {len(pairs)} 쌍 중 {resolved} 해소 → {OUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
