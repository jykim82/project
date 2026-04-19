"""
vision_agent.py — 멀티모달 현장 진단 에이전트 (포트 8100)

ai_server.py(8000)와 분리된 FastAPI 프로세스. Zero-Hallucination 경계를 프로세스
수준에서 강제하기 위해 분리. 다음 3가지 엔드포인트를 제공한다:

  POST /vision/diagnose       — 채팅 진단 (PLC/유량계/모뎀/RTU 사진 + 텍스트)
  POST /vision/register-parse — 명판/외관 OCR+VLM 파싱 → 설비 등록 초안
  POST /vision/manual-search  — 매뉴얼 RAG 단독 검색 (P3에서 구현)
  GET  /health                — 모델 상태

이 프로세스는 gemma3:4b (Ollama) 만 사용한다:
  - 멀티모달 VLM 호출 (이미지 → 텍스트 설명)
  - OCR 기능 내장 (한국어 명판 텍스트 파싱)
  - 동일 모델로 2차 검증 (OCR 결과 재확인)

원칙 (work-history.md 1946~1957 / [E-019/E-023] 근거):
  1. advice_text는 "[AI 참고 의견] " 접두어 고정
  2. 수치·운영 지시·조치 생성 금지 (정규식 후처리로 강제)
  3. 매뉴얼 인용은 원문 그대로만 (요약·재구성 금지)
  4. DB 사실 응답과 시각적 분리를 위해 응답 스키마에 separate 필드
"""

import base64
import logging
import os
import re
import time
from typing import Optional, Any

import httpx
import numpy as np
import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────

OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
# ai_server의 gemma4:26b-a4b-it-q4_K_M이 multimodal(capabilities=['vision',...])
# 지원하므로 동일 모델 재사용. Ollama /api/show로 `vision` capability 확인됨.
# 같은 모델을 두 프로세스에서 참조해도 Ollama가 단일 인스턴스로 serving해서
# VRAM 중복 없음 (19.7GB 1벌 + keep_alive 24h 공유).
VISION_MODEL = os.environ.get("VISION_MODEL", "gemma4:26b-a4b-it-q4_K_M")
VISION_KEEP_ALIVE = os.environ.get("VISION_KEEP_ALIVE", "24h")
# 비전 호출은 이미지 처리로 cold-start 60~90s 발생 가능 → 텍스트 pipeline보다 여유
VISION_TIMEOUT_S = int(os.environ.get("VISION_TIMEOUT_S", "120"))

# facility-files / chat-attachments 저장소 — backend(컨테이너)와 host의
# vision_agent가 **같은 파일**을 읽어야 하므로 env로 각자 경로 주입.
# - backend 컨테이너: docker-compose.dev.yml에서 /data/files/... 로 설정
# - host vision_agent: 기본값 ../web/files/... (/Users/jykim/slm 기준)
# vision_agent.py는 /Users/jykim/slm/ 에 위치 → ".." + "web/files" = /Users/jykim/web/files
_DEFAULT_FILES_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "files")
)
FACILITY_FILE_BASE_DIR = os.environ.get(
    "FACILITY_FILE_BASE_DIR",
    os.path.join(_DEFAULT_FILES_ROOT, "facility"),
)
CHAT_ATTACHMENT_DIR = os.environ.get(
    "CHAT_ATTACHMENT_DIR",
    os.path.join(_DEFAULT_FILES_ROOT, "chat_attachments"),
)
# 레거시 환경변수명 하위호환
FILES_BASE = os.environ.get("FILES_BASE", FACILITY_FILE_BASE_DIR)

# [E-025 P3] 매뉴얼 RAG 설정
EMBEDDINGS_DIR = os.environ.get(
    "EMBEDDINGS_DIR",
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "manual_embeddings")),
)
# [P3-F] 고장 케이스 RAG — fault_case 엔드포인트와 동일 NPZ 디렉터리
FAULT_CASE_EMBEDDINGS_DIR = os.environ.get(
    "FAULT_CASE_EMBEDDINGS_DIR",
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fault_case_embeddings")),
)
EMBED_MODEL = os.environ.get("EMBED_MODEL", "snowflake-arctic-embed2:latest")

DB_HOST = os.environ.get("DB_HOST", "timescaledb")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "slm_dev_1234")

logger = logging.getLogger("vision_agent")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ─────────────────────────────────────────────────────────────────────
# FastAPI 앱
# ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="SLM Vision Agent",
    version="0.1.0",
    description="멀티모달 현장 진단 에이전트 (Zero-Hallucination 참고 의견 전용)",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev 환경 — 프로덕션은 ai_server만 허용
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────
# 모델
# ─────────────────────────────────────────────────────────────────────

EQUIPMENT_WHITELIST = [
    "PLC", "유량계", "모뎀", "RTU", "인버터", "펌프", "밸브",
    "수위계", "압력계", "UPS", "기타",
]


class DiagnoseRequest(BaseModel):
    image_url: str = Field(..., description="facility-files 저장 경로 (/files/...)")
    user_text: Optional[str] = Field("", description="사용자 질의 (옵셔널)")
    sitename: Optional[str] = Field(None, description="GIS 컨텍스트")
    facilitytype: Optional[str] = Field(None, description="GIS 컨텍스트")


class ManualExcerpt(BaseModel):
    manual_id: int
    manual_title: str
    page: int
    text: str
    score: float
    # [E-025 폐쇄망] 매뉴얼 PDF 다운로드 URL (프록시 경로)
    file_url: Optional[str] = None


class ActiveAlarm(BaseModel):
    """[E-025 P9] 매칭 설비와 연결된 활성 알람 1건.

    PK 매칭 키: (alarm_start_time, tagsn) — tb_equipment_alarm_report 기준.
    """
    alarm_start_time: str  # ISO string
    tagsn: str
    alarm_category: Optional[str] = None
    alarm_msg: Optional[str] = None
    alarm_severity: Optional[str] = None
    diagnosed_cause: Optional[str] = None
    duration_hours: Optional[float] = None


