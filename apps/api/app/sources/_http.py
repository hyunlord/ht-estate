"""공공 API 공통 HTTP — 타임아웃·바운디드 재시도 + 응답코드 검사.

서비스키는 decoded 형태로 받아 httpx `params=`에 넣는다(httpx가 1회 인코딩).
encoded 키를 넣으면 이중 인코딩으로 키 미등록 에러가 난다(settings 참고).
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Mapping
from typing import Any
from xml.etree.ElementTree import Element

import httpx

from .errors import PublicDataError

DEFAULT_TIMEOUT = httpx.Timeout(10.0)
SUCCESS_CODES = {"00", "000"}

# 멀티데이 적재 회복력(C22): 일시 네트워크 오류(끊김·타임아웃·DNS·5xx·429)를 지수 백오프+지터로
# 재시도해 짧은 끊김(수십 초~분)을 라이드아웃한다. 영구 오류(4xx, 단 429 제외)는 빠르게 실패
# (무한 재시도로 일일캡 낭비·실패 마스킹 금지). 기본 ride-out ≈ 0.5+1+2+4+8+16+30 ≈ 61s.
DEFAULT_RETRIES = 8
DEFAULT_BACKOFF = 0.5
DEFAULT_MAX_BACKOFF = 30.0
DEFAULT_JITTER = 0.25


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """429 응답의 `Retry-After`(초) → float. HTTP-date 형식이거나 없으면 None(백오프 폴백)."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # HTTP-date는 미지원 — 일반 백오프로 폴백


def _backoff_delay(
    attempt: int, backoff: float, max_backoff: float, jitter: float, rng: Callable[[], float]
) -> float:
    """attempt회차의 백오프 지연 — 지수(2^n) capped + 지터(0..jitter 비율 가산). full-jitter류."""
    base = min(backoff * (2**attempt), max_backoff)
    return base * (1.0 + jitter * rng())


def _is_permanent_status(status: int) -> bool:
    """HTTP 상태가 영구 오류(재시도 무의미)인지 — 4xx 중 429(레이트리밋)만 예외로 재시도."""
    return status < 500 and status != 429


