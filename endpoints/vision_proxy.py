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
import re
import time
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

# [Scenario 2] 사진 + FAULT 키워드 감지 시 /chat/fault/draft 로 분기
from endpoints.chat_fault_record import FAULT_KEYWORDS, build_fault_draft

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


async def _save_upload(upload: UploadFile) -> tuple[str, str]:
    """업로드를 저장하고 (로컬 절대경로, API URL) 쌍을 반환.

    vision_agent는 호스트 프로세스라 backend 컨테이너의 로컬경로를 직접 열 수 없다.
    따라서 vision_agent에 전달할 땐 **URL 형식(/api/files/chat_attachments/<name>)**
    을 사용하고, 양쪽이 각자의 CHAT_ATTACHMENT_DIR env로 해석한다.
    """
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
    image_url = f"/api/files/chat_attachments/{stored_name}"
    logger.info(f"chat attachment saved: {path} url={image_url} ({len(content)} bytes)")
    return path, image_url


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


# ─────────────────────────────────────────────────────────────────────
# [Scenario 2] 사진 + FAULT 키워드 → 장애 등록 초안 분기
# ─────────────────────────────────────────────────────────────────────

# 프런트의 isFaultRecordIntent (use-chat-submit.ts) 와 동일 규칙으로 맞추되
# FAULT_KEYWORDS 기준. "조회/상태 확인" 질의는 제외.
_FAULT_INTENT_QUERY_EXCLUSIONS = re.compile(r"(상태|어때|얼마|몇|보여|알려|조회)")
_FAULT_INTENT_RECORD_HINTS = re.compile(r"(기록|저장|남겨|등록|발생|났|발견)")


def _detect_fault_intent(text: str) -> bool:
    """사진과 함께 온 텍스트가 장애 기록 의도인지 판정.

    True 조건:
      - FAULT_KEYWORDS 중 하나 포함
      - "조회성 질의" 예외(상태/어때/얼마/몇/보여/알려/조회) 에 해당하더라도
        기록 힌트(기록/저장/남겨/등록/발생/났/발견)가 함께 있으면 True
    """
    if not text:
        return False
    if not any(kw in text for (kw, _cat) in FAULT_KEYWORDS):
        return False
    if _FAULT_INTENT_QUERY_EXCLUSIONS.search(text) and not _FAULT_INTENT_RECORD_HINTS.search(text):
        return False
    return True


async def _fault_branch(
    user_text: str,
    user_id: Optional[str],
    saved_urls: list[str],
    session_id: Optional[str],
    t_start: float,
):
    """사진 + FAULT 텍스트 → build_fault_draft 호출 후 fault_draft 결과 emit.

    DB 커넥션은 build_fault_draft 내부에서 INSERT 하므로 호출자 commit 책임.
    user_id 없으면 에러 — 프런트가 로그인 후에만 호출하므로 정상 케이스 없음.
    """
    yield _sse("progress", {
        "stage": "classify",
        "intent": "FAULT_RECORD_DRAFT",
        "message": "장애 등록 의도 감지 — 사진과 함께 등록합니다",
    })

    if not user_id:
        yield _sse("error", {
            "detail": "user_id 누락 — 로그인 세션 확인 필요",
            "fallback_message": "장애 기록을 위해서는 로그인이 필요합니다.",
        })
        return

    if _get_db_connection is None:
        yield _sse("error", {
            "detail": "DB 커넥션 미초기화",
            "fallback_message": "서버 구성 오류 — 관리자에게 문의해 주세요.",
        })
        return

    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        result = build_fault_draft(
            cur,
            user_id=user_id,
            text=user_text,
            occurred_at_str=None,
            photo_urls=saved_urls,
        )
        conn.commit()
        cur.close()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.exception("fault_branch build_fault_draft 실패")
        yield _sse("error", {
            "detail": f"fault draft 실패: {str(e)[:120]}",
            "fallback_message": "장애 등록 초안 생성 중 오류가 발생했습니다.",
        })
        return
    finally:
        if conn:
            conn.close()

    elapsed_ms = int((time.perf_counter() - t_start) * 1000)
    yield _sse("result", {
        "intent": "FAULT_RECORD_DRAFT",
        "session_id": session_id,
        "fault_draft": result,  # { session_id, draft, ready, missing, confirm_message, suggested_alarms }
        "answer_text": None,
        "graph_type": "none",
        "elapsed_ms": elapsed_ms,
        "stage": "render",
    })
    logger.info(
        f"/ask/multimodal/stream FAULT 분기 완료: session={result['session_id']} "
        f"photos={len(saved_urls)} elapsed={elapsed_ms}ms"
    )


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
    saved_urls: list[str] = []
    for upload in images:
        p, u = await _save_upload(upload)
        saved_paths.append(p)
        saved_urls.append(u)

    # [Scenario 2] 사진 + FAULT 키워드 감지 — 진단 대신 장애 등록 분기
    is_fault_intent = _detect_fault_intent(user_question)

    async def event_stream():
        t_start = time.perf_counter()

        # ── 시나리오 2: 사진 + "고장 등록" — FaultRecordConfirmCard 로 분기
        if is_fault_intent:
            async for ev in _fault_branch(user_question, user_id, saved_urls, session_id, t_start):
                yield ev
            return

        # ── 기존 VISION_DIAGNOSE 플로우 (시나리오 1-b / 사진+일반 질의)
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

        primary_image_url = saved_urls[0]
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
                "image_url": primary_image_url,
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
            image_url=primary_image_url,
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
                "image_url": primary_image_url,
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


