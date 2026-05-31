// T0-6 API 계약과 일치하는 타입. gym 필드 없음(R1 — hard filter 제외).

export type Preference = "required" | "preferred" | "none";

// soft 선호 — 랭킹(ORDER)만 바꾸고 후보 SET은 안 바꾼다(설계 §7 demote-not-exclude).
export interface SoftSpec {
  gym: Preference;
  pet: Preference;
}

export interface HardFilterSpec {
  approval_year_min?: number | null;
  approval_year_max?: number | null;
  parking_ratio_gte?: number | null;
  parking_underground?: boolean | null;
  household_count_min?: number | null;
  household_count_max?: number | null;
  net_area_min?: number | null;
  net_area_max?: number | null;
  price_min?: number | null;
  price_max?: number | null;
  deal_since?: string | null; // ISO date
  min_lat?: number | null;
  max_lat?: number | null;
  min_lng?: number | null;
  max_lng?: number | null;
  limit?: number;
  soft?: SoftSpec; // 랭킹 전용. 생략/none → 중립 정렬.
}

export interface RepresentativeTrade {
  net_area: number | null;
  price: number | null; // 만원
  floor: number | null;
  deal_date: string | null;
  match_confidence: number | null;
}

// Tier-2 gym(soft, R1: hard filter 아님). 후보 산출 후 부착. http=클릭 / urn sentinel=비링크.
export interface GymSource {
  source_type: string;
  source_url: string;
}

export interface GymSummary {
  has_gym: "yes" | "no" | "unknown" | "none"; // none=미조사, unknown=조사했으나 불명
  confidence: number | null;
  evidence: string | null;
  sources: GymSource[];
}

// Tier-2 pet_allowed(soft, R1: hard filter 아님). gym 패턴 재사용 + pet 고유(conditional·
// caveats·confirm_with_office). source는 GymSource와 동형(backend EnrichSource).
export interface PetSummary {
  pet_allowed: "yes" | "conditional" | "no" | "unknown" | "none";
  confidence: number | null;
  evidence: string | null;
  caveats: string[];
  confirm_with_office: boolean; // 관리사무소 확인 권고(§11 — 카드가 표면화)
  sources: GymSource[];
}

export interface Candidate {
  complex_id: string;
  name: string | null;
  approval_date: string | null;
  parking_ratio: number | null;
  parking_underground: number | null;
  household_count: number | null;
  lat: number | null;
  lng: number | null;
  source_url: string | null;
  transaction_count: number;
  price_min: number | null;
  price_max: number | null;
  representative_trade: RepresentativeTrade | null;
  gym?: GymSummary | null; // API는 항상 채움(미시드→none). optional: 구버전 mock 호환.
  pet?: PetSummary | null;
}

export interface Bbox {
  min_lat: number;
  max_lat: number;
  min_lng: number;
  max_lng: number;
}
