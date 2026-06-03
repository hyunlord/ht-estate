"use client";

import { useState } from "react";

import { convertArea, toSqm } from "@/lib/format";
import type { AreaUnit, DealType, HardFilterSpec, SoftCriterion } from "@/lib/types";

// 상단 필터바 — 거래유형 세그먼트 + 필터 드롭다운(슬라이더+입력 동기) + 인프라 칩 + 평/㎡ 토글
// + NL placeholder(#3b). 필터 변경 → onChange(spec). 면적은 현 단위 입력 → ㎡로 환산해 전송.

const SEG: { value: DealType; label: string }[] = [
  { value: "sale", label: "매매" },
  { value: "jeonse", label: "전세" },
  { value: "monthly", label: "월세" },
];

const CHIPS = [
  { id: "has_daycare", label: "어린이집", kind: "soft" as const, criterion: "has_daycare" },
  { id: "elevator", label: "엘베", kind: "soft" as const, criterion: "elevator_count" },
  { id: "underground", label: "지하주차", kind: "underground" as const },
  { id: "cctv", label: "CCTV", kind: "soft" as const, criterion: "cctv_count" },
];

type Ranges = Record<string, string>;
const num = (v: string | undefined): number | undefined =>
  v && v.trim() !== "" ? Number(v) : undefined;

export function TopBar({
  onChange,
  onUnitChange,
}: {
  onChange: (spec: HardFilterSpec) => void;
  onUnitChange: (unit: AreaUnit) => void;
}) {
  const [dealType, setDealType] = useState<DealType>("sale");
  const [chips, setChips] = useState<Record<string, boolean>>({});
  const [ranges, setRanges] = useState<Ranges>({});
  const [open, setOpen] = useState<string | null>(null);
  const [unit, setUnit] = useState<AreaUnit>("pyeong");

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
    const criteria: SoftCriterion[] = [];
    for (const c of CHIPS) {
      if (!ch[c.id]) continue;
      if (c.kind === "underground") spec.parking_underground = true;
      else criteria.push({ key: c.criterion, weight: 1 });
    }
    if (criteria.length > 0) spec.soft = { gym: "none", pet: "none", criteria };
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

      <div className="chips" data-testid="infra-chips">
        {CHIPS.map((c) => (
          <button
            key={c.id}
            type="button"
            data-testid={`chip-${c.id}`}
            aria-pressed={!!chips[c.id]}
            className={`chip${chips[c.id] ? " on" : ""}`}
            onClick={() => toggleChip(c.id)}
          >
            {c.label}
          </button>
        ))}
      </div>

      <div className="searchbox">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input data-testid="nl-search" disabled placeholder="강남 역세권 신축 큰 단지, 강아지 되면 좋고 (#3b)" />
      </div>
    </div>
  );
}
