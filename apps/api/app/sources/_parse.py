"""XML 파싱 공용 헬퍼 — 공공 API의 지저분한 문자열(콤마·공백·YYYYMMDD)을 정규화."""

from __future__ import annotations

from datetime import date
from xml.etree.ElementTree import Element


def text(item: Element, tag: str) -> str | None:
    """태그의 텍스트를 strip해 반환. 없거나 빈 문자열이면 None."""
    raw = item.findtext(tag)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def required_text(item: Element, tag: str) -> str:
    """필수 텍스트. 없거나 비면 ValueError (호출부가 잡아 해당 행을 skip)."""
    value = text(item, tag)
    if value is None:
        raise ValueError(f"required field <{tag}> missing or empty")
    return value


def to_int(raw: str) -> int:
    """'82,500' / ' 12 ' 같은 문자열을 int로. 빈/비숫자면 ValueError."""
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        raise ValueError("empty integer")
    return int(cleaned)


def to_float(raw: str) -> float:
    """'84.97' / ' 1,234.5 ' 같은 문자열을 float로. 빈/비숫자면 ValueError."""
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        raise ValueError("empty float")
    return float(cleaned)


def opt_int(item: Element, tag: str) -> int | None:
    """선택 정수 — 없거나 파싱 실패면 None (graceful)."""
    value = text(item, tag)
    if value is None:
        return None
    try:
        return to_int(value)
    except ValueError:
        return None


def yyyymmdd_to_date(raw: str | None) -> date | None:
    """'20150327' → date. 8자리 숫자가 아니면 None (graceful)."""
    if raw is None:
        return None
    cleaned = raw.replace("-", "").replace(".", "").strip()
    if len(cleaned) != 8 or not cleaned.isdigit():
        return None
    try:
        return date(int(cleaned[0:4]), int(cleaned[4:6]), int(cleaned[6:8]))
    except ValueError:
        return None