class FaultCaseHit(BaseModel):
    """[P3-F] tb_fault_case 검색 결과 1건."""
    case_id: int
    equipment_type: str
    brand: Optional[str] = None
    model: Optional[str] = None
    symptom: str
    cause: Optional[str] = None
    action: Optional[str] = None
    severity: Optional[str] = None
    reference_url: Optional[str] = None
    score: float


class DiagnoseResponse(BaseModel):
    equipment_guess: str
    equipment_type: str
    brand: Optional[str] = None
    model: Optional[str] = None
    confidence: float
    observed_state: list[str]
    advice_text: str
    manual_excerpts: list[ManualExcerpt] = []
    # [P3-F] 고장 케이스 DB 검색 결과 — 매뉴얼보다 우선 표시
    fault_cases: list[FaultCaseHit] = []
    is_registered: bool = False
    matched_equipment_id: Optional[str] = None
    sitename: Optional[str] = None
    facilitytype: Optional[str] = None
    # [E-025 P9] 문제 감지 + 활성 알람
    has_issue: bool = False
    issue_reasons: list[str] = []
    active_alarms: list[ActiveAlarm] = []
    # 디버그·감사
    vlm_generate_ms: int
    raw_vlm_response: Optional[str] = None


class RegisterParseRequest(BaseModel):
    image_url: str
    image_kind: str = Field("nameplate", description="nameplate|exterior|location")


class RegisterParseFields(BaseModel):
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    capacity: Optional[str] = None
    installed_year: Optional[str] = None
    equipment_type: Optional[str] = None  # PLC/유량계/...


class RegisterParseResponse(BaseModel):
    ocr_text: str
    fields: RegisterParseFields
    confidence_per_field: dict[str, float]
    needs_manual_input: bool
    raw_vlm_response: Optional[str] = None
    vlm_generate_ms: int


# ─────────────────────────────────────────────────────────────────────
# 유틸 — 이미지 로드 + Ollama 호출
# ─────────────────────────────────────────────────────────────────────

def _resolve_image_path(image_url: str) -> str:
    """이미지 URL을 로컬 파일 경로로 변환.

    지원 포맷:
      /api/files/facility/site_photo/<uuid>.jpg  (실제 facility-files 업로드)
        → {FACILITY_FILE_BASE_DIR}/site_photo/<uuid>.jpg
      /files/samples/ls_plc.jpg                  (레거시 / 테스트용)
        → {FILES_BASE}/samples/ls_plc.jpg
      http(s)://...                              (원격 — 미지원)
      절대경로(/app/test.jpg 등)                  (테스트 편의)
    """
    if image_url.startswith(("http://", "https://")):
        raise HTTPException(400, "원격 URL은 현재 지원하지 않습니다. facility-files 업로드 후 경로 전달 필요")
    # 절대 경로로 들어온 경우 (테스트 편의)
    if image_url.startswith("/") and os.path.exists(image_url):
        return image_url
    # 실제 API 경로
    if image_url.startswith("/api/files/facility/"):
        return os.path.join(FACILITY_FILE_BASE_DIR, image_url[len("/api/files/facility/"):])
    # 채팅 첨부 (vision_proxy 업로드)
    if image_url.startswith("/api/files/chat_attachments/"):
        return os.path.join(CHAT_ATTACHMENT_DIR, image_url[len("/api/files/chat_attachments/"):])
    # 레거시 /files/ 경로
    if image_url.startswith("/files/"):
        return os.path.join(FILES_BASE, image_url[len("/files/"):])
    # 상대 경로 fallback
    return os.path.join(FILES_BASE, image_url.lstrip("/"))


def _load_image_base64(image_url: str) -> str:
    """이미지 파일 → base64 (Ollama /api/generate images 파라미터용)."""
    path = _resolve_image_path(image_url)
    if not os.path.exists(path):
        raise HTTPException(404, f"이미지 파일을 찾을 수 없습니다: {path}")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _ollama_generate_vision(
    prompt: str,
    image_b64: str,
    num_predict: Optional[int] = None,
) -> tuple[str, int]:
    """Ollama /api/generate 호출 (gemma3:4b vision).

    Returns:
        (response_text, elapsed_ms)
    """
    payload: dict[str, Any] = {
        "model": VISION_MODEL,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "keep_alive": VISION_KEEP_ALIVE,
        "options": {
            "temperature": 0.1,  # vision은 deterministic 선호
        },
    }
    if num_predict is not None:
        payload["options"]["num_predict"] = num_predict

    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=VISION_TIMEOUT_S) as client:
            resp = client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(504, f"Ollama 응답 타임아웃 ({VISION_TIMEOUT_S}s)")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ollama 호출 실패: {e}")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    text = (data.get("response") or "").strip()
    return text, elapsed_ms


# ─────────────────────────────────────────────────────────────────────
# Zero-Hallucination 가드 — 수치 생성 차단
# ─────────────────────────────────────────────────────────────────────

# 운영 수치 패턴 — 이 패턴이 응답에 있으면 로그 경고 (LLM이 수치를 지어낸 것)
_NUMERIC_ALERT_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kgf|MPa|bar|m3|m³|L|l/min|m3/h|%|Hz|V|A|kW|kWh)\b",
    re.IGNORECASE,
)


def _sanitize_advice(text: str) -> tuple[str, list[str]]:
    """advice_text에서 수치 생성 여부 검출. 발견되면 경고 로그만 + text는 유지.

    Returns:
        (text, warnings[])
    """
    warnings = []
    matches = _NUMERIC_ALERT_PATTERN.findall(text)
    if matches:
        warnings.append(f"VLM이 수치 언급: {matches}")
        logger.warning(f"VLM 수치 생성 감지 (정보용): {matches}")
    return text, warnings


