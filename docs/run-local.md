# 로컬 실행 — 지도-퍼스트 화면 시각 확인 (P4-3a)

지도-퍼스트 browse 화면(가격 마커·줌 클러스터·상세 카드)을 로컬에서 띄워 눈으로 확인하는 절차.

## TL;DR

```bash
# 0) 적재된 DB가 있는 체크아웃에서 map-first 브랜치로 (gitignored DB는 체크아웃 전환에도 유지됨)
cd /Users/rexxa/github/ht-estate && git checkout feat/map-first   # (머지 후라면 main)

# 1) Kakao JS 키를 web .env.local에 (게이트는 키리스라 커밋 안 됨 — 로컬 전용)
echo 'NEXT_PUBLIC_KAKAO_JS_KEY=<발급받은 JavaScript 키>' >> apps/web/.env.local

# 2) 원커맨드 런처 — API(:8000) + web(:3000) 동시 부팅 (Ctrl-C로 둘 다 종료)
make dev

# 3) http://localhost:3000 → 서울로 pan → 가격 마커·줌 클러스터·마커클릭→상세 카드·인프라 필터
```

## Kakao 지도 키 — 발급 + ⚠ 도메인 등록(흔한 함정)

1. **JS 키 발급** — [developers.kakao.com](https://developers.kakao.com) → 내 애플리케이션 → 앱 선택 →
   **앱 키 > JavaScript 키** 탭의 값. (REST API 키 아님 — Maps SDK는 **JavaScript 키**.)
2. **`apps/web/.env.local`에 주입**:
   ```
   NEXT_PUBLIC_KAKAO_JS_KEY=<JavaScript 키>
   # (선택) API가 8000이 아니면:
   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
   ```
   `.env.local`은 gitignore라 커밋되지 않는다(로컬 전용).
3. **⚠ 사이트 도메인 등록 (안 하면 유효한 키라도 지도 안 뜸)** — Kakao Developers 콘솔 →
   내 앱 → **플랫폼 > Web > 사이트 도메인**에 `http://localhost:3000` 추가.
   이걸 빠뜨리면 Maps JS SDK가 로드를 거부해 지도가 안 뜬다(에러도 조용함). 가장 흔한 함정.

> 키/도메인이 없어도 앱은 **크래시하지 않고** placeholder를 띈다(게이트 키리스 경로). 지도만 안 보임.

## 실행 — 원커맨드 vs 2-터미널

- **원커맨드**: `make dev` — API(uvicorn :8000) + web(next dev :3000)를 함께 띄우고 Ctrl-C로 둘 다 종료.
  - `make dev`는 `NEXT_PUBLIC_KAKAO_JS_KEY`를 unset해 `.env.local` 값이 먹게 한다(루트 Makefile이
    게이트용으로 빈값을 export하기 때문 — 이게 .env.local보다 우선이라 unset 필요).
- **2-터미널**(원하면):
  ```bash
  make api-run                       # 터미널 A — uvicorn :8000
  cd apps/web && npm run dev         # 터미널 B — next dev :3000 (.env.local 읽음)
  ```

## API가 서빙하는 DB

- 경로: **`apps/api/data/ht-estate.db`** (SQLite, 이 체크아웃 기준 · `app/store/db.py`의 `DEFAULT_DB_PATH`).
- 적재된 단지/실거래/지오코드가 이 파일에 있다. 비어 있으면 `/complexes/search`가 빈 결과(마커 없음).
- **gitignored 파일은 브랜치 전환에도 보존**되므로, 이미 적재한 메인 체크아웃에서 `feat/map-first`로
  체크아웃하면 그 DB를 그대로 서빙한다(별도 복사 불필요).

## ⚠ geocode 커버리지 — 서울/강남으로 pan

현재 지오코딩 커버리지가 부분(서울·강남 우선 완료, 전국은 단발 geocode 완료 후)이라
**마커는 좌표가 있는 단지(서울/강남)에 집중**된다. 처음 화면(강남 중심)에서 보이지 않으면
**서울 도심으로 지도를 이동(pan)**하면 가격 마커와 줌 클러스터가 나타난다.
전국 마커 밀도/클러스터 캘리브레이션은 geocode 단발 완료 후.

## 화면에서 확인할 것

- **가격 마커** — 단지 위치에 대표 실거래가(거래유형별) 라벨.
- **줌 클러스터** — 줌 아웃 시 격자 집계 배지(숫자) → 클릭하면 확대.
- **마커/리스트 클릭 → 상세 카드** — 조건 평가 **✓/△/✗/○** + value + confidence + **K-apt 출처 딥링크**,
  대표 실거래, gym/pet/후기/평면도 행.
- **인프라 필터** — 거래유형 토글 + 칩(어린이집·엘리베이터·CCTV·지하주차) + 수치 입력 → 검색.
- **랭크 리스트 ↔ 지도** — 리스트 선택 시 해당 마커 강조(동기).
