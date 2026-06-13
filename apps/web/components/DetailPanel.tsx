"use client";

import { useEffect, useRef, useState } from "react";

import { fetchEnrichment, fetchReputation, fetchUnitTypes } from "@/lib/api";
import { formatArea, hogangnonoSearchUrl, naverSearchUrl, wonToShort } from "@/lib/format";
import type {
  AreaBucket,
  AreaUnit,
  AssignmentRow,
  Candidate,
  Citation,
  CriterionEval,
  FloorplanSummary,
  GymSection,
  GymSummary,
  PetSection,
  PetSummary,
  PoiNear,
  ReputationResponse,
  SchoolNear,
  ReviewSummary,
  UnitTypeCatalog,
  UnitTypeRow,
} from "@/lib/types";

// 온디맨드 폴링 — 라이브 추출 ~22–60s(로컬 Gemma 경합). 3s 간격·최대 25회(≈75s) 후 멈춤.
const POLL_MS = 3000;
const MAX_POLLS = 25;

const NONE_GYM: GymSummary = { has_gym: "none", confidence: null, evidence: null, sources: [] };
const NONE_PET: PetSummary = {
  pet_allowed: "none", confidence: null, evidence: null,
  caveats: [], confirm_with_office: true, sources: [],
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
// detail-panel-polish ④: 단정(✓있음/✗없음)은 신뢰 가능할 때만 — 비아파트는 K-apt amenities가 없어
// 저신뢰 온디맨드 추출이라 단정 "없음"이 오탐(예 KCC: agent_research conf 0.31). 고신뢰(conf≥0.7) 또는
// 신뢰 출처(official/kapt)일 때만 단정·아니면 advisory(△ "정보 없음 · 확인 권장"). 검색 필터는 무관(표시만).
// gym-kakao: Kakao Local 동일위치 헬스장 POI = 신뢰 출처(물리 co-located·conf 0.88도 ≥0.7 게이트 통과).
const GYM_TRUST_SOURCES = new Set(["official", "kapt", "kakao_local"]);
function gymDefinitive(gym: GymSummary): boolean {
  if (gym.confidence != null && gym.confidence >= 0.7) return true;
  return gym.sources.some((s) => GYM_TRUST_SOURCES.has(s.source_type));
}
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
  // 저신뢰 yes/no·unknown → advisory(오탐 단정 방지). 신뢰 가능 yes/no만 ✓/✗.
  const advisory =
    gym.has_gym === "unknown" ||
    ((gym.has_gym === "yes" || gym.has_gym === "no") && !gymDefinitive(gym));
  return (
    <div className="r" data-testid="gym-row">
      <div className="b">
        <div className="k">
          헬스장 <span data-testid="gym-status">{advisory ? "△" : GYM_ICON[gym.has_gym]}</span>
        </div>
        <div className="v">
          {advisory && <span data-testid="gym-advisory">정보 없음 · 확인 권장</span>}
          {gym.evidence && <span data-testid="gym-evidence">{gym.evidence}</span>}
          {gym.confidence != null && <span className="conf">(conf {gym.confidence.toFixed(2)})</span>}
          <SourceLinks sources={gym.sources} prefix="gym" />
        </div>
      </div>
    </div>
  );
}

