"""
LLM 자연어 서술 공통 유틸 (C안 확장 공유 모듈)

4개 엔드포인트가 사용하는 공통 로직을 이 모듈로 통합:
- trend.py (/trend/explain)
- anomaly_explain.py (/anomaly/explain)
- tag_latest_explain.py (/tag/latest/explain)
- scan_all_explain.py (/anomaly/scan-all/explain)
- equipment_mtbf_explain.py (/equipment-mtbf/explain)

포함 함수:
- extract_numbers: 텍스트에서 숫자 추출
- strip_identifier_strings: 식별자(설비ID·태그명) 내부 숫자 오탐 방지
- validate_numbers_in_text: 허용 수치와 대조 (2% tolerance)
- is_context_enabled: tb_comm_code 기반 컨텍스트 토글 조회
"""

import logging
import re
from typing import Callable, Optional

logger = logging.getLogger("slm")

# 숫자 추출용 정규식 — 천단위 구분자·소수·부호 포함
_NUM_RE = re.compile(r"(?<![0-9A-Za-z])(-?\d+(?:,\d{3})*(?:\.\d+)?)")

# 문장 형식상 불가피한 정수 (ex: "2문장", "1회")
_NUM_IGNORE = {"1", "2"}


def extract_numbers(text: str) -> list[float]:
    """텍스트에서 숫자를 추출. 콤마 제거, ignore 단어 제외."""
    out = []
    for m in _NUM_RE.finditer(text):
        tok = m.group(1).replace(",", "")
        if tok in _NUM_IGNORE:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def strip_identifier_strings(text: str, strings: list[str]) -> str:
    """식별자 문자열(설비ID·태그명)을 공백으로 치환.
    내부 숫자·하이픈이 검증에 오탐되지 않게 한다 (긴 문자열 우선 치환).
    """
    result = text
    for s in sorted(set(strings), key=len, reverse=True):
        if s and s in result:
            result = result.replace(s, " ")
    return result


def validate_numbers_in_text(
    text: str,
    allowed: list[float],
    strip_strings: Optional[list[str]] = None,
    tolerance: float = 0.02,
) -> tuple[bool, list[float]]:
    """텍스트 내 모든 숫자가 허용 수치와 tolerance 내 일치하는지 검증.

    Args:
        text: LLM 출력
        allowed: DB에서 가져온 허용 수치 목록
        strip_strings: 검증 전 제거할 식별자 (설비ID·태그명·시설명 등)
        tolerance: 상대 오차 (기본 2%, 최소 절대 오차 0.01)

    Returns:
        (유효 여부, 위반 숫자 목록)
    """
    cleaned = strip_identifier_strings(text, strip_strings or [])
    nums = extract_numbers(cleaned)
    violations: list[float] = []
    for n in nums:
        ok = any(abs(n - a) <= max(abs(a) * tolerance, 0.01) for a in allowed)
        if not ok:
            violations.append(n)
    return (len(violations) == 0, violations)


def is_context_enabled(
    get_db_connection: Optional[Callable],
    grp_cd: str,
    comm_cd: str,
) -> bool:
    """tb_comm_code 기반 컨텍스트 주입 토글. 에러/미설정 시 기본 True."""
    if get_db_connection is None:
        return True
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT use_yn FROM tb_comm_code "
                    "WHERE region = 'R01' AND grp_cd = %s AND comm_cd = %s",
                    (grp_cd, comm_cd),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return True
        return (row[0] or "Y") == "Y"
    except Exception:
        return True
