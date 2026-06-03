"use client";

import type {
  Candidate,
  CriterionEval,
  FloorplanSummary,
  GymSummary,
  PetSummary,
  ReviewSummary,
} from "@/lib/types";

// 단지 카드 — 조건 평가(criteria_eval ✓/△/○ + 출처) + 대표거래 + Tier-2 soft(gym·pet·후기·평면도).
// 모든 사실에 출처(딥링크). soft는 hard filter 아님(R1) — 표시·랭킹근거만.

function year(date: string | null): string {
  return date ? date.slice(0, 4) : "—";
}

type Source = { source_type: string; source_url: string };

// 공유 출처 딥링크 — http는 클릭(새 탭), urn sentinel은 "에이전트 조사" 비링크. prefix로 testid 구분.
function SourceLinks({ sources, prefix }: { sources: Source[]; prefix: string }) {
  if (sources.length === 0) return null;
  return (
    <span className="ml-1">
      ↳ 출처:{" "}
      {sources.map((s, i) => (
        <span key={i}>
          {i > 0 && " · "}
          {s.source_url.startsWith("http") ? (
            <a
              data-testid={`${prefix}-source-link`}
              href={s.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-cool underline decoration-dotted underline-offset-2 hover:text-ink"
            >
              이동 ↗
            </a>
          ) : (
            <span data-testid={`${prefix}-source-agent`} className="text-faint">
              에이전트 조사
            </span>
          )}
        </span>
      ))}
    </span>
  );
}

// ── 조건 평가(P4-2a) — 랭킹 근거 ✓/△/✗/○. status→아이콘·색. 출처=단지(K-apt source_url) 딥링크. ──
const CRIT_ICON: Record<CriterionEval["status"], string> = {
  match: "✓",
  partial: "△",
  miss: "✗",
  unknown: "○",
};
const CRIT_COLOR: Record<CriterionEval["status"], string> = {
  match: "text-green",
  partial: "text-warm",
  miss: "text-pink",
  unknown: "text-cool",
};

function critValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "있음" : "없음";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

function CriteriaEval({
  evals,
  sourceUrl,
}: {
  evals: CriterionEval[];
  sourceUrl: string | null;
}) {
  if (evals.length === 0) return null;
  return (
    <section data-testid="criteria-eval" className="flex flex-col gap-1.5">
      <span className="eyebrow">조건 평가 · 근거</span>
      <div className="flex flex-col">
        {evals.map((ev) => (
          <div
            key={ev.key}
            data-testid="criteria-eval-row"
            className="flex items-baseline gap-2.5 border-b border-border-soft py-1.5 text-[12.5px] last:border-0"
          >
            <span
              data-testid="criteria-eval-status"
              className={`w-3.5 flex-none text-center font-mono ${CRIT_COLOR[ev.status]}`}
            >
              {CRIT_ICON[ev.status]}
            </span>
            <span className="w-20 flex-none font-medium text-ink">{ev.label}</span>
            <span className="flex-1 text-muted">
              {critValue(ev.value)}
              {ev.confidence != null && (
                <span className="text-faint"> · conf {ev.confidence.toFixed(2)}</span>
              )}
            </span>
            {sourceUrl?.startsWith("http") && (
              <a
                data-testid="criteria-eval-source"
                href={sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-none whitespace-nowrap font-mono text-[10.5px] text-cool decoration-dotted underline-offset-2 hover:text-ink hover:underline"
              >
                K-apt ↗
              </a>
            )}
          </div>
        ))}
      </div>
    </section>
  );
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
      <div data-testid="gym-row" className="text-[12.5px] text-muted">
        <span className="text-faint">헬스장</span>{" "}
        <span data-testid="gym-status">정보 없음 / 미조사</span>
      </div>
    );
  }
  return (
    <div data-testid="gym-row" className="text-[12.5px] text-muted">
      <span className="text-faint">헬스장</span>{" "}
      <span data-testid="gym-status">{GYM_ICON[gym.has_gym]}</span>{" "}
      {gym.evidence && <span data-testid="gym-evidence">{gym.evidence}</span>}
      {gym.confidence != null && (
        <span className="text-faint"> (conf {gym.confidence.toFixed(2)})</span>
      )}
      <SourceLinks sources={gym.sources} prefix="gym" />
    </div>
  );
}

