"""Kakao 지오코더 — 실캡처 파싱 + geocode(MockTransport) hit/empty/graceful.

geocode_sample.json은 라이브 실응답 캡처(언주로 420 → 역삼자이). empty는 무결과 실응답.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.geo.geocoder import geocode, parse_geocode

FixtureLoader = Callable[[str], str]


def test_parse_geocode_real_capture(load_fixture: FixtureLoader) -> None:
    coord = parse_geocode(load_fixture("geocode_sample.json"))
    assert coord is not None
    lat, lng = coord
    # x=경도, y=위도 → (lat, lng). 강남 정상 범위
    assert abs(lat - 37.5010) < 0.001
    assert abs(lng - 127.0442) < 0.001


def test_parse_geocode_empty_is_none(load_fixture: FixtureLoader) -> None:
    assert parse_geocode(load_fixture("geocode_empty.json")) is None


def test_parse_geocode_garbage_is_none() -> None:
    assert parse_geocode("not json") is None
    assert parse_geocode('{"documents": [{"x": "bad"}]}') is None


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_geocode_hit(load_fixture: FixtureLoader) -> None:
    body = load_fixture("geocode_sample.json")
    client = _client(lambda _r: httpx.Response(200, text=body))
    coord = geocode("서울특별시 강남구 언주로 420", api_key="dummy", client=client)
    assert coord is not None
    assert abs(coord[0] - 37.5010) < 0.001


def test_geocode_sends_auth_header(load_fixture: FixtureLoader) -> None:
    body = load_fixture("geocode_sample.json")
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("Authorization", "")
        return httpx.Response(200, text=body)

    geocode("서울특별시 강남구 언주로 420", api_key="MYKEY", client=_client(handler))
    assert seen["auth"] == "KakaoAK MYKEY"


def test_geocode_empty_address_is_none() -> None:
    # 호출 없이 None (네트워크 안 탐)
    def handler(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call")

    assert geocode(None, api_key="dummy", client=_client(handler)) is None
    assert geocode("  ", api_key="dummy", client=_client(handler)) is None


def test_geocode_propagates_http_error() -> None:
    client = _client(lambda _r: httpx.Response(401, text="unauthorized"))
    with pytest.raises(httpx.HTTPStatusError):
        geocode("서울특별시 강남구 언주로 420", api_key="badkey", client=client)
