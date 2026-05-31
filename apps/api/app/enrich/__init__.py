"""Tier-2 enrichment — lazy read-through (설계 §6).

후보 단지 × 속성 → store 조회(TTL) → miss면 주입형 추출기 → write-back(TTL+provenance).
추출기가 주입형이라 키리스로 게이트 가능(실 웹/LLM은 P1-2).
"""