// pet 상태 → 아이콘 + 라벨. conditional·unknown 둘 다 △지만 라벨로 구분(조건부 vs 확인 불가).
const PET_ICON: Record<PetSummary["pet_allowed"], string> = {
  yes: "✓",
  conditional: "△",
  no: "✗",
  unknown: "△",
  none: "",
};
const PET_LABEL: Record<PetSummary["pet_allowed"], string> = {
  yes: "",
  conditional: "조건부",
  no: "",
  unknown: "확인 불가",
  none: "정보 없음",
};

// §11 "가장 약한 고리": 모든 pet 행에 관리사무소 확인 권고를 표면화(보수성).
function ConfirmBadge() {
  return (
    <span
      data-testid="pet-confirm-badge"
      className="ml-1 rounded border border-warm-line bg-[var(--warm-bg)] px-1.5 py-0.5 text-[11px] text-warm"
    >
      ⚠ 관리사무소 확인 권장
    </span>
  );
}

function PetRow({ pet }: { pet: PetSummary }) {
  const label = PET_LABEL[pet.pet_allowed];
  const status =
    pet.pet_allowed === "none" ? label : `${PET_ICON[pet.pet_allowed]} ${label}`.trim();
  return (
    <div data-testid="pet-row" className="text-[12.5px] text-muted">
      <span className="text-faint">강아지</span>{" "}
      <span data-testid="pet-status">{status}</span>{" "}
      {pet.evidence && <span data-testid="pet-evidence">{pet.evidence}</span>}
      {pet.confidence != null && (
        <span className="text-faint"> (conf {pet.confidence.toFixed(2)})</span>
      )}
      {pet.caveats.length > 0 && (
        <span data-testid="pet-caveats" className="text-warm">
          {" "}
          · 제한: {pet.caveats.join(" · ")}
        </span>
      )}
      {pet.confirm_with_office && <ConfirmBadge />}
      <SourceLinks sources={pet.sources} prefix="pet" />
    </div>
  );
}

// 후기(P3-1) — 표시 전용(랭킹 신호 아님). 짧은 요약 + 핵심 포인트 + 출처 딥링크.
function ReviewRow({ review }: { review: ReviewSummary }) {
  if (review.summary == null) {
    return (
      <div data-testid="review-row" className="text-[12.5px] text-muted">
        <span className="text-faint">후기</span>{" "}
        <span data-testid="review-status">정보 없음 / 미조사</span>
      </div>
    );
  }
  return (
    <div data-testid="review-row" className="text-[12.5px] text-muted">
      <span className="text-faint">후기</span> <span className="text-faint">(주관적)</span>{" "}
      <span data-testid="review-summary">{review.summary}</span>
      {review.confidence != null && (
        <span className="text-faint"> (conf {review.confidence.toFixed(2)})</span>
      )}
      {review.points.length > 0 && (
        <span data-testid="review-points" className="text-muted">
          {" "}
          · {review.points.join(" · ")}
        </span>
      )}
      <SourceLinks sources={review.sources} prefix="review" />
    </div>
  );
}

