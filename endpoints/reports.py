"""
endpoints/reports.py — 보고서 (장애 조치 / 일 점검) CRUD

사양: docs/report-spec.md (v2)

엔드포인트:
  GET    /reports                       — 목록
  GET    /reports/{report_id}           — 상세 (items 포함)
  POST   /reports                       — 신규 + 항목 일괄 요약
  PATCH  /reports/{report_id}           — 메타 수정 (title, photo_layout)
  POST   /reports/{report_id}/finalize  — 확정
  POST   /reports/{report_id}/reopen    — 재오픈
  DELETE /reports/{report_id}           — 삭제
  POST   /reports/{report_id}/items     — 항목 추가
  PATCH  /reports/{report_id}/items/reorder — seq 일괄 갱신
  PATCH  /reports/items/{item_id}       — 항목 편집
  POST   /reports/items/{item_id}/resummarize — 재요약
  DELETE /reports/items/{item_id}       — 항목 삭제
  POST   /reports/items/{item_id}/photos       — 사진 추가 (URL 등록)
  DELETE /reports/items/{item_id}/photos       — 사진 삭제
  PATCH  /reports/items/{item_id}/photos       — 사진 캡션·출처 수정
"""

from __future__ import annotations

import json
import re
import logging
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from report_summarizer import summarize_task, refine_item_summary
from root_cause_classifier import classify_item

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise HTTPException(status_code=500, detail="DB 커넥션 미초기화")
    return _get_db_connection()


# ============================================================================
# 헬퍼
# ============================================================================

