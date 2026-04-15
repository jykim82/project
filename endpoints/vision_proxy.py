"""
ai_server.py ↔ vision_agent.py 프록시 엔드포인트 [E-025]

ai_server.py(8000)에 등록되는 라우터. 클라이언트로부터 multipart 이미지 +
질의를 받아 vision_agent(8100)로 포워딩하고 SSE로 스트리밍한다.

설계:
  - 기존 /ask/stream(JSON)은 그대로 두고 별도 경로 /ask/multimodal/stream 추가
  - multipart 파싱 → 이미지를 chat_attachments에 저장 → vision_agent 호출
  - vision_agent 응답을 SSE 프레임으로 감싸 전송
  - tb_vision_session INSERT → vision_session_id 반환

Zero-Hallucination 격리:
  - vision_agent 응답은 `vision_advice` 필드로만 전달
  - 기존 DB 사실 응답과 절대 섞이지 않음 (answer_text: null)
  - 프론트는 VisionAdviceCard로 시각적 분리 렌더
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

logger = logging.getLogger("slm")

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────

VISION_AGENT_URL = os.environ.get("VISION_AGENT_URL", "http://localhost:8100")
VISION_AGENT_TIMEOUT_S = int(os.environ.get("VISION_AGENT_TIMEOUT_S", "180"))

CHAT_ATTACHMENT_DIR = os.environ.get(
    "CHAT_ATTACHMENT_DIR",
    os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "web", "files", "chat_attachments"
    )),
)

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn
    os.makedirs(CHAT_ATTACHMENT_DIR, exist_ok=True)
    logger.info(f"vision_proxy init: agent={VISION_AGENT_URL} dir={CHAT_ATTACHMENT_DIR}")


async def _save_upload(upload: UploadFile) -> str:
    content = await upload.read()
    if not content:
        raise HTTPException(400, "빈 파일")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "이미지 크기는 10MB 이하여야 합니다")
    ext = os.path.splitext(upload.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, f"지원하지 않는 이미지 형식: {ext}")
    stored_name = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(CHAT_ATTACHMENT_DIR, stored_name)
    with open(path, "wb") as f:
        f.write(content)
    logger.info(f"chat attachment saved: {path} ({len(content)} bytes)")
    return path


async def _call_vision_agent(endpoint: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=VISION_AGENT_TIMEOUT_S) as client:
        resp = await client.post(f"{VISION_AGENT_URL}{endpoint}", json=payload)
        resp.raise_for_status()
        return resp.json()


def _insert_vision_session(
    user_id: Optional[str],
    region: Optional[str],
    image_url: str,
    image_kind: str,
    agent_response: dict,
    sitename: Optional[str],
    facilitytype: Optional[str],
) -> Optional[int]:
    if _get_db_connection is None:
        logger.warning("vision_proxy: DB connection not injected")
        return None
    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tb_vision_session
                    (user_id, region, image_url, image_kind, agent_response,
                     sitename, facilitytype)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                RETURNING vision_session_id
                """,
                (user_id, region, image_url, image_kind,
                 json.dumps(agent_response, ensure_ascii=False),
                 sitename, facilitytype),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row[0]) if row else None
    except Exception as e:
        logger.warning(f"tb_vision_session insert 실패: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


# [E-025 P9] 질의 텍스트에서 sitename/facilitytype 추론 (사용자가 직접 명시 안 한 경우)
_sitename_cache: list[str] = []
_facilitytype_cache: list[str] = []


def _load_site_cache() -> None:
    global _sitename_cache, _facilitytype_cache
    if _sitename_cache:
        return
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT sitename FROM tb_equipment_info WHERE sitename IS NOT NULL")
        _sitename_cache = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT facilitytype FROM tb_equipment_info WHERE facilitytype IS NOT NULL")
        _facilitytype_cache = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        logger.info(
            f"[vision_proxy] site cache: {len(_sitename_cache)} sites, "
            f"{len(_facilitytype_cache)} facility types"
        )
    except Exception as e:
        logger.warning(f"[vision_proxy] site cache 로드 실패: {e}")


def _infer_site_from_text(user_text: str) -> tuple[Optional[str], Optional[str]]:
    """user_text에서 알려진 sitename/facilitytype substring 매칭.

    가장 긴 매칭을 우선 (행정1수청 > 행정).
    """
    if not user_text:
        return None, None
    _load_site_cache()
    matched_site: Optional[str] = None
    matched_ft: Optional[str] = None
    for s in sorted(_sitename_cache, key=lambda x: -len(x)):
        if s in user_text:
            matched_site = s
            break
    for ft in sorted(_facilitytype_cache, key=lambda x: -len(x)):
        if ft in user_text:
            matched_ft = ft
            break
    return matched_site, matched_ft


@router.post("/ask/multimodal/stream")
async def ask_multimodal_stream(
    user_question: str = Form(""),
    session_id: Optional[str] = Form(None),
    sitename: Optional[str] = Form(None),
    facilitytype: Optional[str] = Form(None),
    region: Optional[str] = Form(None),
    user_id: Optional[str] = Form(None),
    images: list[UploadFile] = File(...),
):
    """사진 + 텍스트 질의를 받아 vision_agent 호출 후 SSE 스트리밍. [E-025]"""
    if not images:
        raise HTTPException(400, "최소 1장의 이미지가 필요합니다")
    if len(images) > 3:
        raise HTTPException(400, "이미지는 최대 3장까지 업로드 가능합니다")

    saved_paths: list[str] = []
    for upload in images:
        saved_paths.append(await _save_upload(upload))

    async def event_stream():
        t_start = time.perf_counter()

        yield _sse("progress", {
            "stage": "classify",
            "intent": "VISION_DIAGNOSE",
            "message": "이미지 첨부 감지 — 비전 진단 모드",
        })

        yield _sse("progress", {
            "stage": "extract",
            "image_count": len(saved_paths),
            "message": f"{len(saved_paths)}장의 이미지 업로드 완료",
        })

        yield _sse("progress", {
            "stage": "fetch",
            "message": "비전 에이전트 분석 중 (15~45초 소요)",
        })

        primary_image_path = saved_paths[0]

        # [E-025 P9] sitename/facilitytype 미지정 시 질의 텍스트에서 추론
        effective_site = sitename
        effective_ft = facilitytype
        if not effective_site or not effective_ft:
            inferred_site, inferred_ft = _infer_site_from_text(user_question)
            if not effective_site:
                effective_site = inferred_site
            if not effective_ft:
                effective_ft = inferred_ft
            if effective_site or effective_ft:
                logger.info(
                    f"[vision_proxy] site inferred from text: "
                    f"site={effective_site} ft={effective_ft}"
                )

        try:
            agent_resp = await _call_vision_agent("/vision/diagnose", {
                "image_url": primary_image_path,
                "user_text": user_question,
                "sitename": effective_site,
                "facilitytype": effective_ft,
            })
        except httpx.TimeoutException:
            yield _sse("error", {
                "detail": "비전 에이전트 응답 타임아웃",
                "fallback_message": "사진은 접수되었으나 분석을 완료하지 못했습니다.",
            })
            return
        except httpx.HTTPError as e:
            logger.warning(f"vision_agent 호출 실패: {e}")
            yield _sse("error", {
                "detail": f"비전 에이전트 호출 실패: {str(e)[:120]}",
                "fallback_message": "사진은 접수되었으나 분석에 실패했습니다.",
            })
            return

        vision_session_id = _insert_vision_session(
            user_id=user_id,
            region=region,
            image_url=primary_image_path,
            image_kind="chat_attachment",
            agent_response=agent_resp,
            sitename=effective_site,
            facilitytype=effective_ft,
        )

        elapsed_ms = int((time.perf_counter() - t_start) * 1000)
        yield _sse("result", {
            "intent": "VISION_DIAGNOSE",
            "session_id": session_id,
            "vision_session_id": vision_session_id,
            "vision_advice": {
                **agent_resp,
                "vision_session_id": vision_session_id,
                "image_url": primary_image_path,
            },
            # Zero-Hallucination — DB 사실 필드는 명시적으로 null
            "answer_text": None,
            "graph_type": "none",
            "elapsed_ms": elapsed_ms,
            "stage": "render",
        })

        logger.info(
            f"/ask/multimodal/stream 완료: vision_session={vision_session_id} "
            f"elapsed={elapsed_ms}ms"
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
