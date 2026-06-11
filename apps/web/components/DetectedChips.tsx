"use client";

import { chipId, chipLevelOptions, LEVEL_LABELS, type ChipLevel } from "@/lib/nlChips";
import type { Detected } from "@/lib/types";

// 감지 조건 칩 strip(#3b) — NL이 뭘 파싱했는지 노출 + 칩별 강/약/제외 가중치 조정.
// 강=hard 유지(또는 강한 soft) · 약=soft 랭킹 · 제외=조건 제거. 매핑 불가 구절은 muted 노트.
// 가중치 조정 → onLevelChange → 페이지가 조정 spec으로 재검색(demote-not-exclude).

export function DetectedChips({
  detected,
  levels,
  unsupported,
  reputationQuery,
  onLevelChange,
  onClear,
}: {
  detected: Detected[];
  levels: Record<string, ChipLevel>;
  unsupported: string[];
  reputationQuery?: string | null; // reputation-routing: 주관 평판 의도(필터 아님 — 별도 표식)
  onLevelChange: (id: string, level: ChipLevel) => void;
  onClear?: () => void;
}) {
  if (detected.length === 0 && unsupported.length === 0 && !reputationQuery) return null;

  return (
    <div className="detected" data-testid="detected-chips">
      <span className="detected-title">감지된 조건</span>
      <div className="detected-row">
        {detected.map((d) => {
          const id = chipId(d);
          const level = levels[id] ?? "strong";
          const opts = chipLevelOptions(d);
          return (
            <div
              key={id}
              className={`dchip lv-${level}`}
              data-testid={`detected-chip-${d.criterion_key}`}
            >
              <span className="dchip-label" title={d.phrase ?? undefined}>
                {d.label}
                {d.phrase ? <span className="dchip-phrase">“{d.phrase}”</span> : null}
              </span>
              <div className="dchip-seg" role="group" aria-label={`${d.label} 가중치`}>
                {opts.map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    data-testid={`chip-level-${d.criterion_key}-${opt}`}
                    aria-pressed={level === opt}
                    className={level === opt ? "on" : ""}
                    onClick={() => onLevelChange(id, opt)}
                  >
                    {LEVEL_LABELS[opt]}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
        {detected.length > 0 && onClear ? (
          <button type="button" className="dchip-clear" data-testid="detected-clear" onClick={onClear}>
            지우기
          </button>
        ) : null}
      </div>
      {reputationQuery ? (
        <div className="detected-reputation" data-testid="nl-reputation">
          <span className="rep-badge">평판</span> “{reputationQuery}” — 단지 상세에서 후기 분석
        </div>
      ) : null}
      {unsupported.length > 0 ? (
        <div className="detected-unsup" data-testid="nl-unsupported">
          반영 못 함: {unsupported.join(" · ")}
        </div>
      ) : null}
    </div>
  );
}
