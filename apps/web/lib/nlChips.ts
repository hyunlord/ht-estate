// 감지 조건 칩 ↔ filter_spec 매핑(#3b) — 강/약/제외 가중치 조정의 순수 로직.
//
// 백엔드 무수정: /search/nl이 준 detected(레지스트리-grounded)와 spec을 받아, 사용자가 칩별로
// 고른 레벨(강/약/제외)을 조정된 HardFilterSpec으로 재구성한다. 재검색은 /complexes/search(수동
// 경로·LLM 없음)로 보낸다. **demote-not-exclude 불변식**: soft 조정은 후보 SET을 절대 안 바꾸고,
// hard→soft 강등(약)은 SET을 넓히기만 한다(제외 0). 강(strong)은 soft 감지에선 hard 승격이 아니라
// 강한 soft 가중치(2.0) — 승격은 SET 축소 + 임계값을 요구해 불변식·UX에 안 맞는다.

import type { Detected, HardFilterSpec, Preference, SoftCriterion } from "./types";

export type ChipLevel = "strong" | "soft" | "exclude";

export const LEVEL_LABELS: Record<ChipLevel, string> = {
  strong: "강",
  soft: "약",
  exclude: "제외",
};

// criterion_key → clear할 HardFilterSpec 필드들(criteria.py REGISTRY.hard_fields + nl_parse
// _CORE_FIELDS 미러 — 출처는 백엔드 레지스트리). deal_type은 "sale" 리셋이라 여기 없음.
const HARD_FIELDS: Record<string, (keyof HardFilterSpec)[]> = {
  // 레지스트리 hard_able
  subway_time: ["subway_walkable"],
  has_daycare: ["has_daycare"],
  elevator_count: ["elevator_count_min"],
  cctv_count: ["cctv_count_min"],
  parking_ratio: ["parking_ratio_gte"],
  household_count: ["household_count_min", "household_count_max"],
  approval_year: ["approval_year_min", "approval_year_max"],
  top_floor: ["top_floor_min"],
  heat_type: ["heat_type"],
  builder: ["builder"],
  property_type: ["property_type"],
  // core hard 필드(nl_parse._CORE_FIELDS) + region bbox
  net_area: ["net_area_min", "net_area_max"],
  price: ["price_min", "price_max"],
  deposit: ["deposit_min", "deposit_max"],
  monthly_rent: ["monthly_rent_min", "monthly_rent_max"],
  region: ["min_lat", "max_lat", "min_lng", "max_lng"],
};

// soft-able 조건(criteria.py soft_scorer 보유). 약 옵션 제공 + 강을 soft 가중치로 매핑하는 기준.
// gym/pet은 spec.soft.gym/pet(Preference 축), 나머지는 spec.soft.criteria[{key,weight}].
const SOFT_ABLE = new Set<string>([
  "gym",
  "pet",
  "subway_time",
  "has_daycare",
  "elevator_count",
  "cctv_count",
  "parking_ratio",
  "household_count",
  "approval_year",
  "top_floor",
]);
const PREF_KEYS = new Set<string>(["gym", "pet"]);

/** 칩 식별자 — 한 criterion_key가 hard·soft 둘로 감지될 수 있어 mode까지 포함. */
export function chipId(d: Detected): string {
  return `${d.criterion_key}:${d.mode}`;
}

/** 이 칩이 제공하는 레벨 — soft-able만 약(soft)을 제공. hard-only/core는 강·제외만. */
export function chipLevelOptions(d: Detected): ChipLevel[] {
  return SOFT_ABLE.has(d.criterion_key)
    ? ["strong", "soft", "exclude"]
    : ["strong", "exclude"];
}

/** 칩 초기 레벨 — hard 감지=강. soft 감지=현 가중치 반영(required/2.0+=강, else 약). */
export function defaultLevel(baseSpec: HardFilterSpec, d: Detected): ChipLevel {
  if (d.mode === "hard") return "strong";
  const key = d.criterion_key;
  if (key === "gym") return baseSpec.soft?.gym === "required" ? "strong" : "soft";
  if (key === "pet") return baseSpec.soft?.pet === "required" ? "strong" : "soft";
  const c = baseSpec.soft?.criteria?.find((x) => x.key === key);
  return c && c.weight >= 2 ? "strong" : "soft";
}

/** 감지 전체의 초기 레벨 맵(칩 id → 레벨). */
export function initialLevels(
  baseSpec: HardFilterSpec,
  detected: Detected[],
): Record<string, ChipLevel> {
  const out: Record<string, ChipLevel> = {};
  for (const d of detected) out[chipId(d)] = defaultLevel(baseSpec, d);
  return out;
}

function clearHard(spec: HardFilterSpec, key: string): void {
  if (key === "deal_type") {
    spec.deal_type = "sale"; // 거래유형 제외 = 기본(매매)로 리셋
    return;
  }
  for (const f of HARD_FIELDS[key] ?? []) delete spec[f];
}

/** baseSpec(=NL 확정 spec) + 칩 레벨 → 조정된 spec. soft는 칩에서 전부 재구성(stale 방지),
 * hard 필드 값은 baseSpec에서 보존(강 유지 시 그대로). demote-not-exclude 준수. */
export function buildSpecFromChips(
  baseSpec: HardFilterSpec,
  detected: Detected[],
  levels: Record<string, ChipLevel>,
): HardFilterSpec {
  const spec: HardFilterSpec = { ...baseSpec };
  let gym: Preference = "none";
  let pet: Preference = "none";
  const criteria: SoftCriterion[] = [];

  for (const d of detected) {
    const key = d.criterion_key;
    const level = levels[chipId(d)] ?? defaultLevel(baseSpec, d);
    const softAble = SOFT_ABLE.has(key);

    if (level === "exclude") {
      clearHard(spec, key); // hard였으면 제거 / soft면 no-op (soft에 안 넣음)
      continue;
    }
    if (level === "soft") {
      clearHard(spec, key); // 약 = soft 강등. hard였으면 SET을 넓힘(안전).
      if (!softAble) continue; // soft 불가(면적·가격 등)는 약 미제공 — 방어적 skip
      if (PREF_KEYS.has(key)) {
        if (key === "gym") gym = "preferred";
        else pet = "preferred";
      } else {
        criteria.push({ key, weight: 1 });
      }
      continue;
    }
    // level === "strong"
    if (d.mode === "hard") continue; // hard 감지 → hard 유지(baseSpec 값 그대로)
    // soft 감지 → 강한 soft 가중치(2.0). hard 승격 ❌ (demote-not-exclude·임계값 없음).
    if (!softAble) continue;
    if (PREF_KEYS.has(key)) {
      if (key === "gym") gym = "required";
      else pet = "required";
    } else {
      criteria.push({ key, weight: 2 });
    }
  }

  spec.soft = { gym, pet, criteria };
  return spec;
}
