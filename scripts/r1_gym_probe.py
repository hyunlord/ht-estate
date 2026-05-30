"""R1 [PROBE] gym-signal — K-apt 시설필드 천장 재확인 (throwaway, 배포코드 아님).

docs/orders R1 스파이크. 18단지(강남권+비강남) 표본에서 K-apt 두 V4 엔드포인트
(basis=getAphusBassInfoV4 / detail=getAphusDtlInfoV4)의 **모든 문자열 필드**를
헬스장 토큰으로 스캔해 welfareFacility/convenientFacility 외에 더 완전한 신호가
있는지 확인한다. 표본 단지코드는 list_complexes로 라이브 확보.

실행: uv run --project apps/api python scripts/r1_gym_probe.py
출력: scripts/r1_kapt_probe.json  (docs/reports/R1-gym-signal.md 근거)
호출은 순차 + throttle (레이트리밋/봇차단 존중). GT(헬스장 유무)는 리포트에서 별도 판정.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

# scripts/ 는 repo 루트, app 패키지는 apps/api/app → 경로 주입(uv run --project apps/api 기준).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api"))

import httpx  # noqa: E402

from app.settings import get_api_key  # noqa: E402
from app.sources._http import fetch_text, json_body  # noqa: E402
from app.sources._parse import json_int, yyyymmdd_to_date  # noqa: E402
from app.sources.kapt import BASIS_URL, DETAIL_URL, list_complexes  # noqa: E402

THROTTLE_S = 0.25
OUT_PATH = Path(__file__).resolve().parent / "r1_kapt_probe.json"

# 실내 피트니스 신호 토큰. '운동시설'은 실내/실외 모호 → 매칭하되 매칭문자열을 기록해 감사 가능.
GYM_TOKENS = [
    "헬스",
    "헬스장",
    "헬스클럽",
    "피트니스",
    "휘트니스",
    "fitness",
    "gym",
    "주민운동",
    "체력단련",
    "스포츠센터",
    "다목적체육",
    "실내체육",
    "g.x",
    "운동시설",
]

# 시군구코드 → 이름 (강남권 3 + 비강남 3)
SIGUNGU = {
    "11680": "강남구",
    "11650": "서초구",
    "11710": "송파구",
    "11440": "마포구",
    "11350": "노원구",
    "28237": "인천부평구",
}
GANGNAM = {"11680", "11650", "11710"}

# 유명 단지: 이름 부분일치 → (시군구, 카테고리). 신축프리미엄(GT≈YES) + 대형/구축 대조.
FAMOUS: list[tuple[str, str, str]] = [
    ("역삼자이", "11680", "강남·신축프리미엄"),
    ("래미안대치팰리스", "11680", "강남·신축프리미엄"),
    ("디에이치아너힐즈", "11680", "강남·신축프리미엄"),
    ("은마", "11680", "강남·구축대형"),
    ("아크로리버파크", "11650", "강남·신축프리미엄"),
    ("반포자이", "11650", "강남·준신축대형"),
    ("헬리오시티", "11710", "강남·신축프리미엄"),
    ("잠실엘스", "11710", "강남·준신축대형"),
    ("마포래미안푸르지오", "11440", "비강남·신축프리미엄"),
]
LONGTAIL_SIGUNGU = ["11350", "28237"]  # 노원·부평 — 비강남 일반/소형/구축 long-tail
LONGTAIL_PER = 4  # 시군구당 최종 선별 수
LONGTAIL_CANDIDATES = 8  # 시군구당 info fetch 후보 상한

_CACHE: dict[tuple[str, str], dict[str, Any] | None] = {}


def norm(s: str | None) -> str:
    return (s or "").replace(" ", "")


def fetch_item(url: str, code: str, key: str, client: httpx.Client) -> dict[str, Any] | None:
    """단건 item(dict) 반환 + 캐시 + throttle. 실패/없음이면 None."""
    ck = (url, code)
    if ck in _CACHE:
        return _CACHE[ck]
    try:
        body = json_body(fetch_text(url, {"serviceKey": key, "kaptCode": code}, client=client))
        item = body.get("item")
        item = item if isinstance(item, dict) else None
    except Exception as exc:  # noqa: BLE001  — 프로브: 단지 단위로 graceful
        print(f"[warn] fetch 실패 {code} {url.rsplit('/', 1)[-1]}: {exc}")
        item = None
    _CACHE[ck] = item
    time.sleep(THROTTLE_S)
    return item


def approval_year(basis: dict[str, Any]) -> int | None:
    d = yyyymmdd_to_date(str(basis.get("kaptUsedate")) if basis.get("kaptUsedate") else None)
    return d.year if d else None


def scan_gym(fields: dict[str, Any]) -> list[dict[str, str]]:
    """필드 전체를 토큰 스캔. 필드당 첫 매칭만(중복 방지), 매칭 텍스트 보존."""
    hits: list[dict[str, str]] = []
    for k, v in fields.items():
        if isinstance(v, str) and v.strip():
            low = v.lower()
            for tok in GYM_TOKENS:
                if tok.lower() in low:
                    hits.append({"field": k, "token": tok, "text": v.strip()[:300]})
                    break
    return hits


def build_sample(key: str, client: httpx.Client) -> list[dict[str, Any]]:
    lists: dict[str, list[Any]] = {}
    for code, name in SIGUNGU.items():
        refs = list_complexes(api_key=key, sigungu=code, client=client)
        lists[code] = refs
        print(f"[list] {name}({code}): {len(refs)} 단지")
        time.sleep(THROTTLE_S)

    sample: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1) 유명 단지 — 이름 부분일치
    for target, sgg, cat in FAMOUS:
        refs = lists.get(sgg, [])
        match = next((r for r in refs if norm(target) in norm(r.name)), None)
        if match and match.kapt_code not in seen:
            seen.add(match.kapt_code)
            sample.append(
                {
                    "name": match.name,
                    "kapt_code": match.kapt_code,
                    "sido": match.sido,
                    "sigungu": SIGUNGU[sgg],
                    "sigungu_code": sgg,
                    "category": cat,
                }
            )
        else:
            print(f"[warn] FAMOUS 미매칭: '{target}' in {SIGUNGU[sgg]}")

    # 2) long-tail — 리스트 스프레드 후보 → info로 소형/구축 우선 선별
    for sgg in LONGTAIL_SIGUNGU:
        refs = sorted(
            (r for r in lists.get(sgg, []) if r.kapt_code not in seen),
            key=lambda r: r.kapt_code,
        )
        if not refs:
            continue
        step = max(1, len(refs) // LONGTAIL_CANDIDATES)
        cands = refs[::step][:LONGTAIL_CANDIDATES]
        scored: list[tuple[Any, int | None, int | None]] = []
        for r in cands:
            basis = fetch_item(BASIS_URL, r.kapt_code, key, client) or {}
            scored.append((r, json_int(basis.get("kaptdaCnt")), approval_year(basis)))
        # 소형(<300세대) 또는 구축(<2005) 우선, 그다음 세대수 오름차순
        scored.sort(
            key=lambda t: (not ((t[1] or 9999) < 300 or (t[2] or 9999) < 2005), t[1] or 9999)
        )
        for r, hh, yr in scored[:LONGTAIL_PER]:
            if r.kapt_code in seen:
                continue
            seen.add(r.kapt_code)
            sample.append(
                {
                    "name": r.name,
                    "kapt_code": r.kapt_code,
                    "sido": r.sido,
                    "sigungu": SIGUNGU[sgg],
                    "sigungu_code": sgg,
                    "category": f"비강남·일반(세대 {hh}, 승인 {yr})",
                }
            )
    return sample


def probe(entry: dict[str, Any], key: str, client: httpx.Client) -> dict[str, Any]:
    code = entry["kapt_code"]
    basis = fetch_item(BASIS_URL, code, key, client) or {}
    detail = fetch_item(DETAIL_URL, code, key, client) or {}
    hits = scan_gym(basis) + scan_gym(detail)
    return {
        **entry,
        "is_gangnam": entry["sigungu_code"] in GANGNAM,
        "approval_date": basis.get("kaptUsedate"),
        "approval_year": approval_year(basis),
        "household_count": json_int(basis.get("kaptdaCnt")),
        "welfareFacility": detail.get("welfareFacility"),
        "convenientFacility": detail.get("convenientFacility"),
        "educationFacility": detail.get("educationFacility"),
        "gym_signal": bool(hits),
        "gym_hits": hits,
        "basis_keys": sorted(basis.keys()),
        "detail_keys": sorted(detail.keys()),
    }


def main() -> None:
    key = get_api_key()
    with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
        sample = build_sample(key, client)
        print(f"\n[sample] {len(sample)} 단지 확보 → 시설필드 스캔\n")
        results = [probe(e, key, client) for e in sample]

    all_basis_keys = sorted({k for r in results for k in r["basis_keys"]})
    all_detail_keys = sorted({k for r in results for k in r["detail_keys"]})

    print("=== R1 K-apt gym-signal 표본 ===")
    print(f"{'gym':4} {'권역':4} {'시군구':7} {'단지':20} {'세대':>5} {'승인':>5}  welfareFacility(앞40)")
    for r in sorted(results, key=lambda x: (not x["is_gangnam"], x["sigungu"])):
        flag = "✓" if r["gym_signal"] else "—"
        zone = "강남" if r["is_gangnam"] else "비강남"
        wf = (r["welfareFacility"] or "")[:40]
        print(
            f"{flag:4} {zone:4} {str(r['sigungu']):7} {str(r['name'])[:20]:20} "
            f"{str(r['household_count']):>5} {str(r['approval_year']):>5}  {wf}"
        )
    gym_n = sum(r["gym_signal"] for r in results)
    gn = [r for r in results if r["is_gangnam"]]
    ng = [r for r in results if not r["is_gangnam"]]
    print(
        f"\nK-apt 헬스장 신호: 전체 {gym_n}/{len(results)} | "
        f"강남권 {sum(r['gym_signal'] for r in gn)}/{len(gn)} | "
        f"비강남 {sum(r['gym_signal'] for r in ng)}/{len(ng)}"
    )
    print(f"\nbasis 필드({len(all_basis_keys)}): {all_basis_keys}")
    print(f"detail 필드({len(all_detail_keys)}): {all_detail_keys}")

    OUT_PATH.write_text(
        json.dumps(
            {
                "gym_tokens": GYM_TOKENS,
                "sigungu": SIGUNGU,
                "n": len(results),
                "gym_signal_total": gym_n,
                "all_basis_keys": all_basis_keys,
                "all_detail_keys": all_detail_keys,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[written] {OUT_PATH}")


if __name__ == "__main__":
    main()
