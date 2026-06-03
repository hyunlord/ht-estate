"use client";

import { useState } from "react";

import type { DealType, HardFilterSpec, SoftCriterion } from "@/lib/types";

// 상단 필터바 — 거래유형 세그먼트 + 필터 드롭다운(가격·면적·사용승인) + 인프라 칩 + NL placeholder(#3b).
// 필터 변경 → buildSpec() → onChange(spec). bbox는 page가 합쳐 auto-viewport 검색.

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

export function TopBar({ onChange }: { onChange: (spec: HardFilterSpec) => void }) {
  const [dealType, setDealType] = useState<DealType>("sale");
  const [chips, setChips] = useState<Record<string, boolean>>({});
  const [ranges, setRanges] = useState<Ranges>({});
  const [open, setOpen] = useState<string | null>(null);

  function buildSpec(dt: DealType, ch: Record<string, boolean>, rg: Ranges): HardFilterSpec {
    const spec: HardFilterSpec = { limit: 100 };
    if (dt !== "sale") spec.deal_type = dt;
    // 면적·사용승인 (공유)
    if (num(rg.areaMin) !== undefined) spec.net_area_min = num(rg.areaMin);
    if (num(rg.areaMax) !== undefined) spec.net_area_max = num(rg.areaMax);
    if (num(rg.approvalMin) !== undefined) spec.approval_year_min = num(rg.approvalMin);
    // 가격축 (거래유형별)
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
    // 인프라 칩 → soft(기본 weight) + 지하주차 hard
    const criteria: SoftCriterion[] = [];
    for (const c of CHIPS) {
      if (!ch[c.id]) continue;
      if (c.kind === "underground") spec.parking_underground = true;
      else criteria.push({ key: c.criterion, weight: 1 });
    }
    if (criteria.length > 0) spec.soft = { gym: "none", pet: "none", criteria };
    return spec;
  }

  const commit = (dt: DealType, ch: Record<string, boolean>, rg: Ranges) =>
    onChange(buildSpec(dt, ch, rg));

  const pickSeg = (dt: DealType) => {
    setDealType(dt);
    commit(dt, chips, ranges);
  };
  const toggleChip = (id: string) => {
    const next = { ...chips, [id]: !chips[id] };
    setChips(next);
    commit(dealType, next, ranges);
  };
  const setRange = (k: string, v: string) => setRanges((p) => ({ ...p, [k]: v }));
  const applyDropdown = () => {
    setOpen(null);
    commit(dealType, chips, ranges);
  };

  const priceLabel = dealType === "sale" ? "가격" : "보증금";
  const priceActive =
    ranges.priceMin || ranges.priceMax || (dealType === "monthly" && (ranges.rentMin || ranges.rentMax));
  const areaActive = ranges.areaMin || ranges.areaMax;
  const approvalActive = ranges.approvalMin;

  const rangeInput = (key: string, label: string) => (
    <label>
      {label}
      <input
        data-testid={`in-${key}`}
        type="number"
        inputMode="decimal"
        value={ranges[key] ?? ""}
        onChange={(e) => setRange(key, e.target.value)}
      />
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

      {/* 가격 */}
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
          <div className="popover" data-testid="pop-price">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {rangeInput("priceMin", `${priceLabel} 최소(만원)`)}
              {rangeInput("priceMax", `${priceLabel} 최대(만원)`)}
              {dealType === "monthly" && rangeInput("rentMin", "월세 최소(만원)")}
              {dealType === "monthly" && rangeInput("rentMax", "월세 최대(만원)")}
            </div>
            <button
              type="button"
              data-testid="apply-price"
              onClick={applyDropdown}
              className="chip on"
              style={{ marginTop: 10, width: "100%" }}
            >
              적용
            </button>
          </div>
        )}
      </div>

      {/* 면적 */}
      <div style={{ position: "relative" }}>
        <button
          type="button"
          data-testid="fdrop-area"
          className={`fdrop${areaActive ? " act" : ""}`}
          onClick={() => setOpen(open === "area" ? null : "area")}
        >
          {areaActive ? `면적 ${ranges.areaMin || ""}–${ranges.areaMax || ""}㎡` : "면적"}{" "}
          <span className="ca">▾</span>
        </button>
        {open === "area" && (
          <div className="popover" data-testid="pop-area">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {rangeInput("areaMin", "전용 최소(㎡)")}
              {rangeInput("areaMax", "전용 최대(㎡)")}
            </div>
            <button
              type="button"
              data-testid="apply-area"
              onClick={applyDropdown}
              className="chip on"
              style={{ marginTop: 10, width: "100%" }}
            >
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
          {approvalActive ? `사용승인 ${ranges.approvalMin}+` : "사용승인"}{" "}
          <span className="ca">▾</span>
        </button>
        {open === "approval" && (
          <div className="popover" data-testid="pop-approval">
            {rangeInput("approvalMin", "사용승인 이후(년)")}
            <button
              type="button"
              data-testid="apply-approval"
              onClick={applyDropdown}
              className="chip on"
              style={{ marginTop: 10, width: "100%" }}
            >
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

      {/* NL 검색 — #3b. 지금은 placeholder만(동작 X). */}
      <div className="searchbox">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input
          data-testid="nl-search"
          disabled
          placeholder="강남 역세권 신축 큰 단지, 강아지 되면 좋고 (#3b)"
        />
      </div>
    </div>
  );
}
