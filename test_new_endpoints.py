"""
Phase 0~1 신규 엔드포인트 통합 스모크 테스트

대상 (5종):
1. POST /tags                    — 태그 생성 (D-3)
2. POST /chat/feedback           — 오분류 피드백 등록 (A-1)
3. POST /admin/facility-alias    — 시설 약칭 매핑 CRUD (A-3)
4. POST /trend/explain           — 트렌드 AI 요약 (C + C안)
5. POST /anomaly/explain         — 이상감지 원인 서술 (Phase 1 + C안)

실행:
    docker exec slm-backend python3 /app/test_new_endpoints.py

의존성: 표준 라이브러리만 (urllib, json)
종료 코드: 0 (성공) | 1 (실패)
"""

import io
import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE_URL = "http://localhost:8000"


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _request(
    method: str, path: str, payload: dict | None = None,
) -> tuple[int, dict]:
    """HTTP 호출 후 (status_code, json_body) 반환."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") or "{}"
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"_raw": body}


class _Report:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str) -> None:
        self.passed.append(name)
        print(f"  ✓ {name}")

    def fail(self, name: str, reason: str) -> None:
        self.failed.append((name, reason))
        print(f"  ✗ {name}: {reason}")

    def expect(self, name: str, cond: bool, reason: str = "") -> None:
        if cond:
            self.ok(name)
        else:
            self.fail(name, reason or "expectation failed")


# ── 테스트 ───────────────────────────────────────────────────────────────────

def test_create_tag(r: _Report) -> None:
    print("\n[1] POST /tags — 태그 생성")
    tagsn = f"TEST_SMOKE_{int(time.time())}"
    payload = {
        "tagsn": tagsn,
        "tagtype": "Analog Input",
        "sitename": "스모크테스트",
        "facilitytype": "배수지",
        "datainfo": "수위",
        "datadesc": "integration test",
        "unit": "m",
    }
    status, body = _request("POST", "/tags", payload)
    r.expect("create 201", status == 201, f"status={status} body={body}")
    r.expect("status=OK", body.get("status") == "OK", f"body={body}")
    r.expect("tagsn match", body.get("data", {}).get("tagsn") == tagsn)

    # 중복 생성 → 409
    status2, _ = _request("POST", "/tags", payload)
    r.expect("duplicate 409", status2 == 409, f"status={status2}")

    # 테스트 레코드 정리 (DB 직접, endpoint에 DELETE 없음)
    try:
        import os
        import psycopg2
        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST", "timescaledb"),
            port=os.environ.get("DB_PORT", "5432"),
            database=os.environ.get("DB_NAME", "slm"),
            user=os.environ.get("DB_USER", "slm_dev"),
            password=os.environ.get("DB_PASSWORD", "slm_dev_1234"),
        )
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tb_tag_info WHERE tagsn = %s", (tagsn,))
        conn.commit()
        conn.close()
        r.ok("cleanup")
    except Exception as e:
        r.fail("cleanup", str(e))


def test_chat_feedback(r: _Report) -> None:
    print("\n[2] POST /chat/feedback — 오분류 피드백 등록")
    # FK 제약상 tb_user에 존재하는 user_id여야 함 — admin 사용
    unique_comment = f"integration test {int(time.time())}"
    payload = {
        "region": "R01",
        "user_id": "admin",
        "user_question": "어제 수위 알려줘",
        "bot_answer": "조회된 데이터가 없습니다.",
        "intent_name": "FACILITY_TAG_LATEST_VALUE",
        "feedback_type": "wrong_answer",
        "comment": unique_comment,
    }
    status, body = _request("POST", "/chat/feedback", payload)
    r.expect("create 201", status == 201, f"status={status} body={body}")
    fid = body.get("feedback_id")
    r.expect("feedback_id present", fid is not None)

    # 목록 조회
    status_l, list_body = _request("GET", "/chat/feedback?region=R01&reviewed=false&limit=5")
    r.expect("list 200", status_l == 200)
    r.expect("list is array", isinstance(list_body, list))

    # 검토 완료 마킹
    if fid is not None:
        status_p, pbody = _request(
            "PATCH", f"/chat/feedback/{fid}/review",
            {"reviewed_by": "admin"},
        )
        r.expect("review 200", status_p == 200, f"status={status_p}")
        r.expect("reviewed=true", pbody.get("reviewed") is True)

        # 테스트 레코드 정리 (DB 직접)
        try:
            import os
            import psycopg2
            conn = psycopg2.connect(
                host=os.environ.get("DB_HOST", "timescaledb"),
                port=os.environ.get("DB_PORT", "5432"),
                database=os.environ.get("DB_NAME", "slm"),
                user=os.environ.get("DB_USER", "slm_dev"),
                password=os.environ.get("DB_PASSWORD", "slm_dev_1234"),
            )
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM tb_ai_chat_feedback WHERE feedback_id = %s", (fid,)
                )
            conn.commit()
            conn.close()
            r.ok("cleanup")
        except Exception as e:
            r.fail("cleanup", str(e))


def test_facility_alias(r: _Report) -> None:
    print("\n[3] /admin/facility-alias — 시설 약칭 CRUD")
    alias_name = f"SMOKE_{int(time.time())}"
    payload = {
        "region": "R01",
        "alias": alias_name,
        "sitename": "죽동",
        "priority": 1,
        "note": "integration test",
    }
    status, body = _request("POST", "/admin/facility-alias", payload)
    r.expect("create 201", status == 201, f"status={status} body={body}")
    aid = body.get("alias_id")
    r.expect("alias_id present", aid is not None)

    # 목록 조회
    status_l, list_body = _request("GET", "/admin/facility-alias?region=R01")
    r.expect("list 200", status_l == 200)

    # 수정
    if aid is not None:
        status_u, ubody = _request(
            "PATCH", f"/admin/facility-alias/{aid}",
            {"note": "updated"},
        )
        r.expect("update 200", status_u == 200, f"status={status_u}")
        r.expect("note updated", ubody.get("note") == "updated")

        # 삭제
        status_d, _ = _request("DELETE", f"/admin/facility-alias/{aid}")
        r.expect("delete 204", status_d == 204, f"status={status_d}")


def test_trend_explain(r: _Report) -> None:
    print("\n[4] POST /trend/explain — 트렌드 AI 요약 (C + C안)")
    payload = {
        "tag_name": "가곡(배) 수위",
        "tagsn": "44270_24904_LEI_N001",
        "unit": "m",
        "from_ts": "2026-04-11T00:00:00",
        "to_ts": "2026-04-11T23:59:59",
        "min": 1.05,
        "max": 1.18,
        "avg": 1.12,
        "count": 288,
        "anomaly_count": 0,
    }
    status, body = _request("POST", "/trend/explain", payload)
    r.expect("200", status == 200, f"status={status}")
    summary = body.get("summary", "") if isinstance(body, dict) else ""
    r.expect("summary not empty", bool(summary))
    src = body.get("source") if isinstance(body, dict) else None
    r.expect("source llm or fallback", src in ("llm", "fallback"), f"source={src}")


def test_anomaly_explain(r: _Report) -> None:
    print("\n[5] POST /anomaly/explain — 이상감지 원인 서술 (Phase 1 + C안)")
    payload = {
        "equipment_id": "SMOKE_PLC_001",
        "equipmenttype": "PLC",
        "sitename": "죽동",
        "facilitytype": "배수지",
        "health_score": 55,
        "health_grade": "주의",
        "failures": ["comm_error"],
        "failure_labels": ["통신 오류"],
        "anomaly_tag_count": 1,
        "total_tag_count": 12,
        "linked_anomaly_tags": ["44270_24110_LEA_N001"],
    }
    status, body = _request("POST", "/anomaly/explain", payload)
    r.expect("200", status == 200, f"status={status}")
    summary = body.get("summary", "") if isinstance(body, dict) else ""
    r.expect("summary not empty", bool(summary))
    src = body.get("source") if isinstance(body, dict) else None
    r.expect("source llm or fallback", src in ("llm", "fallback"), f"source={src}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> int:
    report = _Report()
    tests = [
        test_create_tag,
        test_chat_feedback,
        test_facility_alias,
        test_trend_explain,
        test_anomaly_explain,
    ]

    # 서버 health 확인
    try:
        status, _ = _request("GET", "/health")
        if status != 200:
            print(f"서버 비정상: /health status={status}")
            return 2
    except Exception as e:
        print(f"서버 접속 실패: {e}")
        return 2

    for tfn in tests:
        try:
            tfn(report)
        except Exception as e:
            report.fail(tfn.__name__, f"exception: {e}")

    print("\n" + "=" * 60)
    print(f"통합 스모크 테스트 결과: {len(report.passed)} passed, {len(report.failed)} failed")
    if report.failed:
        print("\n실패 항목:")
        for name, reason in report.failed:
            print(f"  ✗ {name}: {reason}")
        return 1
    print("모든 테스트 통과 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
