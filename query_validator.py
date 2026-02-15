"""
query_validator.py
질의 검증 + 정정 메시지 생성

검증 순서:
1. 범위 검증 (Stage 1 결과가 "범위외")
2. 필수 파라미터 검증 (SQL 템플릿의 {param} vs 추출된 params)
3. sitename 존재 검증
4. 날짜 범위 논리 검증 (from_ts > to_ts)
5. 시설-INTENT 정합성

정정 메시지는 템플릿 기반 (SLM 미사용)
"""

import logging
from datetime import datetime
from typing import Optional

from intent_index import IntentIndex

logger = logging.getLogger(__name__)

# 정정 메시지 템플릿
CORRECTION_TEMPLATES = {
    "out_of_scope": "해당 질문은 상수도 운영 관련 질의가 아닙니다. 상수도 시설(배수지, 가압장, 감압시설, 블록) 관련 질문을 해 주세요.",
    "no_intent_match": "질문을 이해하지 못했습니다. 다른 방식으로 질문해 주세요.",
    "missing_sitename": "현장명을 명시해 주세요. 예: '신평 배수지 일반현황은?'",
    "missing_facilitytype": "시설 유형을 명시해 주세요. (배수지/가압장/감압시설/소블록/중블록/대블록)",
    "missing_block_level": "블록 유형을 명시해 주세요. (소블록, 중블록, 대블록)",
    "missing_datainfo": "데이터 유형을 명시해 주세요. (압력/유량/수위)",
    "missing_date_range": "조회 기간을 명시해 주세요. 예: '최근 7일', '2024-01-01~2024-01-31'",
    "invalid_date_range": "시작일이 종료일보다 나중입니다. 기간을 확인해 주세요.",
    "unknown_sitename": "'{sitename}'은(는) 등록되지 않은 현장명입니다.",
    "max_turns_exceeded": "대화 턴 수가 초과되었습니다. 새로운 질문을 시작해 주세요.",
}

# 정정 힌트
CORRECTION_HINTS = {
    "out_of_scope": ["배수지 일반현황은?", "가압장 운영현황은?", "소블록 압력 현황은?"],
    "no_intent_match": ["배수지 일반현황은?", "가압장 운영현황은?", "소블록 압력 현황은?"],
    "missing_sitename": ["신평 배수지 일반현황은?", "행정 가압장 운영현황은?", "합덕3 소블록 압력 현황은?"],
    "missing_facilitytype": ["신평 배수지 수위 현황은?", "행정 가압장 압력 현황은?", "합덕3 소블록 유량 현황은?"],
    "missing_block_level": ["합덕3 소블록 일반현황은?", "행정1-1 중블록 운영현황은?"],
    "missing_datainfo": ["신평 배수지 수위 트렌드는?", "행정 가압장 압력 현황은?"],
    "missing_date_range": ["최근 7일", "최근 한달", "2024-01-01~2024-01-31"],
    "invalid_date_range": ["시작일과 종료일을 확인해 주세요."],
    "unknown_sitename": ["신평", "행정", "합덕3", "고대리"],
    "max_turns_exceeded": ["새 질문을 입력해 주세요."],
}


class ValidationResult:
    """검증 결과"""

    def __init__(
        self,
        is_valid: bool,
        error_type: Optional[str] = None,
        message: Optional[str] = None,
        hints: Optional[list] = None,
        missing_params: Optional[list] = None,
    ):
        self.is_valid = is_valid
        self.error_type = error_type
        self.message = message
        self.hints = hints or []
        self.missing_params = missing_params or []


