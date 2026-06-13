"use client";

import { useState } from "react";

import { convertArea, toSqm } from "@/lib/format";
import type {
  AreaUnit,
  DealType,
  HardFilterSpec,
  Preference,
  QuickFilter,
  SoftCriterion,
} from "@/lib/types";

// 상단 필터바 — 거래유형 세그먼트 + 필터 드롭다운(슬라이더+입력 동기) + 인프라 칩 + 평/㎡ 토글
// + NL placeholder(#3b). 필터 변경 → onChange(spec). 면적은 현 단위 입력 → ㎡로 환산해 전송.
//
// frontend-polish-1: 인프라 칩 = **registry-driven**(quickFilters는 page가 GET /criteria서 받아 주입
// — 하드코딩 드리프트 0·REGISTRY 진화 시 자동 동기). 지하주차만 core 필드(비-criterion)라 고정.

const SEG: { value: DealType; label: string }[] = [
  { value: "sale", label: "매매" },
  { value: "jeonse", label: "전세" },
  { value: "monthly", label: "월세" },
];

// 지하주차(parking_underground)는 REGISTRY criterion이 아닌 core hard 필드 → 고정 칩(net_area처럼).
const UNDERGROUND_ID = "underground";

type Ranges = Record<string, string>;
const num = (v: string | undefined): number | undefined =>
  v && v.trim() !== "" ? Number(v) : undefined;

