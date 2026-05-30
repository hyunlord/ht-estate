"""공공 API 공통 HTTP — 타임아웃·바운디드 재시도 + 응답코드 검사.

서비스키는 decoded 형태로 받아 httpx `params=`에 넣는다(httpx가 1회 인코딩).
encoded 키를 넣으면 이중 인코딩으로 키 미등록 에러가 난다(settings 참고).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any
from xml.etree.ElementTree import Element

import httpx

from .errors import PublicDataError

DEFAULT_TIMEOUT = httpx.Timeout(10.0)
SUCCESS_CODES = {"00", "000"}


def fetch_text(
    url: str,
    params: Mapping[str, str | int],
    *,
    client: httpx.Client | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    retries: int = 3,
    backoff: float = 0.5,
) -> str:
    """GET → 응답 본문 텍스트(XML/JSON 무관). 전송오류/5xx는 지수 백오프 재시도, 4xx 즉시 raise.

    `client`를 주입하면(테스트의 MockTransport 등) 그걸 쓰고 닫지 않는다.
    """
    own = client is None
    cl = client or httpx.Client(timeout=timeout)
    try:
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = cl.get(url, params=params)
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise  # 4xx는 재시도 무의미
                last_exc = exc
            except httpx.TransportError as exc:
                last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))
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
    (cmmMsgHeader/returnReasonCode·errMsg) 양쪽을 본다. 코드가 아예 없으면
    (최소 응답) 성공으로 간주하고 본문 파싱에 맡긴다.
    """
    code = root.findtext(".//resultCode")
    msg = root.findtext(".//resultMsg")
    if code is None:
        code = root.findtext(".//returnReasonCode")
        msg = root.findtext(".//errMsg") or root.findtext(".//returnAuthMsg")
    if code is not None and code.strip() not in SUCCESS_CODES:
        raise PublicDataError(code.strip(), msg.strip() if msg else None)


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