def _ensure_advice_prefix(text: str) -> str:
    """advice_text는 반드시 '[AI 참고 의견] '으로 시작."""
    prefix = "[AI 참고 의견] "
    stripped = text.strip()
    if not stripped.startswith(prefix):
        return prefix + stripped
    return stripped


# ─────────────────────────────────────────────────────────────────────
# /health
# ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    """vision_agent 상태 + Ollama 모델 로드 확인."""
    ollama_ok = False
    model_loaded = False
    vram_mb = None
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{OLLAMA_BASE_URL}/api/tags")
            r.raise_for_status()
            tags = r.json().get("models", [])
            ollama_ok = True
            model_loaded = any(m.get("name", "").startswith(VISION_MODEL.split(":")[0]) for m in tags)
            # running models
            rr = client.get(f"{OLLAMA_BASE_URL}/api/ps")
            rr.raise_for_status()
            for m in rr.json().get("models", []):
                if m.get("name") == VISION_MODEL:
                    vram_mb = int(m.get("size_vram", 0) / 1024 / 1024)
    except Exception as e:
        logger.warning(f"health check Ollama 확인 실패: {e}")
    return {
        "status": "ok",
        "service": "vision_agent",
        "version": "0.1.0",
        "vision_model": VISION_MODEL,
        "ollama_available": ollama_ok,
        "model_installed": model_loaded,
        "vram_mb": vram_mb,
        "files_base": FILES_BASE,
    }


# ─────────────────────────────────────────────────────────────────────
# /vision/diagnose — 채팅 진단
# ─────────────────────────────────────────────────────────────────────

_DIAGNOSE_PROMPT_TEMPLATE = """당신은 산업 설비 사진을 분석하는 참고 조언 전문가입니다.

## 절대 규칙 (위반 시 응답 거부)
1. 장비를 **식별**하고, 사진에서 **관찰 가능한 상태**만 서술하세요.
2. **수치를 생성하지 마세요** (예: "온도 45도", "압력 3bar" 등 — 사진에 명시된 계기판 수치가 아닌 추정값 금지).
3. **운영 지시·조치 절차·판단**을 포함하지 마세요. 참고 의견만 제공하세요.
4. 사진에 없는 정보는 "사진에서 확인되지 않음"으로 표시하세요.
5. 답변은 한국어 존댓말 "~습니다" 종결.

## LED·표시등 관찰 가이드 (관찰 사실만, 단정 금지)
사진에서 LED·표시등 라벨이 보이면 다음 형식으로 **개별** 서술하세요:
- 각 LED 라벨을 **하나씩** 분리 (예: "ERR LED 점등", "RUN LED 소등")
- 색상이 보이면 함께 명시 (빨강/녹색/노랑)
- "모두 점등" 같은 뭉뚱그린 표현 대신 라벨별 상태 나열
- 일반적인 의미는 advice_text 끝에 **참조 안내**로 한 문장 추가:
  "각 표시등의 정확한 의미는 제조사 매뉴얼을 참조해 주세요."
- **단정 금지**: "ERR이 점등되었으니 CPU 고장입니다" ❌
- **허용 패턴**: "ERR LED가 빨간색으로 점등되어 있습니다. 각 표시등의 정확한 의미는 제조사 매뉴얼을 참조해 주세요." ✅

## 장비 종류 화이트리스트 (반드시 이 중 하나 선택)
PLC, 유량계, 모뎀, RTU, 인버터, 펌프, 밸브, 수위계, 압력계, UPS, 기타

## 사용자 컨텍스트
- 사용자 질의: {user_text}
- 시설명: {sitename}
- 시설유형: {facilitytype}

## 출력 형식 (JSON, 반드시 이 스키마만)
```json
{{
  "equipment_type": "PLC|유량계|모뎀|RTU|인버터|펌프|밸브|수위계|압력계|UPS|기타",
  "brand": "LS | SIEMENS | 삼성 | 미상",
  "model": "모델명 | 미상",
  "equipment_guess": "제조사 모델명 (예: LS XGB-XBCH)",
  "confidence": 0.0~1.0,
  "observed_state": [
    "각 LED·표시등 라벨을 1개씩 분리해서 나열 — 예: 'POWER LED 점등 (빨강)', 'RUN LED 소등', 'ERR LED 점등 (빨강)'",
    "외관·라벨·표시등 외 관찰 사항도 1줄씩 분리"
  ],
  "advice_text": "[AI 참고 의견] 사진에서 관찰된 내용을 2~4문장으로 서술. 권고·지시·수치 금지. LED 라벨이 있다면 마지막 문장에 '각 표시등의 정확한 의미는 제조사 매뉴얼을 참조해 주세요.' 추가."
}}
```

JSON만 출력하세요. 추가 텍스트 금지."""


def _parse_diagnose_json(text: str) -> dict:
    """VLM의 JSON 응답을 파싱. 실패 시 기본값."""
    # ```json ... ``` 추출
    m = re.search(r"```(?:json)?\s*(\{[\s\S]+?\})\s*```", text)
    blob = m.group(1) if m else text
    # 중괄호 내부만 추출 (JSON 앞뒤 쓰레기 텍스트 제거)
    m2 = re.search(r"\{[\s\S]+\}", blob)
    if m2:
        blob = m2.group(0)
    try:
        import json
        data = json.loads(blob)
    except Exception as e:
        logger.warning(f"VLM JSON 파싱 실패: {e}, raw={text[:200]}")
        return {
            "equipment_type": "기타",
            "brand": "미상",
            "model": "미상",
            "equipment_guess": "식별 실패",
            "confidence": 0.0,
            "observed_state": [],
            "advice_text": "[AI 참고 의견] 사진을 분석했으나 구조화된 결과를 추출하지 못했습니다. 다른 각도에서 재촬영하거나 수기 입력을 시도해 주세요.",
        }
    # 화이트리스트 강제
    et = data.get("equipment_type", "기타")
    if et not in EQUIPMENT_WHITELIST:
        data["equipment_type"] = "기타"
    return data


