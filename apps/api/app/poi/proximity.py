"""POI 근접 — Kakao Local 카테고리/키워드 검색 + 결정론 compute. (poi-1)

정적 좌표↔정적 POI: LLM 0·DB권 0. (단지,카테고리)당 Kakao 1콜(radius·sort=distance) →
nearest_dist/count_500m/count_1km. 429는 QuotaExceeded로 올려 러너가 우아 중단(C48 동형).
키리스: httpx client 주입(MockTransport).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

# (category, keyword) — keyword None이면 category_group_code 경로, 아니면 keyword 검색(공원).
CATEGORIES: tuple[tuple[str, str | None], ...] = (
    ("SW8", None),   # 지하철역
    ("MT1", None),   # 대형마트
    ("CS2", None),   # 편의점
    ("HP8", None),   # 병원
    ("PM9", None),   # 약국
    ("PARK", "공원"),  # 공원(키워드)
)
CATEGORY_LABELS: dict[str, str] = {
    "SW8": "지하철역", "MT1": "대형마트", "CS2": "편의점",
    "HP8": "병원", "PM9": "약국", "PARK": "공원",
}


class QuotaExceeded(RuntimeError):
    """Kakao 일쿼터 초과(429) — 러너가 우아 중단(다음날 resume). C48 패턴 동형."""


@dataclass
class PoiResult:
    nearest_dist_m: int | None
    nearest_name: str | None
    count_500m: int | None
    count_1km: int | None


@dataclass
class KakaoLocalClient:
    """Kakao Local 검색 — category_group_code / keyword. 429→QuotaExceeded, 그 외 실패→예외."""

    api_key: str
    radius: int = 1000
    size: int = 15
    timeout: float = 10.0
    client: httpx.Client | None = None

    def _get(self, path: str, params: dict) -> dict:
        url = f"https://dapi.kakao.com/v2/local/search/{path}.json"
        headers = {"Authorization": f"KakaoAK {self.api_key}"}
        own = self.client is None
        cl = self.client or httpx.Client(timeout=self.timeout)
        try:
            resp = cl.get(url, headers=headers, params=params)
            if resp.status_code == 429:
                raise QuotaExceeded("Kakao 429 — 일쿼터 초과")
            resp.raise_for_status()
            return resp.json()
        finally:
            if own:
                cl.close()

    def search(self, category: str, keyword: str | None, x: float, y: float) -> PoiResult:
        """(category, keyword, 좌표) → PoiResult. 반경 내 0건이면 전부 None/0."""
        base = {"x": x, "y": y, "radius": self.radius, "sort": "distance", "size": self.size}
        if keyword is None:
            data = self._get("category", {**base, "category_group_code": category})
        else:
            data = self._get("keyword", {**base, "query": keyword})
        return compute(data.get("documents", []), data.get("meta", {}).get("total_count", 0))


def compute(documents: list[dict], total_count: int) -> PoiResult:
    """Kakao documents(distance 정렬) + total_count → 근접 지표.

    nearest=documents[0].distance · count_1km=total_count · count_500m=반환 중 distance≤500
    (total_count>size면 하한 — 페이지1 한정). 0건이면 nearest None.
    """
    def _dist(doc: dict) -> int | None:
        try:
            return int(doc.get("distance", ""))
        except (TypeError, ValueError):
            return None

    if not documents:
        return PoiResult(None, None, None, total_count or 0)
    first = documents[0]
    n500 = sum(1 for d in documents if (dd := _dist(d)) is not None and dd <= 500)
    return PoiResult(
        nearest_dist_m=_dist(first),
        nearest_name=first.get("place_name"),
        count_500m=n500,
        count_1km=total_count,
    )


def client_from_env(api_key: str) -> KakaoLocalClient | None:
    """KAKAO_REST_API_KEY로 클라 구성(미설정이면 None — 러너가 graceful skip)."""
    key = (api_key or "").strip()
    return KakaoLocalClient(api_key=key) if key else None
