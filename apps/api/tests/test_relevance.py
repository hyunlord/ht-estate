"""rag-corpus-quality/recall — 건물검증(코어 하드·region 강등) + 노이즈 필터. 실 오염 케이스 검증.

진단: 케이씨씨엠파이어타워(부천 심곡본동) 코퍼스가 **해운대 케이씨씨스위첸**(딴 건물)·**파라곤**(딴
단지)·**경매/인테리어/대출** 광고를 담음. recall 발견: region 하드-AND가 동 없는 진짜 후기 과잉
reject. → 코어는 하드, region은 강등(distinctive면 동 불요·gemma 디스앰비). 정밀은 보존(코어+noise
+gemma가 오염 reject — 강등이 오염 재유입 0). 지역 미상이면 이름만(과잉 reject 방지).
"""

from __future__ import annotations

from app.corpus.relevance import (
    building_name_core,
    doc_building_relevant,
    doc_is_noise,
    filter_docs,
    is_distinctive_name,
    region_tokens,
)
from app.enrich.fetcher import SourceDoc

NAME = "케이씨씨엠파이어타워"
RTOK = region_tokens("부천소사구", "심곡본동")


# ── 단지명 코어(generic 접미 제거) ──
def test_name_core_strips_generic_suffix() -> None:
    assert building_name_core("케이씨씨엠파이어타워") == "케이씨씨엠파이어"
    assert building_name_core("힐스테이트") == "힐스테이트"  # 전체가 코어면 보존


def test_distinctiveness_threshold() -> None:
    assert is_distinctive_name("케이씨씨엠파이어") is True   # 길어 충돌 낮음
    assert is_distinctive_name("헬리오시티") is True
    assert is_distinctive_name("현대") is False              # 짧고 흔함 → region 요구
    assert is_distinctive_name("한양") is False


# ── 건물검증: 코어 불일치는 하드 reject(강등 무관) ──
def test_rejects_other_building_kcc_switzen() -> None:
    # 해운대 케이씨씨스위첸 — "케이씨씨" 토큰만 겹침. 코어 미포함 → reject(하드).
    txt = "해운대 케이씨씨스위첸 13억 거래 후기 바다뷰 최고"
    assert doc_building_relevant(txt, NAME, RTOK) is False


def test_rejects_paragon() -> None:
    txt = "심곡본동 파라곤 아파트 노래방 근처 술집 후기"  # 지역은 맞아도 단지명 미포함
    assert doc_building_relevant(txt, NAME, RTOK) is False


# ── region 강등(recall): distinctive 이름은 동 없는 진짜 후기 통과 ──
def test_distinctive_name_keeps_without_dong_in_doc() -> None:
    # 제목에 단지명 有·스니펫에 동 無 → 이전엔 reject, 이제 keep(recall↑). gemma가 디스앰비.
    txt = "케이씨씨엠파이어타워 주차 넉넉하고 관리 잘돼요 살기 좋네요"
    assert doc_building_relevant(txt, NAME, RTOK) is True


def test_right_name_wrong_region_passes_rule_gemma_disambiguates() -> None:
    # 강등: distinctive·이름 맞으면 룰 통과(동명-타지 reject는 gemma 몫). 룰 자체는 True.
    txt = "해운대 케이씨씨엠파이어 분위기 좋네요"
    assert doc_building_relevant(txt, NAME, RTOK) is True
    # gemma(지역 인지) 분류기가 해운대(딴 지역) reject → filter_docs 0.
    docs = [SourceDoc(source_type="blog", source_url="u1", text=txt)]
    assert filter_docs(docs, name=NAME, region_toks=RTOK,
                       classifier=lambda _t: False) == []


def test_generic_name_still_requires_region() -> None:
    # generic 짧은 이름(현대)은 region 강등 면제 X — 동 없으면 reject, 있으면 keep.
    gtok = region_tokens("강남구", "압구정동")
    assert doc_building_relevant("현대 살기 좋아요 주차", "현대", gtok) is False  # 동 없음
    assert doc_building_relevant("압구정동 현대 주차 넉넉", "현대", gtok) is True   # 동 있음


def test_keeps_name_plus_region() -> None:
    txt = "부천 심곡본동 케이씨씨엠파이어타워 주차 넉넉하고 관리 잘돼요"
    assert doc_building_relevant(txt, NAME, RTOK) is True


def test_lenient_when_region_unknown() -> None:
    # 지역 미상(토큰 빈) → 이름만으로 판정(과잉 reject 방지·테스트 단지 보존).
    assert doc_building_relevant("가단지 주차 넉넉", "가단지", []) is True
    assert doc_building_relevant("나단지 후기", "가단지", []) is False


# ── 노이즈 필터: 경매/인테리어/대출/매물 drop · 거주후기 keep ──
def test_noise_drops_auction_interior_loan() -> None:
    assert doc_is_noise("케이씨씨엠파이어 경매 감정가 3억 입찰") is True
    assert doc_is_noise("아파트 인테리어 시공 샷시 교체") is True
    assert doc_is_noise("담보대출 후순위 한도 상담") is True
    assert doc_is_noise("급매 매물 신고가 분양권 전매") is True


def test_noise_keeps_living_experience() -> None:
    assert doc_is_noise("주차 넉넉하고 층간소음 적어요") is False
    assert doc_is_noise("관리 잘되고 교통 입지 좋습니다") is False


# ── filter_docs 통합: 오염 reject + 진짜 후기 keep ──
def test_filter_docs_keeps_only_clean_review() -> None:
    docs = [
        SourceDoc(source_type="blog", source_url="u1",
                  text="부천 심곡본동 케이씨씨엠파이어타워 주차 넉넉 층간소음 적음"),  # keep
        SourceDoc(source_type="blog", source_url="u2",
                  text="해운대 케이씨씨스위첸 바다뷰"),  # 딴 건물 reject
        SourceDoc(source_type="cafe", source_url="u3",
                  text="심곡본동 케이씨씨엠파이어타워 경매 감정가 입찰"),  # 노이즈 reject
    ]
    kept = filter_docs(docs, name=NAME, region_toks=RTOK)
    assert [d.source_url for d in kept] == ["u1"]


def test_filter_docs_llm_classifier_precision() -> None:
    # 룰 통과 doc도 LLM이 reject하면 drop(경계 precision). 분류기 호출 텍스트 확인.
    docs = [SourceDoc(source_type="blog", source_url="u1",
                      text="부천 심곡본동 케이씨씨엠파이어타워 주차 넉넉")]
    seen: list[str] = []

    def reject_all(text: str) -> bool:
        seen.append(text)
        return False

    assert filter_docs(docs, name=NAME, region_toks=RTOK, classifier=reject_all) == []
    assert seen  # 룰 통과분만 LLM에 도달
