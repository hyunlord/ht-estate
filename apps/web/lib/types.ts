// T0-6 API 계약과 일치하는 타입. gym 필드 없음(R1 — hard filter 제외).

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
}

export interface RepresentativeTrade {
  net_area: number | null;
  price: number | null; // 만원
  floor: number | null;
  deal_date: string | null;
  match_confidence: number | null;
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
}

export interface Bbox {
  min_lat: number;
  max_lat: number;
  min_lng: number;
  max_lng: number;
}
