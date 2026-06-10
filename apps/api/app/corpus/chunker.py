"""청킹 — 소스 본문을 인용 가능한 청크로 분할 + span_ref 부여. (E3-2)

blog/cafe: 문단(빈 줄/문장경계) 단위. 긴 문단은 문장경계로 soft-split(max_chars 상한). 각 청크에
span_ref='p{idx}'(문단 인덱스) — E3-3 인용 정밀/딥링크용. youtube 자막(타임스탬프 구간 't{a}-{b}')은
PART0 viable일 때 추가(현재 defer) — chunk() 시그니처는 source-agnostic이라 무변경 확장.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.enrich.fetcher import SourceDoc

# 문장 경계(한국어 종결/구두점) — 긴 문단 soft-split용.
_SENT_SPLIT = re.compile(r"(?<=[.!?。…\n])\s+")
_PARA_SPLIT = re.compile(r"\n\s*\n+")
_WS = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class Chunk:
    """청크 1건 — 본문 + 인용정밀(span_ref). source_type/url은 SourceDoc에서 builder가 채움."""

    span_ref: str
    text: str


def _norm(s: str) -> str:
    return _WS.sub(" ", s.replace("\r", "")).strip()


def _split_long(para: str, max_chars: int) -> list[str]:
    """max_chars 초과 문단을 문장경계로 누적 분할(경계 없으면 hard-cut)."""
    if len(para) <= max_chars:
        return [para]
    out: list[str] = []
    buf = ""
    for sent in _SENT_SPLIT.split(para):
        sent = sent.strip()
        if not sent:
            continue
        if buf and len(buf) + 1 + len(sent) > max_chars:
            out.append(buf)
            buf = sent
        else:
            buf = f"{buf} {sent}".strip()
        while len(buf) > max_chars:  # 문장 자체가 길면 hard-cut
            out.append(buf[:max_chars])
            buf = buf[max_chars:]
    if buf:
        out.append(buf)
    return out


def chunk_text(text: str, *, max_chars: int = 400) -> list[Chunk]:
    """평문 → 문단 청크 리스트(span_ref='p{idx}'). 빈/공백은 제외. idx는 출력 청크 순번."""
    chunks: list[Chunk] = []
    paras = _PARA_SPLIT.split(text or "")
    for para in paras:
        norm = _norm(para)
        if not norm:
            continue
        for piece in _split_long(norm, max_chars):
            piece = piece.strip()
            if piece:
                chunks.append(Chunk(span_ref=f"p{len(chunks)}", text=piece))
    return chunks


def chunk_doc(doc: SourceDoc, *, max_chars: int = 400) -> list[Chunk]:
    """SourceDoc(blog/cafe/web) → 청크들. youtube 자막은 차후(타임스탬프 청커) — 현재 문단 경로."""
    return chunk_text(doc.text, max_chars=max_chars)
