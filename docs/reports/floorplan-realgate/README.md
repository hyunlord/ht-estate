# floorplan-realgate — 실 도면 robustness 표본 (PROXY)

> **이 이미지는 LH 15037046이 아니다.** DATA_GO_KR 키가 이 환경에 없어 실 LH 다운로드를 못 했고,
> 게이트의 *실 도면 robustness*(스캔·치수선·door swing·라벨 등 messy 실 linework) 질문을 닫기 위해
> **공개 라이선스 실 평면도**를 proxy로 썼다. Korean LH-특이성·K-apt 조인 커버리지는 키 확보 후 별도.

## 출처 / 라이선스 (재현·재배포 준수)
- `proxy_sample_floorplan.jpg` — **Public Domain**. 저자 Boereck. Wikimedia Commons
  `File:Sample_Floorplan.jpg`. (미국식 단독주택 — out-of-domain 스트레스 테스트용.)
- `proxy_focsa_apartment.jpeg` — **CC BY-SA 4.0**. 저자 Osvaldo Valdes. Wikimedia Commons
  `File:Typical_apartment_floor_plan_FOCSA_Building.jpeg`. (FOCSA 아파트 단위 — 실 apt plan.)

## 산출물
- `extractions.json` — 각 이미지의 육안 reading ↔ `claude -p` 추출(feature) 쌍. **Web 육안 대조용.**
- 평가·go/no-go는 `docs/reports/SPIKE-floorplan-realgate.md`.
