"use client";

import { useState } from "react";

import type { HardFilterSpec } from "@/lib/types";

// HardFilterSpec 편집 폼. gym 토글 없음(R1 — hard filter 제외). bbox는 지도 뷰포트 자동.
const NUMERIC_FIELDS: { key: keyof HardFilterSpec; label: string; step?: string }[] = [
  { key: "approval_year_min", label: "사용승인 최소(년)" },
  { key: "approval_year_max", label: "사용승인 최대(년)" },
  { key: "net_area_min", label: "전용 최소(㎡)", step: "0.01" },
  { key: "net_area_max", label: "전용 최대(㎡)", step: "0.01" },
  { key: "parking_ratio_gte", label: "세대당 주차 ≥", step: "0.1" },
  { key: "household_count_min", label: "세대수 최소" },
  { key: "household_count_max", label: "세대수 최대" },
  { key: "price_min", label: "가격 최소(만원)" },
  { key: "price_max", label: "가격 최대(만원)" },
];

function toNumber(value: string): number | undefined {
  return value.trim() === "" ? undefined : Number(value);
}

export function FilterPanel({ onSearch }: { onSearch: (spec: HardFilterSpec) => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [underground, setUnderground] = useState(false);
  const [dealSince, setDealSince] = useState("");

  const update = (key: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setValues((prev) => ({ ...prev, [key]: e.target.value }));

  const submit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const spec: HardFilterSpec = { limit: 50 };
    for (const { key } of NUMERIC_FIELDS) {
      const n = toNumber(values[key] ?? "");
      if (n !== undefined) {
        (spec as Record<string, unknown>)[key] = n;
      }
    }
    if (underground) spec.parking_underground = true;
    if (dealSince) spec.deal_since = dealSince;
    onSearch(spec);
  };

  return (
    <form
      data-testid="filter-panel"
      onSubmit={submit}
      className="flex flex-col gap-3 p-4 text-sm"
    >
      <h2 className="font-semibold">필터</h2>
      <div className="grid grid-cols-2 gap-2">
        {NUMERIC_FIELDS.map(({ key, label, step }) => (
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
