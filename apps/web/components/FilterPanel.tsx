"use client";

import { useState } from "react";

import type { DealType, HardFilterSpec, Preference, SoftCriterion } from "@/lib/types";

type Field = { key: keyof HardFilterSpec; label: string; step?: string };

// deal_type 무관 공유 필드(단지 속성 + 전용/거래일은 별도).
const SHARED_FIELDS: Field[] = [
  { key: "approval_year_min", label: "사용승인 최소(년)" },
  { key: "approval_year_max", label: "사용승인 최대(년)" },
  { key: "net_area_min", label: "전용 최소(㎡)", step: "0.01" },
  { key: "net_area_max", label: "전용 최대(㎡)", step: "0.01" },
  { key: "parking_ratio_gte", label: "세대당 주차 ≥", step: "0.1" },
  { key: "household_count_min", label: "세대수 최소" },
  { key: "household_count_max", label: "세대수 최대" },
];

// 거래유형별 적응형 금액 입력 — 매매=가격 / 전세=보증금 / 월세=보증금+월세.
const AMOUNT_FIELDS: Record<DealType, Field[]> = {
  sale: [
    { key: "price_min", label: "가격 최소(만원)" },
    { key: "price_max", label: "가격 최대(만원)" },
  ],
  jeonse: [
    { key: "deposit_min", label: "보증금 최소(만원)" },
    { key: "deposit_max", label: "보증금 최대(만원)" },
  ],
  monthly: [
    { key: "deposit_min", label: "보증금 최소(만원)" },
    { key: "deposit_max", label: "보증금 최대(만원)" },
    { key: "monthly_rent_min", label: "월세 최소(만원)" },
    { key: "monthly_rent_max", label: "월세 최대(만원)" },
  ],
};

const DEAL_TYPES: { value: DealType; label: string }[] = [
  { value: "sale", label: "매매" },
  { value: "jeonse", label: "전세" },
  { value: "monthly", label: "월세" },
];

// 고정 인프라 칩. soft = #2a 레지스트리 soft-able key를 기본 weight로 켬(demote-not-exclude — SET 불변).
// underground = hard parking_underground(>0 보유 요구). 헬스장/강아지는 아래 soft 선호 셀렉트.
type Chip =
  | { id: string; label: string; kind: "soft"; criterion: string }
  | { id: string; label: string; kind: "underground" };
const INFRA_CHIPS: Chip[] = [
  { id: "has_daycare", label: "어린이집", kind: "soft", criterion: "has_daycare" },
  { id: "elevator_count", label: "엘리베이터", kind: "soft", criterion: "elevator_count" },
  { id: "cctv_count", label: "CCTV", kind: "soft", criterion: "cctv_count" },
  { id: "underground", label: "지하주차", kind: "underground" },
];

const PREF_OPTS: { value: Preference; label: string }[] = [
  { value: "none", label: "없음" },
  { value: "preferred", label: "선호" },
  { value: "required", label: "필수" },
];

function toNumber(value: string): number | undefined {
  return value.trim() === "" ? undefined : Number(value);
}