@app.post("/vision/diagnose", response_model=DiagnoseResponse)
def diagnose(req: DiagnoseRequest) -> DiagnoseResponse:
    """사진 + 사용자 질의 → 장비 식별 + 관찰 상태 + 참고 의견."""
    logger.info(f"/vision/diagnose image={req.image_url} user_text={req.user_text!r}")

    image_b64 = _load_image_base64(req.image_url)

    prompt = _DIAGNOSE_PROMPT_TEMPLATE.format(
        user_text=(req.user_text or "(질의 없음)"),
        sitename=(req.sitename or "미상"),
        facilitytype=(req.facilitytype or "미상"),
    )

    raw_text, elapsed_ms = _ollama_generate_vision(prompt, image_b64)
    data = _parse_diagnose_json(raw_text)

    # advice_text 가드
    advice = data.get("advice_text", "")
    advice = _ensure_advice_prefix(advice)
    advice, warnings = _sanitize_advice(advice)

    equipment_guess = data.get("equipment_guess", "식별 실패")
    equipment_type = data.get("equipment_type", "기타")
    brand = data.get("brand")
    model = data.get("model")
    observed_state = data.get("observed_state", [])

    # [E-025 P3] 매뉴얼 RAG — 장비 식별 성공 시에만 검색
    manual_excerpts_raw: list[dict] = []
    # [P3-F] 고장 케이스 RAG — 매뉴얼보다 우선. equipment_type 이 기타여도 증상 기반으로 일부 매치 가능.
    fault_cases_raw: list[dict] = []
    if equipment_guess and equipment_guess != "식별 실패" and equipment_type != "기타":
        manual_excerpts_raw = _retrieve_manual_excerpts(
            equipment_guess=equipment_guess,
            equipment_type=equipment_type,
            brand=brand,
            observed_state=observed_state,
            user_text=req.user_text,
            top_k=3,
        )
    if equipment_type != "기타" or observed_state:
        fault_cases_raw = _retrieve_fault_cases(
            equipment_type=equipment_type,
            brand=brand,
            observed_state=observed_state,
            user_text=req.user_text,
            top_k=3,
        )

    # [E-025 P8] 기존 설비 DB 매칭 — sitename + equipmenttype + (model|brand)
    matched_equipment_id: Optional[str] = None
    try:
        matched_equipment_id = _match_existing_equipment(
            brand=brand,
            model=model,
            equipment_type=equipment_type,
            sitename=req.sitename,
        )
    except Exception as e:
        logger.warning(f"[MATCH] 매칭 중 에러: {e}")
    if matched_equipment_id:
        logger.info(f"[MATCH] is_registered=True equipment_id={matched_equipment_id}")

    # [E-025 P9] 문제 감지 + 활성 알람 조회
    has_issue, issue_reasons = _detect_issue(observed_state, req.user_text)
    active_alarms_raw: list[dict] = []
    if equipment_type != "기타":
        active_alarms_raw = _fetch_active_alarms(
            sitename=req.sitename,
            equipmenttype=equipment_type,
            limit=5,
        )
    if active_alarms_raw:
        has_issue = True  # 활성 알람이 있으면 무조건 문제 있음
        if "연결된 활성 알람" not in issue_reasons:
            issue_reasons.append("연결된 활성 알람")
    logger.info(
        f"[ISSUE] has_issue={has_issue} reasons={issue_reasons} "
        f"active_alarms={len(active_alarms_raw)}"
    )

    return DiagnoseResponse(
        equipment_guess=equipment_guess,
        equipment_type=equipment_type,
        brand=brand,
        model=model,
        confidence=float(data.get("confidence", 0.0)),
        observed_state=observed_state,
        advice_text=advice,
        manual_excerpts=[ManualExcerpt(**e) for e in manual_excerpts_raw],
        fault_cases=[FaultCaseHit(**c) for c in fault_cases_raw],
        is_registered=bool(matched_equipment_id),
        matched_equipment_id=matched_equipment_id,
        has_issue=has_issue,
        issue_reasons=issue_reasons,
        active_alarms=[ActiveAlarm(**a) for a in active_alarms_raw],
        sitename=req.sitename,
        facilitytype=req.facilitytype,
        vlm_generate_ms=elapsed_ms,
        raw_vlm_response=raw_text[:500] if os.environ.get("VISION_DEBUG") else None,
    )


# ─────────────────────────────────────────────────────────────────────
# /vision/register-parse — 명판/외관 파싱
# ─────────────────────────────────────────────────────────────────────

_REGISTER_PARSE_PROMPT = """당신은 산업 설비 명판(nameplate) 사진에서 텍스트를 추출하는 OCR+파싱 전문가입니다.

## 작업
사진에 찍힌 명판의 텍스트를 읽고, 구조화된 필드로 추출하세요.

## 출력 형식 (JSON만)
```json
{{
  "ocr_text": "명판에 적힌 모든 텍스트를 원본 그대로 (줄바꿈 유지)",
  "fields": {{
    "manufacturer": "제조사 (LS, LSIS, SIEMENS, 삼성, ABB 등 | 미상)",
    "model": "모델명 (예: XGB-XBCH | 미상)",
    "serial": "일련번호 (S/N, Serial | 미상)",
    "capacity": "용량 (예: 5.5kW, 100m3/h | 미상)",
    "installed_year": "제조/설치 연도 (4자리 | 미상)",
    "equipment_type": "PLC|유량계|모뎀|RTU|인버터|펌프|밸브|수위계|압력계|UPS|기타"
  }},
  "confidence_per_field": {{
    "manufacturer": 0.0~1.0,
    "model": 0.0~1.0,
    "serial": 0.0~1.0,
    "capacity": 0.0~1.0,
    "installed_year": 0.0~1.0,
    "equipment_type": 0.0~1.0
  }},
  "needs_manual_input": false
}}
```

## 규칙
1. 사진에서 읽을 수 없는 필드는 `"미상"` + confidence 0.0
2. 모든 필드가 불확실하면 `"needs_manual_input": true`
3. OCR 원본 텍스트는 절대 수정하지 말고 그대로 보존
4. 장비 종류는 명판의 단서(제조사/형식/모델 패턴)로 추정

JSON만 출력하세요."""


