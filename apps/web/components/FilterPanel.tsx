"use client";

import { useState } from "react";

import type { DealType, HardFilterSpec, Preference } from "@/lib/types";

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

function toNumber(value: string): number | undefined {
  return value.trim() === "" ? undefined : Number(value);
}

export function FilterPanel({ onSearch }: { onSearch: (spec: HardFilterSpec) => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [dealType, setDealType] = useState<DealType>("sale");
  const [underground, setUnderground] = useState(false);
  const [dealSince, setDealSince] = useState("");
  const [gymPref, setGymPref] = useState<Preference>("none");
  const [petPref, setPetPref] = useState<Preference>("none");

  const update = (key: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setValues((prev) => ({ ...prev, [key]: e.target.value }));

  const submit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const spec: HardFilterSpec = { limit: 50 };
    // 현재 거래유형의 금액 필드만 전송(전환 시 다른 축 값은 무시 — 적응형).
    for (const { key } of [...SHARED_FIELDS, ...AMOUNT_FIELDS[dealType]]) {
      const n = toNumber(values[key] ?? "");
      if (n !== undefined) {
        (spec as Record<string, unknown>)[key] = n;
      }
    }
    if (dealType !== "sale") spec.deal_type = dealType; // sale은 기본 → 미전송(매매 회귀 0)
    if (underground) spec.parking_underground = true;
    if (dealSince) spec.deal_since = dealSince;
    // soft 선호 — none 아니면만 보낸다(없으면 서버 기본 none = 중립 정렬).
    if (gymPref !== "none" || petPref !== "none") {
      spec.soft = { gym: gymPref, pet: petPref };
    }
    onSearch(spec);
  };

  const PREF_OPTS: { value: Preference; label: string }[] = [
    { value: "none", label: "없음" },
    { value: "preferred", label: "선호" },
    { value: "required", label: "필수" },
  ];
  const amountFields = AMOUNT_FIELDS[dealType];

  return (
    <form
      data-testid="filter-panel"
      onSubmit={submit}
      className="flex flex-col gap-3 p-4 text-sm"
    >
      <h2 className="font-semibold">필터</h2>
      <fieldset data-testid="deal-type" className="flex gap-1">
        {DEAL_TYPES.map((d) => (
          <button
            key={d.value}
            type="button"
            data-testid={`deal-type-${d.value}`}
            aria-pressed={dealType === d.value}
            onClick={() => setDealType(d.value)}
            className={`flex-1 rounded border px-2 py-1 ${
              dealType === d.value
                ? "border-zinc-900 bg-zinc-900 text-white"
                : "border-zinc-300"
            }`}
          >
            {d.label}
          </button>
        ))}
      </fieldset>
      <div className="grid grid-cols-2 gap-2">
        {[...SHARED_FIELDS, ...amountFields].map(({ key, label, step }) => (
          <label key={key} className="flex flex-col gap-1">
            <span className="text-xs text-zinc-500">{label}</span>
            <input
              type="number"
              step={step}
              inputMode="decimal"
              value={values[key] ?? ""}
              onChange={update(key)}
              className="rounded border border-zinc-300 px-2 py-1"
            />
          </label>
        ))}
      </div>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={underground}
          onChange={(e) => setUnderground(e.target.checked)}
        />
        <span>지하주차 보유</span>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs text-zinc-500">거래일 이후</span>
        <input
          type="date"
          value={dealSince}
          onChange={(e) => setDealSince(e.target.value)}
          className="rounded border border-zinc-300 px-2 py-1"
        />
      </label>
      <fieldset data-testid="soft-prefs" className="flex flex-col gap-2 border-t border-zinc-200 pt-2">
        <legend className="text-xs text-zinc-500">soft 선호 (랭킹 — 후보는 그대로, 순서만)</legend>
        <label className="flex items-center justify-between gap-2">
          <span>헬스장</span>
          <select
            data-testid="gym-pref"
            value={gymPref}
            onChange={(e) => setGymPref(e.target.value as Preference)}
            className="rounded border border-zinc-300 px-2 py-1"
          >
            {PREF_OPTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center justify-between gap-2">
          <span>강아지</span>
          <select
            data-testid="pet-pref"
            value={petPref}
            onChange={(e) => setPetPref(e.target.value as Preference)}
            className="rounded border border-zinc-300 px-2 py-1"
          >
            {PREF_OPTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </fieldset>
      <button
        type="submit"
        data-testid="search-button"
        className="rounded bg-zinc-900 px-4 py-2 text-white"
      >
        검색
      </button>
    </form>
  );
}