# [E-025 P13] 시설물 사진 등록 — chat_attachment → tb_file_storage + tb_facility_file
FACILITY_FILE_BASE_DIR = os.environ.get(
    "FACILITY_FILE_BASE_DIR",
    os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "web", "files", "facility"
    )),
)

_FACILITY_FILE_TYPES = {"site_photo", "system_diagram", "manual"}


@router.post("/vision/register-facility-photo")
async def register_facility_photo(request: dict):
    """chat_attachment에 업로드된 이미지를 tb_facility_file에 등록.

    기존 admin 업로드 엔드포인트(multipart)와 달리, 이미 서버에 저장된 파일을
    경로로만 받아서 facility/<file_type>/<new_uuid> 로 복사 후 DB UPSERT 한다.

    Body:
        {"image_url": "/api/files/chat_attachments/<uuid>.jpg",
         "region": "R01", "sitename": "행정", "file_type": "site_photo",
         "vision_session_id": 123}
    Response:
        {"status": "OK", "facility_file_id": N, "file_url": "/api/files/facility/site_photo/<uuid>.jpg"}
    """
    import pathlib
    import shutil

    image_url = request.get("image_url") or ""
    region = request.get("region") or "R01"
    sitename = request.get("sitename") or ""
    file_type = request.get("file_type") or "site_photo"
    vision_session_id = request.get("vision_session_id")

    if not sitename:
        raise HTTPException(400, "sitename 필수")
    if file_type not in _FACILITY_FILE_TYPES:
        raise HTTPException(400, f"file_type 허용되지 않음: {file_type}")
    if not image_url:
        raise HTTPException(400, "image_url 필수")

    # 1. image_url → 로컬 경로 해결
    if image_url.startswith("/api/files/chat_attachments/"):
        src_path = os.path.join(
            CHAT_ATTACHMENT_DIR,
            image_url[len("/api/files/chat_attachments/"):],
        )
    elif image_url.startswith("/") and os.path.exists(image_url):
        src_path = image_url
    else:
        raise HTTPException(400, f"지원하지 않는 image_url 형식: {image_url[:60]}")

    if not os.path.exists(src_path):
        raise HTTPException(404, f"원본 파일을 찾을 수 없습니다: {src_path}")

    # 2. facility/<file_type>/<new_uuid>.<ext> 로 복사
    ext = pathlib.Path(src_path).suffix.lower() or ".jpg"
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest_dir = os.path.join(FACILITY_FILE_BASE_DIR, file_type)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, stored_name)
    try:
        shutil.copyfile(src_path, dest_path)
    except OSError as e:
        raise HTTPException(500, f"파일 복사 실패: {e}")

    file_url = f"/api/files/facility/{file_type}/{stored_name}"
    file_size = os.path.getsize(dest_path)

    # 3. DB INSERT — tb_file_storage + tb_facility_file UPSERT
    if _get_db_connection is None:
        raise HTTPException(500, "DB 커넥션 미초기화")

    conn = None
    try:
        conn = _get_db_connection()
        # _PooledConnection wrapper는 autocommit 속성이 없을 수 있음 — getattr로 안전 처리
        try:
            conn.autocommit = False
        except Exception:
            pass
        cur = conn.cursor()

        # 기존 파일 조회 (UPSERT 시 제거 대상)
        cur.execute(
            "SELECT ff.file_id, fs.stored_name "
            "FROM tb_facility_file ff JOIN tb_file_storage fs ON ff.file_id = fs.file_id "
            "WHERE ff.region=%s AND ff.sitename=%s AND ff.file_type=%s",
            (region, sitename, file_type),
        )
        old_row = cur.fetchone()

        cur.execute(
            "INSERT INTO tb_file_storage "
            "(region, file_category, original_name, stored_name, file_path, file_url, mime_type, file_size, uploaded_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING file_id",
            (region, "facility", os.path.basename(src_path), stored_name,
             f"facility/{file_type}/{stored_name}", file_url,
             "image/jpeg", file_size, "vision_agent"),
        )
        new_file_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO tb_facility_file (region, sitename, file_type, file_id) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (region, sitename, file_type) "
            "DO UPDATE SET file_id = EXCLUDED.file_id, updated_at = now() "
            "RETURNING facility_file_id",
            (region, sitename, file_type, new_file_id),
        )
        facility_file_id = cur.fetchone()[0]

        # vision_session 역연결 — savepoint로 격리 (linked_facility_file_id 컬럼
        # 없는 구버전 스키마에서도 facility_file 삽입이 롤백되지 않도록)
        if vision_session_id:
            cur.execute("SAVEPOINT vision_link")
            try:
                cur.execute(
                    "UPDATE tb_vision_session SET linked_facility_file_id=%s WHERE vision_session_id=%s",
                    (facility_file_id, vision_session_id),
                )
                cur.execute("RELEASE SAVEPOINT vision_link")
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT vision_link")
                logger.info(f"vision_session linked_facility_file_id 업데이트 스킵: {e}")

        conn.commit()

        # 이전 파일 정리
        if old_row:
            old_file_id, old_stored_name = old_row
            try:
                cur.execute("DELETE FROM tb_file_storage WHERE file_id=%s", (old_file_id,))
                conn.commit()
            except Exception as e:
                logger.warning(f"이전 시설 파일 DB 삭제 실패: {e}")
            old_path = os.path.join(FACILITY_FILE_BASE_DIR, file_type, old_stored_name)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass

        cur.close()
        logger.info(
            f"시설 사진 등록 완료 (vision): {sitename}/{file_type}/{stored_name}"
        )
        return {
            "status": "OK",
            "facility_file_id": facility_file_id,
            "file_url": file_url,
        }
    except Exception as e:
        if conn:
            conn.rollback()
        # 복사된 파일 롤백
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
        logger.error(f"시설 사진 등록 실패: {e}")
        raise HTTPException(500, f"시설 사진 등록 실패: {e}")
    finally:
        if conn:
            conn.close()


# [E-025 P12] 명판/계기판 OCR 파싱 프록시 — vision_agent /vision/register-parse 포워딩
@router.post("/vision/register-parse")
async def vision_register_parse(request: dict):
    """프런트가 image_url(+image_kind)을 주면 vision_agent로 포워딩해서 OCR+필드 파싱.

    Body:
        {"image_url": "/api/files/...", "image_kind": "nameplate|exterior|location"}
    Response: vision_agent RegisterParseResponse 그대로 (ocr_text, fields, confidence_per_field, needs_manual_input)
    """
    image_url = request.get("image_url") or ""
    image_kind = request.get("image_kind") or "nameplate"
    if not image_url:
        raise HTTPException(400, "image_url 필수")

    try:
        async with httpx.AsyncClient(timeout=VISION_AGENT_TIMEOUT_S) as client:
            resp = await client.post(
                f"{VISION_AGENT_URL}/vision/register-parse",
                json={"image_url": image_url, "image_kind": image_kind},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "vision_agent register-parse 타임아웃")
    except httpx.HTTPError as e:
        logger.warning(f"register-parse 포워딩 실패: {e}")
        raise HTTPException(502, f"vision_agent 호출 실패: {e}")