def _parse_register_json(text: str) -> dict:
    """register-parse VLM 응답 파싱."""
    m = re.search(r"```(?:json)?\s*(\{[\s\S]+?\})\s*```", text)
    blob = m.group(1) if m else text
    m2 = re.search(r"\{[\s\S]+\}", blob)
    if m2:
        blob = m2.group(0)
    try:
        import json
        data = json.loads(blob)
    except Exception as e:
        logger.warning(f"register-parse JSON 파싱 실패: {e}, raw={text[:200]}")
        return {
            "ocr_text": text[:500],
            "fields": {},
            "confidence_per_field": {},
            "needs_manual_input": True,
        }
    return data


@app.post("/vision/register-parse", response_model=RegisterParseResponse)
def register_parse(req: RegisterParseRequest) -> RegisterParseResponse:
    """명판/외관 사진 → OCR + 구조화 필드 파싱."""
    logger.info(f"/vision/register-parse image={req.image_url} kind={req.image_kind}")

    image_b64 = _load_image_base64(req.image_url)
    raw_text, elapsed_ms = _ollama_generate_vision(_REGISTER_PARSE_PROMPT, image_b64)
    data = _parse_register_json(raw_text)

    fields_raw = data.get("fields", {}) or {}
    fields = RegisterParseFields(
        manufacturer=fields_raw.get("manufacturer") if fields_raw.get("manufacturer") not in ("미상", "", None) else None,
        model=fields_raw.get("model") if fields_raw.get("model") not in ("미상", "", None) else None,
        serial=fields_raw.get("serial") if fields_raw.get("serial") not in ("미상", "", None) else None,
        capacity=fields_raw.get("capacity") if fields_raw.get("capacity") not in ("미상", "", None) else None,
        installed_year=fields_raw.get("installed_year") if fields_raw.get("installed_year") not in ("미상", "", None) else None,
        equipment_type=fields_raw.get("equipment_type"),
    )

    # needs_manual_input 추가 판정
    needs_manual = data.get("needs_manual_input", False)
    conf = data.get("confidence_per_field", {}) or {}
    if conf and sum(conf.values()) / max(1, len(conf)) < 0.3:
        needs_manual = True
    if not any([fields.manufacturer, fields.model, fields.serial]):
        needs_manual = True

    return RegisterParseResponse(
        ocr_text=data.get("ocr_text", ""),
        fields=fields,
        confidence_per_field=conf,
        needs_manual_input=needs_manual,
        raw_vlm_response=raw_text[:500] if os.environ.get("VISION_DEBUG") else None,
        vlm_generate_ms=elapsed_ms,
    )


# ─────────────────────────────────────────────────────────────────────
# [E-025 P3] 매뉴얼 RAG 인덱스 (lazy-load NPZ + cosine search)
# ─────────────────────────────────────────────────────────────────────

