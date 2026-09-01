"""
업데이트 번들 API — docs/module-version-spec.md P2/P3

- POST   /system/update/upload            — 번들 업로드 → 압축 해제 → 검증(sha256·SKU)
- GET    /system/update/bundles           — 목록 (+에이전트 결과 파일 병합)
- POST   /system/update/bundles/{id}/approve   — 승인 → 적용 잡 스풀
- POST   /system/update/bundles/{id}/rollback  — 적용됨 → 롤백 잡 스풀
- DELETE /system/update/bundles/{id}      — 스테이징 삭제 (적용 전만)

원칙:
- 검증은 서버, **적용은 호스트 에이전트**(scripts/update_agent.py) — 컨테이너는
  자신을 교체할 수 없다. 백엔드는 잡 파일을 스풀에 쓰고 결과 파일을 병합만.
- required_sku 미보유 모듈이 든 번들은 **반입 단계에서 차단** (라이선스 관리).
- 경로 안전: 아카이브 멤버·target_dir 에 절대경로/'..' 금지.

manifest.json 규격:
{ "name": str, "modules": [ { "module_key", "version", "kind": "bundle",
    "artifact": "파일명", "sha256", "target_dir": "files/ 하위 상대경로",
    "required_sku": null|"CODE" } ], "migrations": ["NNNN_x.sql", ...] }
"""

import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
import time
import uuid

from fastapi import APIRouter, File, Form, UploadFile

logger = logging.getLogger("slm")

router = APIRouter(tags=["module-update"])

_get_db_connection = None

_FILES_DIR = os.environ.get("FILES_DIR", "/data/files")
_UPD = f"{_FILES_DIR}/updates"
_MAX_BUNDLE_MB = 2048


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn
    for sub in ("incoming", "staged", "jobs", "backup"):
        os.makedirs(f"{_UPD}/{sub}", exist_ok=True)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member(name: str) -> bool:
    return not (name.startswith("/") or ".." in name.split("/"))


def _validate_manifest(staged: str, region: str) -> tuple[bool, str, dict]:
    """manifest 파싱 + sha256 + SKU 검증. (ok, detail, manifest)"""
    mf_path = f"{staged}/manifest.json"
    if not os.path.exists(mf_path):
        return False, "manifest.json 없음", {}
    try:
        mf = json.load(open(mf_path, encoding="utf-8"))
    except Exception as e:
        return False, f"manifest 파싱 실패: {e}", {}

    modules = mf.get("modules") or []
    if not modules:
        return False, "modules 비어 있음", mf

    lines = []
    # SKU 게이트 — 미보유 모듈은 반입 자체를 차단
    skus = {m.get("required_sku") for m in modules if m.get("required_sku")}
    if skus:
        conn = _get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT comm_cd, use_yn FROM tb_comm_code "
                "WHERE region = %s AND grp_cd = 'SITE_SETTING' AND comm_cd = ANY(%s)",
                (region, list(skus)),
            )
            active = {r[0] for r in cur.fetchall() if r[1] == "Y"}
            cur.close()
        finally:
            conn.close()
        locked = skus - active
        if locked:
            return False, f"라이선스 미보유 모듈 포함 — 반입 차단: {sorted(locked)}", mf

    for m in modules:
        art = m.get("artifact") or ""
        tgt = m.get("target_dir") or ""
        if not m.get("module_key") or not m.get("version"):
            return False, "module_key/version 누락", mf
        if not _safe_member(art) or not _safe_member(tgt) or not tgt:
            return False, f"경로 위반: {art} → {tgt}", mf
        ap = f"{staged}/{art}"
        if not os.path.exists(ap):
            return False, f"아티팩트 없음: {art}", mf
        digest = _sha256(ap)
        if digest != (m.get("sha256") or "").lower():
            return False, f"sha256 불일치: {art} (계산 {digest[:12]}…)", mf
        lines.append(f"{m['module_key']} v{m['version']} — sha256 확인")

    for mig in mf.get("migrations") or []:
        if not _safe_member(mig) or not re.match(r"^\d{4}_[\w\-]+\.sql$", mig):
            return False, f"마이그레이션 파일명 위반: {mig}", mf
        if not os.path.exists(f"{staged}/migrations/{mig}"):
            return False, f"마이그레이션 파일 없음: {mig}", mf
        lines.append(f"migration {mig} — 존재 확인")

    return True, " · ".join(lines), mf


