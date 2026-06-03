"use client";

import { formatArea, hogangnonoSearchUrl, naverSearchUrl } from "@/lib/format";
import type {
  AreaUnit,
  Candidate,
  CriterionEval,
  FloorplanSummary,
  GymSummary,
  PetSummary,
  ReviewSummary,
} from "@/lib/types";

// 상세 패널 = 차별점 hero. 근거·출처(criteria_eval ✓/△/✗/○ + 출처 딥링크) + 대표거래 + Tier-2
// 행(gym/pet/후기/평면도, 출처 보존) + 네이버/호갱노노 아웃링크. 실거래 추이 차트는 history 없어 OUT(spec §6).

function year(date: string | null): string {
  return date ? date.slice(0, 4) : "—";
}

type Source = { source_type: string; source_url: string };

function SourceLinks({ sources, prefix }: { sources: Source[]; prefix: string }) {
  if (sources.length === 0) return null;
  return (
    <>
      {" "}
      {sources.map((s, i) => (
        <span key={i}>
          {i > 0 && " · "}
          {s.source_url.startsWith("http") ? (
            <a
              data-testid={`${prefix}-source-link`}
              href={s.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="src"
            >
              출처 ↗
            </a>
          ) : (
            <span data-testid={`${prefix}-source-agent`} className="agent">
              에이전트 조사
            </span>
          )}
        </span>
      ))}
    </>
  );
}

// ── criteria_eval 행 (hero, .ic 박스) ──
const CRIT: Record<CriterionEval["status"], { icon: string; cls: string }> = {
  match: { icon: "✓", cls: "ok" },
  partial: { icon: "△", cls: "mid" },
  miss: { icon: "✗", cls: "miss" },
  unknown: { icon: "○", cls: "info" },
};
function critValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "있음" : "없음";
  if (typeof v === "number") return v.toLocaleString();
  return String(v);
}
function CriteriaEval({ evals, sourceUrl }: { evals: CriterionEval[]; sourceUrl: string | null }) {
  if (evals.length === 0) return null;
  return (
    <div data-testid="criteria-eval">
      {evals.map((ev) => {
        const s = CRIT[ev.status];
        return (
          <div className="r" data-testid="criteria-eval-row" key={ev.key}>
            <span className={`ic ${s.cls}`} data-testid="criteria-eval-status">
              {s.icon}
            </span>
            <div className="b">
              <div className="k">{ev.label}</div>
              <div className="v">
                {critValue(ev.value)}
                {ev.confidence != null && <span className="conf">conf {ev.confidence.toFixed(2)}</span>}
                {sourceUrl?.startsWith("http") && (
                  <>
                    {" "}
                    <a
                      data-testid="criteria-eval-source"
                      href={sourceUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="src"
                    >
                      K-apt ↗
                    </a>
                  </>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Tier-2 행 (출처/상태 보존 — 기존 카드 계약) ──
const GYM_ICON: Record<GymSummary["has_gym"], string> = { yes: "✓", no: "✗", unknown: "△", none: "" };
function GymRow({ gym }: { gym: GymSummary }) {
  if (gym.has_gym === "none") {
    return (
      <div className="r" data-testid="gym-row">
        <div className="b">
          <div className="k">헬스장</div>
          <div className="v">
            <span data-testid="gym-status">정보 없음 / 미조사</span>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="r" data-testid="gym-row">
      <div className="b">
        <div className="k">
          헬스장 <span data-testid="gym-status">{GYM_ICON[gym.has_gym]}</span>
        </div>
        <div className="v">
          {gym.evidence && <span data-testid="gym-evidence">{gym.evidence}</span>}
          {gym.confidence != null && <span className="conf">(conf {gym.confidence.toFixed(2)})</span>}
          <SourceLinks sources={gym.sources} prefix="gym" />
        </div>
      </div>
    </div>
  );
}

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
function PetRow({ pet }: { pet: PetSummary }) {
  const label = PET_LABEL[pet.pet_allowed];
  const status =
    pet.pet_allowed === "none" ? label : `${PET_ICON[pet.pet_allowed]} ${label}`.trim();
  return (
    <div className="r" data-testid="pet-row">
      <div className="b">
        <div className="k">
          강아지 <span data-testid="pet-status">{status}</span>
          {pet.confirm_with_office && (
            <span className="badge" data-testid="pet-confirm-badge">
              ⚠ 관리사무소 확인 권장
            </span>
          )}
        </div>
        <div className="v">
          {pet.evidence && <span data-testid="pet-evidence">{pet.evidence}</span>}
          {pet.confidence != null && <span className="conf">(conf {pet.confidence.toFixed(2)})</span>}
          {pet.caveats.length > 0 && (
            <span data-testid="pet-caveats"> · 제한: {pet.caveats.join(" · ")}</span>
          )}
          <SourceLinks sources={pet.sources} prefix="pet" />
        </div>
      </div>
    </div>
  );
}

function ReviewRow({ review }: { review: ReviewSummary }) {
  if (review.summary == null) {
    return (
      <div className="r" data-testid="review-row">
        <div className="b">
          <div className="k">후기</div>
          <div className="v">
            <span data-testid="review-status">정보 없음 / 미조사</span>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="r" data-testid="review-row">
      <div className="b">
        <div className="k">후기 (주관적)</div>
        <div className="v">
          <span data-testid="review-summary">{review.summary}</span>
          {review.confidence != null && (
            <span className="conf">(conf {review.confidence.toFixed(2)})</span>
          )}
          {review.points.length > 0 && (
            <span data-testid="review-points"> · {review.points.join(" · ")}</span>
          )}
          <SourceLinks sources={review.sources} prefix="review" />
        </div>
      </div>
    </div>
  );
}

function FloorplanRow({ floorplan }: { floorplan: FloorplanSummary }) {
  const parts = [
    floorplan.bay != null ? `${floorplan.bay}bay` : null,
    floorplan.orientation,
    floorplan.structure,
  ].filter((p): p is string => p != null);
  if (parts.length === 0) {
    return (
      <div className="r" data-testid="floorplan-row">
        <div className="b">
          <div className="k">평면도</div>
          <div className="v">
            <span data-testid="floorplan-status">정보 없음 / 미조사</span>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="r" data-testid="floorplan-row">
      <div className="b">
        <div className="k">평면도</div>
        <div className="v">
          <span data-testid="floorplan-features">{parts.join(" · ")}</span>
          {floorplan.confidence != null && (
            <span className="conf">(conf {floorplan.confidence.toFixed(2)})</span>
          )}
          {floorplan.evidence && <span data-testid="floorplan-evidence"> · {floorplan.evidence}</span>}
          <SourceLinks sources={floorplan.sources} prefix="floorplan" />
        </div>
      </div>
    </div>
  );
}

function repText(c: Candidate): string {
  const rep = c.representative_trade;
  if (!rep) return "—";
  if (rep.rent_type === "monthly" && rep.deposit != null) {
    return `월세 ${rep.deposit.toLocaleString()}/${(rep.monthly_rent ?? 0).toLocaleString()}만원`;
  }
  if (rep.rent_type === "jeonse" && rep.deposit != null) {
    return `전세 ${rep.deposit.toLocaleString()}만원`;
  }
  if (rep.price != null) return `${rep.price.toLocaleString()}만원`;
  return "—";
}

export function DetailPanel({
  candidate,
  unit,
  onClose,
}: {
  candidate: Candidate;
  unit: AreaUnit;
  onClose: () => void;
}) {
  const rep = candidate.representative_trade;
  const low = rep?.match_confidence != null && rep.match_confidence < 0.7;
  const tags: { text: string; brand?: boolean }[] = [];
  if (candidate.household_count != null) tags.push({ text: `${candidate.household_count.toLocaleString()}세대`, brand: true });
  if (candidate.floorplan?.structure) tags.push({ text: candidate.floorplan.structure });
  if (candidate.parking_ratio != null) tags.push({ text: `주차 ${candidate.parking_ratio.toFixed(2)}` });
  if (candidate.floorplan?.orientation) tags.push({ text: candidate.floorplan.orientation });

  return (
    <div className="detail" data-testid="complex-card">
      <div className="d-head">
        <button type="button" className="close" data-testid="detail-close" onClick={onClose}>
          ✕
        </button>
        <div className="nm">{candidate.name ?? "이름 미상"}</div>
        <div className="addr">
          사용승인 {year(candidate.approval_date)}
          {low && (
            <span data-testid="estimated-match-badge" className="badge" style={{ marginLeft: 8 }}>
              추정 매칭
            </span>
          )}
        </div>
        {tags.length > 0 && (
          <div className="tags">
            {tags.map((t, i) => (
              <span key={i} className={`tag${t.brand ? " b" : ""}`}>
                {t.text}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="d-price">
        <span className="big" data-testid="representative-trade">
          {repText(candidate)}
        </span>
        {rep && (
          <span className="meta">
            {rep.net_area != null ? `전용 ${formatArea(rep.net_area, unit)}` : ""}
            {rep.deal_date ? (
              <>
                <br />
                {rep.deal_date} 실거래
              </>
            ) : null}
          </span>
        )}
      </div>

      <div className="evhead">근거 · 출처</div>
      <div className="rows">
        {candidate.criteria_eval && candidate.criteria_eval.length > 0 && (
          <CriteriaEval evals={candidate.criteria_eval} sourceUrl={candidate.source_url} />
        )}
        {candidate.gym && <GymRow gym={candidate.gym} />}
        {candidate.pet && <PetRow pet={candidate.pet} />}
        {candidate.review && <ReviewRow review={candidate.review} />}
        {candidate.floorplan && <FloorplanRow floorplan={candidate.floorplan} />}
        {candidate.source_url && (
          <div className="r">
            <div className="b">
              <div className="v">
                <a
                  data-testid="source-link"
                  href={candidate.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="src"
                >
                  단지 출처(K-apt) ↗
                </a>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="outlink">
        <a data-testid="naver-link" href={naverSearchUrl(candidate.name)} target="_blank" rel="noopener noreferrer">
          네이버 매물 ↗
        </a>
        <a
          data-testid="hogangnono-link"
          href={hogangnonoSearchUrl(candidate.name)}
          target="_blank"
          rel="noopener noreferrer"
        >
          호갱노노 ↗
        </a>
      </div>
    </div>
  );
}
