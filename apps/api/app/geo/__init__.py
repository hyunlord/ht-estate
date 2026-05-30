"""지오코딩 — 실시간 geocoder(Kakao Local) + 영구 캐시 (설계 §5 step4·§8).

개인 단계: 실시간 geocode + complex.lat/lng 캐시. 소유 좌표DB(오프라인)는 서비스화
시점에 재검토(PR #8 supersede).
"""