class _ManualRagIndex:
    """tb_equipment_manual + NPZ 임베딩 캐시를 메모리로 로드해서 cosine 검색 제공.

    - 최초 검색 호출 시점에 일괄 로드 (startup 지연 회피)
    - NPZ는 L2 normalized float32 (index_manuals.py가 보장)
    - 17개 매뉴얼 × ~150 청크 = ~2580 × 1024 × 4B ≈ 10MB — 전량 RAM 유지
    """

    def __init__(self) -> None:
        self._loaded = False
        self._embeddings: Optional[np.ndarray] = None  # (N, 1024)
        self._rows: list[dict] = []

    def load(self) -> None:
        if self._loaded:
            return
        t0 = time.perf_counter()
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        try:
            cur = conn.cursor()
            # manual_type 컬럼 존재 확인 후 조회 (migration 전 스키마 호환)
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name='tb_equipment_manual' AND column_name='manual_type'
                """
            )
            has_manual_type = cur.fetchone() is not None
            if has_manual_type:
                cur.execute(
                    """
                    SELECT manual_id, equipment_type, brand, model, title, embedding_key,
                           COALESCE(manual_type, 'user_manual'), file_url
                    FROM tb_equipment_manual
                    WHERE embedding_key IS NOT NULL
                    ORDER BY manual_id
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT manual_id, equipment_type, brand, model, title, embedding_key,
                           'user_manual' AS manual_type, file_url
                    FROM tb_equipment_manual
                    WHERE embedding_key IS NOT NULL
                    ORDER BY manual_id
                    """
                )
            manuals = cur.fetchall()
            cur.close()
        finally:
            conn.close()

        all_embs: list[np.ndarray] = []
        for manual_id, etype, brand, model, title, key, manual_type, file_url in manuals:
            npz_path = os.path.join(EMBEDDINGS_DIR, f"{key}.npz")
            if not os.path.exists(npz_path):
                logger.warning(f"[RAG] npz missing: {npz_path}")
                continue
            try:
                data = np.load(npz_path, allow_pickle=True)
                embs = data["embeddings"].astype(np.float32)
                pages = data["pages"]
                texts = data["texts"]
            except Exception as e:
                logger.warning(f"[RAG] npz load failed {npz_path}: {e}")
                continue
            for i in range(embs.shape[0]):
                self._rows.append({
                    "manual_id": int(manual_id),
                    "title": title,
                    "brand": brand,
                    "model": model,
                    "equipment_type": etype,
                    "manual_type": manual_type or "user_manual",
                    "file_url": file_url,
                    "page": int(pages[i]),
                    "text": str(texts[i]),
                })
            all_embs.append(embs)

        if all_embs:
            self._embeddings = np.concatenate(all_embs, axis=0)
        else:
            self._embeddings = np.zeros((0, 1024), dtype=np.float32)

        self._loaded = True
        logger.info(
            f"[RAG] loaded {len(manuals)} manuals / {len(self._rows)} chunks / "
            f"{int((time.perf_counter()-t0)*1000)}ms"
        )

    def search(
        self,
        query_emb: np.ndarray,
        top_k: int = 3,
        equipment_type: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> list[dict]:
        """query_emb (shape (1024,), L2 normalized) → top_k 인용."""
        if not self._loaded or self._embeddings is None or self._embeddings.shape[0] == 0:
            return []

        scores = self._embeddings @ query_emb  # (N,)

        # 장비 타입/브랜드 일치 + manual_type 선호도 soft boost
        # (filter 대신 boost — catalog에도 의미 있는 내용이 있을 수 있어 완전 제외하지 않음)
        bonus = np.zeros(len(self._rows), dtype=np.float32)
        for i, row in enumerate(self._rows):
            if equipment_type and row["equipment_type"] == equipment_type:
                bonus[i] += 0.15
            if brand and row["brand"] and row["brand"].lower() == brand.lower():
                bonus[i] += 0.10
            # [E-025 P14] user_manual은 트러블슈팅·조치방법 등 실전 정보가 많으므로
            # catalog(표지/스펙/목차 위주)보다 우선. 점수 차는 작게(0.08)해서
            # 의미 없는 user_manual이 의미 있는 catalog를 덮지 않도록 함.
            mt = row.get("manual_type")
            if mt == "user_manual":
                bonus[i] += 0.08
            elif mt == "catalog":
                bonus[i] -= 0.05
        final_scores = scores + bonus

        # top-k 후보를 넉넉히 뽑아 중복 페이지 제거
        k_pool = min(top_k * 4, len(self._rows))
        top_idx = np.argpartition(-final_scores, k_pool - 1)[:k_pool]
        top_idx = top_idx[np.argsort(-final_scores[top_idx])]

        seen: set[tuple[int, int]] = set()
        results: list[dict] = []
        for idx in top_idx:
            r = self._rows[int(idx)]
            key = (r["manual_id"], r["page"])
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "manual_id": r["manual_id"],
                "manual_title": r["title"],
                "page": r["page"],
                "text": r["text"][:600],
                "score": float(scores[int(idx)]),
                "file_url": r.get("file_url"),
            })
            if len(results) >= top_k:
                break
        return results


_rag_index = _ManualRagIndex()


def _embed_query(text: str) -> np.ndarray:
    """Ollama /api/embed → snowflake-arctic-embed2, L2 normalized (1024,) float32."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
        )
        resp.raise_for_status()
        data = resp.json()
    emb = np.array(data["embeddings"][0], dtype=np.float32)
    norm = float(np.linalg.norm(emb))
    if norm > 0:
        emb = emb / norm
    return emb


def _build_search_query(
    equipment_guess: str,
    observed_state: list[str],
    user_text: Optional[str],
) -> str:
    """VLM 결과에서 매뉴얼 검색용 쿼리 문자열 조립."""
    parts: list[str] = []
    if equipment_guess and equipment_guess != "식별 실패":
        parts.append(equipment_guess)
    if observed_state:
        parts.extend(observed_state[:6])
    if user_text:
        parts.append(user_text)
    return " ".join(parts).strip() or equipment_guess or "PLC status LED"


def _match_existing_equipment(
    brand: Optional[str],
    model: Optional[str],
    equipment_type: str,
    sitename: Optional[str],
) -> Optional[str]:
    """[E-025 P8 + review #6] VLM 식별 결과 → tb_equipment_info 매칭 → equipment_id 반환.

    매칭 우선순위 (sitename + equipmenttype 내에서만):
      1. meta.model ILIKE %VLM model% (substring, 대소문자 무시)
      2. meta.manufacturer ILIKE %VLM brand% (model 실패 시 fallback)

    [review #6] 글로벌 매칭(sitename 없이)은 **비활성화**. 사이트 컨텍스트 없이
    brand/model만으로 매칭하면 동명 장비가 여러 사이트에 있을 때 오탐 발생.
    sitename이 없거나 site 내 매칭 실패면 `None` 반환 (is_registered=False로
    노출되어 사용자가 명시적으로 등록하도록 유도).
    """
    if not sitename:
        return None
    if not model and not brand:
        return None
    if not equipment_type or equipment_type == "기타":
        return None

    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
    except Exception as e:
        logger.warning(f"[MATCH] DB 연결 실패: {e}")
        return None

    try:
        cur = conn.cursor()

        def _search_model() -> Optional[str]:
            if not model:
                return None
            cur.execute(
                """
                SELECT equipment_id FROM tb_equipment_info
                WHERE equipmenttype = %s AND sitename = %s
                  AND (meta->>'model') ILIKE %s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (equipment_type, sitename, f"%{model}%"),
            )
            row = cur.fetchone()
            return row[0] if row else None

        def _search_brand() -> Optional[str]:
            if not brand:
                return None
            cur.execute(
                """
                SELECT equipment_id FROM tb_equipment_info
                WHERE equipmenttype = %s AND sitename = %s
                  AND (meta->>'manufacturer') ILIKE %s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (equipment_type, sitename, f"%{brand}%"),
            )
            row = cur.fetchone()
            return row[0] if row else None

        eq_id = _search_model() or _search_brand()
        cur.close()
        return eq_id
    finally:
        conn.close()


