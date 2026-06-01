"use client";

import type { Candidate, GymSummary, PetSummary, ReviewSummary } from "@/lib/types";

// 단지 카드 — hard 조건 ✓ 값 + 추정매칭 배지 + 출처 딥링크 + 대표거래 + Tier-2 soft(gym·pet).
// soft는 hard filter 아님(R1) — 표시만. floorplan·후기는 Phase 1+.

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
              className="text-blue-600 underline"
            >
              이동 ↗
            </a>
          ) : (
            <span data-testid={`${prefix}-source-agent`} className="text-zinc-500">
              에이전트 조사
            </span>
          )}
        </span>
      ))}
    </span>
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
      className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800"
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
    <div data-testid="pet-row" className="text-sm">
      <span className="text-zinc-500">강아지</span>{" "}
      <span data-testid="pet-status">{status}</span>{" "}
      {pet.evidence && <span data-testid="pet-evidence">{pet.evidence}</span>}
      {pet.confidence != null && (
        <span className="text-zinc-400"> (conf {pet.confidence.toFixed(2)})</span>
      )}
      {pet.caveats.length > 0 && (
        <span data-testid="pet-caveats" className="text-amber-700">
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
// summary 없으면 '미조사'. 주관적이라 출처 전부 노출(원문은 출처에서 — 저작권: 요약은 짧게).
function ReviewRow({ review }: { review: ReviewSummary }) {
  if (review.summary == null) {
    return (
      <div data-testid="review-row" className="text-sm">
        <span className="text-zinc-500">후기</span>{" "}
        <span data-testid="review-status">정보 없음 / 미조사</span>
      </div>
    );
  }
  return (
    <div data-testid="review-row" className="text-sm">
      <span className="text-zinc-500">후기</span>{" "}
      <span className="text-zinc-400">(주관적)</span>{" "}
      <span data-testid="review-summary">{review.summary}</span>
      {review.confidence != null && (
        <span className="text-zinc-400"> (conf {review.confidence.toFixed(2)})</span>
      )}
      {review.points.length > 0 && (
        <span data-testid="review-points" className="text-zinc-600">
          {" "}
          · {review.points.join(" · ")}
        </span>
      )}
      <SourceLinks sources={review.sources} prefix="review" />
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
      {candidate.pet && <PetRow pet={candidate.pet} />}
      {candidate.review && <ReviewRow review={candidate.review} />}

      {rep && (
        <p className="text-sm" data-testid="representative-trade">
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
          className="text-sm font-medium text-blue-600 underline"
        >
          출처 이동 ↗
        </a>
      )}
    </article>
  );
}
