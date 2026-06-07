"""법정동코드 정적 참조 룩업(enrich-1) — (sgg_cd, 법정동명) → bjdongCd. 키리스(committed CSV)."""

from __future__ import annotations

from app.store.regions import bjdong_code


def test_known_pair_resolves() -> None:
    # 강남 대치동 — probe로 확인한 bjdongCd 10600(은마 대치동 316 표제부와 동일)
    assert bjdong_code("11680", "대치동") == "10600"


def test_unknown_pair_is_none() -> None:
    assert bjdong_code("00000", "없는법정동") is None


def test_none_inputs() -> None:
    assert bjdong_code(None, "대치동") is None
    assert bjdong_code("11680", None) is None