# [E-025 P9] 문제 감지 heuristic + 활성 알람 조회
# ─────────────────────────────────────────────────────────────────────

# observed_state 텍스트에 이 패턴이 있으면 "문제 있음"으로 간주
# Zero-Hallucination — LLM이 판단하지 않고 후처리로만 태깅
_ISSUE_KEYWORDS = [
    # LED 에러 계열 — 색상 포함 점등 (GREEN/녹색 제외)
    (re.compile(r"(ERR|ERROR|CHK|CHECK|BAT|BATTERY|FAULT|ALARM|ALM)\s*LED.*(점등|ON|켜)", re.IGNORECASE), "에러 LED 점등"),
    (re.compile(r"(빨간|빨강|적색|RED).*(점등|켜|ON)", re.IGNORECASE), "빨간 LED 점등"),
    (re.compile(r"(POWER|PWR|RUN)\s*LED.*(소등|OFF|꺼)", re.IGNORECASE), "전원/RUN LED 소등"),
    # 외관 이상
    (re.compile(r"(파손|균열|부식|누수|변색|그을|탄|찢어|깨진)", re.IGNORECASE), "외관 이상"),
    (re.compile(r"(연기|화염|스파크)", re.IGNORECASE), "연기·화재 징후"),
]


def _detect_issue(observed_state: list[str], user_text: Optional[str]) -> tuple[bool, list[str]]:
    """observed_state + user_text에서 문제 단서 탐지 (heuristic, LLM 판단 아님).

    Returns:
        (has_issue, matched_reasons[])
    """
    haystack = " ".join(observed_state or []) + " " + (user_text or "")
    reasons: list[str] = []
    for pattern, label in _ISSUE_KEYWORDS:
        if pattern.search(haystack):
            if label not in reasons:
                reasons.append(label)
    return bool(reasons), reasons


def _fetch_active_alarms(
    sitename: Optional[str],
    equipmenttype: Optional[str],
    limit: int = 5,
) -> list[dict]:
    """tb_equipment_alarm_report에서 (sitename, equipmenttype)의 활성 알람 조회.

    active = alarm_end_time IS NULL.
    equipment_id 컬럼은 현재 모두 NULL이라 사용 불가 — sitename/equipmenttype 기반.
    """
    if not sitename or not equipmenttype:
        return []

    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
    except Exception as e:
        logger.warning(f"[ALARM] DB 연결 실패: {e}")
        return []

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT alarm_start_time, tagsn, alarm_category, alarm_msg,
                   alarm_severity, diagnosed_cause,
                   EXTRACT(EPOCH FROM (NOW() - alarm_start_time))/3600.0 AS duration_h
            FROM tb_equipment_alarm_report
            WHERE sitename = %s
              AND equipmenttype = %s
              AND alarm_end_time IS NULL
            ORDER BY alarm_start_time DESC
            LIMIT %s
            """,
            (sitename, equipmenttype, limit),
        )
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        logger.warning(f"[ALARM] 조회 실패: {e}")
        conn.close()
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    results: list[dict] = []
    for r in rows:
        results.append({
            "alarm_start_time": r[0].isoformat() if r[0] else "",
            "tagsn": r[1] or "",
            "alarm_category": r[2],
            "alarm_msg": r[3],
            "alarm_severity": r[4],
            "diagnosed_cause": r[5],
            "duration_hours": round(float(r[6]), 1) if r[6] is not None else None,
        })
    return results


# ─────────────────────────────────────────────────────────────────────
# [P3-F] tb_fault_case RAG 인덱스 (NPZ + DB 메타 조인)
# ─────────────────────────────────────────────────────────────────────

class _FaultCaseIndex:
    """tb_fault_case + data/fault_case_embeddings/ NPZ 를 메모리에 로드.

    매뉴얼 인덱스와 구조 동일. 차이:
      - 1케이스 = 1 벡터 (청크 X)
      - symptom/cause/action 결합 텍스트로 임베딩
      - equipment_type/brand/model 메타로 boost
    """
    def __init__(self) -> None:
        self._loaded_count: int = -1  # -1 = 아직 로드 안 함
        self._embeddings: Optional[np.ndarray] = None
        self._rows: list[dict] = []

    def reload(self) -> None:
        """DB 변경이 있을 때 강제 재로드. 매 진단 요청마다 호출 비용 낮추려면
        `_fault_case_index.load()` 대신 `_FaultCaseIndex.reload_if_stale()` 같은
        경량 API 추가 가능 — 현재는 in-memory 캐시를 그대로 사용.
        """
        self._loaded_count = -1
        self.load()

    def load(self) -> None:
        if self._loaded_count >= 0:
            return
        t0 = time.perf_counter()
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT case_id, equipment_type, brand, model, symptom, cause, action,
                       severity, reference_url, embedding_key
                FROM tb_fault_case
                WHERE is_active = TRUE AND embedding_key IS NOT NULL
                """
            )
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()
        embs: list[np.ndarray] = []
        self._rows = []
        for case_id, etype, brand, model, symptom, cause, action, sev, ref_url, key in rows:
            path = os.path.join(FAULT_CASE_EMBEDDINGS_DIR, f"{key}.npz")
            if not os.path.exists(path):
                logger.warning(f"[FAULT_RAG] npz missing: {path}")
                continue
            try:
                data = np.load(path)
                emb = data["embedding"].astype(np.float32)
            except Exception as e:
                logger.warning(f"[FAULT_RAG] npz load 실패 {path}: {e}")
                continue
            embs.append(emb)
            self._rows.append({
                "case_id": int(case_id),
                "equipment_type": etype,
                "brand": brand,
                "model": model,
                "symptom": symptom,
                "cause": cause,
                "action": action,
                "severity": sev,
                "reference_url": ref_url,
            })
        self._embeddings = np.stack(embs, axis=0) if embs else np.zeros((0, 1024), dtype=np.float32)
        self._loaded_count = len(self._rows)
        logger.info(
            f"[FAULT_RAG] loaded {self._loaded_count} cases / {int((time.perf_counter()-t0)*1000)}ms"
        )

    def search(
        self,
        query_emb: np.ndarray,
        equipment_type: Optional[str] = None,
        brand: Optional[str] = None,
        top_k: int = 3,
        min_score: float = 0.35,
    ) -> list[dict]:
        if self._embeddings is None or self._embeddings.shape[0] == 0:
            return []
        scores = self._embeddings @ query_emb
        bonus = np.zeros(len(self._rows), dtype=np.float32)
        for i, row in enumerate(self._rows):
            if equipment_type and row["equipment_type"] == equipment_type:
                bonus[i] += 0.15
            if brand and row["brand"] and row["brand"].lower() == brand.lower():
                bonus[i] += 0.10
        final = scores + bonus
        idx_sorted = np.argsort(-final)
        results: list[dict] = []
        for idx in idx_sorted[: top_k * 2]:
            raw_score = float(scores[int(idx)])
            if raw_score < min_score:
                continue
            row = self._rows[int(idx)]
            results.append({**row, "score": raw_score})
            if len(results) >= top_k:
                break
        return results


