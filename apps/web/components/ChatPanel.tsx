"use client";

import type { ChatCitation } from "@/lib/types";

// E5-2 채팅 패널(옵션 3) — NL 바가 구동하는 멀티턴 스레드. 지도/필터 칩과 공존(지도 위 하단 도킹).
// 유저 버블 + 에이전트 근거 prose(가벼운 마크다운) + citations 출처 링크 + referenced_complexes
// 클릭 칩(→지도 이동+DetailPanel). 기존 다크 UI와 일관. crash 0(에러 턴 인라인).

export interface ChatThreadItem {
  role: "user" | "agent";
  content: string;
  citations?: ChatCitation[];
  referenced?: string[]; // 실 complex_id (백엔드 보장) — 클릭→select 경로
  error?: boolean;
}

// 가벼운 인라인 마크다운 — **굵게**만(전체 MD 파서 불요). 줄바꿈은 pre-wrap(CSS)로 보존.
function renderInline(text: string): React.ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p,
  );
}

export function ChatPanel({
  items,
  busy,
  refLabel,
  onRefClick,
  onClose,
}: {
  items: ChatThreadItem[];
  busy: boolean;
  refLabel: (id: string) => string; // 단지 id → 표시 라벨(후보명 lookup·없으면 id 파싱)
  onRefClick: (id: string) => void; // 클릭 → 기존 select 경로(지도 이동+DetailPanel)
  onClose: () => void;
}) {
  if (items.length === 0 && !busy) return null;
  return (
    <div className="chat-panel" data-testid="chat-panel">
      <div className="chat-head">
        <span className="chat-title">🏠 단지 에이전트</span>
        <button type="button" data-testid="chat-close" className="chat-x" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="chat-thread" data-testid="chat-thread">
        {items.map((it, i) => (
          <div
            key={i}
            className={`chat-turn ${it.role}${it.error ? " err" : ""}`}
            data-testid={`chat-turn-${it.role}`}
          >
            <div className="chat-bubble">
              <div className="chat-prose">{renderInline(it.content)}</div>
              {it.referenced && it.referenced.length > 0 && (
                <div className="chat-refs" data-testid="chat-refs">
                  {it.referenced.map((id) => (
                    <button
                      key={id}
                      type="button"
                      data-testid="chat-ref"
                      data-cid={id}
                      className="chat-ref"
                      onClick={() => onRefClick(id)}
                    >
                      📍 {refLabel(id)}
                    </button>
                  ))}
                </div>
              )}
              {it.citations && it.citations.length > 0 && (
                <div className="chat-cites" data-testid="chat-citations">
                  {it.citations.map((c, j) =>
                    c.source_url.startsWith("http") ? (
                      <a
                        key={j}
                        data-testid="chat-citation-link"
                        href={c.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="chat-cite"
                      >
                        출처 ↗
                      </a>
                    ) : (
                      <span key={j} data-testid="chat-citation" className="chat-cite muted">
                        {c.source_type}
                      </span>
                    ),
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="chat-turn agent" data-testid="chat-busy">
            <div className="chat-bubble">
              <span className="chat-typing">답변 생성 중…</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