def fetch_text(
    url: str,
    params: Mapping[str, str | int],
    *,
    headers: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    max_backoff: float = DEFAULT_MAX_BACKOFF,
    jitter: float = DEFAULT_JITTER,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> str:
    """GET → 응답 본문 텍스트(XML/JSON 무관). 일시 오류(전송오류·5xx·429)는 백오프+지터 재시도,
    영구 4xx(400/401/403/404)는 즉시 raise.

    일시 끊김 라이드아웃(C22): `ConnectError`·`ReadTimeout`·`ConnectTimeout`·DNS(TransportError)
    + 5xx + 429(`Retry-After` 존중)를 재시도한다. `throttle`(호출 간 간격)과 독립 — 호출 *내부*
    재시도라 공존한다. `sleep`/`rng`는 테스트 주입용(결정론). `client` 주입 시 닫지 않는다.
    """
    own = client is None
    cl = client or httpx.Client(timeout=timeout)
    try:
        last_exc: Exception | None = None
        for attempt in range(retries):
            override: float | None = None  # 429 Retry-After 등 명시 지연
            try:
                resp = cl.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPStatusError as exc:
                if _is_permanent_status(exc.response.status_code):
                    raise  # 영구 4xx — 빠른 실패(일일캡·인가 오류는 재시도 무의미)
                last_exc = exc
                if exc.response.status_code == 429:
                    override = _retry_after_seconds(exc.response)
            except httpx.TransportError as exc:
                last_exc = exc  # 연결/타임아웃/DNS 등 일시 전송 오류
            if attempt < retries - 1:
                delay = (
                    min(override, max_backoff)
                    if override is not None
                    else _backoff_delay(attempt, backoff, max_backoff, jitter, rng)
                )
                sleep(delay)
        assert last_exc is not None
        raise last_exc
    finally:
        if own:
            cl.close()


def paginate[T](
    fetch_page: Callable[[int], tuple[list[T], int]],
    *,
    num_of_rows: int,
) -> list[T]:
    """공통 페이지 루프. `fetch_page(page) -> (items, total_count)`를 받아 전부 모은다.

    빈 페이지를 만나거나 수집분이 total_count에 도달하면 정지. MOLIT·K-apt 목록이
    동일 패턴이라 여기로 통일(중복 제거).
    """
    collected: list[T] = []
    page = 1
    while True:
        items, total_count = fetch_page(page)
        collected.extend(items)
        if not items or page * num_of_rows >= total_count:
            break
        page += 1
    return collected


def ensure_success(root: Element) -> None:
    """응답 헤더의 resultCode가 성공(00/000)이 아니면 `PublicDataError`.

    표준 엔벨로프(header/resultCode·resultMsg)와 시스템 에러 엔벨로프
    (cmmMsgHeader/returnReasonCode·errMsg) 양쪽을 본다. 유효한 data.go.kr 응답은
    **반드시** 둘 중 하나의 코드를 갖는다 — 둘 다 없으면 잘린/과부하 응답(transient)으로
    보고 raise한다. (코드 없는 빈 응답을 '성공 0건'으로 통과시키면 버스트로 비어 온 월이
    ledger에 '완료 0건'으로 영구 박혀 데이터가 마스킹됨 — fix/rent-empty-ledger.)
    """
    code = root.findtext(".//resultCode")
    msg = root.findtext(".//resultMsg")
    if code is None:
        code = root.findtext(".//returnReasonCode")
        msg = root.findtext(".//errMsg") or root.findtext(".//returnAuthMsg")
    if code is None:
        raise PublicDataError(None, "resultCode/returnReasonCode 없음 — transient/malformed")
    if code.strip() not in SUCCESS_CODES:
        raise PublicDataError(code.strip(), msg.strip() if msg else None)


def resolve_total_count(total_text: str | None, item_count: int) -> int:
    """페이지 `totalCount` 해석 + **빈 응답 진위 검증**.

    items가 있으면 totalCount(태그 없으면 item_count 폴백)를 반환한다. items==0이면
    **totalCount가 명시적 '0'일 때만** 진짜 빈 월(거래 없음)로 보고 0을 반환하고, 그 외
    (totalCount 태그 없음 · total>0인데 page가 빔)은 미확정 빈응답으로 보고 `PublicDataError`.

    근거(fix/rent-empty-ledger): 진짜 빈 응답은 resultCode 000 + totalCount 0을 모두 갖는다
    (molit_empty.xml). 버스트로 비어 온 transient 응답은 이 둘을 확정하지 못하므로 raise →
    `_ingest_months_resumable`이 그 월을 ledger에 기록 못 함 → pending 유지 → 재개 재시도.
    """
    total: int | None = None
    if total_text and total_text.strip():
        try:
            total = int(total_text.strip())
        except ValueError:
            total = None
    if item_count == 0:
        if total == 0:
            return 0
        raise PublicDataError(None, f"빈 응답 미확정(totalCount={total_text!r}) — transient 의심")
    return total if total is not None else item_count


def json_body(json_text: str) -> dict[str, Any]:
    """data.go.kr JSON 응답 → body dict. resultCode가 성공 아니면 `PublicDataError`.

    K-apt(목록·기본·상세)는 XML이 아니라 JSON을 준다. 본문이 JSON이 아니면
    (시스템 에러 텍스트 등) PublicDataError로 변환한다. body가 없으면 빈 dict.
    """
    try:
        payload = json.loads(json_text)
    except ValueError as exc:
        raise PublicDataError(None, json_text[:200]) from exc
    response = payload.get("response", {}) if isinstance(payload, dict) else {}
    header = response.get("header", {})
    code = header.get("resultCode")
    if code is not None and str(code).strip() not in SUCCESS_CODES:
        raise PublicDataError(str(code).strip(), header.get("resultMsg"))
    body = response.get("body")
    return body if isinstance(body, dict) else {}
