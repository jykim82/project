"""EPANET 수리 시뮬레이션 모듈 (Phase 1).

활성화 상태: tb_comm_code (region, 'SITE_SETTING', 'EPANET_ENABLED').use_yn
- 'Y' → 모듈 사용 가능
- 'N' (default) → 엔드포인트 503 반환

wntr 가용성: import 성공 여부로 판단 (Docker 이미지 재빌드 필요).
"""

from typing import Callable, Optional


_get_db_connection: Optional[Callable] = None


def init(get_db_connection_fn: Callable) -> None:
    """ai_server.py 가 startup 시 DB 커넥션 함수를 주입."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def is_enabled(region: str = "R01") -> bool:
    """현재 region 의 EPANET_ENABLED 토글 상태 조회 (default False)."""
    if _get_db_connection is None:
        return False
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT use_yn FROM tb_comm_code "
            "WHERE region = %s AND grp_cd = 'SITE_SETTING' AND comm_cd = 'EPANET_ENABLED'",
            (region,),
        )
        row = cur.fetchone()
        cur.close()
        return bool(row and row[0] == "Y")
    except Exception:
        return False
    finally:
        if conn:
            conn.close()


def is_wntr_available() -> bool:
    """wntr 라이브러리 임포트 가능 여부."""
    try:
        import wntr  # noqa: F401
        return True
    except ImportError:
        return False


def get_db():
    """엔드포인트에서 DB 커넥션을 얻을 때 사용."""
    if _get_db_connection is None:
        raise RuntimeError("epanet 모듈이 초기화되지 않았습니다.")
    return _get_db_connection()
