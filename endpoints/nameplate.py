"""
명판 비전 입력 API — docs/vision-input-spec.md §2 (로드맵 E P1)

- POST /nameplate/extract — 명판 사진 → 동적 키-값 추출 + 표준 키 승격 제안
- POST /nameplate/save    — 확인·수정된 항목을 설비 meta.nameplate 에 저장

원칙 (사전 검증 2026-07-29, 실물 5장):
- 추출은 초안, 확정은 사람 — 모든 항목(수치 포함) 수정 가능 전제
- 인식한 수준까지만 — 명판 밖 지식 생성 금지 (흐린 사진 환각 실증)
- 원본 사진이 정본 — files/facility/nameplates/ 보관, 경로를 컬럼·meta 에

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import base64
import json
import logging
import os
import re
import time

import httpx
from fastapi import APIRouter, Request

from slm_config import OLLAMA_BASE_URL, OLLAMA_MODEL

logger = logging.getLogger("slm")

router = APIRouter(tags=["nameplate"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


_PROMPT = """이 사진은 설비(펌프/모터 등)의 명판이다. 명판에 실제로 보이는 항목을
{"items": [{"label": "명판의 항목명 원문", "value": "값 원문(단위 포함)"}]}
JSON 으로 추출하라.
- 명판에 보이는 항목만. 읽을 수 없거나 흐린 항목은 넣지 말 것
- 명판 밖 지식(브랜드 상식 등)으로 값을 추정하지 말 것
- 항목명·값 모두 명판 표기 그대로 (번역·변환 금지)
JSON 만 출력."""

# 표준 키 승격 별칭 사전 — 원문 키-값이 정본, 승격은 파생 (§2.5)
_PROMOTE_ALIASES = {
    "manufacturer": ["manufacturer", "maker", "제조사", "제조원", "corporation"],
    # '품명'은 제외 — 품명은 설비 명칭이지 모델이 아니다 (HANIL 실측:
    # 품명='배수용 수중펌프', 형식명='IPV-0733' 이 model)
    "model": ["model", "형식명", "형식", "type", "모델"],
    "rated_output": ["rated_output", "정격출력", "output", "motor", "kw"],
    "mfg_date": ["mfg_date", "제조년월", "제조일", "mfg.date", "date", "year"],
    "serial_no": ["serial_no", "ser.no", "ser no", "serial", "제조번호", "no."],
}

_STD_VOLTAGES = {110, 220, 380, 440, 460, 3300, 6600}


def _promote(items: list[dict]) -> dict:
    """별칭 사전으로 핵심 표준 키 파생 — 첫 매칭 라벨의 값."""
    out = {}
    for it in items:
        label = re.sub(r"[\s_.\-]", "", str(it.get("label", ""))).lower()
        if not label:
            continue
        for key, aliases in _PROMOTE_ALIASES.items():
            if key in out:
                continue
            for a in aliases:
                if re.sub(r"[\s_.\-]", "", a).lower() in label:
                    out[key] = it.get("value")
                    break
    return out


def _validate(items: list[dict]) -> list[str]:
    """범위·형식 검증 — 통과 못 한 항목은 경고로만 (저장은 사람이 결정)."""
    warns = []
    for it in items:
        label = str(it.get("label", "")).lower()
        val = str(it.get("value", ""))
        nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", val)]
        if ("kw" in label or "출력" in label) and nums and not (0.05 <= nums[0] <= 5000):
            warns.append(f"'{it.get('label')}' 출력값 범위 의심: {val}")
        if ("volt" in label or "전압" in label) and nums:
            if not any(int(n) in _STD_VOLTAGES for n in nums):
                warns.append(f"'{it.get('label')}' 표준 전압 아님: {val}")
        if ("hz" in label or "주파수" in label or "hertz" in label) and nums:
            if not any(int(n) in (50, 60) for n in nums):
                warns.append(f"'{it.get('label')}' 주파수 의심: {val}")
    return warns


@router.post("/nameplate/extract")
def extract_nameplate(body: dict):
    """명판 사진(base64) → 동적 키-값 + 표준 키 승격 제안 + 검증 경고.

    동기 def — 비전 추론 30~60초 블로킹을 threadpool 로 (event loop 보호).
    """
    try:
        img_b64 = body.get("image_base64") or ""
        if not img_b64:
            return {"status": "error", "message": "image_base64 필수"}
        t0 = time.time()
        resp = httpx.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": _PROMPT,
                              "images": [img_b64]}],
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=180.0,
        )
        resp.raise_for_status()
        txt = (resp.json().get("message") or {}).get("content", "").strip()
        if txt.startswith("```"):
            txt = txt.strip("`").replace("json\n", "", 1)
        items = (json.loads(txt).get("items") or [])
        items = [
            {"label": str(i.get("label", "")).strip(),
             "value": str(i.get("value", "")).strip()}
            for i in items
            if isinstance(i, dict) and str(i.get("label", "")).strip()
        ]
        elapsed = int((time.time() - t0) * 1000)
        logger.info(f"[nameplate] 추출 {len(items)}항목 {elapsed}ms")
        return {
            "status": "OK",
            "items": items,
            "promoted": _promote(items),
            "warnings": _validate(items),
            "elapsed_ms": elapsed,
        }
    except Exception as e:
        logger.error(f"명판 추출 실패: {e}")
        return {"status": "ERROR", "message": str(e)}


_FILES_DIR = os.environ.get("FILES_DIR", "/data/files")


@router.post("/nameplate/save")
async def save_nameplate(request: Request):
    """확인·수정된 항목 저장 — meta.nameplate + 사진 보관.

    Body: { equipment_id, items[], promoted{}, image_base64?, user_id }
    사진은 files/facility/nameplates/ 에 저장하고 nameplate_photo_url 갱신.
    """
    conn = None
    try:
        body = await request.json()
        equipment_id = (body.get("equipment_id") or "").strip()
        items = body.get("items") or []
        if not equipment_id or not items:
            return {"status": "error", "message": "equipment_id, items 필수"}
        user_id = (body.get("user_id") or "").strip() or "unknown"

        photo_url = None
        img_b64 = body.get("image_base64")
        if img_b64:
            sub = "facility/nameplates"
            os.makedirs(f"{_FILES_DIR}/{sub}", exist_ok=True)
            safe_id = re.sub(r"[^\w\-]", "_", equipment_id)
            fname = f"{safe_id}_{int(time.time())}.jpg"
            with open(f"{_FILES_DIR}/{sub}/{fname}", "wb") as f:
                f.write(base64.b64decode(img_b64))
            photo_url = f"/api/files/{sub}/{fname}"

        nameplate = {
            "items": items,
            "promoted": body.get("promoted") or {},
            "photo_url": photo_url,
            "source": "vision_ocr",   # 인식 수준 표시 — 수기와 구분
            "saved_by": user_id,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_equipment_info
            SET meta = COALESCE(meta, '{}'::jsonb)
                       || jsonb_build_object('nameplate', %s::jsonb),
                nameplate_photo_url = COALESCE(%s, nameplate_photo_url),
                updated_at = now()
            WHERE equipment_id = %s
            """,
            [json.dumps(nameplate, ensure_ascii=False), photo_url, equipment_id],
        )
        if cur.rowcount == 0:
            conn.rollback()
            return {"status": "error", "message": "설비를 찾을 수 없습니다"}
        conn.commit()
        cur.close()
        return {"status": "OK", "photo_url": photo_url,
                "item_count": len(items)}
    except Exception as e:
        logger.error(f"명판 저장 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