// pet-evidence: PetRow 재추가(C80서 제거됨) — 이젠 gemma 검증 증거 기반(pet_verified). ★★ 안전 바닥:
// 반려동물 허용은 관리규약·세대/견종별 가변·잘못된 "가능"이 실제 피해 → **무조건 advisory**(하드 ✓
// "가능" 절대 금지·항상 "관리사무소 확인 권장"+견종/무게 단서+출처). 'allowed'도 definitive yes 아님.
const PET_LABEL: Record<PetSummary["pet_allowed"], string> = {
  yes: "가능(확인 권장)", conditional: "조건부", no: "불가", unknown: "미확인", none: "",
};
function PetRow({ pet }: { pet: PetSummary }) {
  if (pet.pet_allowed === "none") {
    return (
      <div className="r" data-testid="pet-row">
        <div className="b">
          <div className="k">반려동물</div>
          <div className="v">
            <span data-testid="pet-status">정보 없음 / 미조사</span>
          </div>
        </div>
      </div>
    );
  }
  // ★ pet은 무조건 advisory — 하드 ✓ 아이콘 없음(텍스트 라벨)·항상 관리사무소 확인 안내.
  return (
    <div className="r" data-testid="pet-row">
      <div className="b">
        <div className="k">
          반려동물 <span data-testid="pet-status">{PET_LABEL[pet.pet_allowed]}</span>
        </div>
        <div className="v">
          <span data-testid="pet-advisory">관리규약·세대별 상이 · 확인 권장: 관리사무소</span>
          {pet.evidence && <span data-testid="pet-evidence">{pet.evidence}</span>}
          {pet.caveats.map((c, i) => (
            <span key={i} data-testid="pet-caveat" className="conf">
              {c}
            </span>
          ))}
          {pet.confidence != null && <span className="conf">(conf {pet.confidence.toFixed(2)})</span>}
          <SourceLinks sources={pet.sources} prefix="pet" />
        </div>
      </div>
    </div>
  );
}

