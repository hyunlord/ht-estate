"use client";

import { formatArea, markerLabel } from "@/lib/format";
import type { AreaUnit, Candidate, CatalogCriterion, CriterionEval } from "@/lib/types";

// 좌측 랭크 리스트 — 단지 카드(순위·이름·대표가·메타·근거 뱃지). 지도와 동기(선택↔강조).
// 사진 자리를 근거 뱃지(criteria_eval ✓/△/✗/○)가 대신한다.

const STATUS: Record<CriterionEval["status"], { icon: string; cls: string }> = {
  match: { icon: "✓", cls: "ok" },
  partial: { icon: "△", cls: "mid" },
  miss: { icon: "✗", cls: "miss" },
  unknown: { icon: "○", cls: "info" },
};

// frontend-polish-1: 뱃지 값 포맷 — 카탈로그(value_type/direction)로 단위 결정(registry-driven).
// lower_better numeric = 거리(m) / higher_better numeric = 개수 / 그 외(상태·bool)는 값 생략.
function badgeValue(ev: CriterionEval, cat: Map<string, CatalogCriterion>): string {
  const c = cat.get(ev.key);
  if (!c || ev.value == null || ev.status === "unknown") return "";
  if (c.value_type === "numeric" && typeof ev.value === "number") {
    return c.direction === "lower_better" ? ` ${ev.value}m` : ` ${ev.value}`;
  }
  return "";
}

function meta(c: Candidate, unit: AreaUnit): string {
  const parts: string[] = [];
  if (c.approval_date) parts.push(c.approval_date.slice(0, 4));
  if (c.household_count != null) parts.push(`${c.household_count.toLocaleString()}세대`);
  const na = c.representative_trade?.net_area;
  if (na != null) parts.push(`전용 ${formatArea(na, unit)}`);
  return parts.join(" · ") || "—";
}

export function ResultList({
  candidates,
  selectedId,
  loading,
  unit,
  onSelect,
  catalog = [],
}: {
  candidates: Candidate[];
  selectedId: string | null;
  loading: boolean;
  unit: AreaUnit;
  onSelect: (c: Candidate) => void;
  catalog?: CatalogCriterion[]; // 뱃지 값 포맷(registry-driven 단위)
}) {
  const catMap = new Map(catalog.map((c) => [c.key, c]));
  return (
    <aside className="list">
      <div className="l-head">
        <span className="t">
          결과 <b>{candidates.length}</b> 단지
        </span>
        <span className="sort">랭크순 ▾</span>
      </div>
      <div className="l-scroll" data-testid="results">
        {candidates.length === 0 && (
          <div className="l-empty" data-testid="results-empty">
            {loading ? "불러오는 중…" : "이 영역에 표시할 단지가 없어요"}
          </div>
        )}
        {candidates.map((c, i) => {
          const badges = (c.criteria_eval ?? []).slice(0, 4);
          const price = markerLabel(c);
          return (
            <button
              key={c.complex_id}
              type="button"
              data-testid="result-item"
              aria-pressed={c.complex_id === selectedId}
              onClick={() => onSelect(c)}
              className={`card${c.complex_id === selectedId ? " on" : ""}`}
            >
              <div className="row1">
                <span className="rk">{i + 1}</span>
                <span className="nm">{c.name ?? c.complex_id}</span>
                <span className="pr">{price ?? "—"}</span>
              </div>
              <div className="meta">{meta(c, unit)}</div>
              {badges.length > 0 && (
                <div className="evid">
                  {badges.map((ev) => {
                    const s = STATUS[ev.status];
                    return (
                      <span key={ev.key} className={`ev ${s.cls}`}>
                        {s.icon} {ev.label}
                        {badgeValue(ev, catMap)}
                      </span>
                    );
                  })}
                </div>
              )}
            </button>
          );
        })}
      </div>
    </aside>
  );
}