class QueryValidator:
    def __init__(self, index: IntentIndex, known_sitenames: list,
                 sitename_facility_map: dict = None):
        self._index = index
        self._sitenames = known_sitenames
        self._sitename_facility_map = sitename_facility_map or {}

    def validate(
        self,
        category: str,
        intent_name: Optional[str],
        intent_def: Optional[dict],
        params: dict,
    ) -> ValidationResult:
        """
        질의를 검증한다.

        검증 순서:
        1. 범위 검증
        2. INTENT 매칭 확인
        3. 필수 파라미터 검증
        4. sitename 존재 검증
        5. 날짜 범위 논리 검증
        """
        # 1. 범위 검증
        if category == "범위외":
            return ValidationResult(
                is_valid=False,
                error_type="out_of_scope",
                message=CORRECTION_TEMPLATES["out_of_scope"],
                hints=CORRECTION_HINTS["out_of_scope"],
            )

        # 2. INTENT 매칭 확인
        if not intent_name or not intent_def:
            return ValidationResult(
                is_valid=False,
                error_type="no_intent_match",
                message=CORRECTION_TEMPLATES["no_intent_match"],
                hints=CORRECTION_HINTS["no_intent_match"],
            )

        # 3. 필수 파라미터 검증
        # fn_realtime_missing_summary()는 빈 문자열 = 전체 조회이므로 검증 스킵
        _SKIP_REQUIRED_CHECK = {"FACILITY_ABNORMAL_STATUS_SUMMARY"}
        intent_info = self._index.get_intent_summary(intent_name)
        if intent_info and intent_name not in _SKIP_REQUIRED_CHECK:
            required = intent_info.get("required_params", [])
            sql_template = intent_def.get("sql", "")
            if isinstance(sql_template, list):
                sql_template = " ".join(sql_template)

            missing = []

            # sitename 검증
            if "sitename" in required and not params.get("sitename"):
                missing.append("sitename")

            # facilitytype 검증
            if "facilitytype" in required and not params.get("facilitytype"):
                missing.append("facilitytype")

            # block_level 검증
            if "block_level" in required and not params.get("block_level"):
                missing.append("block_level")

            # datainfo 검증
            if "datainfo" in required and not params.get("datainfo"):
                missing.append("datainfo")

            # from_ts / to_ts 검증
            if "from_ts" in required and not params.get("from_ts"):
                missing.append("from_ts")
            if "to_ts" in required and not params.get("to_ts"):
                missing.append("to_ts")

            if missing:
                # 첫 번째 누락 파라미터 기준으로 메시지 생성
                error_type = self._missing_param_to_error_type(missing[0])
                return ValidationResult(
                    is_valid=False,
                    error_type=error_type,
                    message=CORRECTION_TEMPLATES.get(
                        error_type, f"필수 파라미터가 누락되었습니다: {', '.join(missing)}"
                    ),
                    hints=CORRECTION_HINTS.get(error_type, []),
                    missing_params=missing,
                )

        # 4. sitename 존재 검증 (와일드카드 %% 는 전체 조회용이므로 스킵)
        sitename = params.get("sitename")
        if sitename and sitename != "%%" and sitename not in self._sitenames:
            msg = CORRECTION_TEMPLATES["unknown_sitename"].format(sitename=sitename)
            return ValidationResult(
                is_valid=False,
                error_type="unknown_sitename",
                message=msg,
                hints=CORRECTION_HINTS["unknown_sitename"],
                missing_params=["sitename"],
            )

        # 5. sitename-facilitytype 정합성 검증
        facilitytype = params.get("facilitytype") or params.get("block_level")
        if (sitename and sitename != "%%"
                and facilitytype and facilitytype != "%%"
                and self._sitename_facility_map):
            available = self._sitename_facility_map.get(sitename)
            if available and facilitytype not in available:
                # 해당 현장에서 사용 가능한 시설 유형 안내
                avail_str = ", ".join(sorted(available))
                # 요청한 시설 유형에 등록된 다른 현장 안내
                sites_for_ftype = sorted([
                    s for s, ftypes in self._sitename_facility_map.items()
                    if facilitytype in ftypes
                ])
                hints = sites_for_ftype[:5] if sites_for_ftype else []
                msg = (
                    f"'{sitename}'은(는) '{facilitytype}'에 등록되지 않은 현장입니다. "
                    f"'{sitename}'의 등록 시설: {avail_str}."
                )
                if hints:
                    msg += f" '{facilitytype}' 등록 현장: {', '.join(hints)}."
                return ValidationResult(
                    is_valid=False,
                    error_type="sitename_facility_mismatch",
                    message=msg,
                    hints=[f"{h} {facilitytype}" for h in hints[:3]],
                    missing_params=["sitename"],
                )

        # 6. 날짜 범위 논리 검증
        from_ts = params.get("from_ts")
        to_ts = params.get("to_ts")
        if from_ts and to_ts:
            try:
                from_dt = datetime.strptime(from_ts, "%Y-%m-%d")
                to_dt = datetime.strptime(to_ts, "%Y-%m-%d")
                if from_dt > to_dt:
                    return ValidationResult(
                        is_valid=False,
                        error_type="invalid_date_range",
                        message=CORRECTION_TEMPLATES["invalid_date_range"],
                        hints=CORRECTION_HINTS["invalid_date_range"],
                        missing_params=["from_ts", "to_ts"],
                    )
            except ValueError:
                pass  # 형식 오류는 무시 (이미 추출 단계에서 검증)

        # 모든 검증 통과
        return ValidationResult(is_valid=True)

    def _missing_param_to_error_type(self, param_name: str) -> str:
        """누락 파라미터명을 error_type으로 변환한다."""
        mapping = {
            "sitename": "missing_sitename",
            "facilitytype": "missing_facilitytype",
            "block_level": "missing_block_level",
            "datainfo": "missing_datainfo",
            "from_ts": "missing_date_range",
            "to_ts": "missing_date_range",
        }
        return mapping.get(param_name, "missing_sitename")
