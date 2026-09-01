"""
모듈 버전·라이선스 API — docs/module-version-spec.md (P1)

- GET /system/modules — 버전 + 라이선스(SKU 실시간 조인) + live health

health 는 저장하지 않는다 — 매 조회가 곧 검사다 (병렬, 개당 2s 타임아웃).
SKU 활성 여부도 저장하지 않는다 — tb_comm_code(SITE_SETTING) 가 정본.

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import asyncio
import glob
import logging
import os

import httpx
from fastapi import APIRouter

from slm_config import OLLAMA_BASE_URL

logger = logging.getLogger("slm")

router = APIRouter(tags=["module-version"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


_HEALTH_TIMEOUT_S = 2.0
_FILES_DIR = os.environ.get("FILES_DIR", "/data/files")
_NODE_RED_URL = os.environ.get("NODE_RED_URL", "http://slm-node-red:1880")
_FRONTEND_URL = os.environ.get("FRONTEND_HEALTH_URL", "http://frontend:3000")
_VISION_URL = os.environ.get("VISION_AGENT_URL", "http://host.docker.internal:8100")


async def _http_ok(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S) as client:
            r = await client.get(url)
            return r.status_code < 500
    except Exception:
        return False


def _db_ok() -> bool:
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        return True
    except Exception:
        return False
    finally:
        if conn:
            conn.close()


def _map_bundle_ok() -> tuple[bool, str]:
    """files/map/*.pmtiles 존재 — 관리 UI 업로드 교체본 우선, 없으면 미확인.

    프런트 public/ 은 이 컨테이너에서 안 보이므로 files/ 만 본다.
    둘 다 없어도 frontend 내장본이 있을 수 있어 down 이 아니라 unknown.
    """
    try:
        hits = glob.glob(f"{_FILES_DIR}/map/*.pmtiles")
        if hits:
            mb = sum(os.path.getsize(h) for h in hits) / 1e6
            return True, f"{len(hits)}개 · {mb:.0f}MB"
        return False, "files/map 에 없음 (frontend 내장본 여부는 미확인)"
    except Exception:
        return False, ""


async def _collect_health() -> dict:
    """모듈별 live health — 실패도 결과다 (down 표시)."""
    ollama_t = _http_ok(f"{OLLAMA_BASE_URL}/api/tags")
    nodered_t = _http_ok(_NODE_RED_URL)
    frontend_t = _http_ok(_FRONTEND_URL)
    vision_t = _http_ok(f"{_VISION_URL}/health")
    db_t = asyncio.to_thread(_db_ok)
    ollama, nodered, frontend, vision, db = await asyncio.gather(
        ollama_t, nodered_t, frontend_t, vision_t, db_t,
    )
    map_ok, map_detail = _map_bundle_ok()
    return {
        "backend": {"status": "ok", "detail": "응답 = 정상"},
        "db": {"status": "ok" if db else "down", "detail": ""},
        "ai-weights": {"status": "ok" if ollama else "down",
                       "detail": "Ollama 응답" if ollama else "Ollama 무응답"},
        "node-red": {"status": "ok" if nodered else "down", "detail": ""},
        "frontend": {"status": "ok" if frontend else "down", "detail": ""},
        "vision-agent": {"status": "ok" if vision else "down", "detail": ""},
        "map-bundle": {"status": "ok" if map_ok else "unknown",
                       "detail": map_detail},
        # feature 모듈은 프로세스가 아니라 별도 health 없음
        "epanet": {"status": "unknown", "detail": "기능 모듈 — SKU 상태 참조"},
    }


@router.get("/system/modules")
async def get_system_modules(region: str = "R01"):
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT v.module_key, v.name, v.kind, v.version,
                   TO_CHAR(v.installed_at, 'YYYY-MM-DD'), v.installed_by,
                   v.notes,
                   l.sku_code, l.oss_notices,
                   c.use_yn
            FROM tb_module_version v
            LEFT JOIN tb_module_license l
                ON l.region = v.region AND l.module_key = v.module_key
            LEFT JOIN tb_comm_code c
                ON c.region = v.region AND c.grp_cd = 'SITE_SETTING'
                AND c.comm_cd = l.sku_code
            WHERE v.region = %s
            ORDER BY v.module_key
            """,
            (region,),
        )
        rows = cur.fetchall()
        cur.close()

        health = await _collect_health()

        items = []
        for r in rows:
            sku_code = r[7]
            if not sku_code:
                lic = "included"
            elif r[9] == "Y":
                lic = "active"
            else:
                lic = "locked"
            h = health.get(r[0], {"status": "unknown", "detail": ""})
            items.append({
                "module_key": r[0], "name": r[1], "kind": r[2],
                "version": r[3], "installed_at": r[4], "installed_by": r[5],
                "notes": r[6] or "",
                "sku_code": sku_code, "license_status": lic,
                "oss_notices": r[8] or [],
                "health": h["status"], "health_detail": h["detail"],
            })
        return {"status": "OK", "items": items}
    except Exception as e:
        logger.error(f"모듈 버전 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e), "items": []}
    finally:
        if conn:
            conn.close()
