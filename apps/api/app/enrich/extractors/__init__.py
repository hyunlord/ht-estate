"""속성별 lazy 실추출기 (E1) — Extractor 프로토콜 구현(source-fetch + provider-LLM → facts).

설계 §6의 P1-2: runner.enrich의 주입형 Extractor seam에 꽂는 실추출기. stub(읽기전용)을
대체하되 같은 경로(miss→추출→write-back). gym(비아파트)·pet(advisory)는 extractors/gym·pet.
"""