export function TopBar({
  onChange,
  onUnitChange,
  onNlSearch,
  nlLoading,
  quickFilters = [],
}: {
  onChange: (spec: HardFilterSpec) => void;
  onUnitChange: (unit: AreaUnit) => void;
  onNlSearch: (query: string) => void; // #3b NL 질의 제출(Enter)
  nlLoading?: boolean;
  quickFilters?: QuickFilter[]; // registry-driven 퀵 토글(GET /criteria) — page가 주입
}) {
  const [dealType, setDealType] = useState<DealType>("sale");
  const [chips, setChips] = useState<Record<string, boolean>>({});
  const [ranges, setRanges] = useState<Ranges>({});
  const [open, setOpen] = useState<string | null>(null);
  const [unit, setUnit] = useState<AreaUnit>("pyeong");
  const [query, setQuery] = useState(""); // #3b NL 검색 입력

  // 면적 슬라이더 bounds(현 단위). pyeong 0–60 / sqm 0–200.
  const areaMax = unit === "pyeong" ? 60 : 200;
  const areaStep = unit === "pyeong" ? 0.5 : 1;

  function buildSpec(dt: DealType, ch: Record<string, boolean>, rg: Ranges, u: AreaUnit): HardFilterSpec {
    const spec: HardFilterSpec = { limit: 100 };
    if (dt !== "sale") spec.deal_type = dt;
    // 면적: 현 단위 → ㎡ canonical.
    const aMin = num(rg.areaMin);
    const aMax = num(rg.areaMax);
    if (aMin !== undefined) spec.net_area_min = toSqm(aMin, u);
    if (aMax !== undefined) spec.net_area_max = toSqm(aMax, u);
    if (num(rg.approvalMin) !== undefined) spec.approval_year_min = num(rg.approvalMin);
    // school-assignment: 배정 초등 학교명(부분명) → assigned_school(백엔드 fuzzy positive-match).
    if (rg.assignedSchool && rg.assignedSchool.trim() !== "") {
      spec.assigned_school = rg.assignedSchool.trim();
    }
    if (dt === "sale") {
      if (num(rg.priceMin) !== undefined) spec.price_min = num(rg.priceMin);
      if (num(rg.priceMax) !== undefined) spec.price_max = num(rg.priceMax);
    } else {
      if (num(rg.priceMin) !== undefined) spec.deposit_min = num(rg.priceMin);
      if (num(rg.priceMax) !== undefined) spec.deposit_max = num(rg.priceMax);
      if (dt === "monthly") {
        if (num(rg.rentMin) !== undefined) spec.monthly_rent_min = num(rg.rentMin);
        if (num(rg.rentMax) !== undefined) spec.monthly_rent_max = num(rg.rentMax);
      }
    }
    // 지하주차(core 고정 칩)
    if (ch[UNDERGROUND_ID]) spec.parking_underground = true;
    // registry-driven 퀵 토글 — apply=hard(필드=값) / apply=soft(criterion weight·gym/pet은 Preference)
    const criteria: SoftCriterion[] = [];
    let gym: Preference = "none";
    let pet: Preference = "none";
    for (const q of quickFilters) {
      if (!ch[q.id]) continue;
      if (q.apply === "hard" && q.hard_field) {
        (spec as Record<string, unknown>)[q.hard_field] = q.hard_value;
      } else if (q.apply === "soft" && q.soft_key) {
        if (q.soft_key === "gym") gym = "preferred";
        else if (q.soft_key === "pet") pet = "preferred";
        else criteria.push({ key: q.soft_key, weight: 1 });
      }
    }
    if (criteria.length > 0 || gym !== "none" || pet !== "none") {
      spec.soft = { gym, pet, criteria };
    }
    return spec;
  }

  const commit = (dt: DealType, ch: Record<string, boolean>, rg: Ranges, u: AreaUnit) =>
    onChange(buildSpec(dt, ch, rg, u));

  const pickSeg = (dt: DealType) => {
    setDealType(dt);
    commit(dt, chips, ranges, unit);
  };
  const toggleChip = (id: string) => {
    const next = { ...chips, [id]: !chips[id] };
    setChips(next);
    commit(dealType, next, ranges, unit);
  };
  const setRange = (k: string, v: string) => setRanges((p) => ({ ...p, [k]: v }));
  const applyDropdown = () => {
    setOpen(null);
    commit(dealType, chips, ranges, unit);
  };

  const pickUnit = (u: AreaUnit) => {
    if (u === unit) return;
    // 면적 입력값을 새 단위로 환산해 동등 면적 보존(spec ㎡는 불변 → 재검색 불필요, 표기만 전환).
    const conv = (s: string | undefined) =>
      s && s.trim() !== "" ? convertArea(Number(s), unit, u).toFixed(u === "pyeong" ? 1 : 0) : (s ?? "");
    setRanges((p) => ({ ...p, areaMin: conv(p.areaMin), areaMax: conv(p.areaMax) }));
    setUnit(u);
    onUnitChange(u);
  };

  const priceLabel = dealType === "sale" ? "가격" : "보증금";
  const priceActive = ranges.priceMin || ranges.priceMax;
  const areaActive = ranges.areaMin || ranges.areaMax;
  const approvalActive = ranges.approvalMin;
  const assignedActive = ranges.assignedSchool && ranges.assignedSchool.trim() !== "";
  const areaUnitSym = unit === "pyeong" ? "평" : "㎡";

  // 슬라이더 + 숫자입력(동일 state 바인딩 → 양방향 동기).
  const rangeRow = (key: string, label: string, max: number, step: number, min = 0) => (
    <label>
      {label}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input
          data-testid={`slider-${key}`}
          type="range"
          min={min}
          max={max}
          step={step}
          value={ranges[key] && ranges[key] !== "" ? ranges[key] : String(min)}
          onChange={(e) => setRange(key, e.target.value)}
          style={{ flex: 1 }}
        />
        <input
          data-testid={`in-${key}`}
          type="number"
          inputMode="decimal"
          value={ranges[key] ?? ""}
          onChange={(e) => setRange(key, e.target.value)}
          style={{ width: 78 }}
        />
      </div>
    </label>
  );

  return (
    <div className="top">
      <span className="logo">
        ht-estate<span className="dot">.</span>
      </span>

      <div className="seg" data-testid="deal-type">
        {SEG.map((s) => (
          <button
            key={s.value}
            type="button"
            data-testid={`deal-type-${s.value}`}
            aria-pressed={dealType === s.value}
            className={dealType === s.value ? "on" : ""}
            onClick={() => pickSeg(s.value)}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* 가격/보증금 */}
      <div style={{ position: "relative" }}>
        <button
          type="button"
          data-testid="fdrop-price"
          className={`fdrop${priceActive ? " act" : ""}`}
          onClick={() => setOpen(open === "price" ? null : "price")}
        >
          {priceLabel} <span className="ca">▾</span>
        </button>
        {open === "price" && (
          <div className="popover" data-testid="pop-price" style={{ minWidth: 280 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {rangeRow("priceMin", `${priceLabel} 최소(만원)`, 300000, 1000)}
              {rangeRow("priceMax", `${priceLabel} 최대(만원)`, 300000, 1000)}
              {dealType === "monthly" && rangeRow("rentMin", "월세 최소(만원)", 500, 10)}
              {dealType === "monthly" && rangeRow("rentMax", "월세 최대(만원)", 500, 10)}
            </div>
            <button type="button" data-testid="apply-price" onClick={applyDropdown}
              className="chip on" style={{ marginTop: 10, width: "100%" }}>
              적용
            </button>
          </div>
        )}
      </div>

      {/* 전용면적 + 평/㎡ */}
      <div style={{ position: "relative" }}>
        <button
          type="button"
          data-testid="fdrop-area"
          className={`fdrop${areaActive ? " act" : ""}`}
          onClick={() => setOpen(open === "area" ? null : "area")}
        >
          {areaActive ? `전용 ${ranges.areaMin || ""}–${ranges.areaMax || ""}${areaUnitSym}` : "전용면적"}{" "}
          <span className="ca">▾</span>
        </button>
        {open === "area" && (
          <div className="popover" data-testid="pop-area" style={{ minWidth: 280 }}>
            <div className="seg" data-testid="unit-toggle" style={{ marginBottom: 10, width: "fit-content" }}>
              <button type="button" data-testid="unit-pyeong" aria-pressed={unit === "pyeong"}
                className={unit === "pyeong" ? "on" : ""} onClick={() => pickUnit("pyeong")}>
                평
              </button>
              <button type="button" data-testid="unit-sqm" aria-pressed={unit === "sqm"}
                className={unit === "sqm" ? "on" : ""} onClick={() => pickUnit("sqm")}>
                ㎡
              </button>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {rangeRow("areaMin", `전용 최소(${areaUnitSym})`, areaMax, areaStep)}
              {rangeRow("areaMax", `전용 최대(${areaUnitSym})`, areaMax, areaStep)}
            </div>
            <button type="button" data-testid="apply-area" onClick={applyDropdown}
              className="chip on" style={{ marginTop: 10, width: "100%" }}>
              적용
            </button>
          </div>
        )}
      </div>

      {/* 사용승인 */}
      <div style={{ position: "relative" }}>
        <button
          type="button"
          data-testid="fdrop-approval"
          className={`fdrop${approvalActive ? " act" : ""}`}
          onClick={() => setOpen(open === "approval" ? null : "approval")}
        >
          {approvalActive ? `사용승인 ${ranges.approvalMin}+` : "사용승인"} <span className="ca">▾</span>
        </button>
        {open === "approval" && (
          <div className="popover" data-testid="pop-approval" style={{ minWidth: 280 }}>
            {rangeRow("approvalMin", "사용승인 이후(년)", 2025, 1, 1980)}
            <button type="button" data-testid="apply-approval" onClick={applyDropdown}
              className="chip on" style={{ marginTop: 10, width: "100%" }}>
              적용
            </button>
          </div>
        )}
      </div>

      {/* 배정 초등(통학구역) — 학교명 텍스트 입력(부분명 OK·백엔드 fuzzy positive-match) */}
      <div style={{ position: "relative" }}>
        <button
          type="button"
          data-testid="fdrop-assigned"
          className={`fdrop${assignedActive ? " act" : ""}`}
          onClick={() => setOpen(open === "assigned" ? null : "assigned")}
        >
          {assignedActive ? `배정 ${ranges.assignedSchool}` : "배정 초등"}{" "}
          <span className="ca">▾</span>
        </button>
        {open === "assigned" && (
          <div className="popover" data-testid="pop-assigned" style={{ minWidth: 240 }}>
            <label>
              배정 초등학교명
              <input
                data-testid="in-assigned-school"
                type="text"
                value={ranges.assignedSchool ?? ""}
                placeholder="예: 서울잠원초 / 반원초등학교"
                onChange={(e) => setRange("assignedSchool", e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") applyDropdown();
                }}
                style={{ width: "100%", marginTop: 4 }}
              />
            </label>
            <div className="ca" style={{ marginTop: 6, fontSize: 10 }}>
              ⚠ 통학구역 추정 · 교육청 확인 권장
            </div>
            <button type="button" data-testid="apply-assigned" onClick={applyDropdown}
              className="chip on" style={{ marginTop: 10, width: "100%" }}>
              적용
            </button>
          </div>
        )}
      </div>

      <div className="chips" data-testid="infra-chips">
        <button
          type="button"
          data-testid={`chip-${UNDERGROUND_ID}`}
          aria-pressed={!!chips[UNDERGROUND_ID]}
          className={`chip${chips[UNDERGROUND_ID] ? " on" : ""}`}
          onClick={() => toggleChip(UNDERGROUND_ID)}
        >
          지하주차
        </button>
        {/* filter-trim: major 플래그된 퀵필터만 기본 칩(흔히 쓰는 고가치). long-tail은 NL로 도달
            (registry 단일 소스 — 프론트 하드코딩 0). 행이 fit·고정 컨트롤(지하주차)과 함께. */}
        {quickFilters.filter((q) => q.major).map((q) => (
          <button
            key={q.id}
            type="button"
            data-testid={`chip-${q.id}`}
            aria-pressed={!!chips[q.id]}
            className={`chip${chips[q.id] ? " on" : ""}`}
            onClick={() => toggleChip(q.id)}
          >
            {q.label}
          </button>
        ))}
      </div>

      <div className={`searchbox${nlLoading ? " busy" : ""}`}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input
          data-testid="nl-search"
          value={query}
          placeholder="자연어로 더 많은 조건: 어린이집 가까운 · CCTV 많은 · 공원 근처 · 오피스텔"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              const q = query.trim();
              if (q) onNlSearch(q);
            }
          }}
        />
      </div>
      {/* filter-trim: long-tail 조건은 NL로 도달(registry-grounded 파서·어휘 손실 0) — 안내 표면화. */}
      <div className="nl-hint" data-testid="nl-hint">
        칩에 없는 조건은 검색창에 자연어로: <b>어린이집 가까운</b> · <b>CCTV 많은</b> · <b>공원 근처</b> ·
        <b>병원 가까운</b> · <b>오피스텔</b>
      </div>
    </div>
  );
}
