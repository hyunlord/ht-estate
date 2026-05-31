"use client";

import type { Candidate, GymSummary } from "@/lib/types";

// 단지 카드 — hard 조건 ✓ 값 + 추정매칭 배지 + 출처 딥링크 + 대표거래 + Tier-2 gym(soft).
// gym은 hard filter 아님(R1) — 표시만. pet·floorplan·후기는 Phase 1+.

function year(date: string | null): string {
  return date ? date.slice(0, 4) : "—";
}

// gym 상태 → 아이콘. none(미조사)은 아이콘 없이 텍스트로 unknown(불명)과 구분.
const GYM_ICON: Record<GymSummary["has_gym"], string> = {
  yes: "✓",
  no: "✗",
  unknown: "△",
  none: "",
};

function GymRow({ gym }: { gym: GymSummary }) {
  if (gym.has_gym === "none") {
    return (
      <div data-testid="gym-row" className="text-sm">
        <span className="text-zinc-500">헬스장</span>{" "}
        <span data-testid="gym-status">정보 없음 / 미조사</span>
      </div>
    );
  }
  return (
    <div data-testid="gym-row" className="text-sm">
      <span className="text-zinc-500">헬스장</span>{" "}
      <span data-testid="gym-status">{GYM_ICON[gym.has_gym]}</span>{" "}
      {gym.evidence && <span data-testid="gym-evidence">{gym.evidence}</span>}
      {gym.confidence != null && (
        <span className="text-zinc-400"> (conf {gym.confidence.toFixed(2)})</span>
      )}
      {gym.sources.length > 0 && (
        <span className="ml-1">
          ↳ 출처:{" "}
          {gym.sources.map((s, i) => (
            <span key={i}>
              {i > 0 && " · "}
              {s.source_url.startsWith("http") ? (
                <a
                  data-testid="gym-source-link"
                  href={s.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 underline"
                >
                  이동 ↗
                </a>
              ) : (
                <span data-testid="gym-source-agent" className="text-zinc-500">
                  에이전트 조사
                </span>
              )}
            </span>
          ))}
        </span>
      )}
    </div>
  );
}

export function ComplexCard({ candidate }: { candidate: Candidate }) {
  const rep = candidate.representative_trade;
  const lowConfidence = rep?.match_confidence != null && rep.match_confidence < 0.7;

  return (
    <article data-testid="complex-card" className="flex flex-col gap-2 rounded border border-zinc-200 p-4">
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-lg font-semibold">{candidate.name ?? "이름 미상"}</h3>
        {lowConfidence && (
          <span
            data-testid="estimated-match-badge"
            className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800"
          >
            추정 매칭
          </span>
        )}
      </header>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
        <dt className="text-zinc-500">사용승인</dt>
        <dd>✓ {year(candidate.approval_date)}</dd>
        <dt className="text-zinc-500">세대당 주차</dt>
        <dd>{candidate.parking_ratio != null ? `✓ ${candidate.parking_ratio.toFixed(2)}대` : "—"}</dd>
        <dt className="text-zinc-500">지하주차</dt>
        <dd>{candidate.parking_underground != null ? `✓ ${candidate.parking_underground}대` : "—"}</dd>
        <dt className="text-zinc-500">세대수</dt>
        <dd>{candidate.household_count ?? "—"}</dd>
      </dl>

      {candidate.gym && <GymRow gym={candidate.gym} />}

      {rep && (
        <p className="text-sm" data-testid="representative-trade">
          실거래 {rep.net_area != null ? `전용 ${rep.net_area}㎡ ` : ""}
          {rep.price != null ? `${rep.price.toLocaleString()}만원 ` : ""}
          {rep.deal_date ? `(${rep.deal_date})` : ""}
        </p>
      )}

      {candidate.source_url && (
        <a
          data-testid="source-link"
          href={candidate.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm font-medium text-blue-600 underline"
        >
          출처 이동 ↗
        </a>
      )}
    </article>
  );
}
