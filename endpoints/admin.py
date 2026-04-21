"""
관리자 API 엔드포인트 모듈

- GET  /health                              — 서버 상태 확인
- GET  /anomaly/profiles                    — 현장 프로파일 조회
- GET  /models                              — Ollama 모델 목록
- POST /models/select                       — 모델 변경
- POST /admin/facility-files/upload         — 시설 파일 업로드
- GET  /admin/facility-files                — 시설 파일 목록
- GET  /admin/facilities-summary            — 시설 요약
- DELETE /admin/facility-files/{id}         — 시설 파일 삭제
- GET  /admin/site-settings                 — 사이트 설정 조회
- PUT  /admin/site-settings                 — 사이트 설정 업데이트

ai_server.py에서 분리된 모듈 — init()으로 의존성을 주입받아 사용.
"""

import logging
import os
import json
import pathlib
import shutil
import uuid

import psycopg2
from fastapi import APIRouter, Request, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger("slm")

router = APIRouter()

# ---------------------------------------------------------------------------
# ai_server.py에서 주입받는 의존성
# ---------------------------------------------------------------------------
_get_db_connection = None
_ollama_client = None
_get_model = None
_set_model = None
_session_manager = None
_site_profiler = None
_ai_settings = None
_demo_mode = False
_demo_restore_text = None

# DB 직접 연결용 (커넥션 풀 바이패스)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5433")
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


def init(get_db_connection_fn, ollama_client, get_model_fn, set_model_fn,
         session_manager, site_profiler, ai_settings, demo_mode, demo_restore_text_fn):
    """ai_server.py에서 의존성을 주입받는다."""
    global _get_db_connection, _ollama_client, _get_model, _set_model
    global _session_manager, _site_profiler, _ai_settings
    global _demo_mode, _demo_restore_text
    _get_db_connection = get_db_connection_fn
    _ollama_client = ollama_client
    _get_model = get_model_fn
    _set_model = set_model_fn
    _session_manager = session_manager
    _site_profiler = site_profiler
    _ai_settings = ai_settings
    _demo_mode = demo_mode
    _demo_restore_text = demo_restore_text_fn


# ---------------------------------------------------------------------------
# 시설 파일 관련 상수
# ---------------------------------------------------------------------------
FACILITY_FILE_BASE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "web", "files", "facility"
)
FACILITY_FILE_ALLOWED_TYPES = {"site_photo", "system_diagram", "manual"}
FACILITY_FILE_MAX_SIZE = 10 * 1024 * 1024  # 10MB
FACILITY_FILE_ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/webp", "image/svg+xml", "application/pdf",
}

# 시설 테이블 -> URL 컬럼 매핑 (원격 DB 호환)
_FACILITY_TABLE_MAP = {
    "배수지": "tb_service_reservoir_info",
    "가압장": "tb_service_booster_station_info",
    "감압시설": "tb_pressure_reducing_facility_info",
    "블록": None,  # 블록은 별도 처리 (block_level)
}
_FILE_TYPE_TO_COLUMN = {
    "site_photo": "site_photo_url",
    "system_diagram": "system_diagram_url",
    "manual": "manual_url",
}


# ---------------------------------------------------------------------------
# Pydantic 모델
# ---------------------------------------------------------------------------
class ModelSelectRequest(BaseModel):
    model_name: str


# =============================================================================
# 서버 상태 확인
# =============================================================================

@router.get("/health")
async def health_check():
    """서버 상태 확인용 엔드포인트"""
    ollama_ok = _ollama_client.health_check()
    return {
        "status": "ok",
        "ollama_available": ollama_ok,
        "current_model": _get_model(),
        "active_sessions": _session_manager.active_session_count(),
    }


# =============================================================================
# 현장 프로파일 조회 엔드포인트
# =============================================================================