_fault_case_index = _FaultCaseIndex()


def _retrieve_fault_cases(
    equipment_type: str,
    brand: Optional[str],
    observed_state: list[str],
    user_text: Optional[str],
    top_k: int = 3,
) -> list[dict]:
    """tb_fault_case RAG 검색. 매뉴얼 RAG 보다 높은 정밀도 기대 (구조화된 증상 텍스트)."""
    try:
        _fault_case_index.load()
    except Exception as e:
        logger.warning(f"[FAULT_RAG] index load 실패: {e}")
        return []
    if _fault_case_index._loaded_count == 0:
        return []
    query = _build_search_query("", observed_state, user_text)
    if not query:
        return []
    try:
        query_emb = _embed_query(query)
    except Exception as e:
        logger.warning(f"[FAULT_RAG] embed 실패: {e}")
        return []
    results = _fault_case_index.search(
        query_emb,
        equipment_type=equipment_type if equipment_type and equipment_type != "기타" else None,
        brand=brand,
        top_k=top_k,
    )
    logger.info(f"[FAULT_RAG] query={query[:80]!r} → {len(results)} cases")
    return results


def _retrieve_manual_excerpts(
    equipment_guess: str,
    equipment_type: str,
    brand: Optional[str],
    observed_state: list[str],
    user_text: Optional[str],
    top_k: int = 3,
) -> list[dict]:
    """VLM 출력 → 매뉴얼 RAG 검색 → ManualExcerpt dict 리스트."""
    try:
        _rag_index.load()
    except Exception as e:
        logger.warning(f"[RAG] index load 실패: {e}")
        return []

    query = _build_search_query(equipment_guess, observed_state, user_text)
    try:
        query_emb = _embed_query(query)
    except Exception as e:
        logger.warning(f"[RAG] query embed 실패: {e}")
        return []

    # equipment_type은 화이트리스트(PLC/유량계/...) — tb_equipment_manual과 동일 키 사용
    results = _rag_index.search(
        query_emb,
        top_k=top_k,
        equipment_type=equipment_type if equipment_type and equipment_type != "기타" else None,
        brand=brand,
    )
    logger.info(f"[RAG] query={query[:80]!r} → {len(results)} excerpts")
    return results


# ─────────────────────────────────────────────────────────────────────
# [P3-F] fault_case 인덱스 수동 재로드 (CRUD 후 즉시 반영)
# ─────────────────────────────────────────────────────────────────────

@app.post("/vision/fault-cases/reload")
def reload_fault_cases() -> dict:
    """tb_fault_case 가 변경되면 호출 — 메모리 인덱스 강제 재로드."""
    try:
        _fault_case_index.reload()
        return {"status": "ok", "loaded": _fault_case_index._loaded_count}
    except Exception as e:
        logger.warning(f"fault_case reload 실패: {e}")
        raise HTTPException(500, f"reload 실패: {e}")


# ─────────────────────────────────────────────────────────────────────
# /vision/manual-search — 독립 RAG 엔드포인트
# ─────────────────────────────────────────────────────────────────────

class ManualSearchRequest(BaseModel):
    equipment_type: str
    brand: Optional[str] = None
    model: Optional[str] = None
    query: str
    top_k: int = 3


@app.post("/vision/manual-search")
def manual_search(req: ManualSearchRequest) -> dict:
    """매뉴얼 RAG 검색 — snowflake-arctic-embed2 쿼리 임베딩 → cosine top-k."""
    logger.info(
        f"/vision/manual-search type={req.equipment_type} brand={req.brand} q={req.query[:60]!r}"
    )
    try:
        _rag_index.load()
    except Exception as e:
        logger.error(f"[RAG] index load 실패: {e}")
        raise HTTPException(500, f"RAG 인덱스 로드 실패: {e}")

    try:
        query_emb = _embed_query(req.query)
    except Exception as e:
        raise HTTPException(502, f"embedding 호출 실패: {e}")

    excerpts = _rag_index.search(
        query_emb,
        top_k=req.top_k,
        equipment_type=req.equipment_type if req.equipment_type != "기타" else None,
        brand=req.brand,
    )
    return {
        "status": "ok",
        "query": req.query,
        "excerpts": excerpts,
        "total_chunks": len(_rag_index._rows),
    }


# ─────────────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("VISION_AGENT_PORT", "8100"))
    logger.info(f"vision_agent starting on :{port} (model={VISION_MODEL})")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