function PetBlock({ section, fallback }: { section: PetSection | null; fallback?: PetSummary | null }) {
  if (section?.status === "pending") return <PendingRow label="반려동물" prefix="pet" />;
  const pet =
    section?.status === "ready"
      ? (section.summary ?? NONE_PET)
      : (fallback ?? (section ? NONE_PET : null));
  return pet ? <PetRow pet={pet} /> : null;
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

// ── POI 근접(poi-1, 정적 eager) — computed-or-dash. 미적재(배치 안 돈 단지)는 '정보 없음'. ──
function PoiSection({ poi }: { poi?: PoiNear[] | null }) {
  if (!poi || poi.length === 0) {
    return (
      <div className="r" data-testid="poi-row">
        <div className="b">
          <div className="k">주변</div>
          <div className="v">
            <span data-testid="poi-status">정보 없음 / 미계산</span>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="r" data-testid="poi-row">
      <div className="b">
        <div className="k">주변</div>
        <div className="v" data-testid="poi-list">
          {poi.map((p, i) => (
            <span key={p.category} data-testid={`poi-${p.category}`}>
              {i > 0 && " · "}
              {p.label} {p.nearest_dist_m != null ? `${p.nearest_dist_m}m` : "—"}
              {p.count_1km != null && p.count_1km > 0 ? ` (1km ${p.count_1km})` : ""}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── 학교 거리 근접(school-1, 정적 eager) — 가까운 초/중/고 + 거리. computed-or-dash. ──
const SCHOOL_ORDER: SchoolNear["level"][] = ["elem", "mid", "high"];
function SchoolSection({ school }: { school?: SchoolNear[] | null }) {
  if (!school || school.length === 0) {
    return (
      <div className="r" data-testid="school-row">
        <div className="b">
          <div className="k">학교</div>
          <div className="v">
            <span data-testid="school-status">정보 없음 / 미계산</span>
          </div>
        </div>
      </div>
    );
  }
  const byLevel = new Map(school.map((s) => [s.level, s]));
  return (
    <div className="r" data-testid="school-row">
      <div className="b">
        <div className="k">학교 (거리)</div>
        <div className="v" data-testid="school-list">
          {SCHOOL_ORDER.filter((lv) => byLevel.has(lv)).map((lv, i) => {
            const s = byLevel.get(lv)!;
            return (
              <span key={lv} data-testid={`school-${lv}`}>
                {i > 0 && " · "}
                {s.label} {s.nearest_name ?? "—"}{" "}
                {s.nearest_dist_m != null ? `${s.nearest_dist_m}m` : "—"}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── 배정 초등 통학구역(school-2, advisory) — 거리 섹션 아래. 단정 금지·교육청 확인 권장. ──
function AssignmentSection({ assignment }: { assignment?: AssignmentRow[] | null }) {
  if (!assignment || assignment.length === 0) {
    return (
      <div className="r" data-testid="assignment-row">
        <div className="b">
          <div className="k">배정 초등 (통학구역)</div>
          <div className="v">
            <span data-testid="assignment-status">정보 없음 / 미계산</span>
          </div>
        </div>
      </div>
    );
  }
  const names = assignment.map((a) => a.school_name ?? "—");
  const shared = assignment.some((a) => a.is_shared) || names.length > 1;
  return (
    <div className="r" data-testid="assignment-row">
      <div className="b">
        <div className="k">
          배정 초등 (통학구역)
          <span className="badge" data-testid="assignment-confirm-badge">
            ⚠ 교육청 확인 권장
          </span>
        </div>
        <div className="v">
          <span data-testid="assignment-schools">
            {shared ? `${names.join(" 또는 ")} (공동통학구역)` : names[0]}
          </span>
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
  // detail-panel-polish ①: 보증금은 raw(30,000) 대신 억 축약 + 명확 라벨(보증금/월세 둘 다 노출).
  if (rep.rent_type === "monthly" && rep.deposit != null) {
    return `보증금 ${wonToShort(rep.deposit)} · 월세 ${(rep.monthly_rent ?? 0).toLocaleString()}만원`;
  }
  if (rep.rent_type === "jeonse" && rep.deposit != null) {
    return `전세 ${wonToShort(rep.deposit)}`;
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
// ── unit-type-catalog: 전 세대타입(거래+미거래) — 행=전용면적·세대수·(실거래/미거래) ──
function unitAmount(r: UnitTypeRow): string {
  if (r.recent_amount == null) return "—";
  if (r.recent_rent_type === "monthly") {
    return `${wonToShort(r.recent_amount)}/${(r.recent_monthly_rent ?? 0).toLocaleString()}`;
  }
  return wonToShort(r.recent_amount);
}
function UnitTypes({ types, unit }: { types: UnitTypeRow[]; unit: AreaUnit }) {
  if (types.length === 0) return null;
  return (
    <div className="abreak" data-testid="unit-types">
      <div className="abreak-h">전체 세대타입</div>
      {types.map((r, i) => (
        <div className="abreak-r" data-testid="unit-type-row" key={i}>
          <span className="ab-a" data-testid="unit-type-area">
            전용 {formatArea(r.net_area, unit)}
          </span>
          <span className="ab-n" data-testid="unit-type-households">
            {r.household_count != null ? `${r.household_count.toLocaleString()}세대` : ""}
          </span>
          <span className="ab-p" data-testid="unit-type-amount">
            {r.traded ? (
              <>
                {unitAmount(r)}
                {r.recent_deal_date ? ` (${r.recent_deal_date.slice(0, 7)})` : ""}
                {` · ${r.transaction_count}건`}
              </>
            ) : (
              <span className="untraded" data-testid="unit-type-untraded">
                미거래
              </span>
            )}
          </span>
        </div>
      ))}
    </div>
  );
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

// ── 후기/평판 RAG 섹션(E3-3) — 프리셋 칩 → reputation 엔드포인트 → 종합+인용·advisory. ──
// 검색 패스 아님(느림·detail 트리거). pending=코퍼스 수집/분석 중(폴링). summary 없고 인용만이면
// evidence-only(gemma degrade) — 인용 노출. 코퍼스 없음/unavailable → '정보 없음'(computed-or-dash).
const REPUTATION_PRESETS: { label: string; query: string }[] = [
  { label: "주차", query: "주차 어때" },
  { label: "층간소음", query: "층간소음 어때" },
  { label: "관리", query: "관리 상태 어때" },
  { label: "교통", query: "교통 편의 어때" },
  { label: "소음", query: "소음 어때" },
];
const REP_POLL_MS = 3000;
const REP_MAX_POLLS = 25;

function CitationLinks({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <div className="v" data-testid="reputation-citations">
      {citations.map((c, i) => (
        <span key={i} data-testid="reputation-citation">
          {i > 0 && " · "}
          {c.source_url.startsWith("http") ? (
            <a
              data-testid="reputation-citation-link"
              href={c.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="src"
            >
              {c.source_type}
              {c.span_ref ? ` ${c.span_ref}` : ""} ↗
            </a>
          ) : (
            <span className="agent">{c.source_type}</span>
          )}
        </span>
      ))}
    </div>
  );
}

function ReputationSection({ cid, reputationQuery }: { cid: string; reputationQuery?: string | null }) {
  const [active, setActive] = useState<string | null>(null);
  const [result, setResult] = useState<ReputationResponse | null>(null);
  const [loading, setLoading] = useState(false);

  // reputation-routing: 검색 NL이 평판 의도를 표했으면 detail 진입 시 그 쿼리로 평판 섹션 자동 트리거
  // (pre-seed). detail-트리거·lazy 유지(검색 경로 인라인 synth 아님). 패널은 cid로 리마운트라 1회.
  useEffect(() => {
    if (reputationQuery) ask(reputationQuery);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid, reputationQuery]);

  function ask(query: string) {
    setActive(query);
    setResult(null);
    setLoading(true);
    const ctrl = new AbortController();
    const poll = async (attempt: number) => {
      try {
        const r = await fetchReputation(cid, query, ctrl.signal);
        setResult(r);
        // pending(코퍼스 수집 중) → 폴링 계속(MAX 후 멈춤·무한 스피너 없음).
        if (r.status === "pending" && attempt < REP_MAX_POLLS) {
          setTimeout(() => poll(attempt + 1), REP_POLL_MS);
        } else {
          setLoading(false);
        }
      } catch {
        setLoading(false); // graceful: 네트워크/엔드포인트 실패 → 조용히 멈춤(스피너 해제)
      }
    };
    poll(0);
  }

  const pending = loading || result?.status === "pending";
  return (
    <div className="r" data-testid="reputation-row">
      <div className="b">
        <div className="k">
          후기 · 평판 (RAG)
          <span className="badge" data-testid="reputation-advisory">
            ⚠ 회자되는 평판 · 확인 권장
          </span>
        </div>
        <div className="v">
          <div className="rep-chips" data-testid="reputation-chips">
            {REPUTATION_PRESETS.map((p) => (
              <button
                type="button"
                key={p.query}
                data-testid="reputation-chip"
                data-query={p.query}
                className={`chip${active === p.query ? " on" : ""}`}
                onClick={() => ask(p.query)}
              >
                {p.label}
              </button>
            ))}
          </div>
          {pending && (
            <span className="conf" data-testid="reputation-pending">
              후기 수집/분석 중…
            </span>
          )}
          {!pending && result?.status === "unavailable" && (
            <span data-testid="reputation-status">후기 분석 미구성</span>
          )}
          {!pending && result?.status === "ready" && (
            <>
              {result.summary ? (
                <span data-testid="reputation-summary">{result.summary}</span>
              ) : (
                result.citations.length === 0 && (
                  // rag-corpus-quality: 코퍼스 0/thin·매치 0 → 빈 "정보 없음"이 아니라 정직하게 "미수집".
                  // 청크가 있으면 위 summary 또는 아래 인용이 렌더(현재 동작 유지).
                  <span className="conf" data-testid="reputation-empty">
                    아직 수집된 후기가 없어요 (후기 미수집)
                  </span>
                )
              )}
              <CitationLinks citations={result.citations} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function DetailPanel({
  candidate,
  unit,
  reputationQuery,
  onClose,
}: {
  candidate: Candidate;
  unit: AreaUnit;
  reputationQuery?: string | null; // reputation-routing: NL 평판 의도 → 평판 섹션 자동 트리거
  onClose: () => void;
}) {
  // 온디맨드 gym — 상세 진입 시 fetch(캐시 즉답·miss는 백그라운드+pending) 후 폴링으로 완료 픽업.
  // detail-panel-polish ⑤: pet은 기본 패널서 표시 제거(부동산 표준 필드 아님). **백엔드 pet 데이터·
  // 엔드포인트·추출 파이프라인은 유지**(enrichment는 gym/pet 함께 반환 — 여전히 fetch, gym만 사용).
  // 검색 경로는 무관(_run_search 캐시 그대로) — 이 패널만 단건 온디맨드. graceful: 실패 시 캐시 유지.
  const cid = candidate.complex_id;
  const [gymSec, setGymSec] = useState<GymSection | null>(null);
  // pet-evidence: pet 섹션도 온디맨드(gym 미러) — doc 검증(pet_verified) 트리거·폴링. advisory 렌더.
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
        if (pending && attempt < MAX_POLLS) {
          timer = setTimeout(() => poll(attempt + 1), POLL_MS);
        }
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

  // unit-type-catalog: 전 세대타입(거래+미거래) — /unit-types 병합 조회(deal_type별 실거래 매칭).
  // graceful: 실패/미적재(has_catalog=false)면 candidate.area_buckets 폴백(현 거동·무회귀).
  const dealType = candidate.representative_trade?.rent_type ?? "sale";
  const [unitCatalog, setUnitCatalog] = useState<UnitTypeCatalog | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    fetchUnitTypes(cid, dealType, ctrl.signal)
      .then((c) => setUnitCatalog(c))
      .catch(() => {
        /* graceful: 실패 → area_buckets 폴백 유지 */
      });
    return () => ctrl.abort();
  }, [cid, dealType]);

  // detail-panel-sidebar: 좌측 엣지 드래그로 패널 너비 조절(clamp). 맵은 MapView ResizeObserver가
  // relayout. 값은 컬럼 width(인라인)로 적용 → .map flex가 그만큼 줄어 맵을 덮지 않는다.
  const [width, setWidth] = useState(460);
  const draggingRef = useRef(false);
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      const w = window.innerWidth - e.clientX;
      setWidth(Math.max(340, Math.min(760, w)));
    };
    const onUp = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const rep = candidate.representative_trade;
  const low = rep?.match_confidence != null && rep.match_confidence < 0.7;
  const tags: { text: string; brand?: boolean }[] = [];
  if (candidate.household_count != null) tags.push({ text: `${candidate.household_count.toLocaleString()}세대`, brand: true });
  if (candidate.floorplan?.structure) tags.push({ text: candidate.floorplan.structure });
  if (candidate.parking_ratio != null) tags.push({ text: `주차 ${candidate.parking_ratio.toFixed(2)}` });
  if (candidate.floorplan?.orientation) tags.push({ text: candidate.floorplan.orientation });

  return (
    <div className="detail" data-testid="complex-card" style={{ width }}>
      <div
        className="d-resize"
        data-testid="detail-resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="패널 너비 조절"
        onMouseDown={(e) => {
          e.preventDefault();
          draggingRef.current = true;
          document.body.style.cursor = "col-resize";
          document.body.style.userSelect = "none";
        }}
      />
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

      <div className="d-scroll" data-testid="detail-scroll">
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

      {/* unit-type-catalog: catalog 있으면 전 세대타입(거래+미거래·세대수)·없으면 거래된 평형만(폴백). */}
      {unitCatalog?.has_catalog && unitCatalog.types.length > 0 ? (
        <>
          <UnitTypes types={unitCatalog.types} unit={unit} />
          <div className="abreak-note" data-testid="unit-types-note">
            전용면적별 <b>전체 세대타입</b>(세대수·<b>건축물대장</b>) + 실거래(매칭 평형). 미거래 타입 포함.
          </div>
        </>
      ) : (
        candidate.area_buckets &&
        candidate.area_buckets.length > 0 && (
          <>
            <AreaBuckets buckets={candidate.area_buckets} unit={unit} />
            <div className="abreak-note" data-testid="area-buckets-note">
              평형은 <b>MOLIT 실거래</b> 기반(거래된 평형만). 미거래 세대타입은 포함되지 않습니다.
            </div>
          </>
        )
      )}

      <div className="evhead">근거 · 출처</div>
      <div className="rows">
        {candidate.criteria_eval && candidate.criteria_eval.length > 0 && (
          <CriteriaEval evals={candidate.criteria_eval} sourceUrl={candidate.source_url} />
        )}
        <PoiSection poi={candidate.poi} />
        <SchoolSection school={candidate.school} />
        <AssignmentSection assignment={candidate.assignment} />
        <GymBlock section={gymSec} fallback={candidate.gym} />
        {/* pet-evidence: pet 행 재추가 — advisory(하드 ✓ 없음·관리사무소 확인·견종/무게 단서·출처). */}
        <PetBlock section={petSec} fallback={candidate.pet} />
        {candidate.review && <ReviewRow review={candidate.review} />}
        {candidate.floorplan && <FloorplanRow floorplan={candidate.floorplan} />}
        <ReputationSection cid={cid} reputationQuery={reputationQuery} />
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
    </div>
  );
}
