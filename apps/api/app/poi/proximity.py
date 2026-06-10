"""POI 근접 — Kakao Local 카테고리/키워드 검색 + 결정론 compute. (poi-1)

정적 좌표↔정적 POI: LLM 0·DB권 0. (단지,카테고리)당 Kakao 1콜(radius·sort=distance) →
nearest_dist/count_500m/count_1km. 429는 QuotaExceeded로 올려 러너가 우아 중단(C48 동형).
키리스: httpx client 주입(MockTransport).
"""

from __future__ import annotations

import time
from collections.abc import Callable
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
    """Kakao 일쿼터 초과 — 러너가 우아 중단(다음날 resume). C48 패턴 동형.

    **두 신호 모두 포함(C62 실측 진단):** HTTP **429**, 그리고 Kakao Local이 일쿼터 초과를
    내보내는 또 다른 형태인 **HTTP 400 + body code=-10**("API limit has been exceeded.").
    후자가 C61 크래시의 진짜 원인 — 이건 per-row 데이터 이슈가 **아니라** quota라, skip+continue로
    묻으면 quota 정전 동안 단지 수만 개를 영구 skip 마킹해 백필을 오염시킨다 → QuotaExceeded로
    올려 우아 중단·다음 run resume(quota 리셋 시 자가치유)."""


class BadRequestError(RuntimeError):
    """Kakao **진짜** bad-request 4xx(HTTP 400·**quota code -10 아님**) — per-row 데이터 이슈
    (좌표/파라미터 단발). 러너가 **해당 (단지,카테고리) skip+continue·attempted 마킹**(재시도 무의미
    ·400은 재요청해도 안 변함). TransientError와 달리 단지 전체가 아니라 그 한 카테고리만 skip하고
    같은 단지 나머지 카테고리는 계속 처리. 401/403(체계적 auth) 아님 — 그건 여전히 raise(abort)."""


class TransientError(RuntimeError):
    """일시적 Kakao 오류(timeout·connect·handshake·5xx) — 재시도 소진 후. 러너가 **해당 단지 skip
    +continue**(크래시 금지). 미완 카테고리는 done_categories로 다음 run retry(영구 갭 0). 장기
    외부-API 배치의 graceful-degrade 표준 — 단발 네트워크 블립이 전체 런을 죽이지 않게."""


@dataclass
class PoiResult:
    nearest_dist_m: int | None
    nearest_name: str | None
    count_500m: int | None
    count_1km: int | None


@dataclass
class KakaoLocalClient:
    """Kakao Local 검색 — category_group_code / keyword.

    오류 3분류(graceful-degrade 전수): **429→QuotaExceeded**(우아 중단·C48) · **일시적**(timeout·
    connect·handshake·5xx)→짧은 백오프 재시도 후 **TransientError**(러너가 단지 skip·continue) ·
    **영구**(그 외 4xx: 401/403/400 — 키·요청 문제)→raise(즉시 abort). 키리스: client·sleep 주입.
    """

    api_key: str
    radius: int = 1000
    size: int = 15
    timeout: float = 10.0
    max_retries: int = 2  # 일시적 오류 재시도 횟수(지수 백오프)
    backoff: float = 0.5  # 재시도 기본 대기(초) — backoff * 2**attempt
    client: httpx.Client | None = None
    sleep: Callable[[float], None] = time.sleep  # 주입형(테스트 무대기)

    def _get(self, path: str, params: dict) -> dict:
        url = f"https://dapi.kakao.com/v2/local/search/{path}.json"
        headers = {"Authorization": f"KakaoAK {self.api_key}"}
        own = self.client is None
        cl = self.client or httpx.Client(timeout=self.timeout)
        try:
            last: Exception | None = None
            for attempt in range(self.max_retries + 1):
                try:
                    resp = cl.get(url, headers=headers, params=params)
                except httpx.TransportError as exc:  # timeout·connect·handshake·protocol = 일시적
                    last = exc
                else:
                    if resp.status_code == 429:
                        raise QuotaExceeded("Kakao 429 — 일쿼터 초과")
                    if resp.status_code == 400:
                        # Kakao는 일쿼터 초과를 HTTP 400 + code -10로도 신호(429 아님·C62 실측).
                        # quota → QuotaExceeded(우아 중단·자가치유) · 그 외 400(진짜 bad-request·
                        # per-row) → BadRequestError(skip+continue·재시도 무의미).
                        if _is_quota_400(resp):
                            raise QuotaExceeded("Kakao 400 code=-10 — 일쿼터 초과(400 신호)")
                        raise BadRequestError(f"Kakao 400 bad-request: {resp.text[:200]}")
                    if resp.status_code < 500:
                        resp.raise_for_status()  # 401/403 등 체계적 4xx → 영구 abort · 2xx → 통과
                        return resp.json()
                    last = httpx.HTTPStatusError(  # 5xx → 일시적(재시도)
                        f"Kakao {resp.status_code}", request=resp.request, response=resp
                    )
                if attempt < self.max_retries:
                    self.sleep(self.backoff * (2**attempt))
            raise TransientError(
                f"Kakao 일시적 오류(재시도 {self.max_retries}회 소진): {type(last).__name__}"
            )
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


def _is_quota_400(resp: httpx.Response) -> bool:
    """HTTP 400이 Kakao 일쿼터 초과(code -10·"API limit has been exceeded.")인지 판별.

    JSON body의 `code == -10`가 1차 신호, 메시지 substring이 fallback(body 비-JSON 방어).
    이게 true면 quota(우아 중단)·아니면 진짜 bad-request(per-row skip)."""
    try:
        body = resp.json()
    except (ValueError, TypeError):
        body = {}
    if isinstance(body, dict) and body.get("code") == -10:
        return True
    blob = f"{body} {resp.text}".lower() if isinstance(body, dict) else resp.text.lower()
    return "limit has been exceeded" in blob


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