export function FilterPanel({ onSearch }: { onSearch: (spec: HardFilterSpec) => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [dealType, setDealType] = useState<DealType>("sale");
  const [chips, setChips] = useState<Record<string, boolean>>({});
  const [dealSince, setDealSince] = useState("");
  const [gymPref, setGymPref] = useState<Preference>("none");
  const [petPref, setPetPref] = useState<Preference>("none");

  const update = (key: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setValues((prev) => ({ ...prev, [key]: e.target.value }));
  const toggleChip = (id: string) => setChips((prev) => ({ ...prev, [id]: !prev[id] }));

  const submit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const spec: HardFilterSpec = { limit: 50 };
    // 현재 거래유형의 금액 필드만 전송(전환 시 다른 축 값은 무시 — 적응형).
    for (const { key } of [...SHARED_FIELDS, ...AMOUNT_FIELDS[dealType]]) {
      const n = toNumber(values[key] ?? "");
      if (n !== undefined) (spec as Record<string, unknown>)[key] = n;
    }
    if (dealType !== "sale") spec.deal_type = dealType; // sale은 기본 → 미전송(매매 회귀 0)
    if (dealSince) spec.deal_since = dealSince;

    // 인프라 칩 → soft 조건(기본 weight 1) + 지하주차 hard. (gym/pet은 아래 선호 셀렉트)
    const criteria: SoftCriterion[] = [];
    for (const chip of INFRA_CHIPS) {
      if (!chips[chip.id]) continue;
      if (chip.kind === "underground") spec.parking_underground = true;
      else criteria.push({ key: chip.criterion, weight: 1 });
    }
    // soft 선호 — 무엇이든 활성이면 보낸다(없으면 서버 기본 none = 중립 정렬).
    if (gymPref !== "none" || petPref !== "none" || criteria.length > 0) {
      spec.soft = { gym: gymPref, pet: petPref, criteria };
    }
    onSearch(spec);
  };

  const amountFields = AMOUNT_FIELDS[dealType];

  return (
    <form data-testid="filter-panel" onSubmit={submit} className="flex flex-col gap-4 text-sm">
      {/* 거래유형 토글 */}
      <fieldset data-testid="deal-type" className="flex gap-1.5">
        {DEAL_TYPES.map((d) => (
          <button
            key={d.value}
            type="button"
            data-testid={`deal-type-${d.value}`}
            aria-pressed={dealType === d.value}
            onClick={() => setDealType(d.value)}
            className={`flex-1 rounded-lg border px-3 py-1.5 font-mono text-xs tracking-wide transition ${
              dealType === d.value
                ? "border-cool bg-cool/15 text-cool"
                : "border-border text-muted hover:border-faint hover:text-ink"
            }`}
          >
            {d.label}
          </button>
        ))}
      </fieldset>

      {/* 인프라 칩 — 고정 조건(soft 랭킹 + 지하주차 hard) */}
      <div className="flex flex-col gap-2">
        <span className="eyebrow">인프라</span>
        <div data-testid="infra-chips" className="flex flex-wrap gap-1.5">
          {INFRA_CHIPS.map((chip) => {
            const on = !!chips[chip.id];
            return (
              <button
                key={chip.id}
                type="button"
                data-testid={`chip-${chip.id}`}
                aria-pressed={on}
                onClick={() => toggleChip(chip.id)}
                className={`rounded-md border px-2.5 py-1 font-mono text-[11px] transition ${
                  on
                    ? "border-green/45 bg-green/10 text-green"
                    : "border-border bg-surface2 text-muted hover:border-faint hover:text-ink"
                }`}
              >
                {chip.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* 키 슬라이더격 수치 입력(사용승인·전용·주차·세대수) + 거래유형별 금액 */}
      <div className="grid grid-cols-2 gap-2">
        {[...SHARED_FIELDS, ...amountFields].map(({ key, label, step }) => (
          <label key={key} className="flex flex-col gap-1">
            <span className="text-[11px] text-faint">{label}</span>
            <input
              type="number"
              step={step}
              inputMode="decimal"
              value={values[key] ?? ""}
              onChange={update(key)}
              className="rounded-md border border-border bg-surface2 px-2 py-1.5 text-ink outline-none focus:border-cool"
            />
          </label>
        ))}
      </div>

      <label className="flex flex-col gap-1">
        <span className="text-[11px] text-faint">거래일 이후</span>
        <input
          type="date"
          value={dealSince}
          onChange={(e) => setDealSince(e.target.value)}
          className="rounded-md border border-border bg-surface2 px-2 py-1.5 text-ink outline-none focus:border-cool"
        />
      </label>

      {/* soft 선호 — 헬스장/강아지(랭킹 전용, 후보 SET 불변) */}
      <fieldset
        data-testid="soft-prefs"
        className="flex flex-col gap-2 border-t border-border-soft pt-3"
      >
        <legend className="eyebrow">soft 선호 · 순서만</legend>
        {(
          [
            ["헬스장", "gym-pref", gymPref, setGymPref],
            ["강아지", "pet-pref", petPref, setPetPref],
          ] as const
        ).map(([label, testid, val, setter]) => (
          <label key={testid} className="flex items-center justify-between gap-2">
            <span className="text-muted">{label}</span>
            <select
              data-testid={testid}
              value={val}
              onChange={(e) => setter(e.target.value as Preference)}
              className="rounded-md border border-border bg-surface2 px-2 py-1 text-ink outline-none focus:border-cool"
            >
              {PREF_OPTS.map((o) => (
                <option key={o.value} value={o.value} className="bg-surface text-ink">
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        ))}
      </fieldset>

      <button
        type="submit"
        data-testid="search-button"
        className="rounded-lg bg-cool px-4 py-2.5 font-mono text-xs font-medium tracking-wide text-bg transition hover:brightness-110"
      >
        검색 →
      </button>
    </form>
  );
}
