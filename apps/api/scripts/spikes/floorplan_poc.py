"""SPIKE — 평면도 PoC (LH 15037046 → VLM feature → K-apt 조인). HARNESS R2: 학습용·throwaway.

세 미지수를 작은 표본으로 답하기 위한 *하네스*. 라이브 의존(데이터 키·네트워크·claude -p 비전)이라
키리스 샌드박스에선 parse+join 로직만 self-test로 검증, vision+download는 사용자(ops)가 실행.

  uv run python scripts/spikes/floorplan_poc.py --selftest    # 키리스 parse+join 검증
  DATA_GO_KR_API_KEY=... uv run python scripts/spikes/floorplan_poc.py --run --limit 5  # 라이브

라이브 --run: ① LH 평면도 인벤토리 N개 fetch → ② base64 이미지 디코드 → ③ claude -p 비전으로
feature 추출(객관만) → ④ K-apt complex 조인 시도 → ⑤ 표본 리포트(이미지↔추출 대조·조인 커버리지).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# apps/api를 sys.path에(scripts/spikes/ 2단계 깊이라 _bootstrap 대신 인라인).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.match.fuzzy import best_match, similarity
from app.match.normalize import extract_dong
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
FLOORPLAN_PROMPT = PROMPTS_DIR / "enrich_floorplan.md"

# data.go.kr 15037046 "LH 주택 평면도 현황" — JSON 이미지 레코드 필드(주문서 명세).
# 필드명은 데이터셋마다 흔들려 방어적으로 후보 키를 훑는다(라이브 확정 전 가정).
_IMG_DATA_KEYS = ("data", "image", "imageData", "img", "base64")
_NAME_KEYS = ("complexName", "단지명", "aptName", "bldgName", "name", "hsmpNm")
_ADDR_KEYS = ("address", "주소", "addr", "legalAddr", "rnAddr", "lotnoAddr")


def _first(d: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


# ───────────────────────── A. parse (키리스 검증 가능) ─────────────────────────


def parse_floorplan_record(rec: dict) -> dict | None:
    """LH 평면도 JSON 레코드 → {name, address, image_bytes, mime}. 디코드 실패/이미지 없으면 None.

    base64 이미지(`data`)를 bytes로 디코드. 단지명/주소는 후보 키에서 방어적으로 뽑는다.
    """
    b64 = _first(rec, _IMG_DATA_KEYS)
    if not b64:
        return None
    try:
        image_bytes = base64.b64decode(b64, validate=False)
    except (ValueError, TypeError):
        return None
    if not image_bytes:
        return None
    return {
        "name": _first(rec, _NAME_KEYS) or "",
        "address": _first(rec, _ADDR_KEYS) or "",
        "mime": _first(rec, ("mime", "mimeType", "contentType")) or "image/jpeg",
        "image_bytes": image_bytes,
    }


# ───────────────────────── B. VLM feature (라이브 — claude -p) ─────────────────────────


def extract_features(image_bytes: bytes, mime: str, *, max_turns: int = 8) -> dict | None:
    """평면도 이미지 → 객관 feature {bay, orientation, structure, evidence}. claude -p 비전(라이브).

    이미지를 임시파일로 쓰고 `claude -p`가 Read(비전)로 도면을 읽게 한다 — gym/pet/review와 같은
    키리스/구독 B0 경로. 점수화 금지(프롬프트 §11 가드). 출력 JSON 첫 객체를 반환, 실패 None.
    """
    prompt = FLOORPLAN_PROMPT.read_text(encoding="utf-8")
    ext = ".png" if "png" in mime else ".jpg"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
        tf.write(image_bytes)
        img_path = tf.name
    try:
        full = f"{prompt}\n\n## 분석할 평면도\n파일: {img_path}\nRead로 열어 feature 추출."
        proc = subprocess.run(
            ["claude", "-p", full, "--allowedTools", "Read", "--max-turns", str(max_turns)],
            capture_output=True, text=True, check=False,
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return None
    finally:
        os.unlink(img_path)


# ───────────────────────── C. K-apt 조인 (키리스 검증 가능) ─────────────────────────


def match_to_complex(
    conn: sqlite3.Connection, name: str, address: str, *, threshold: float = 0.85
) -> tuple[str, float] | None:
    """LH 평면도 단지명/주소 → complex 매칭(P2-4 fuzzy 재사용). (complex_id, score) 또는 None.

    주소에서 법정동을 뽑아 같은 동 complex로 좁힌 뒤 단지명 유사도. 동 못 뽑으면 전 complex 대상.
    억지매칭 금지(임계 미만 None) — P2-4 가드와 동형.
    """
    dong = extract_dong(address)
    if dong:
        rows = conn.execute(
            "SELECT complex_id, name FROM complex WHERE dong = ? OR legal_addr LIKE ?",
            (dong, f"%{dong}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT complex_id, name FROM complex").fetchall()
    candidates = [(r["complex_id"], r["name"]) for r in rows if r["name"]]
    return best_match(name, candidates, threshold=threshold)


# ───────────────────────── 라이브 fetch (key+network 필요) ─────────────────────────


def fetch_inventory(api_key: str, limit: int) -> list[dict]:
    """15037046 JSON 인벤토리 N개 fetch(라이브). 스키마 라이브 확정 전 — _http 재사용·방어 파싱."""
    from app.sources._http import fetch_text  # 지연 import — 키리스 selftest 경로 비의존

    BASE = "https://api.odcloud.kr/api/15037046/v1/uddi"  # 라이브에서 정확한 엔드포인트 확정 필요
    body = fetch_text(BASE, {"serviceKey": api_key, "page": 1, "perPage": limit})
    payload = json.loads(body)
    data = payload.get("data") if isinstance(payload, dict) else None
    return data[:limit] if isinstance(data, list) else []


# ───────────────────────── self-test (키리스) ─────────────────────────


def _selftest() -> int:
    """parse + join 로직 키리스 검증(라이브 의존 없음). 1x1 PNG·합성 complex로."""
    # A. parse — 1x1 투명 PNG base64
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
    )
    rec = {
        "단지명": "LH수서1단지", "주소": "서울특별시 강남구 수서동 750",
        "data": png_b64, "mime": "image/png",
    }
    parsed = parse_floorplan_record(rec)
    assert parsed is not None and parsed["name"] == "LH수서1단지", "parse 실패"
    assert parsed["image_bytes"][:4] == b"\x89PNG", "base64 디코드 실패"
    assert parse_floorplan_record({"단지명": "x"}) is None, "이미지 없으면 None이어야"
    print("✓ A. parse — base64 디코드 + 단지명/주소 추출 OK")

    # C. join — 합성 complex에 LH 단지명 매칭
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, dong, legal_addr) VALUES (?, ?, ?, ?)",
        [
            ("K1", "LH수서1단지", "수서동", "서울특별시 강남구 수서동 750"),
            ("K2", "수서신동아", "수서동", "서울특별시 강남구 수서동 740"),
        ],
    )
    conn.commit()
    # LH 평면도 단지명 '수서1단지' ⊂ complex 'LH수서1단지' → 포함부스트로 매칭(P2-4 fuzzy 재사용).
    m = match_to_complex(conn, "수서1단지", "서울특별시 강남구 수서동 750")
    assert m is not None and m[0] == "K1", f"조인 매칭 실패: {m}"
    # 억지매칭 금지: 동에 유사 단지 없으면 None
    none_m = match_to_complex(conn, "관악LH3단지", "서울특별시 관악구 신림동 100")
    assert none_m is None, "무관 단지는 None이어야(억지 금지)"
    fwd = similarity("수서1단지", "LH수서1단지")
    rev = similarity("LH수서1단지", "수서1단지")
    print(f"✓ C. join — '수서1단지'→K1(LH수서1단지) sim {fwd:.2f}, 무관→None OK")
    # FINDING(비대칭): LH-접두 방향(LH명이 더 긴 경우)은 포함부스트 미발동 → 임계 미달.
    print(f"  ⚠ 비대칭 finding: sim(LH수서1단지→수서1단지)={rev:.2f} (<0.85) — 정규화 확장 필요")
    print("\nself-test PASS — parse+join 로직 동작. vision+download는 라이브(--run, 키 필요).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="floorplan_poc", description="평면도 PoC 스파이크")
    p.add_argument("--selftest", action="store_true", help="키리스 parse+join 로직 검증")
    p.add_argument("--run", action="store_true", help="라이브 PoC(키+네트워크+claude -p 필요)")
    p.add_argument("--limit", type=int, default=5, help="표본 평면도 수")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = p.parse_args(argv)

    if args.selftest or not args.run:
        return _selftest()

    # 라이브 PoC — 학습용 표본 리포트(이미지↔추출 대조·조인 커버리지).
    # 키는 get_api_key()(load_dotenv→.env 인식). molit/kapt와 일관(P3-2 G — 생짜 os.environ 금지).
    from app.settings import MissingApiKeyError, get_api_key

    try:
        key = get_api_key()
    except MissingApiKeyError as exc:
        print(f"{exc} — 라이브 --run 불가(키리스는 --selftest).")
        return 2
    conn = get_connection(args.db)
    init_db(conn)
    records = fetch_inventory(key, args.limit)
    print(f"=== LH 평면도 표본 {len(records)}건 ===")
    matched = 0
    for i, rec in enumerate(records, 1):
        parsed = parse_floorplan_record(rec)
        if parsed is None:
            print(f"[{i}] parse 실패(이미지/디코드)")
            continue
        feats = extract_features(parsed["image_bytes"], parsed["mime"])
        join = match_to_complex(conn, parsed["name"], parsed["address"])
        if join:
            matched += 1
        print(f"[{i}] {parsed['name']} [{parsed['address']}]")
        print(f"     VLM: {feats}")
        print(f"     조인: {join}")
    print(f"\n조인 커버리지: {matched}/{len(records)}  (육안 대조로 feature 타당성 확인)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
