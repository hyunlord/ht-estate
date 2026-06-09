// T0-6 API 계약과 일치하는 타입. gym 필드 없음(R1 — hard filter 제외).

export type Preference = "required" | "preferred" | "none";

// 일반화 soft 조건(P4-2a) — 레지스트리 key + 가중치. 인프라 칩이 등록 soft-able key를 weight로 켠다.
export interface SoftCriterion {
  key: string;
  weight: number;
}

// soft 선호 — 랭킹(ORDER)만 바꾸고 후보 SET은 안 바꾼다(설계 §7 demote-not-exclude).
// 레거시 gym/pet(Preference) + 일반화 criteria(P4-2a) 둘 다 지원.
export interface SoftSpec {
  gym: Preference;
  pet: Preference;
  criteria?: SoftCriterion[];
}

// 거래유형 축(P2-2): 매매/전세/월세. 기본 sale → 기존 매매 동작.
export type DealType = "sale" | "jeonse" | "monthly";

// 주택유형 축(P5-1): 아파트/연립다세대/오피스텔/단독. 생략=전 유형.
export type PropertyType = "apartment" | "rowhouse" | "officetel" | "detached";

// backend HardFilterSpec(spec.py)와 동형. 모든 hard 필드 optional — 준 것만 AND.
export interface HardFilterSpec {
  deal_type?: DealType; // 생략=sale
  approval_year_min?: number | null;
  approval_year_max?: number | null;
  parking_ratio_gte?: number | null;
  parking_underground?: boolean | null;
  household_count_min?: number | null;
  household_count_max?: number | null;
  // P4-2a: 구조화 hard 필드(in/out). NL이 hard 분류 시 set. 칩 강/약/제외가 여기를 조정.
  subway_walkable?: boolean | null; // True=역세권(5~10분이내) 요구
  has_daycare?: boolean | null; // True=단지 내 어린이집 보유 요구
  elevator_count_min?: number | null;
  cctv_count_min?: number | null;
  top_floor_min?: number | null;
  heat_type?: string | null; // 난방방식 정확 일치
  builder?: string | null; // 건설사 부분 일치
  property_type?: PropertyType | null; // 주택유형(P5-1)
  subway_max_dist_m?: number | null; // poi-1: 역세권 최근접 지하철역 ≤ N미터(미적재=keep)
  mart_count_1km_min?: number | null; // poi-1: 1km 내 대형마트 ≥ N개(미적재=keep)
  net_area_min?: number | null;
  net_area_max?: number | null;
  price_min?: number | null; // 만원 (매매)
  price_max?: number | null;
  deposit_min?: number | null; // 만원 (전세·월세 보증금)
  deposit_max?: number | null;
  monthly_rent_min?: number | null; // 만원 (월세)
  monthly_rent_max?: number | null;
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
  price: number | null; // 만원 (매매)
  deposit: number | null; // 만원 (전세·월세)
  monthly_rent: number | null; // 만원 (월세)
  rent_type: "jeonse" | "monthly" | null;
  floor: number | null;
  deal_date: string | null;
  match_confidence: number | null;
}

