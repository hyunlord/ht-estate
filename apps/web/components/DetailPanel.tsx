"use client";

import { useEffect, useState } from "react";

import { fetchEnrichment } from "@/lib/api";
import { formatArea, hogangnonoSearchUrl, naverSearchUrl, wonToShort } from "@/lib/format";
import type {
  AreaBucket,
  AreaUnit,
  Candidate,
  CriterionEval,
  FloorplanSummary,
  GymSection,
  GymSummary,
  PetSection,
  PetSummary,
  ReviewSummary,
} from "@/lib/types";

// 온디맨드 폴링 — 라이브 추출 ~22–60s(로컬 Gemma 경합). 3s 간격·최대 25회(≈75s) 후 멈춤.
const POLL_MS = 3000;
const MAX_POLLS = 25;

const NONE_GYM: GymSummary = { has_gym: "none", confidence: null, evidence: null, sources: [] };
const NONE_PET: PetSummary = {
  pet_allowed: "none",
  confidence: null,
  evidence: null,
  caveats: [],
  confirm_with_office: true,
  sources: [],
};

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

// ── 온디맨드 pending 스피너 행 (무한 스피너 금지 — MAX_POLLS 후 none으로 귀결) ──
function PendingRow({ label, prefix }: { label: string; prefix: string }) {
  return (
    <div className="r" data-testid={`${prefix}-row`}>
      <div className="b">
        <div className="k">
          {label} <span className="conf" data-testid={`${prefix}-pending`}>확인 중…</span>
        </div>
      </div>
    </div>
  );
}

// section(온디맨드)이 도착 전이면 fallback(검색 캐시)로 즉시 렌더 → pending이면 스피너 →
// ready/unavailable이면 summary(없으면 none = '정보 없음'). 무한 스피너 없음.
function GymBlock({ section, fallback }: { section: GymSection | null; fallback?: GymSummary | null }) {
  if (section?.status === "pending") return <PendingRow label="헬스장" prefix="gym" />;
  // ready=추출/캐시 결과(없으면 none) · unavailable/미도착=검색 캐시 fallback(있으면 그걸, 없으면 none).
  const gym =
    section?.status === "ready"
      ? (section.summary ?? NONE_GYM)
      : (fallback ?? (section ? NONE_GYM : null));
  return gym ? <GymRow gym={gym} /> : null;
}

function PetBlock({ section, fallback }: { section: PetSection | null; fallback?: PetSummary | null }) {
  if (section?.status === "pending") return <PendingRow label="강아지" prefix="pet" />;
  const pet =
    section?.status === "ready"
      ? (section.summary ?? NONE_PET)
      : (fallback ?? (section ? NONE_PET : null));
  return pet ? <PetRow pet={pet} /> : null;
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

// ── 평형별 실거래 브레이크다운 (detail-1) ──
// 다평형 건물 = 평형마다 한 줄(대표 전용·최근가+월·거래수). 단일평형이면 한 줄(과분할 없음).
// 금액축은 deal_type별: 매매=억축약 / 전세=보증금 / 월세=보증금/월세. 가격대는 min≠max일 때만.
function bucketAmount(b: AreaBucket): string {
  if (b.recent_amount == null) return "—";
  if (b.recent_rent_type === "monthly") {
    return `${wonToShort(b.recent_amount)}/${(b.recent_monthly_rent ?? 0).toLocaleString()}`;
  }
  return wonToShort(b.recent_amount);
}
function AreaBuckets({ buckets, unit }: { buckets: AreaBucket[]; unit: AreaUnit }) {
  if (buckets.length === 0) return null;
  return (
    <div className="abreak" data-testid="area-buckets">
      <div className="abreak-h">평형별 실거래</div>
      {buckets.map((b, i) => {
        const range =
          b.amount_min != null && b.amount_max != null && b.amount_min !== b.amount_max
            ? ` (${wonToShort(b.amount_min)}~${wonToShort(b.amount_max)})`
            : "";
        return (
          <div className="abreak-r" data-testid="area-bucket-row" key={i}>
            <span className="ab-a" data-testid="area-bucket-area">
              전용 {formatArea(b.net_area, unit)}
            </span>
            <span className="ab-p" data-testid="area-bucket-amount">
              {bucketAmount(b)}
              {b.recent_deal_date ? ` (${b.recent_deal_date.slice(0, 7)})` : ""}
              {range}
            </span>
            <span className="ab-n" data-testid="area-bucket-count">
              {b.transaction_count}건
            </span>
          </div>
        );
      })}
    </div>
  );
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
  // 온디맨드 gym/pet — 상세 진입 시 fetch(캐시 즉답·miss는 백그라운드+pending) 후 폴링으로 완료 픽업.
  // 검색 경로는 무관(_run_search 캐시 그대로) — 이 패널만 단건 온디맨드. graceful: 실패 시 캐시 유지.
  const cid = candidate.complex_id;
  const [gymSec, setGymSec] = useState<GymSection | null>(null);
  const [petSec, setPetSec] = useState<PetSection | null>(null);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ctrl = new AbortController();
    // 상태 리셋은 page.tsx의 key={complex_id} 리마운트로(이펙트 내 동기 setState 회피).
    const poll = async (attempt: number) => {
      try {
        const r = await fetchEnrichment(cid, ctrl.signal);
        if (cancelled) return;
        setGymSec(r.gym);
        setPetSec(r.pet);
        const pending = r.gym.status === "pending" || r.pet.status === "pending";
        if (pending && attempt < MAX_POLLS) timer = setTimeout(() => poll(attempt + 1), POLL_MS);
      } catch {
        /* graceful: 네트워크/엔드포인트 실패 → 검색 캐시(fallback) 유지, 스피너 안 검 */
      }
    };
    poll(0);
    return () => {
      cancelled = true;
      ctrl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [cid]);

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

      {candidate.area_buckets && candidate.area_buckets.length > 0 && (
        <AreaBuckets buckets={candidate.area_buckets} unit={unit} />
      )}

      <div className="evhead">근거 · 출처</div>
      <div className="rows">
        {candidate.criteria_eval && candidate.criteria_eval.length > 0 && (
          <CriteriaEval evals={candidate.criteria_eval} sourceUrl={candidate.source_url} />
        )}
        <GymBlock section={gymSec} fallback={candidate.gym} />
        <PetBlock section={petSec} fallback={candidate.pet} />
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