@router.get("/anomaly/profiles")
async def get_anomaly_profiles():
    """현재 현장 프로파일링 결과를 반환한다 (디버깅/모니터링용)."""
    profiles = _site_profiler.profiles if _site_profiler and _site_profiler.profiles else {}
    result = []
    for (sitename, ft), p in sorted(profiles.items()):
        result.append({
            "sitename": sitename,
            "facilitytype": ft,
            "site_group": p.get("site_group", "B"),
            "avg_outflow_7d": p.get("avg_outflow_7d"),
            "alarm_freq_30d": p.get("alarm_freq_30d", 0),
            "p95_level": p.get("p95_level"),
            "p05_level": p.get("p05_level"),
            "info_count_7d": p.get("info_count_7d", 0),
        })
    group_dist = {"A": 0, "B": 0, "C": 0, "D": 0}
    for p in profiles.values():
        g = p.get("site_group", "B")
        group_dist[g] = group_dist.get(g, 0) + 1
    return {
        "total": len(profiles),
        "group_distribution": group_dist,
        "profiles": result,
    }


# =============================================================================
# 모델 관리 엔드포인트
# =============================================================================

@router.get("/models")
async def list_models():
    """Ollama에 설치된 모델 목록을 반환한다."""
    models = _ollama_client.list_models()
    current = _get_model()
    return {
        "current_model": current,
        "available_models": models,
    }


@router.post("/models/select")
async def select_model(request: ModelSelectRequest):
    """Ollama 모델을 런타임에 변경한다."""
    model_name = request.model_name

    # 설치된 모델 목록에서 확인
    models = _ollama_client.list_models()
    installed_names = [m["name"] for m in models]

    if models and model_name not in installed_names:
        return {
            "status": "ERROR",
            "message": f"'{model_name}'은(는) 설치되지 않은 모델입니다.",
            "available_models": installed_names,
        }

    _set_model(model_name)
    logger.info(f"모델 변경: {model_name}")
    return {
        "status": "OK",
        "current_model": model_name,
    }


# =============================================================================
# 관리자 API: 시설 파일 관리 (위치도, 계통도, 초동대응 매뉴얼)
# =============================================================================

@router.post("/admin/facility-files/upload")
async def upload_facility_file(
    file: UploadFile = File(...),
    region: str = Form("R01"),
    sitename: str = Form(...),
    file_type: str = Form(...),
):
    """시설 파일 업로드 (위치도/계통도/매뉴얼)"""
    # DEMO_MODE: form-data의 sitename 역변환 (코드->원본)
    if _demo_mode and _demo_restore_text:
        sitename = _demo_restore_text(sitename)
        region = _demo_restore_text(region)

    # 유효성 검증
    if file_type not in FACILITY_FILE_ALLOWED_TYPES:
        return {"status": "ERROR", "message": f"허용되지 않는 파일 유형: {file_type}"}

    if file.content_type and file.content_type not in FACILITY_FILE_ALLOWED_MIME:
        return {"status": "ERROR", "message": f"허용되지 않는 MIME 타입: {file.content_type}"}

    # 파일 크기 확인 (읽어서 체크)
    contents = await file.read()
    if len(contents) > FACILITY_FILE_MAX_SIZE:
        return {"status": "ERROR", "message": "파일 크기가 10MB를 초과합니다."}

    # UUID 기반 저장 파일명
    ext = pathlib.Path(file.filename or "file").suffix.lower() or ".bin"
    stored_name = f"{uuid.uuid4().hex}{ext}"
    sub_dir = os.path.join(FACILITY_FILE_BASE_DIR, file_type)
    os.makedirs(sub_dir, exist_ok=True)
    file_path = os.path.join(sub_dir, stored_name)

    # 파일 저장
    try:
        with open(file_path, "wb") as f:
            f.write(contents)
    except OSError as e:
        logger.error(f"파일 저장 실패: {e}")
        return {"status": "ERROR", "message": "파일 저장에 실패했습니다."}

    file_url = f"/api/files/facility/{file_type}/{stored_name}"
    file_size = len(contents)

    # DB 저장
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        conn.autocommit = False
        cur = conn.cursor()

        # 기존 파일 확인
        cur.execute(
            "SELECT ff.file_id, fs.stored_name, fs.file_url "
            "FROM tb_facility_file ff JOIN tb_file_storage fs ON ff.file_id = fs.file_id "
            "WHERE ff.region = %s AND ff.sitename = %s AND ff.file_type = %s",
            (region, sitename, file_type),
        )
        old_row = cur.fetchone()

        # tb_file_storage INSERT
        cur.execute(
            "INSERT INTO tb_file_storage "
            "(region, file_category, original_name, stored_name, file_path, file_url, mime_type, file_size, uploaded_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING file_id",
            (region, "facility", file.filename, stored_name,
             f"facility/{file_type}/{stored_name}", file_url,
             file.content_type, file_size, "admin"),
        )
        new_file_id = cur.fetchone()[0]

        # tb_facility_file UPSERT
        cur.execute(
            "INSERT INTO tb_facility_file (region, sitename, file_type, file_id) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (region, sitename, file_type) "
            "DO UPDATE SET file_id = EXCLUDED.file_id, updated_at = now()",
            (region, sitename, file_type, new_file_id),
        )

        # 시설 테이블 URL 컬럼 업데이트 (원격 DB 호환)
        col_name = _FILE_TYPE_TO_COLUMN.get(file_type)
        if col_name:
            _update_facility_url(cur, region, sitename, col_name, file_url)

        conn.commit()

        # 이전 파일 삭제 (디스크 + DB)
        if old_row:
            old_file_id, old_stored_name, _old_url = old_row
            try:
                cur.execute("DELETE FROM tb_file_storage WHERE file_id = %s", (old_file_id,))
                conn.commit()
            except Exception as e:
                logger.warning(f"이전 시설 파일 DB 삭제 실패 (file_id={old_file_id}): {e}")
            old_path = os.path.join(FACILITY_FILE_BASE_DIR, file_type, old_stored_name)
            if os.path.exists(old_path):
                os.remove(old_path)

        cur.close()
        logger.info(f"시설 파일 업로드 완료: {sitename}/{file_type}/{stored_name}")
        return {
            "status": "OK",
            "facility_file_id": new_file_id,
            "file_url": file_url,
            "original_name": file.filename,
        }

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"시설 파일 DB 저장 실패: {e}")
        # 롤백 시 파일도 삭제
        if os.path.exists(file_path):
            os.remove(file_path)
        return {"status": "ERROR", "message": "DB 저장에 실패했습니다."}
    finally:
        if conn:
            conn.close()