// 평면도(P3-2) — 표시 전용(랭킹 신호 아님). 객관 feature(bay·향·판상/타워) + 출처. §11: 점수화 아님.
function FloorplanRow({ floorplan }: { floorplan: FloorplanSummary }) {
  const parts = [
    floorplan.bay != null ? `${floorplan.bay}bay` : null,
    floorplan.orientation,
    floorplan.structure,
  ].filter((p): p is string => p != null);
  if (parts.length === 0) {
    return (
      <div data-testid="floorplan-row" className="text-[12.5px] text-muted">
        <span className="text-faint">평면도</span>{" "}
        <span data-testid="floorplan-status">정보 없음 / 미조사</span>
      </div>
    );
  }
  return (
    <div data-testid="floorplan-row" className="text-[12.5px] text-muted">
      <span className="text-faint">평면도</span>{" "}
      <span data-testid="floorplan-features">{parts.join(" · ")}</span>
      {floorplan.confidence != null && (
        <span className="text-faint"> (conf {floorplan.confidence.toFixed(2)})</span>
      )}
      {floorplan.evidence && (
        <span data-testid="floorplan-evidence" className="text-muted">
          {" "}
          · {floorplan.evidence}
        </span>
      )}
      <SourceLinks sources={floorplan.sources} prefix="floorplan" />
    </div>
  );
}

export function ComplexCard({ candidate }: { candidate: Candidate }) {
  const rep = candidate.representative_trade;
  const lowConfidence = rep?.match_confidence != null && rep.match_confidence < 0.7;

  return (
    <article
      data-testid="complex-card"
      className="rise flex flex-col gap-3 rounded-xl border border-border-soft bg-surface p-4 shadow-[0_16px_40px_-24px_rgba(0,0,0,0.8)]"
      style={{ borderLeft: "3px solid var(--cool)" }}
    >
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold tracking-tight text-ink">
          {candidate.name ?? "이름 미상"}
        </h3>
        {lowConfidence && (
          <span
            data-testid="estimated-match-badge"
            className="flex-none rounded-full border border-warm-line bg-[var(--warm-bg)] px-2 py-0.5 font-mono text-[10px] text-warm"
          >
            추정 매칭
          </span>
        )}
      </header>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 font-mono text-[12px]">
        <dt className="text-faint">사용승인</dt>
        <dd className="text-ink">✓ {year(candidate.approval_date)}</dd>
        <dt className="text-faint">세대당 주차</dt>
        <dd className="text-ink">
          {candidate.parking_ratio != null ? `✓ ${candidate.parking_ratio.toFixed(2)}대` : "—"}
        </dd>
        <dt className="text-faint">지하주차</dt>
        <dd className="text-ink">
          {candidate.parking_underground != null ? `✓ ${candidate.parking_underground}대` : "—"}
        </dd>
        <dt className="text-faint">세대수</dt>
        <dd className="text-ink">{candidate.household_count ?? "—"}</dd>
      </dl>

      {candidate.criteria_eval && candidate.criteria_eval.length > 0 && (
        <CriteriaEval evals={candidate.criteria_eval} sourceUrl={candidate.source_url} />
      )}

      <div className="flex flex-col gap-1.5 border-t border-border-soft pt-3">
        {candidate.gym && <GymRow gym={candidate.gym} />}
        {candidate.pet && <PetRow pet={candidate.pet} />}
        {candidate.review && <ReviewRow review={candidate.review} />}
        {candidate.floorplan && <FloorplanRow floorplan={candidate.floorplan} />}
      </div>

      {rep && (
        <p className="font-mono text-[12px] text-cool" data-testid="representative-trade">
          실거래 {rep.net_area != null ? `전용 ${rep.net_area}㎡ ` : ""}
          {/* 거래유형별 금액축: 매매=가격 / 전세=보증금 / 월세=보증금/월세 */}
          {rep.price != null ? `${rep.price.toLocaleString()}만원 ` : ""}
          {rep.rent_type === "jeonse" && rep.deposit != null
            ? `전세 ${rep.deposit.toLocaleString()}만원 `
            : ""}
          {rep.rent_type === "monthly" && rep.deposit != null
            ? `월세 ${rep.deposit.toLocaleString()}/${(rep.monthly_rent ?? 0).toLocaleString()}만원 `
            : ""}
          {rep.deal_date ? `(${rep.deal_date})` : ""}
        </p>
      )}

      {candidate.source_url && (
        <a
          data-testid="source-link"
          href={candidate.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-[11px] font-medium text-cool underline decoration-dotted underline-offset-2 hover:text-ink"
        >
          출처 이동 ↗
        </a>
      )}
    </article>
  );
}