@router.post("/system/update/upload")
async def upload_bundle(file: UploadFile = File(...), user_id: str = Form("unknown")):
    """번들(tar.gz) 업로드 → 스테이징 압축 해제 → 검증 → 이력 기록."""
    conn = None
    bundle_id = uuid.uuid4().hex[:12]
    staged = f"{_UPD}/staged/{bundle_id}"
    try:
        raw_path = f"{_UPD}/incoming/{bundle_id}.tar.gz"
        size = 0
        with open(raw_path, "wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > _MAX_BUNDLE_MB * 1024 * 1024:
                    out.close()
                    os.remove(raw_path)
                    return {"status": "error", "message": f"{_MAX_BUNDLE_MB}MB 초과"}
                out.write(chunk)

        os.makedirs(staged, exist_ok=True)
        with tarfile.open(raw_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not _safe_member(member.name):
                    raise ValueError(f"아카이브 경로 위반: {member.name}")
            tar.extractall(staged)

        ok, detail, mf = _validate_manifest(staged, "R01")
        status = "verified" if ok else "failed"
        if not ok:
            shutil.rmtree(staged, ignore_errors=True)

        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_module_update
                (bundle_id, filename, manifest, status, detail, uploaded_by)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s)
            """,
            [bundle_id, file.filename or "bundle.tar.gz",
             json.dumps(mf, ensure_ascii=False), status, detail, user_id],
        )
        conn.commit()
        cur.close()
        logger.info(f"[update] 번들 {bundle_id} {status}: {detail[:120]}")
        return {"status": "OK", "bundle_id": bundle_id,
                "verify": status, "detail": detail}
    except Exception as e:
        shutil.rmtree(staged, ignore_errors=True)
        logger.error(f"번들 업로드 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


def _merge_agent_results(cur) -> None:
    """에이전트 결과 파일(jobs/*.result.json) → DB 상태 병합 후 파일 제거."""
    for fn in os.listdir(f"{_UPD}/jobs"):
        if not fn.endswith(".result.json"):
            continue
        path = f"{_UPD}/jobs/{fn}"
        try:
            res = json.load(open(path, encoding="utf-8"))
            cur.execute(
                """
                UPDATE tb_module_update
                SET status = %s, detail = detail || E'\n' || %s,
                    applied_at = CASE WHEN %s = 'applied' THEN now() ELSE applied_at END,
                    updated_at = now()
                WHERE bundle_id = %s
                """,
                [res.get("status", "apply_failed"), res.get("detail", ""),
                 res.get("status"), res.get("bundle_id")],
            )
            os.remove(path)
        except Exception as e:
            logger.warning(f"[update] 결과 병합 실패 {fn}: {e}")


@router.get("/system/update/bundles")
def list_bundles(region: str = "R01"):
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        _merge_agent_results(cur)
        conn.commit()
        cur.execute(
            """
            SELECT bundle_id, filename, manifest, status, detail,
                   uploaded_by, TO_CHAR(uploaded_at, 'MM-DD HH24:MI'),
                   approved_by, TO_CHAR(applied_at, 'MM-DD HH24:MI')
            FROM tb_module_update WHERE region = %s
            ORDER BY uploaded_at DESC LIMIT 50
            """,
            (region,),
        )
        items = [{
            "bundle_id": r[0], "filename": r[1],
            "name": (r[2] or {}).get("name", ""),
            "modules": [f"{m.get('module_key')} v{m.get('version')}"
                        for m in (r[2] or {}).get("modules", [])],
            "migrations": (r[2] or {}).get("migrations", []),
            "status": r[3], "detail": r[4],
            "uploaded_by": r[5], "uploaded_at": r[6],
            "approved_by": r[7], "applied_at": r[8],
        } for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "items": items}
    except Exception as e:
        logger.error(f"번들 목록 실패: {e}")
        return {"status": "ERROR", "message": str(e), "items": []}
    finally:
        if conn:
            conn.close()


def _spool_job(bundle_id: str, action: str) -> None:
    job = {"bundle_id": bundle_id, "action": action,
           "staged_dir": f"updates/staged/{bundle_id}",
           "requested_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(f"{_UPD}/jobs/{bundle_id}.{action}.json", "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)


def _transition(bundle_id: str, from_states: tuple, to_state: str,
                user_id: str, action: str):
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        # 에이전트 결과가 아직 병합 전이면 상태가 낡아 전이가 거부된다 —
        # 전이 전 결과 병합 (목록 조회를 안 거치고 API 직행하는 경우 대비)
        _merge_agent_results(cur)
        cur.execute(
            """
            UPDATE tb_module_update
            SET status = %s, approved_by = %s, approved_at = now(), updated_at = now()
            WHERE bundle_id = %s AND status = ANY(%s)
            """,
            [to_state, user_id, bundle_id, list(from_states)],
        )
        if cur.rowcount == 0:
            conn.rollback()
            return {"status": "error",
                    "message": f"상태 전이 불가 (현재 상태가 {from_states} 아님)"}
        conn.commit()
        cur.close()
        _spool_job(bundle_id, action)
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"번들 {action} 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/system/update/bundles/{bundle_id}/approve")
async def approve_bundle(bundle_id: str, body: dict | None = None):
    user_id = (body or {}).get("user_id", "unknown")
    return _transition(bundle_id, ("verified",), "approved", user_id, "apply")


@router.post("/system/update/bundles/{bundle_id}/rollback")
async def rollback_bundle(bundle_id: str, body: dict | None = None):
    """적용된 번들 롤백 요청 — 에이전트가 apply 시점 백업으로 복원.
    DB 마이그레이션은 자동 롤백하지 않는다 (데이터 손실 위험 — 수동)."""
    user_id = (body or {}).get("user_id", "unknown")
    return _transition(bundle_id, ("applied",), "rollback_requested",
                       user_id, "rollback")


@router.delete("/system/update/bundles/{bundle_id}")
async def delete_bundle(bundle_id: str):
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tb_module_update WHERE bundle_id = %s "
            "AND status IN ('uploaded','verified','failed')",
            (bundle_id,),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return {"status": "error", "message": "적용 전 번들만 삭제 가능"}
        conn.commit()
        cur.close()
        shutil.rmtree(f"{_UPD}/staged/{bundle_id}", ignore_errors=True)
        for suffix in ("tar.gz",):
            p = f"{_UPD}/incoming/{bundle_id}.{suffix}"
            if os.path.exists(p):
                os.remove(p)
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"번들 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