def _row_to_report(row: dict) -> dict:
    approval = row.get("approval_chain")
    if isinstance(approval, str):
        try:
            approval = json.loads(approval)
        except Exception:
            approval = None
    return {
        "report_id":      row["report_id"],
        "region":         row["region"],
        "report_type":    row["report_type"],
        "report_date":    row["report_date"].isoformat() if row.get("report_date") else None,
        "author_id":      row["author_id"],
        "title":          row["title"],
        "status":         row["status"],
        "finalized_at":   row["finalized_at"].isoformat() if row.get("finalized_at") else None,
        "finalized_by":   row.get("finalized_by"),
        "photo_layout":   row.get("photo_layout"),
        "approval_chain": approval,
        "responsible_name": row.get("responsible_name"),
        "created_at":     row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at":     row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def _row_to_item(row: dict) -> dict:
    def _maybe_json(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return v

    photos = _maybe_json(row.get("photo_urls")) or []
    sys_cats = _maybe_json(row.get("system_categories")) or []
    eq_cats  = _maybe_json(row.get("equipment_categories")) or []
    root_causes = _maybe_json(row.get("root_causes")) or []
    return {
        "item_id":         row["item_id"],
        "report_id":       row["report_id"],
        "seq":             row["seq"],
        "task_id":         row.get("task_id"),
        "site_name":       row.get("site_name"),
        "facility_type":   row.get("facility_type"),
        "equipment_name":  row.get("equipment_name"),
        "fault_category":  row.get("fault_category"),
        "inspection_type": row.get("inspection_type"),
        "occurred_at":     row["occurred_at"].isoformat() if row.get("occurred_at") else None,
        "occurred_text":   row.get("occurred_text"),
        "resolved_at":     row["resolved_at"].isoformat() if row.get("resolved_at") else None,
        "resolved_text":   row.get("resolved_text"),
        "original_text":   row.get("original_text"),
        "photo_urls":      photos,
        "exclude_photo":   bool(row.get("exclude_photo")),
        "ai_summary_at":   row["ai_summary_at"].isoformat() if row.get("ai_summary_at") else None,
        "ai_model":        row.get("ai_model"),
        # Migration 0059 — incident_report 양식 필드
        "symptom":              row.get("symptom"),
        "cause":                row.get("cause"),
        "key_issues":           row.get("key_issues"),
        "system_categories":    sys_cats,
        "equipment_categories": eq_cats,
        # Migration 0063 — LLM 사후 분류 결과
        "root_causes":              root_causes,
        "root_cause_classified_at": row["root_cause_classified_at"].isoformat() if row.get("root_cause_classified_at") else None,
        "root_cause_model":         row.get("root_cause_model"),
    }


def _fetchone_dict(cur) -> Optional[dict]:
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _fetchall_dict(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _ensure_owner_or_403(report: dict, user_id: str) -> None:
    if report["author_id"] != user_id:
        raise HTTPException(status_code=403, detail="작성자 본인만 수정할 수 있습니다")


def _ensure_draft_or_405(report: dict) -> None:
    if report["status"] == "finalized":
        raise HTTPException(status_code=405, detail="확정된 보고서는 수정할 수 없습니다 (재오픈 필요)")


def _resolve_task(cur, task_id: int, region: str) -> dict:
    """task 정보 조회. region 검증은 멀티테넌시 도입 시 활성화 예정.

    현재 환경: tb_facility 가 별도로 없고, tb_equipment_info 에도 region 미존재.
    → 단일 region 환경 가정. region 검증은 미수행 (사양 §3.2 P2 로 보류).
    """
    cur.execute(
        """
        SELECT t.task_id, t.sitename, t.facilitytype, t.task_category, t.fault_category,
               t.inspection_type, t.equipmenttype, t.equipment_id,
               t.task_start_time, t.resolved_at, t.task_content, t.resolution_note,
               t.photo_urls, t.resolution_photo_urls,
               COALESCE(ei.equipmenttype, t.equipmenttype) AS equipment_name
          FROM tb_task_master t
          LEFT JOIN tb_equipment_info ei ON ei.equipment_id = t.equipment_id
         WHERE t.task_id = %s
        """,
        (task_id,),
    )
    row = _fetchone_dict(cur)
    if not row:
        raise HTTPException(status_code=404, detail=f"task_id {task_id} 를 찾을 수 없습니다")
    return row


def _build_photo_objects(task_row: dict, ai_summary_at: datetime) -> list[dict]:
    """task 의 photo_urls (source=fault) + resolution_photo_urls (source=action) 를
    객체 배열로 합친다."""
    out: list[dict] = []
    fault_photos = task_row.get("photo_urls")
    if isinstance(fault_photos, str):
        try:
            fault_photos = json.loads(fault_photos)
        except Exception:
            fault_photos = []
    fault_photos = fault_photos or []

    action_photos = task_row.get("resolution_photo_urls")
    if isinstance(action_photos, str):
        try:
            action_photos = json.loads(action_photos)
        except Exception:
            action_photos = []
    action_photos = action_photos or []

    occurred_iso = (
        task_row["task_start_time"].isoformat()
        if task_row.get("task_start_time") else None
    )
    resolved_iso = (
        task_row["resolved_at"].isoformat()
        if task_row.get("resolved_at") else None
    )

    for url in fault_photos:
        if not url:
            continue
        out.append({
            "url": url,
            "source": "fault",
            "caption": "발생 시점",
            "taken_at": occurred_iso,
        })
    for url in action_photos:
        if not url:
            continue
        out.append({
            "url": url,
            "source": "action",
            "caption": "조치 후",
            "taken_at": resolved_iso,
        })
    return out


def _summarize_and_build_item(
    task_row: dict, seq: int, model_name: str | None = None
) -> dict:
    """단일 task → tb_report_item INSERT 용 dict (seq 포함)."""
    task_content = task_row.get("task_content") or ""
    resolution_note = task_row.get("resolution_note") or ""
    site_name = task_row.get("sitename")
    facility_type = task_row.get("facilitytype")
    equipment_name = task_row.get("equipment_name") or task_row.get("equipmenttype")
    fault_cat = task_row.get("fault_category")
    inspection_type = task_row.get("inspection_type")

    summary = summarize_task(
        task_content=task_content,
        resolution_note=resolution_note,
        site_name=site_name,
        facility_type=facility_type,
        equipment_name=equipment_name,
        fault_category=fault_cat,
        inspection_type=inspection_type,
        model=model_name,
    )

    now_ts = datetime.utcnow()
    photos = _build_photo_objects(task_row, now_ts)
    original_text = (
        f"[발생]\n{task_content}\n\n[조치]\n{resolution_note}".strip()
    )

    return {
        "seq":             seq,
        "task_id":         task_row["task_id"],
        "site_name":       site_name,
        "facility_type":   facility_type,
        "equipment_name":  equipment_name,
        "fault_category":  fault_cat,
        "inspection_type": inspection_type,
        "occurred_at":     task_row.get("task_start_time"),
        "occurred_text":   summary["occurred_text"],
        "resolved_at":     task_row.get("resolved_at"),
        "resolved_text":   summary["resolved_text"],
        "original_text":   original_text,
        "photo_urls":      photos,
        "ai_model":        summary.get("model"),
    }


def _insert_items(cur, report_id: int, items: list[dict]) -> list[int]:
    """item dict 리스트를 일괄 INSERT 후 item_id 리스트 반환."""
    out_ids = []
    for it in items:
        cur.execute(
            """
            INSERT INTO tb_report_item
              (report_id, seq, task_id, site_name, facility_type, equipment_name,
               fault_category, inspection_type, occurred_at, occurred_text,
               resolved_at, resolved_text, original_text, photo_urls,
               exclude_photo, ai_summary_at, ai_model)
            VALUES (%s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s::jsonb,
                    false, now(), %s)
            ON CONFLICT (report_id, task_id) WHERE task_id IS NOT NULL DO NOTHING
            RETURNING item_id
            """,
            (
                report_id, it["seq"], it.get("task_id"),
                it.get("site_name"), it.get("facility_type"), it.get("equipment_name"),
                it.get("fault_category"), it.get("inspection_type"),
                it.get("occurred_at"), it.get("occurred_text"),
                it.get("resolved_at"), it.get("resolved_text"),
                it.get("original_text"),
                json.dumps(it.get("photo_urls") or [], ensure_ascii=False, default=str),
                it.get("ai_model"),
            ),
        )
        row = cur.fetchone()
        if row:
            out_ids.append(row[0])
    return out_ids


# ============================================================================
# Pydantic 모델
# ============================================================================

class CreateReportRequest(BaseModel):
    user_id: str
    region: str
    report_type: str  # fault_action / daily_inspection
    report_date: date
    task_ids: list[int] = Field(default_factory=list)
    title: Optional[str] = None
    photo_layout: Optional[str] = "2up"


class PatchReportRequest(BaseModel):
    user_id: str
    title: Optional[str] = None
    photo_layout: Optional[str] = None
    approval_chain: Optional[dict] = None  # Migration 0059
    responsible_name: Optional[str] = None  # Migration 0073 — 인쇄 본문 담당자


class FinalizeRequest(BaseModel):
    user_id: str


class AddItemsRequest(BaseModel):
    user_id: str
    task_ids: list[int]


class AddManualItemRequest(BaseModel):
    """수동(직접 입력) 항목 — task_id NULL. 일 점검·장애 조치 공통.

    일 점검은 하루 일상 점검을 사용자가 개별 기재하는 것이 기본 취지이고,
    장애 조치도 이력 없이 현장 상황을 직접 기재할 수 있어야 한다 (2026-07-22).
    """
    user_id: str
    site_name: Optional[str] = None
    facility_type: Optional[str] = None
    equipment_name: Optional[str] = None
    fault_category: Optional[str] = None     # 고장/이상/교체/점검
    inspection_type: Optional[str] = None    # 일상/정기/특별 (일 점검)
    occurred_at: Optional[datetime] = None
    occurred_text: str                       # 내용 (필수)
    resolved_at: Optional[datetime] = None
    resolved_text: Optional[str] = None


class ReorderRequest(BaseModel):
    user_id: str
    item_ids: list[int]


class PatchItemRequest(BaseModel):
    user_id: str
    occurred_at: Optional[datetime] = None
    occurred_text: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolved_text: Optional[str] = None
    site_name: Optional[str] = None
    facility_type: Optional[str] = None
    equipment_name: Optional[str] = None
    fault_category: Optional[str] = None
    inspection_type: Optional[str] = None
    exclude_photo: Optional[bool] = None
    # Migration 0059 — incident_report 양식 필드
    symptom: Optional[str] = None
    cause: Optional[str] = None
    key_issues: Optional[str] = None
    system_categories: Optional[list[str]] = None
    equipment_categories: Optional[list[str]] = None


class ResummarizeRequest(BaseModel):
    user_id: str
    dry_run: Optional[bool] = False  # True 면 결과만 반환, DB 미반영 (미리보기)


class AddPhotoRequest(BaseModel):
    user_id: str
    url: str
    source: str = "user"   # user / fault / action
    caption: Optional[str] = None
    taken_at: Optional[datetime] = None


class DeletePhotoRequest(BaseModel):
    user_id: str
    url: str


class PatchPhotoRequest(BaseModel):
    user_id: str
    url: str
    caption: Optional[str] = None
    source: Optional[str] = None


# ============================================================================
# 조회
# ============================================================================

@router.get("")
def list_reports(
    user_id: str = Query(...),
    region: str = Query(...),
    report_type: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    status: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        sql = ["SELECT * FROM tb_report WHERE region = %s"]
        params: list[Any] = [region]
        if report_type:
            sql.append("AND report_type = %s"); params.append(report_type)
        if date_from:
            sql.append("AND report_date >= %s"); params.append(date_from)
        if date_to:
            sql.append("AND report_date <= %s"); params.append(date_to)
        if status:
            sql.append("AND status = %s"); params.append(status)
        if author:
            sql.append("AND author_id = %s"); params.append(author)
        sql.append("ORDER BY report_date DESC, report_id DESC LIMIT %s"); params.append(limit)
        cur.execute(" ".join(sql), tuple(params))
        rows = _fetchall_dict(cur)

        # item_count 별도 조회
        if rows:
            ids = [r["report_id"] for r in rows]
            cur.execute(
                "SELECT report_id, COUNT(*) c FROM tb_report_item "
                "WHERE report_id = ANY(%s) GROUP BY report_id",
                (ids,),
            )
            counts = {r[0]: r[1] for r in cur.fetchall()}
        else:
            counts = {}

        out = []
        for r in rows:
            d = _row_to_report(r)
            d["item_count"] = counts.get(r["report_id"], 0)
            out.append(d)
        return {"reports": out, "total": len(out)}
    finally:
        conn.close()


@router.get("/stats")
def report_stats(
    region: str = Query(...),
    report_type: str = Query("fault_action"),
    days: int = Query(30, ge=0, le=3650, description="기간(일). 0=전체"),
):
    """보고서 목록 상단 통계 — KPI 4종 + 장애 분류 분포.

    기간(days)은 보고서 report_date 기준. 항목 지표(조치율·평균 조치시간·분류)는
    해당 기간 보고서에 속한 tb_report_item 집계.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        date_cond = "AND r.report_date >= CURRENT_DATE - %s" if days else ""
        dparams = [days] if days else []

        cur.execute(
            f"""
            SELECT count(*),
                   count(*) FILTER (WHERE r.status = 'draft'),
                   count(*) FILTER (WHERE r.status = 'finalized')
            FROM tb_report r
            WHERE r.region = %s AND r.report_type = %s {date_cond}
            """,
            [region, report_type, *dparams],
        )
        total, draft, finalized = cur.fetchone()

        cur.execute(
            f"""
            SELECT count(*),
                   count(*) FILTER (WHERE ri.resolved_at IS NOT NULL),
                   round(avg(EXTRACT(epoch FROM ri.resolved_at - ri.occurred_at) / 3600)
                         FILTER (WHERE ri.resolved_at IS NOT NULL
                                   AND ri.occurred_at IS NOT NULL
                                   AND ri.resolved_at > ri.occurred_at), 1)
            FROM tb_report_item ri
            JOIN tb_report r ON r.report_id = ri.report_id
            WHERE r.region = %s AND r.report_type = %s {date_cond}
            """,
            [region, report_type, *dparams],
        )
        items_total, items_resolved, avg_hours = cur.fetchone()

        cur.execute(
            f"""
            SELECT COALESCE(ri.fault_category, '미분류'), count(*)
            FROM tb_report_item ri
            JOIN tb_report r ON r.report_id = ri.report_id
            WHERE r.region = %s AND r.report_type = %s {date_cond}
            GROUP BY 1 ORDER BY 2 DESC
            """,
            [region, report_type, *dparams],
        )
        categories = [{"category": c, "count": n} for c, n in cur.fetchall()]
        cur.close()

        return {
            "days": days,
            "total_reports": total,
            "draft": draft,
            "finalized": finalized,
            "items_total": items_total,
            "items_resolved": items_resolved,
            "resolve_rate_pct": round(items_resolved / items_total * 100, 1) if items_total else None,
            "avg_resolve_hours": float(avg_hours) if avg_hours is not None else None,
            "categories": categories,
        }
    finally:
        conn.close()


@router.get("/candidates")
def list_candidate_tasks(
    region: str = Query(...),
    category: str = Query(..., description="'고장보고' or '점검'"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    inspection_type: Optional[str] = Query(None),
    limit: int = Query(200, le=1000),
):
    if category not in ("고장보고", "점검"):
        raise HTTPException(status_code=400, detail="category 는 '고장보고' or '점검'")
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # 현재 환경엔 region 컬럼이 시설 마스터에 없음 → 단일 region 가정.
        # 멀티테넌시 도입 시 ei.region / f.region 조인 추가 (사양 §3.2)
        sql = [
            """
            SELECT t.task_id, t.sitename, t.facilitytype, t.equipmenttype,
                   t.fault_category, t.inspection_type,
                   t.task_start_time, t.resolved_at,
                   t.task_content, t.resolution_note,
                   (COALESCE(jsonb_array_length(t.photo_urls), 0) +
                    COALESCE(jsonb_array_length(t.resolution_photo_urls), 0)) > 0 AS has_photo
              FROM tb_task_master t
             WHERE t.task_category = %s
            """
        ]
        params: list[Any] = [category]
        if date_from:
            sql.append("AND t.task_start_time >= %s"); params.append(date_from)
        if date_to:
            sql.append("AND t.task_start_time < (%s::date + INTERVAL '1 day')"); params.append(date_to)
        if inspection_type:
            sql.append("AND t.inspection_type = %s"); params.append(inspection_type)
        sql.append("ORDER BY t.task_start_time DESC LIMIT %s"); params.append(limit)
        cur.execute(" ".join(sql), tuple(params))
        rows = _fetchall_dict(cur)
        out = []
        for r in rows:
            out.append({
                "task_id":          r["task_id"],
                "sitename":         r["sitename"],
                "facilitytype":     r["facilitytype"],
                "equipmenttype":    r["equipmenttype"],
                "fault_category":   r["fault_category"],
                "inspection_type":  r["inspection_type"],
                "task_start_time":  r["task_start_time"].isoformat() if r["task_start_time"] else None,
                "resolved_at":      r["resolved_at"].isoformat() if r["resolved_at"] else None,
                "task_content":     r["task_content"],
                "resolution_note":  r["resolution_note"],
                "has_photo":        bool(r["has_photo"]),
            })
        return out
    finally:
        conn.close()


@router.get("/{report_id}")
def get_report(report_id: int, user_id: str = Query(...), region: str = Query(...)):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tb_report WHERE report_id = %s", (report_id,))
        report = _fetchone_dict(cur)
        if not report:
            raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다")
        if report["region"] != region:
            raise HTTPException(status_code=403, detail="다른 region 의 보고서입니다")

        cur.execute(
            "SELECT * FROM tb_report_item WHERE report_id = %s ORDER BY seq",
            (report_id,),
        )
        items = [_row_to_item(r) for r in _fetchall_dict(cur)]
        d = _row_to_report(report)
        d["items"] = items
        return d
    finally:
        conn.close()


# ============================================================================
# 신규 / 메타 수정 / 확정 / 삭제
# ============================================================================

@router.post("")
def create_report(req: CreateReportRequest):
    if req.report_type not in ("fault_action", "daily_inspection"):
        raise HTTPException(status_code=400, detail="report_type 은 fault_action / daily_inspection")

    conn = _get_conn()
    try:
        cur = conn.cursor()

        # task region 검증 + 메타 조회
        task_rows: list[dict] = []
        for tid in req.task_ids:
            task_rows.append(_resolve_task(cur, tid, req.region))

        title = req.title or (
            f"{req.report_date.isoformat()} "
            f"{'장애 조치 보고서' if req.report_type == 'fault_action' else '일 점검 보고서'}"
        )

        cur.execute(
            """
            INSERT INTO tb_report
              (region, report_type, report_date, author_id, title, photo_layout)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (req.region, req.report_type, req.report_date, req.user_id,
             title, req.photo_layout or "2up"),
        )
        report = _fetchone_dict(cur)
        report_id = report["report_id"]

        # 요약 + INSERT
        items: list[dict] = []
        for idx, tr in enumerate(task_rows, start=1):
            items.append(_summarize_and_build_item(tr, seq=idx))
        _insert_items(cur, report_id, items)

        conn.commit()

        # 상세 재조회
        cur.execute(
            "SELECT * FROM tb_report_item WHERE report_id = %s ORDER BY seq",
            (report_id,),
        )
        item_rows = [_row_to_item(r) for r in _fetchall_dict(cur)]

        d = _row_to_report(report)
        d["items"] = item_rows
        return d
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.exception(f"create_report 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.patch("/{report_id}")
def patch_report(report_id: int, req: PatchReportRequest):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tb_report WHERE report_id = %s", (report_id,))
        report = _fetchone_dict(cur)
        if not report:
            raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다")
        _ensure_owner_or_403(report, req.user_id)
        _ensure_draft_or_405(report)

        sets, params = [], []
        if req.title is not None:
            sets.append("title = %s"); params.append(req.title)
        if req.photo_layout is not None:
            if req.photo_layout not in ("1up", "2up"):
                raise HTTPException(status_code=400, detail="photo_layout 은 1up/2up")
            sets.append("photo_layout = %s"); params.append(req.photo_layout)
        if req.approval_chain is not None:
            sets.append("approval_chain = %s::jsonb")
            params.append(json.dumps(req.approval_chain, ensure_ascii=False, default=str))
        if req.responsible_name is not None:
            # 빈 문자열 → NULL (공란 출력)
            sets.append("responsible_name = %s")
            params.append(req.responsible_name.strip() or None)
        if not sets:
            return _row_to_report(report)
        sets.append("updated_at = now()")
        params.append(report_id)
        cur.execute(
            f"UPDATE tb_report SET {', '.join(sets)} WHERE report_id = %s RETURNING *",
            tuple(params),
        )
        updated = _fetchone_dict(cur)
        conn.commit()
        return _row_to_report(updated)
    finally:
        conn.close()


@router.post("/{report_id}/finalize")
def finalize_report(report_id: int, req: FinalizeRequest):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tb_report WHERE report_id = %s", (report_id,))
        report = _fetchone_dict(cur)
        if not report:
            raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다")
        _ensure_owner_or_403(report, req.user_id)
        if report["status"] == "finalized":
            return _row_to_report(report)
        cur.execute(
            """
            UPDATE tb_report
               SET status = 'finalized', finalized_at = now(), finalized_by = %s,
                   updated_at = now()
             WHERE report_id = %s RETURNING *
            """,
            (req.user_id, report_id),
        )
        updated = _fetchone_dict(cur)
        conn.commit()
        return _row_to_report(updated)
    finally:
        conn.close()


@router.post("/{report_id}/reopen")
def reopen_report(report_id: int, req: FinalizeRequest):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tb_report WHERE report_id = %s", (report_id,))
        report = _fetchone_dict(cur)
        if not report:
            raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다")
        _ensure_owner_or_403(report, req.user_id)
        cur.execute(
            """
            UPDATE tb_report
               SET status = 'draft', finalized_at = NULL, finalized_by = NULL,
                   updated_at = now()
             WHERE report_id = %s RETURNING *
            """,
            (report_id,),
        )
        updated = _fetchone_dict(cur)
        conn.commit()
        return _row_to_report(updated)
    finally:
        conn.close()


@router.delete("/{report_id}")
def delete_report(report_id: int, user_id: str = Query(...)):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tb_report WHERE report_id = %s", (report_id,))
        report = _fetchone_dict(cur)
        if not report:
            raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다")
        _ensure_owner_or_403(report, user_id)
        cur.execute("DELETE FROM tb_report WHERE report_id = %s", (report_id,))
        conn.commit()
        return {"deleted": True, "report_id": report_id}
    finally:
        conn.close()


# ============================================================================
# 항목
# ============================================================================

@router.post("/{report_id}/items")
def add_items(report_id: int, req: AddItemsRequest):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tb_report WHERE report_id = %s", (report_id,))
        report = _fetchone_dict(cur)
        if not report:
            raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다")
        _ensure_owner_or_403(report, req.user_id)
        _ensure_draft_or_405(report)

        # 현재 max seq
        cur.execute(
            "SELECT COALESCE(MAX(seq),0) FROM tb_report_item WHERE report_id = %s",
            (report_id,),
        )
        next_seq = (cur.fetchone()[0] or 0) + 1

        items = []
        for tid in req.task_ids:
            tr = _resolve_task(cur, tid, report["region"])
            items.append(_summarize_and_build_item(tr, seq=next_seq))
            next_seq += 1
        inserted = _insert_items(cur, report_id, items)
        conn.commit()
        return {"inserted_item_ids": inserted}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.exception(f"add_items 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.post("/{report_id}/items/manual")
def add_manual_item(report_id: int, req: AddManualItemRequest):
    """직접 입력 항목 추가 — 이력(task) 없이 사용자가 개별 기재 (AI 요약 없음)."""
    if not req.occurred_text.strip():
        raise HTTPException(status_code=400, detail="내용을 입력하세요")
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tb_report WHERE report_id = %s", (report_id,))
        report = _fetchone_dict(cur)
        if not report:
            raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다")
        _ensure_owner_or_403(report, req.user_id)
        _ensure_draft_or_405(report)

        cur.execute(
            "SELECT COALESCE(MAX(seq),0) FROM tb_report_item WHERE report_id = %s",
            (report_id,),
        )
        next_seq = (cur.fetchone()[0] or 0) + 1

        item = {
            "seq": next_seq,
            "task_id": None,
            "site_name": req.site_name,
            "facility_type": req.facility_type,
            "equipment_name": req.equipment_name,
            "fault_category": req.fault_category,
            "inspection_type": req.inspection_type,
            "occurred_at": req.occurred_at or datetime.now(),
            "occurred_text": req.occurred_text.strip(),
            "resolved_at": req.resolved_at,
            "resolved_text": (req.resolved_text or "").strip() or None,
            "original_text": None,
            "photo_urls": [],
            "ai_model": None,
        }
        inserted = _insert_items(cur, report_id, [item])
        conn.commit()

        cur.execute("SELECT * FROM tb_report_item WHERE item_id = %s", (inserted[0],))
        row = _fetchone_dict(cur)
        return _row_to_item(row)
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.exception(f"add_manual_item 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.patch("/{report_id}/items/reorder")
def reorder_items(report_id: int, req: ReorderRequest):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tb_report WHERE report_id = %s", (report_id,))
        report = _fetchone_dict(cur)
        if not report:
            raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다")
        _ensure_owner_or_403(report, req.user_id)
        _ensure_draft_or_405(report)
        for idx, item_id in enumerate(req.item_ids, start=1):
            cur.execute(
                "UPDATE tb_report_item SET seq = %s, updated_at = now() "
                "WHERE item_id = %s AND report_id = %s",
                (idx, item_id, report_id),
            )
        conn.commit()
        return {"updated": len(req.item_ids)}
    finally:
        conn.close()


def _load_item_with_report(cur, item_id: int) -> tuple[dict, dict]:
    cur.execute(
        """
        SELECT i.*, r.region AS report_region, r.author_id AS report_author,
               r.status AS report_status
          FROM tb_report_item i
          JOIN tb_report r ON r.report_id = i.report_id
         WHERE i.item_id = %s
        """,
        (item_id,),
    )
    row = _fetchone_dict(cur)
    if not row:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다")
    report = {
        "report_id": row["report_id"],
        "region":    row["report_region"],
        "author_id": row["report_author"],
        "status":    row["report_status"],
    }
    return row, report


@router.patch("/items/{item_id}")
def patch_item(item_id: int, req: PatchItemRequest):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        item, report = _load_item_with_report(cur, item_id)
        _ensure_owner_or_403(report, req.user_id)
        _ensure_draft_or_405(report)

        sets, params = [], []
        # 단순 컬럼
        for f in ("occurred_at", "occurred_text", "resolved_at", "resolved_text",
                  "site_name", "facility_type", "equipment_name", "fault_category",
                  "inspection_type", "exclude_photo",
                  "symptom", "cause", "key_issues"):
            v = getattr(req, f)
            if v is not None:
                sets.append(f"{f} = %s"); params.append(v)
        # JSONB 배열 (Migration 0059 — incident_report 양식 체크박스)
        if req.system_categories is not None:
            sets.append("system_categories = %s::jsonb")
            params.append(json.dumps(req.system_categories, ensure_ascii=False))
        if req.equipment_categories is not None:
            sets.append("equipment_categories = %s::jsonb")
            params.append(json.dumps(req.equipment_categories, ensure_ascii=False))
        if not sets:
            return _row_to_item(item)
        sets.append("updated_at = now()")
        params.append(item_id)
        cur.execute(
            f"UPDATE tb_report_item SET {', '.join(sets)} WHERE item_id = %s RETURNING *",
            tuple(params),
        )
        row = _fetchone_dict(cur)
        conn.commit()
        return _row_to_item(row)
    finally:
        conn.close()


# ============================================================================
# 근본원인 분류 (Migration 0063)
# ============================================================================

def _load_taxonomy(cur) -> list[dict]:
    cur.execute(
        "SELECT code, group_code, label, hint, weight, sort_order "
        "FROM tb_root_cause_taxonomy WHERE use_yn = 'Y' "
        "ORDER BY sort_order, code"
    )
    return _fetchall_dict(cur)


@router.get("/taxonomy/root-causes")
def list_root_cause_taxonomy():
    """관리·통계 페이지용 — 분류 코드 마스터 조회."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        rows = _load_taxonomy(cur)
        return {"taxonomy": rows, "total": len(rows)}
    finally:
        conn.close()


class ClassifyRequest(BaseModel):
    user_id: str


@router.post("/items/{item_id}/classify-causes")
def classify_item_endpoint(item_id: int, req: ClassifyRequest):
    """단일 항목 LLM 사후 분류 — 즉시 실행."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        item, report = _load_item_with_report(cur, item_id)
        _ensure_owner_or_403(report, req.user_id)
        # finalized 라도 분류는 허용 (통계 누적 목적)

        taxonomy = _load_taxonomy(cur)
        result = classify_item(item=item, taxonomy=taxonomy)
        codes = result["codes"]

        cur.execute(
            """
            UPDATE tb_report_item
               SET root_causes = %s::jsonb,
                   root_cause_classified_at = now(),
                   root_cause_model = %s,
                   updated_at = now()
             WHERE item_id = %s
             RETURNING *
            """,
            (json.dumps(codes), result.get("model"), item_id),
        )
        updated = _fetchone_dict(cur)
        conn.commit()
        return {"item": _row_to_item(updated), "fallback": result.get("fallback", False)}
    finally:
        conn.close()


class ClassifyBatchRequest(BaseModel):
    user_id: str
    region: str
    only_unclassified: bool = True
    limit: int = 100


@router.post("/items/classify-causes-batch")
def classify_causes_batch(req: ClassifyBatchRequest):
    """야간 배치 등 일괄 분류. 기본 미분류 항목만 (limit 까지).

    점검 보고서(`daily_inspection`) 항목은 근본원인 분류 대상이 아니므로 제외.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        sql = [
            """
            SELECT i.* FROM tb_report_item i
              JOIN tb_report r ON r.report_id = i.report_id
             WHERE r.region = %s AND r.report_type = 'fault_action'
            """
        ]
        params: list[Any] = [req.region]
        if req.only_unclassified:
            sql.append("AND i.root_cause_classified_at IS NULL")
        sql.append("ORDER BY i.item_id LIMIT %s")
        params.append(req.limit)
        cur.execute(" ".join(sql), tuple(params))
        items = _fetchall_dict(cur)

        if not items:
            return {"processed": 0, "results": []}

        taxonomy = _load_taxonomy(cur)
        results = []
        for it in items:
            res = classify_item(item=it, taxonomy=taxonomy)
            cur.execute(
                """
                UPDATE tb_report_item
                   SET root_causes = %s::jsonb,
                       root_cause_classified_at = now(),
                       root_cause_model = %s,
                       updated_at = now()
                 WHERE item_id = %s
                """,
                (json.dumps(res["codes"]), res.get("model"), it["item_id"]),
            )
            results.append({
                "item_id": it["item_id"],
                "codes": res["codes"],
                "fallback": res.get("fallback", False),
            })
        conn.commit()
        return {"processed": len(results), "results": results}
    finally:
        conn.close()


class CronClassifyRequest(BaseModel):
    only_unclassified: bool = True
    limit_per_region: int = 200


@router.post("/items/classify-causes-cron")
def classify_causes_cron(req: CronClassifyRequest):
    """야간 cron 트리거 — 모든 region 의 미분류 fault_action 항목 일괄 분류.

    외부 cron job 또는 스케줄러에서 호출. 인증 헤더 없이 동작 (운영 시
    내부망에서만 노출 권장).

    응답: { regions: [{region, processed, hits}], total_processed }
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # 1) region 목록 — tb_report 에서 distinct
        cur.execute("SELECT DISTINCT region FROM tb_report ORDER BY region")
        regions = [r[0] for r in cur.fetchall()]
        if not regions:
            return {"regions": [], "total_processed": 0}

        taxonomy = _load_taxonomy(cur)

        out = []
        total = 0
        for region in regions:
            sql = [
                """
                SELECT i.* FROM tb_report_item i
                  JOIN tb_report r ON r.report_id = i.report_id
                 WHERE r.region = %s AND r.report_type = 'fault_action'
                """
            ]
            params: list[Any] = [region]
            if req.only_unclassified:
                sql.append("AND i.root_cause_classified_at IS NULL")
            sql.append("ORDER BY i.item_id LIMIT %s")
            params.append(req.limit_per_region)
            cur.execute(" ".join(sql), tuple(params))
            items = _fetchall_dict(cur)

            hits = 0
            for it in items:
                res = classify_item(item=it, taxonomy=taxonomy)
                cur.execute(
                    """
                    UPDATE tb_report_item
                       SET root_causes = %s::jsonb,
                           root_cause_classified_at = now(),
                           root_cause_model = %s,
                           updated_at = now()
                     WHERE item_id = %s
                    """,
                    (json.dumps(res["codes"]), res.get("model"), it["item_id"]),
                )
                if res["codes"]:
                    hits += 1
            conn.commit()
            out.append({"region": region, "processed": len(items), "hits": hits})
            total += len(items)
            logger.info(f"[cron] root-cause classify: region={region} processed={len(items)} hits={hits}")

        return {"regions": out, "total_processed": total}
    finally:
        conn.close()


@router.get("/stats/unclassified-items")
def stats_unclassified_items(
    region: str = Query(...),
    limit: int = Query(50, le=200),
):
    """미분류 항목 — 분류 시도 후 root_causes 가 빈 배열 또는 UNKNOWN 만 인 항목.

    운영자가 어떤 텍스트가 분류기에 약한지 식별하여 taxonomy.hint 보강하도록.
    추정 사유 (휴리스틱):
    - 'too_short': 텍스트 합계가 30자 미만
    - 'placeholder': '기록해줘'·'테스트' 같은 더미 패턴
    - 'unknown_only': UNKNOWN 1개만 매칭
    - 'no_match': 분류 시도했으나 코드 매칭 0
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT i.item_id, i.report_id, i.occurred_at, i.root_causes,
                   i.root_cause_classified_at,
                   COALESCE(i.site_name,'') AS site_name,
                   COALESCE(i.equipment_name,'') AS equipment_name,
                   COALESCE(i.fault_category,'') AS fault_category,
                   COALESCE(i.occurred_text,'') AS occurred_text,
                   COALESCE(i.symptom,'') AS symptom,
                   COALESCE(i.cause,'') AS cause,
                   COALESCE(i.resolved_text,'') AS resolved_text,
                   COALESCE(i.key_issues,'') AS key_issues
              FROM tb_report_item i
              JOIN tb_report r ON r.report_id = i.report_id
             WHERE r.region = %s AND r.report_type = 'fault_action'
               AND i.root_cause_classified_at IS NOT NULL
               AND (
                    i.root_causes IS NULL
                    OR jsonb_array_length(i.root_causes) = 0
                    OR i.root_causes = '["UNKNOWN"]'::jsonb
               )
             ORDER BY i.item_id DESC
             LIMIT %s
            """,
            (region, limit),
        )
        rows = _fetchall_dict(cur)
        out = []
        placeholder_re = re.compile(r"(기록해줘|등록해줘|테스트|browser test|\[수정\s*#\d+\])")
        for r in rows:
            text_parts = [
                r.get("occurred_text",""),
                r.get("symptom",""),
                r.get("cause",""),
                r.get("resolved_text",""),
                r.get("key_issues",""),
            ]
            joined = " ".join(p for p in text_parts if p).strip()
            n = len(joined)
            rc_raw = r.get("root_causes")
            if isinstance(rc_raw, str):
                try:
                    rc = json.loads(rc_raw)
                except Exception:
                    rc = []
            else:
                rc = rc_raw or []

            if not joined or n < 30:
                reason = "too_short"
            elif placeholder_re.search(joined):
                reason = "placeholder"
            elif rc == ["UNKNOWN"]:
                reason = "unknown_only"
            else:
                reason = "no_match"

            out.append({
                "item_id":          r["item_id"],
                "report_id":        r["report_id"],
                "occurred_at":      r["occurred_at"].isoformat() if r.get("occurred_at") else None,
                "site_name":        r.get("site_name") or None,
                "equipment_name":   r.get("equipment_name") or None,
                "fault_category":   r.get("fault_category") or None,
                "text_preview":     joined[:120],
                "text_length":      n,
                "root_causes":      rc,
                "classified_at":    r["root_cause_classified_at"].isoformat() if r.get("root_cause_classified_at") else None,
                "reason":           reason,
            })
        return {"items": out, "total": len(out)}
    finally:
        conn.close()


@router.get("/stats/root-causes")
def stats_root_causes(
    region: str = Query(...),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
):
    """근본원인별 빈도 + 설비별 분포 통계.

    P1 기본: 단순 COUNT. P2 에서 weight 곱·시간 감쇠 적용 예정.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # 1. 근본원인별 빈도 (전사)
        # NOTE: lateral alias 는 tb_report_item.cause 컬럼과 충돌하지 않도록 'rc_code' 사용
        sql = [
            """
            SELECT rc.code AS code, COUNT(*) AS cnt
              FROM tb_report_item i
              JOIN tb_report r ON r.report_id = i.report_id
              CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(i.root_causes, '[]'::jsonb)) AS rc(code)
             WHERE r.region = %s AND r.report_type = 'fault_action'
            """
        ]
        params: list[Any] = [region]
        if date_from:
            sql.append("AND i.occurred_at >= %s"); params.append(date_from)
        if date_to:
            sql.append("AND i.occurred_at < (%s::date + INTERVAL '1 day')"); params.append(date_to)
        sql.append("GROUP BY rc.code ORDER BY cnt DESC")
        cur.execute(" ".join(sql), tuple(params))
        by_cause = _fetchall_dict(cur)

        # 2. 설비별 분포 (item.task_id → tb_task_master.equipment_id)
        sql2 = [
            """
            SELECT t.equipment_id, t.equipmenttype, t.sitename,
                   rc.code AS code, COUNT(*) AS cnt
              FROM tb_report_item i
              JOIN tb_report r ON r.report_id = i.report_id
              JOIN tb_task_master t ON t.task_id = i.task_id
              CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(i.root_causes, '[]'::jsonb)) AS rc(code)
             WHERE r.region = %s AND r.report_type = 'fault_action'
               AND t.equipment_id IS NOT NULL
            """
        ]
        params2: list[Any] = [region]
        if date_from:
            sql2.append("AND i.occurred_at >= %s"); params2.append(date_from)
        if date_to:
            sql2.append("AND i.occurred_at < (%s::date + INTERVAL '1 day')"); params2.append(date_to)
        sql2.append("GROUP BY t.equipment_id, t.equipmenttype, t.sitename, rc.code "
                    "ORDER BY t.equipment_id, cnt DESC")
        cur.execute(" ".join(sql2), tuple(params2))
        by_equipment = _fetchall_dict(cur)

        # 3. 교체 후보 순위 — P2(weight) + P3(시간 감쇠) 적용
        # weighted_score = SUM( taxonomy.weight × time_decay )
        # time_decay: 최근 1년 1.0 / 1~2년 0.5 / 그 이상 0.2
        cur.execute(
            """
            WITH item_codes AS (
              SELECT t.equipment_id, t.equipmenttype, t.sitename, t.facilitytype,
                     i.item_id, i.occurred_at, rc.code,
                     CASE
                       WHEN i.occurred_at >= now() - interval '1 year'  THEN 1.0
                       WHEN i.occurred_at >= now() - interval '2 years' THEN 0.5
                       ELSE 0.2
                     END AS time_decay
                FROM tb_report_item i
                JOIN tb_report r       ON r.report_id = i.report_id
                JOIN tb_task_master t  ON t.task_id   = i.task_id
                CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(i.root_causes, '[]'::jsonb)) AS rc(code)
               WHERE r.region = %s AND r.report_type = 'fault_action'
                 AND t.equipment_id IS NOT NULL
            ),
            weighted AS (
              SELECT ic.equipment_id, ic.equipmenttype, ic.sitename, ic.facilitytype,
                     COUNT(*) AS cause_count,
                     SUM(tx.weight * ic.time_decay) AS weighted_score,
                     SUM(CASE WHEN ic.time_decay >= 1.0 THEN 1 ELSE 0 END) AS recent_1y,
                     SUM(CASE WHEN ic.time_decay >= 0.5 AND ic.time_decay < 1.0 THEN 1 ELSE 0 END) AS recent_2y
                FROM item_codes ic
                JOIN tb_root_cause_taxonomy tx ON tx.code = ic.code
               GROUP BY ic.equipment_id, ic.equipmenttype, ic.sitename, ic.facilitytype
            ),
            totals AS (
              SELECT t.equipment_id, COUNT(*) AS total_count
                FROM tb_report_item i
                JOIN tb_report r      ON r.report_id = i.report_id
                JOIN tb_task_master t ON t.task_id   = i.task_id
               WHERE r.region = %s AND r.report_type = 'fault_action'
                 AND t.equipment_id IS NOT NULL
               GROUP BY t.equipment_id
            )
            SELECT w.equipment_id, w.equipmenttype, w.sitename, w.facilitytype,
                   w.cause_count, COALESCE(t.total_count, 0) AS total_count,
                   ROUND(w.weighted_score::numeric, 2) AS weighted_score,
                   w.recent_1y, w.recent_2y
              FROM weighted w
              LEFT JOIN totals t USING (equipment_id)
             ORDER BY weighted_score DESC NULLS LAST, w.cause_count DESC
             LIMIT 50
            """,
            (region, region),
        )
        replacement_ranking = _fetchall_dict(cur)

        return {
            "by_cause": by_cause,
            "by_equipment": by_equipment,
            "replacement_ranking": replacement_ranking,
        }
    finally:
        conn.close()


@router.post("/items/{item_id}/resummarize")
def resummarize_item(item_id: int, req: ResummarizeRequest):
    """현재 항목 본문(사용자 편집 포함)을 LLM 으로 정제.

    중요: 사용자가 추가·편집한 내용은 절대 사라지지 않는다.
    LLM 호출 실패 시에도 현재 텍스트를 그대로 보존.
    원본 task 의 task_content 는 참고만 하고 새로 추가하지 않는다.

    `dry_run=True` 인 경우 결과만 반환하고 DB 는 변경하지 않는다 — 미리보기 용도.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        item, report = _load_item_with_report(cur, item_id)
        _ensure_owner_or_403(report, req.user_id)
        _ensure_draft_or_405(report)

        # 현재 본문(사용자 편집 포함)을 입력으로 — 5개 서술 필드 모두 정제
        refined = refine_item_summary(
            current_occurred=item.get("occurred_text"),
            current_resolved=item.get("resolved_text"),
            current_symptom=item.get("symptom"),
            current_cause=item.get("cause"),
            current_key_issues=item.get("key_issues"),
            original_text=item.get("original_text"),
            site_name=item.get("site_name"),
            facility_type=item.get("facility_type"),
            equipment_name=item.get("equipment_name"),
            fault_category=item.get("fault_category"),
            inspection_type=item.get("inspection_type"),
        )

        if req.dry_run:
            # 미리보기 — DB 미반영. 5개 필드 current/refined 함께 반환
            return {
                "dry_run": True,
                "current": {
                    "occurred_text": item.get("occurred_text") or "",
                    "resolved_text": item.get("resolved_text") or "",
                    "symptom":       item.get("symptom") or "",
                    "cause":         item.get("cause") or "",
                    "key_issues":    item.get("key_issues") or "",
                },
                "refined": {
                    "occurred_text": refined.get("occurred_text") or "",
                    "resolved_text": refined.get("resolved_text") or "",
                    "symptom":       refined.get("symptom") or "",
                    "cause":         refined.get("cause") or "",
                    "key_issues":    refined.get("key_issues") or "",
                },
                "model": refined.get("model"),
                "fallback": refined.get("fallback", False),
            }

        cur.execute(
            """
            UPDATE tb_report_item
               SET occurred_text = %s, resolved_text = %s,
                   symptom = %s, cause = %s, key_issues = %s,
                   ai_summary_at = now(), ai_model = %s, updated_at = now()
             WHERE item_id = %s RETURNING *
            """,
            (refined["occurred_text"], refined["resolved_text"],
             refined.get("symptom"), refined.get("cause"), refined.get("key_issues"),
             refined.get("model"), item_id),
        )
        updated = _fetchone_dict(cur)
        conn.commit()
        return _row_to_item(updated)
    finally:
        conn.close()


@router.delete("/items/{item_id}")
def delete_item(item_id: int, user_id: str = Query(...)):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        item, report = _load_item_with_report(cur, item_id)
        _ensure_owner_or_403(report, user_id)
        _ensure_draft_or_405(report)
        cur.execute("DELETE FROM tb_report_item WHERE item_id = %s", (item_id,))
        conn.commit()
        return {"deleted": True, "item_id": item_id}
    finally:
        conn.close()


# ============================================================================
# 사진 (URL 등록 — 업로드는 chat_attachments 재사용)
# ============================================================================

@router.post("/items/{item_id}/photos")
def add_photo(item_id: int, req: AddPhotoRequest):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        item, report = _load_item_with_report(cur, item_id)
        _ensure_owner_or_403(report, req.user_id)
        _ensure_draft_or_405(report)
        photos = item.get("photo_urls") or []
        if isinstance(photos, str):
            try:
                photos = json.loads(photos)
            except Exception:
                photos = []
        photos.append({
            "url":      req.url,
            "source":   req.source if req.source in ("fault","action","user") else "user",
            "caption":  req.caption or ("추가 참고" if req.source == "user" else ""),
            "taken_at": req.taken_at.isoformat() if req.taken_at else None,
        })
        cur.execute(
            "UPDATE tb_report_item SET photo_urls = %s::jsonb, updated_at = now() "
            "WHERE item_id = %s RETURNING *",
            (json.dumps(photos, ensure_ascii=False, default=str), item_id),
        )
        row = _fetchone_dict(cur)
        conn.commit()
        return _row_to_item(row)
    finally:
        conn.close()


@router.delete("/items/{item_id}/photos")
def delete_photo(item_id: int, req: DeletePhotoRequest):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        item, report = _load_item_with_report(cur, item_id)
        _ensure_owner_or_403(report, req.user_id)
        _ensure_draft_or_405(report)
        photos = item.get("photo_urls") or []
        if isinstance(photos, str):
            photos = json.loads(photos)
        photos = [p for p in photos if p.get("url") != req.url]
        cur.execute(
            "UPDATE tb_report_item SET photo_urls = %s::jsonb, updated_at = now() "
            "WHERE item_id = %s RETURNING *",
            (json.dumps(photos, ensure_ascii=False, default=str), item_id),
        )
        row = _fetchone_dict(cur)
        conn.commit()
        return _row_to_item(row)
    finally:
        conn.close()


@router.patch("/items/{item_id}/photos")
def patch_photo(item_id: int, req: PatchPhotoRequest):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        item, report = _load_item_with_report(cur, item_id)
        _ensure_owner_or_403(report, req.user_id)
        _ensure_draft_or_405(report)
        photos = item.get("photo_urls") or []
        if isinstance(photos, str):
            photos = json.loads(photos)
        for p in photos:
            if p.get("url") == req.url:
                if req.caption is not None:
                    p["caption"] = req.caption
                if req.source in ("fault","action","user"):
                    p["source"] = req.source
                break
        cur.execute(
            "UPDATE tb_report_item SET photo_urls = %s::jsonb, updated_at = now() "
            "WHERE item_id = %s RETURNING *",
            (json.dumps(photos, ensure_ascii=False, default=str), item_id),
        )
        row = _fetchone_dict(cur)
        conn.commit()
        return _row_to_item(row)
    finally:
        conn.close()
