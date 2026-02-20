"""
korean_fuzzy.py
한글 자모 분해 기반 퍼지 매칭 모듈

- 한글을 자모(초성/중성/종성)로 분해하여 편집거리 계산
- 자모 단위 비교이므로 "신펑"↔"신평"처럼 모음 1개 차이도 정확히 감지
- 폐쇄망 환경에서 외부 API 없이 동작
"""

from typing import Optional

# 초성 19자
_CHOSUNG = [
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]

# 중성 21자
_JUNGSUNG = [
    "ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ",
    "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ",
]

# 종성 28자 (첫 번째는 종성 없음)
_JONGSUNG = [
    "", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ",
    "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]

_HANGUL_BASE = 0xAC00
_HANGUL_END = 0xD7A3


def decompose(text: str) -> str:
    """한글 문자열을 자모로 분해한다. 비한글 문자는 그대로 유지."""
    result = []
    for ch in text:
        code = ord(ch)
        if _HANGUL_BASE <= code <= _HANGUL_END:
            offset = code - _HANGUL_BASE
            cho = offset // (21 * 28)
            jung = (offset % (21 * 28)) // 28
            jong = offset % 28
            result.append(_CHOSUNG[cho])
            result.append(_JUNGSUNG[jung])
            if jong > 0:
                result.append(_JONGSUNG[jong])
        else:
            result.append(ch)
    return "".join(result)


def jamo_edit_distance(a: str, b: str) -> int:
    """자모 분해된 두 문자열의 편집거리(Levenshtein distance)를 계산한다."""
    ja = decompose(a)
    jb = decompose(b)
    la, lb = len(ja), len(jb)

    # 최적화: 길이 차이가 너무 크면 빠른 반환
    if abs(la - lb) > max(la, lb) * 0.5:
        return max(la, lb)

    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)

    for i in range(1, la + 1):
        curr[0] = i
        for j in range(1, lb + 1):
            cost = 0 if ja[i - 1] == jb[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,      # 삭제
                curr[j - 1] + 1,  # 삽입
                prev[j - 1] + cost,  # 치환
            )
        prev, curr = curr, prev

    return prev[lb]


def jamo_similarity(a: str, b: str) -> float:
    """자모 기반 유사도 (0.0 ~ 1.0). 1.0이 완전 일치."""
    ja = decompose(a)
    jb = decompose(b)
    max_len = max(len(ja), len(jb))
    if max_len == 0:
        return 1.0
    dist = jamo_edit_distance(a, b)
    return 1.0 - (dist / max_len)


def find_best_match(
    query: str,
    candidates: list[str],
    threshold: float = 0.6,
) -> Optional[tuple[str, float]]:
    """
    query와 가장 유사한 candidate를 찾는다.
    복합 점수: 자모 유사도(80%) + 글자 수 유사도(20%)
    오타는 글자 수를 보존하는 경향이 있으므로 같은 길이 후보를 우선한다.

    Args:
        query: 사용자 입력 텍스트
        candidates: 후보 목록
        threshold: 최소 유사도 임계값 (기본 0.6)

    Returns:
        (best_match, similarity) 또는 None (임계값 미달 시)
    """
    if not query or not candidates:
        return None

    best_match = None
    best_combined = 0.0
    best_jamo = 0.0
    query_len = len(query)

    for candidate in candidates:
        jamo_score = jamo_similarity(query, candidate)
        max_len = max(len(candidate), query_len)
        len_score = 1.0 - (abs(len(candidate) - query_len) / max_len) if max_len > 0 else 1.0
        combined = jamo_score * 0.8 + len_score * 0.2

        if combined > best_combined:
            best_combined = combined
            best_jamo = jamo_score
            best_match = candidate

    if best_match and best_jamo >= threshold:
        return (best_match, best_jamo)
    return None