def _update_facility_url(cur, region: str, sitename: str, col_name: str, file_url: str):
    """시설 테이블의 URL 컬럼을 업데이트한다 (원격 DB 호환)."""
    tables = [
        "tb_service_reservoir_info",
        "tb_service_booster_station_info",
        "tb_pressure_reducing_facility_info",
        "tb_block_info",
    ]
    for table in tables:
        try:
            # 컬럼 존재 확인 후 업데이트
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, col_name),
            )
            if cur.fetchone():
                cur.execute(
                    f"UPDATE {table} SET {col_name} = %s WHERE sitename = %s",  # noqa: S608
                    (file_url, sitename),
                )
                if cur.rowcount > 0:
                    return
        except psycopg2.Error:
            continue


@router.get("/admin/facility-files")
async def list_facility_files(
    region: str = Query("R01"),
    sitename: str = Query(None),
):
    """시설별 파일 목록 조회"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        if sitename:
            cur.execute(
                "SELECT ff.facility_file_id, ff.region, ff.sitename, ff.file_type, "
                "fs.file_url, fs.original_name, fs.mime_type, fs.file_size, ff.created_at "
                "FROM tb_facility_file ff "
                "JOIN tb_file_storage fs ON ff.file_id = fs.file_id "
                "WHERE ff.region = %s AND ff.sitename = %s "
                "ORDER BY ff.file_type",
                (region, sitename),
            )
        else:
            cur.execute(
                "SELECT ff.facility_file_id, ff.region, ff.sitename, ff.file_type, "
                "fs.file_url, fs.original_name, fs.mime_type, fs.file_size, ff.created_at "
                "FROM tb_facility_file ff "
                "JOIN tb_file_storage fs ON ff.file_id = fs.file_id "
                "WHERE ff.region = %s "
                "ORDER BY ff.sitename, ff.file_type",
                (region,),
            )
        cols = ["facility_file_id", "region", "sitename", "file_type",
                "file_url", "original_name", "mime_type", "file_size", "created_at"]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        # datetime -> string
        for row in rows:
            if row["created_at"]:
                row["created_at"] = row["created_at"].isoformat()
        cur.close()
        return {"status": "OK", "data": rows}
    except psycopg2.Error as e:
        logger.error(f"시설 파일 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": []}
    finally:
        if conn:
            conn.close()


@router.get("/admin/facilities-summary")
async def get_facilities_summary(region: str = Query("R01")):
    """전체 시설 목록 + 파일 등록 현황"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        cur.execute("""
            WITH facilities AS (
                SELECT sitename, '배수지' AS facilitytype FROM tb_service_reservoir_info
                UNION ALL
                SELECT sitename, '가압장' FROM tb_service_booster_station_info
                UNION ALL
                SELECT sitename, '감압시설' FROM tb_pressure_reducing_facility_info
                UNION ALL
                SELECT sitename, '블록' FROM tb_block_info
            )
            SELECT
                f.sitename,
                f.facilitytype,
                bool_or(ff.file_type = 'site_photo')      AS has_site_photo,
                bool_or(ff.file_type = 'system_diagram')   AS has_system_diagram,
                bool_or(ff.file_type = 'manual')           AS has_manual
            FROM facilities f
            LEFT JOIN tb_facility_file ff
                ON f.sitename = ff.sitename
            GROUP BY f.sitename, f.facilitytype
            ORDER BY f.facilitytype, f.sitename
        """)
        cols = ["sitename", "facilitytype", "has_site_photo", "has_system_diagram", "has_manual"]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        # None -> False, region 추가
        for row in rows:
            row["region"] = region
            for k in ["has_site_photo", "has_system_diagram", "has_manual"]:
                row[k] = bool(row[k])
        cur.close()
        return {"status": "OK", "data": rows}
    except psycopg2.Error as e:
        logger.error(f"시설 요약 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": []}
    finally:
        if conn:
            conn.close()


@router.delete("/admin/facility-files/{facility_file_id}")
async def delete_facility_file(facility_file_id: int):
    """시설 파일 링크 삭제"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        conn.autocommit = False
        cur = conn.cursor()

        # 기존 정보 조회
        cur.execute(
            "SELECT ff.region, ff.sitename, ff.file_type, fs.stored_name, fs.file_id "
            "FROM tb_facility_file ff "
            "JOIN tb_file_storage fs ON ff.file_id = fs.file_id "
            "WHERE ff.facility_file_id = %s",
            (facility_file_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"status": "ERROR", "message": "파일을 찾을 수 없습니다."}

        _region, _sitename, _file_type, stored_name, file_id = row

        # DB 삭제
        cur.execute("DELETE FROM tb_facility_file WHERE facility_file_id = %s", (facility_file_id,))
        cur.execute("DELETE FROM tb_file_storage WHERE file_id = %s", (file_id,))

        # 시설 테이블 URL 컬럼 초기화
        col_name = _FILE_TYPE_TO_COLUMN.get(_file_type)
        if col_name:
            _update_facility_url(cur, _region, _sitename, col_name, None)

        conn.commit()

        # 물리 파일 삭제
        file_path = os.path.join(FACILITY_FILE_BASE_DIR, _file_type, stored_name)
        if os.path.exists(file_path):
            os.remove(file_path)

        cur.close()
        logger.info(f"시설 파일 삭제: {_sitename}/{_file_type}/{stored_name}")
        return {"status": "OK", "message": "삭제되었습니다."}

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"시설 파일 삭제 실패: {e}")
        return {"status": "ERROR", "message": "삭제에 실패했습니다."}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 사이트 설정 API (관리자용)