// 평형(전용면적 버킷) 집계(detail-1) — 다평형 건물 카드 브레이크다운. backend AreaBucket과 동형.
// net_area=대표 전용(㎡, 프론트가 단위 포맷). recent_amount=금액축(매매=price/전월세=deposit).
export interface AreaBucket {
  net_area: number | null;
  transaction_count: number;
  recent_amount: number | null; // 만원 — 최근 거래 가격축
  recent_monthly_rent: number | null; // 만원 — 월세만
  recent_rent_type: "jeonse" | "monthly" | null;
  recent_deal_date: string | null; // ISO
  amount_min: number | null; // 가격대
  amount_max: number | null;
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

// poi-1: 정적 POI 근접(eager Tier-1). 카드 표시 + subway/mart hard 필터. 미적재 단지는 빈 배열.
export interface PoiNear {
  category: string; // SW8|MT1|CS2|HP8|PM9|PARK
  label: string; // 지하철역·대형마트…
  nearest_dist_m: number | null; // 최근접 거리(m). 반경 내 0건이면 null.
  nearest_name: string | null;
  count_500m: number | null;
  count_1km: number | null;
}

// 온디맨드 enrichment(ux-1) — 디테일뷰 진입 시 단건 gym/pet 추출 상태.
// ready=summary 채움(캐시/추출완료) · pending=백그라운드 추출 중(폴링) · unavailable=라이브 미구성.
export type EnrichStatus = "ready" | "pending" | "unavailable";
export interface GymSection {
  status: EnrichStatus;
  summary: GymSummary | null;
}
export interface PetSection {
  status: EnrichStatus;
  summary: PetSummary | null;
}
export interface EnrichmentResponse {
  complex_id: string;
  gym: GymSection;
  pet: PetSection;
}

// Tier-2 review(후기, P3-1). **표시 전용 — 랭킹 신호 아님**(주관적·SoftSpec에 없음).
// summary=짧은 자기표현 요약(없으면 null=미조사). 다출처는 sources 딥링크로 노출.
export interface ReviewSummary {
  summary: string | null;
  points: string[];
  confidence: number | null;
  sources: GymSource[];
}

// Tier-2 floorplan(평면도, P3-2). **표시 전용 — 랭킹 신호 아님**(객관 feature·SoftSpec에 없음).
// bay·orientation·structure 각각 null 가능(못 읽음). §11: 점수화 아님, 중립 feature.
export interface FloorplanSummary {
  bay: number | null;
  orientation: string | null;
  structure: string | null;
  evidence: string | null;
  confidence: number | null;
  sources: GymSource[];
}

// 조건 평가(P4-2a) — 후보×활성 soft 조건. status: match(✓)·partial(△)·miss(✗)·unknown(○).
// value=원값(표시), score=[0,1]. demote-not-exclude라 점수는 낮아질 뿐 후보를 빼지 않는다.
export interface CriterionEval {
  key: string;
  label: string;
  value: unknown;
  score: number;
  confidence: number | null;
  status: "match" | "partial" | "miss" | "unknown";
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
  area_buckets?: AreaBucket[] | null; // 평형별 집계(detail-1). optional: 구버전 mock 호환.
  gym?: GymSummary | null; // API는 항상 채움(미시드→none). optional: 구버전 mock 호환.
  pet?: PetSummary | null;
  review?: ReviewSummary | null; // 후기(표시 전용). optional: 구버전 mock 호환.
  floorplan?: FloorplanSummary | null; // 평면도 feature(표시 전용). optional: 구버전 mock 호환.
  poi?: PoiNear[] | null; // poi-1: 정적 POI 근접(eager). 미적재=빈 배열(computed-or-dash).
  criteria_eval?: CriterionEval[] | null; // soft 조건 평가(랭킹 근거). optional: 구버전 mock 호환.
}

export interface Bbox {
  min_lat: number;
  max_lat: number;
  min_lng: number;
  max_lng: number;
}

// 지도 마커 전용 경량 레코드(P4-3a-2) — 뷰포트 내 전체 단지. 랭킹·criteria_eval 없음.
// price=대표 금액(매매=price / 전월세=deposit), net_area=전용(㎡).
export interface MarkerCandidate {
  complex_id: string;
  name: string | null;
  lat: number | null;
  lng: number | null;
  price: number | null;
  net_area: number | null;
}

// 면적 단위 토글 — 평/㎡. 1평 = 3.3058㎡.
export type AreaUnit = "pyeong" | "sqm";

// NL 파싱 감지·반영 한 건(#3b) — 어떤 NL 구절을 어떤 조건으로 hard/soft 반영했나.
// backend ParsedQuery.Detected와 동형(criterion_key·label·mode·phrase). spec과 항상 정합.
export interface Detected {
  criterion_key: string;
  label: string;
  mode: "hard" | "soft";
  phrase?: string | null;
}

// NL 검색 응답(#3b) — backend NlSearchResponse와 동형.
// spec=확정 filter_spec(투명성·칩 재료) · detected=감지칩 · unsupported=매핑 불가 · candidates=랭크.
export interface NlSearchResponse {
  spec: HardFilterSpec;
  detected: Detected[];
  unsupported: string[];
  candidates: Candidate[];
}