# =============================================================================

@router.get("/admin/site-settings")
async def get_site_settings():
    """사이트 설정 조회 (랜딩/DB접속/AI파라미터)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT comm_cd, use_yn FROM tb_comm_code "
            "WHERE region = 'R01' AND grp_cd = 'SITE_SETTING'"
        )
        rows = cur.fetchall()
        cur.close()

        settings = {}
        for comm_cd, use_yn in rows:
            if comm_cd == "LANDING_ENABLED":
                settings["landing_enabled"] = use_yn == "Y"
            elif comm_cd == "TREND_EXPLAIN_ENABLED":
                settings["trend_explain_enabled"] = use_yn == "Y"

        if "landing_enabled" not in settings:
            settings["landing_enabled"] = True
        if "trend_explain_enabled" not in settings:
            settings["trend_explain_enabled"] = False

        # DB 접속정보 (읽기 전용, 비밀번호 마스킹)
        settings["db"] = {
            "host": DB_HOST,
            "port": int(DB_PORT),
            "name": DB_NAME,
            "user": DB_USER,
            "password_masked": "****" if DB_PASSWORD else "(미설정)",
            "status": "connected",
        }
        try:
            test_conn = _get_db_connection()
            test_conn.close()
        except Exception:
            settings["db"]["status"] = "disconnected"

        # AI 모델 파라미터
        from slm_config import (
            OLLAMA_BASE_URL, OLLAMA_TIMEOUT,
            CLASSIFIER_TEMPERATURE, get_model,
        )
        settings["ai"] = {
            "base_url": OLLAMA_BASE_URL,
            "model": get_model(),
            "num_ctx": getattr(_ai_settings, "num_ctx", 4096),
            "temperature": getattr(_ai_settings, "temperature", CLASSIFIER_TEMPERATURE),
            "timeout": getattr(_ai_settings, "timeout", OLLAMA_TIMEOUT),
            "ollama_available": _ollama_client.health_check() if _ollama_client else False,
        }

        return settings

    except Exception as e:
        logger.error(f"사이트 설정 조회 실패: {e}")
        return {"landing_enabled": True}
    finally:
        if conn:
            conn.close()


@router.put("/admin/site-settings")
async def update_site_settings(request: Request):
    """사이트 설정 업데이트 (랜딩/AI파라미터)"""
    conn = None
    try:
        body = await request.json()
        conn = _get_db_connection()
        cur = conn.cursor()

        # SITE_SETTING 그룹 코드 보장 (FK 충족)
        cur.execute(
            """
            INSERT INTO tb_grp_code (region, grp_cd, grp_nm, use_yn)
            VALUES ('R01', 'SITE_SETTING', '사이트 설정', 'Y')
            ON CONFLICT (region, grp_cd) DO NOTHING
            """
        )

        # 랜딩 페이지 설정
        if "landing_enabled" in body:
            use_yn = "Y" if body["landing_enabled"] else "N"
            cur.execute(
                """
                INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, use_yn)
                VALUES ('R01', 'SITE_SETTING', 'LANDING_ENABLED', '랜딩 페이지 활성화', %s)
                ON CONFLICT (region, grp_cd, comm_cd)
                DO UPDATE SET use_yn = %s
                """,
                (use_yn, use_yn),
            )
            conn.commit()

        # 트렌드 AI 요약 설정
        if "trend_explain_enabled" in body:
            use_yn = "Y" if body["trend_explain_enabled"] else "N"
            cur.execute(
                """
                INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, use_yn)
                VALUES ('R01', 'SITE_SETTING', 'TREND_EXPLAIN_ENABLED', '트렌드 AI 요약', %s)
                ON CONFLICT (region, grp_cd, comm_cd)
                DO UPDATE SET use_yn = %s
                """,
                (use_yn, use_yn),
            )
            conn.commit()

        # AI 파라미터 변경 (서버 재시작 없이 즉시 반영 + DB 영속 저장)
        # tb_comm_code(SITE_SETTING/AI_*)에 comm_val 로 UPSERT (migration 0055)
        def _upsert_ai_param(comm_cd: str, comm_nm: str, comm_val: str) -> None:
            cur.execute(
                """
                INSERT INTO tb_comm_code
                    (region, grp_cd, comm_cd, comm_nm, comm_val, use_yn)
                VALUES ('R01', 'SITE_SETTING', %s, %s, %s, 'Y')
                ON CONFLICT (region, grp_cd, comm_cd)
                DO UPDATE SET comm_val = EXCLUDED.comm_val
                """,
                (comm_cd, comm_nm, comm_val),
            )

        ai = body.get("ai")
        if ai:
            if "num_ctx" in ai:
                val = max(1024, min(32768, int(ai["num_ctx"])))
                _ai_settings.num_ctx = val
                _upsert_ai_param("AI_NUM_CTX", "AI 컨텍스트 크기", str(val))
                logger.info(f"AI num_ctx 변경: {val}")
            if "temperature" in ai:
                val = max(0.0, min(1.0, float(ai["temperature"])))
                _ai_settings.temperature = val
                _upsert_ai_param("AI_TEMPERATURE", "AI Temperature", str(val))
                logger.info(f"AI temperature 변경: {val}")
            if "timeout" in ai:
                val = max(10, min(120, int(ai["timeout"])))
                _ai_settings.timeout = val
                _upsert_ai_param("AI_TIMEOUT", "AI 타임아웃(초)", str(val))
                logger.info(f"AI timeout 변경: {val}")
            if "model" in ai:
                from slm_config import set_model
                set_model(ai["model"])
                logger.info(f"AI model 변경: {ai['model']}")
            conn.commit()

        cur.close()
        return {"status": "OK"}

    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"사이트 설정 업데이트 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# [E-025 폐쇄망] 매뉴얼 PDF 다운로드 라우트
# =============================================================================

_MANUALS_DIR = os.environ.get(
    "MANUALS_DEST_DIR",
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "manuals")),
)


@router.get("/files/manual/{filename:path}")
async def download_equipment_manual(filename: str):
    """매뉴얼 PDF 직접 서빙 — 로컬 파일만 (외부 링크 없음, 폐쇄망 대응).

    file_url(`/api/proxy/files/manual/<name>.pdf`)이 Next.js BFF 프록시를 거쳐
    이 엔드포인트로 라우팅된다. MANUALS_DEST_DIR(기본 `/app/data/manuals`) 밖의
    경로는 path traversal로 거부.
    """
    # path traversal 방어
    safe = os.path.normpath(os.path.join(_MANUALS_DIR, filename))
    if not safe.startswith(os.path.abspath(_MANUALS_DIR) + os.sep) and safe != os.path.abspath(_MANUALS_DIR):
        logger.warning(f"매뉴얼 path traversal 시도: {filename}")
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="접근 거부")

    if not os.path.exists(safe):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")

    # 파일명에 한글이 있을 수 있어 Content-Disposition은 RFC 5987 (filename*=utf-8)로
    from urllib.parse import quote
    display_name = os.path.basename(safe)
    encoded = quote(display_name)
    return FileResponse(
        safe,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=\"{display_name}\"; filename*=UTF-8''{encoded}",
            "Cache-Control": "public, max-age=3600",
        },
    )


# =============================================================================
# [E-025 review #5] 관리자 API: 장비 매뉴얼 관리 (RAG 인덱스)
# =============================================================================

@router.get("/admin/equipment-manuals")
async def list_equipment_manuals():
    """인덱싱된 장비 매뉴얼 목록 + 각 매뉴얼의 청크 수 조회."""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        cur.execute(
            """
            SELECT manual_id, equipment_type, brand, model, title, file_url,
                   page_count, embedding_key,
                   COALESCE(manual_type, 'user_manual') AS manual_type,
                   uploaded_by, uploaded_at
            FROM tb_equipment_manual
            ORDER BY manual_id DESC
            """
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": rows, "total": len(rows)}
    except psycopg2.Error as e:
        logger.error(f"매뉴얼 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


EQUIPMENT_MANUAL_MAX_SIZE = 100 * 1024 * 1024  # 100MB (큰 PDF 지원)


@router.post("/admin/equipment-manuals/upload")
async def upload_equipment_manual(
    file: UploadFile = File(...),
    equipment_type: str = Form(""),
    brand: str = Form(""),
    model: str = Form(""),
    manual_type: str = Form("user_manual"),
):
    """장비 매뉴얼 PDF 업로드 + 자동 인덱싱 (text extract → embed → NPZ + DB).

    기존 `index_manuals.py` 로직을 재사용(`index_single_pdf`)하여 업로드 즉시
    RAG 인덱스에 반영한다. 동일 title이 이미 있으면 UPSERT (NPZ도 교체).
    인덱싱 후 vision_agent가 새 매뉴얼을 검색 결과에 포함시키려면 **재시작 필요**
    (`_ManualRagIndex._loaded=False`로 강제 재로드하는 API는 미구현 — 수동 restart).
    """
    # 파일 검증
    contents = await file.read()
    if not contents:
        return {"status": "ERROR", "message": "빈 파일"}
    if len(contents) > EQUIPMENT_MANUAL_MAX_SIZE:
        return {"status": "ERROR", "message": f"파일 크기 {EQUIPMENT_MANUAL_MAX_SIZE // (1024*1024)}MB 초과"}
    filename = file.filename or f"{uuid.uuid4().hex}.pdf"
    if not filename.lower().endswith(".pdf"):
        return {"status": "ERROR", "message": "PDF만 지원"}

    # 임시 파일로 저장 (index_single_pdf가 경로 기반으로 동작)
    tmp_dir = "/tmp/admin_manual_upload"
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, filename)
    try:
        with open(tmp_path, "wb") as f:
            f.write(contents)
    except OSError as e:
        logger.error(f"매뉴얼 임시 저장 실패: {e}")
        return {"status": "ERROR", "message": "임시 저장 실패"}

    # index_single_pdf 호출
    try:
        import sys as _sys
        _sys.path.insert(0, "/app")
        from tools.index_manuals import index_single_pdf  # noqa: E402
    except Exception as e:
        logger.error(f"index_manuals 모듈 import 실패: {e}")
        return {"status": "ERROR", "message": f"인덱서 import 실패: {e}"}

    meta_override = {}
    if equipment_type:
        meta_override["equipment_type"] = equipment_type
    if brand:
        meta_override["brand"] = brand
    if model:
        meta_override["model"] = model

    # DB 연결 (index_single_pdf가 commit까지 처리)
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        result = index_single_pdf(
            src_path=tmp_path,
            filename=filename,
            conn=conn,
            meta_override=meta_override or None,
            uploaded_by="admin_upload",
        )

        # manual_type 업데이트 (index_single_pdf는 기본값 사용)
        if result.get("status") == "ok" and manual_type in {"catalog", "user_manual", "datasheet"}:
            cur = conn.cursor()
            cur.execute(
                "UPDATE tb_equipment_manual SET manual_type=%s WHERE manual_id=%s",
                (manual_type, result["manual_id"]),
            )
            conn.commit()
            cur.close()

        logger.info(f"매뉴얼 업로드+인덱싱 완료: {filename} → {result}")
        return {"status": "OK", "result": result, "hint": "vision_agent 재시작 필요"}
    except ValueError as e:
        logger.warning(f"매뉴얼 인덱싱 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    except Exception as e:
        logger.error(f"매뉴얼 업로드 처리 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "ERROR", "message": str(e)}
    finally:
        # 임시 파일 정리
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        if conn:
            conn.close()


@router.delete("/admin/equipment-manuals/{manual_id}")
async def delete_equipment_manual(manual_id: int):
    """매뉴얼 삭제 — tb_equipment_manual row + NPZ 파일."""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        cur.execute("SELECT embedding_key FROM tb_equipment_manual WHERE manual_id=%s", (manual_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return {"status": "ERROR", "message": "manual not found"}
        embedding_key = row[0]

        cur.execute("DELETE FROM tb_equipment_manual WHERE manual_id=%s", (manual_id,))
        conn.commit()
        cur.close()

        # NPZ 파일 삭제
        if embedding_key:
            try:
                import sys as _sys
                _sys.path.insert(0, "/app")
                from tools.index_manuals import EMBEDDINGS_DIR  # noqa: E402
                npz_path = os.path.join(EMBEDDINGS_DIR, f"{embedding_key}.npz")
                if os.path.exists(npz_path):
                    os.remove(npz_path)
            except Exception as e:
                logger.warning(f"NPZ 삭제 실패: {e}")

        return {"status": "OK", "hint": "vision_agent 재시작 필요"}
    except psycopg2.Error as e:
        logger.error(f"매뉴얼 삭제 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
