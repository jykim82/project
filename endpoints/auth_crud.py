"""
인증 엔드포인트 모듈 (auth_crud.py)

로컬 DB tb_user 컬럼:
    region, user_id, user_nm, user_pw (원본), user_pw_hash (bcrypt),
    pw_migrated (bool), user_auth, use_yn, dup_yn, lock_cnt (int),
    last_login, created_at, updated_at, current_session_id

비밀번호 전략:
  1. pw_migrated=TRUE → user_pw_hash(bcrypt) 검증
  2. pw_migrated=FALSE → user_pw(평문) 비교 후 성공 시 bcrypt 마이그레이션

lock_cnt >= MAX_LOGIN_ATTEMPTS(5) → 계정 잠금 (ADMIN 이상 해제 가능)

JWT:
  access_token: 1시간, Authorization: Bearer
  refresh_token: 24시간, /api/auth/refresh 전용
"""

import bcrypt as _bcrypt
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

logger = logging.getLogger("slm")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "slm-dev-secret-change-in-production")
REFRESH_SECRET_KEY = os.environ.get("JWT_REFRESH_SECRET_KEY", SECRET_KEY + "-refresh")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_HOURS = int(os.environ.get("REFRESH_TOKEN_EXPIRE_HOURS", "24"))
MAX_LOGIN_ATTEMPTS = 5  # 초과 시 계정 잠금

# ---------------------------------------------------------------------------
# 의존성
# ---------------------------------------------------------------------------
security = HTTPBearer(auto_error=False)

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py lifespan에서 DB 커넥션 팩토리를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn
    _ensure_schema()


def _ensure_schema() -> None:
    """필요한 컬럼이 없으면 자동 추가 (로컬 개발 DB 마이그레이션)."""
    ddl_statements = [
        "ALTER TABLE tb_user ADD COLUMN IF NOT EXISTS user_pw_hash varchar(200)",
        "ALTER TABLE tb_user ADD COLUMN IF NOT EXISTS pw_migrated boolean DEFAULT FALSE",
        "ALTER TABLE tb_user ADD COLUMN IF NOT EXISTS lock_cnt integer DEFAULT 0",
        "ALTER TABLE tb_user ADD COLUMN IF NOT EXISTS last_login timestamp",
        "ALTER TABLE tb_user ADD COLUMN IF NOT EXISTS current_session_id varchar(100)",
        "ALTER TABLE tb_user ADD COLUMN IF NOT EXISTS created_at timestamp DEFAULT NOW()",
        "ALTER TABLE tb_user ADD COLUMN IF NOT EXISTS updated_at timestamp DEFAULT NOW()",
    ]
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            for stmt in ddl_statements:
                try:
                    cur.execute(stmt)
                except Exception:
                    conn.rollback()
        conn.commit()
        logger.info("[auth] tb_user 스키마 확인 완료")
    except Exception as e:
        logger.warning(f"[auth] 스키마 확인 실패 (무시): {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _db():
    if _get_db_connection is None:
        raise RuntimeError("auth_crud: DB 커넥션이 초기화되지 않았습니다.")
    return _get_db_connection()


# ---------------------------------------------------------------------------
# 요청/응답 모델
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    region: str = "R01"
    user_id: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    session_id: Optional[str] = None


class UserUpdateRequest(BaseModel):
    user_nm: Optional[str] = None
    user_auth: Optional[str] = None
    user_tel: Optional[str] = None
    user_mail: Optional[str] = None
    use_yn: Optional[str] = None
    password: Optional[str] = None


class UserCreateRequest(BaseModel):
    region: str = "R01"
    user_id: str
    user_nm: str
    password: str
    user_auth: str = "USER"
    user_tel: Optional[str] = None
    user_mail: Optional[str] = None


# ---------------------------------------------------------------------------
# 비밀번호 헬퍼
# ---------------------------------------------------------------------------

def _verify_password(plain: str, stored_plain: Optional[str], stored_hash: Optional[str], migrated: bool) -> bool:
    """bcrypt 해시(마이그레이션 완료) 또는 평문(미이행)을 검증한다."""
    if migrated and stored_hash:
        try:
            return _bcrypt.checkpw(plain.encode(), stored_hash.encode())
        except Exception:
            return False
    # 평문 비교 (마이그레이션 전)
    return plain == (stored_plain or "")


def _hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def _migrate_password(conn, region: str, user_id: str, plain: str) -> None:
    """평문 → bcrypt 마이그레이션 (로그인 성공 시 자동 실행)."""
    try:
        hashed = _hash_password(plain)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_user SET user_pw_hash=%s, pw_migrated=TRUE, updated_at=NOW() WHERE region=%s AND user_id=%s",
                (hashed, region, user_id),
            )
        conn.commit()
        logger.info(f"[auth] bcrypt 마이그레이션 완료: {user_id}")
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning(f"[auth] 마이그레이션 실패 (무시): {e}")


# ---------------------------------------------------------------------------
# JWT 헬퍼
# ---------------------------------------------------------------------------

def _create_access_token(payload: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({**payload, "exp": expire, "type": "access"}, SECRET_KEY, algorithm=ALGORITHM)


def _create_refresh_token(payload: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=REFRESH_TOKEN_EXPIRE_HOURS)
    return jwt.encode({**payload, "exp": expire, "type": "refresh"}, REFRESH_SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str, is_refresh: bool = False) -> dict:
    secret = REFRESH_SECRET_KEY if is_refresh else SECRET_KEY
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
        expected_type = "refresh" if is_refresh else "access"
        if payload.get("type") != expected_type:
            raise JWTError("토큰 타입 불일치")
        return payload
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"유효하지 않은 토큰: {e}")


def _get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")
    return _decode_token(credentials.credentials)


def _require_auth_level(required: str, user: dict) -> None:
    """MASTER > ADMIN > USER 계층 권한 검사."""
    levels = {"MASTER": 3, "ADMIN": 2, "USER": 1}
    if levels.get(user.get("auth_idn", ""), 0) < levels.get(required, 99):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="권한이 부족합니다.")


# ---------------------------------------------------------------------------
# 접속 이력 헬퍼
# ---------------------------------------------------------------------------

def _log_access(conn, *, region: str, user_id: str, request: Request) -> None:
    """tb_access_log에 로그인 이력을 기록한다."""
    try:
        ua = request.headers.get("user-agent", "")
        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tb_access_log
                    (region, user_id, request_path, request_method, client_ip, access_time)
                VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                (region, user_id, "/api/auth/login", "POST", client_ip),
            )
        conn.commit()
    except Exception as e:
        logger.warning(f"[auth] 접속 이력 기록 실패 (무시): {e}")
        try:
            conn.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 라우트 핸들러
# ---------------------------------------------------------------------------

@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """
    로그인 — tb_user에서 계정 조회, 비밀번호 검증, JWT 발급.
    실패 시 lock_cnt 증가, MAX_LOGIN_ATTEMPTS 도달 시 계정 잠금.
    성공 시 bcrypt 미이행 계정 자동 마이그레이션.
    """
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, user_nm, user_pw, user_pw_hash, pw_migrated,
                       user_auth, lock_cnt, use_yn
                FROM tb_user
                WHERE region = %s AND user_id = %s
                """,
                (body.region, body.user_id),
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="아이디 또는 비밀번호가 올바르지 않습니다.")

        user_id, user_nm, user_pw, user_pw_hash, pw_migrated, user_auth, lock_cnt, use_yn = row

        # 계정 비활성 확인
        if use_yn and str(use_yn).strip() not in ("Y", "1", "true"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="비활성화된 계정입니다.")

        # 잠금 확인 (lock_cnt가 int 또는 char인 경우 모두 처리)
        try:
            lock_count = int(str(lock_cnt).strip()) if lock_cnt is not None else 0
        except (ValueError, TypeError):
            lock_count = 0

        if lock_count >= MAX_LOGIN_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"로그인 {MAX_LOGIN_ATTEMPTS}회 초과로 계정이 잠겼습니다. 관리자에게 문의하세요.",
            )

        # 비밀번호 검증
        migrated = bool(pw_migrated)
        if not _verify_password(body.password, user_pw, user_pw_hash, migrated):
            new_count = lock_count + 1
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tb_user SET lock_cnt=%s WHERE region=%s AND user_id=%s",
                    (new_count, body.region, body.user_id),
                )
            conn.commit()
            remaining = MAX_LOGIN_ATTEMPTS - new_count
            msg = (
                f"아이디 또는 비밀번호가 올바르지 않습니다. ({remaining}회 남음)"
                if remaining > 0
                else "로그인 시도 횟수를 초과하여 계정이 잠겼습니다."
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=msg)

        # bcrypt 미이행 계정 자동 마이그레이션
        if not migrated:
            _migrate_password(conn, body.region, body.user_id, body.password)

        # 세션 ID 발급
        session_id = str(uuid.uuid4())
        token_payload = {
            "sub": user_id,
            "region": body.region,
            "auth_idn": user_auth or "USER",
            "session_id": session_id,
        }
        access_token = _create_access_token(token_payload)
        refresh_token = _create_refresh_token(token_payload)

        # 잠금 초기화 + 세션/마지막 로그인 갱신
        # last_login은 varchar(20)이므로 YYYY-MM-DD HH24:MI:SS 포맷으로 저장
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tb_user
                SET lock_cnt=0,
                    current_session_id=%s,
                    last_login=TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
                    updated_at=NOW()
                WHERE region=%s AND user_id=%s
                """,
                (session_id, body.region, user_id),
            )
        conn.commit()

        # 접속 이력
        _log_access(conn, region=body.region, user_id=user_id, request=request)

        logger.info(f"[auth] 로그인 성공: {user_id} ({user_auth})")

        return {
            "token": access_token,
            "refresh_token": refresh_token,
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "session_id": session_id,
            "user": {
                "region": body.region,
                "user_id": user_id,
                "user_nm": user_nm,
                "user_auth": user_auth or "USER",
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[auth] 로그인 처리 오류: {e}")
        raise HTTPException(status_code=500, detail="로그인 처리 중 오류가 발생했습니다.")
    finally:
        conn.close()


@router.post("/refresh")
async def refresh_token_endpoint(body: RefreshRequest):
    """refresh_token으로 새 access_token을 발급한다."""
    payload = _decode_token(body.refresh_token, is_refresh=True)
    new_payload = {k: v for k, v in payload.items() if k not in ("exp", "iat", "type")}
    access_token = _create_access_token(new_payload)
    return {
        "token": access_token,
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.post("/logout")
async def logout(body: LogoutRequest, current_user: dict = Depends(_get_current_user)):
    """로그아웃 — current_session_id 초기화."""
    user_id = current_user.get("sub")
    region = current_user.get("region", "R01")
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_user SET current_session_id=NULL, updated_at=NOW() WHERE region=%s AND user_id=%s",
                (region, user_id),
            )
        conn.commit()
        logger.info(f"[auth] 로그아웃: {user_id}")
        return {"message": "로그아웃되었습니다."}
    except Exception as e:
        logger.error(f"[auth] 로그아웃 오류: {e}")
        return {"message": "로그아웃 처리 중 오류가 발생했습니다."}
    finally:
        conn.close()


@router.get("/me")
async def get_me(current_user: dict = Depends(_get_current_user)):
    """현재 로그인 사용자 정보 + 메뉴 권한 목록."""
    user_id = current_user.get("sub")
    region = current_user.get("region", "R01")
    auth_idn = current_user.get("auth_idn", "USER")
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_nm FROM tb_user WHERE region=%s AND user_id=%s",
                (region, user_id),
            )
            row = cur.fetchone()
        user_nm = row[0] if row else user_id

        # tb_auth_menu 기반 메뉴 권한 (비어있으면 전체 허용)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM tb_auth_menu WHERE region=%s AND auth_idn=%s",
                (region, auth_idn),
            )
            menu_count = cur.fetchone()[0]

        allowed_menus = []
        if menu_count > 0:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT am.menu_idn, m.menu_nm, m.app_path, m.pmenu_idn, m.menu_idx
                    FROM tb_auth_menu am
                    JOIN tb_menu m ON m.region=am.region AND m.menu_idn=am.menu_idn
                    WHERE am.region=%s AND am.auth_idn=%s AND am.use_yn='Y' AND m.use_yn='Y'
                    ORDER BY m.menu_idx
                    """,
                    (region, auth_idn),
                )
                allowed_menus = [
                    {"menu_idn": r[0], "menu_nm": r[1], "app_path": r[2], "pmenu_idn": r[3], "menu_idx": r[4]}
                    for r in cur.fetchall()
                ]

        return {
            "user_id": user_id,
            "user_nm": user_nm,
            "region": region,
            "auth_idn": auth_idn,
            "menus": allowed_menus,
        }
    except Exception as e:
        logger.error(f"[auth] /me 오류: {e}")
        raise HTTPException(status_code=500, detail="사용자 정보 조회 오류")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 사용자 관리 (ADMIN/MASTER 전용)
# ---------------------------------------------------------------------------

@router.get("/users")
async def list_users(region: str = "R01", current_user: dict = Depends(_get_current_user)):
    """사용자 목록 조회 — ADMIN 이상."""
    _require_auth_level("ADMIN", current_user)
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, user_nm, user_auth, use_yn, lock_cnt, last_login, created_at
                FROM tb_user
                WHERE region=%s
                ORDER BY user_auth DESC, user_id
                """,
                (region,),
            )
            rows = cur.fetchall()
        def _fmt(v) -> Optional[str]:
            """timestamp or string → ISO string, None → None."""
            if v is None:
                return None
            if hasattr(v, "isoformat"):
                return v.isoformat()
            return str(v)

        return [
            {
                "user_id": r[0],
                "user_nm": r[1],
                "user_auth": r[2],
                "use_yn": str(r[3]).strip() if r[3] else "Y",
                "lock_cnt": int(r[4]) if r[4] is not None else 0,
                "is_locked": (int(r[4]) if r[4] is not None else 0) >= MAX_LOGIN_ATTEMPTS,
                "last_login": _fmt(r[5]),
                "created_at": _fmt(r[6]),
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[auth] 사용자 목록 오류: {e}")
        raise HTTPException(status_code=500, detail="사용자 목록 조회 오류")
    finally:
        conn.close()


@router.post("/users")
async def create_user(body: UserCreateRequest, current_user: dict = Depends(_get_current_user)):
    """사용자 생성 — MASTER 전용."""
    _require_auth_level("MASTER", current_user)
    caller_id = current_user.get("sub")
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM tb_user WHERE region=%s AND user_id=%s",
                (body.region, body.user_id),
            )
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="이미 존재하는 사용자 ID입니다.")

        pw_hash = _hash_password(body.password)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tb_user
                    (region, user_id, user_nm, user_pw_hash, pw_migrated, user_auth,
                     use_yn, lock_cnt, created_at, updated_at)
                VALUES (%s, %s, %s, %s, TRUE, %s, 'Y', 0, NOW(), NOW())
                """,
                (body.region, body.user_id, body.user_nm, pw_hash, body.user_auth),
            )
        conn.commit()
        logger.info(f"[auth] 사용자 생성: {body.user_id} by {caller_id}")
        return {
            "user_id": body.user_id,
            "user_nm": body.user_nm,
            "user_auth": body.user_auth,
            "use_yn": "Y",
            "lock_cnt": 0,
            "last_login": None,
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"[auth] 사용자 생성 오류: {e}")
        raise HTTPException(status_code=500, detail="사용자 생성 오류")
    finally:
        conn.close()


@router.put("/users/{user_id}")
async def update_user(
    user_id: str, body: UserUpdateRequest, current_user: dict = Depends(_get_current_user)
):
    """
    사용자 수정.
    본인: user_nm, password
    ADMIN 이상: use_yn (잠금 해제 포함)
    MASTER 전용: user_auth 변경
    """
    caller_id = current_user.get("sub")
    caller_auth = current_user.get("auth_idn", "USER")
    region = current_user.get("region", "R01")

    if caller_id != user_id:
        _require_auth_level("ADMIN", current_user)
    if body.user_auth is not None and caller_auth != "MASTER":
        raise HTTPException(status_code=403, detail="권한 변경은 MASTER만 가능합니다.")

    conn = _db()
    try:
        fields, values = [], []

        if body.user_nm is not None:
            fields.append("user_nm=%s"); values.append(body.user_nm)
        if body.user_auth is not None:
            fields.append("user_auth=%s"); values.append(body.user_auth)
        if body.use_yn is not None:
            fields.append("use_yn=%s"); values.append(body.use_yn)
            if body.use_yn == "Y":
                fields.append("lock_cnt=0")
        if body.password is not None:
            fields.append("user_pw_hash=%s"); values.append(_hash_password(body.password))
            fields.append("pw_migrated=TRUE")

        if not fields:
            return {"message": "변경할 내용이 없습니다."}

        fields.append("updated_at=NOW()")
        values.extend([region, user_id])

        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE tb_user SET {', '.join(fields)} WHERE region=%s AND user_id=%s",
                values,
            )
        conn.commit()
        logger.info(f"[auth] 사용자 수정: {user_id} by {caller_id}")
        return {"message": "사용자 정보가 수정되었습니다."}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"[auth] 사용자 수정 오류: {e}")
        raise HTTPException(status_code=500, detail="사용자 수정 오류")
    finally:
        conn.close()


@router.post("/users/{user_id}/unlock")
@router.put("/users/{user_id}/unlock")
async def unlock_user(user_id: str, current_user: dict = Depends(_get_current_user)):
    """계정 잠금 해제 — ADMIN 이상."""
    _require_auth_level("ADMIN", current_user)
    region = current_user.get("region", "R01")
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_user SET lock_cnt=0, updated_at=NOW() WHERE region=%s AND user_id=%s",
                (region, user_id),
            )
        conn.commit()
        logger.info(f"[auth] 잠금 해제: {user_id} by {current_user.get('sub')}")
        return {"message": f"'{user_id}' 계정 잠금이 해제되었습니다."}
    except Exception as e:
        conn.rollback()
        logger.error(f"[auth] 잠금 해제 오류: {e}")
        raise HTTPException(status_code=500, detail="잠금 해제 오류")
    finally:
        conn.close()


class ToggleActiveRequest(BaseModel):
    use_yn: str  # "Y" | "N"


@router.put("/users/{user_id}/active")
async def toggle_user_active(
    user_id: str, body: ToggleActiveRequest, current_user: dict = Depends(_get_current_user)
):
    """사용자 활성/비활성 토글 — ADMIN 이상."""
    _require_auth_level("ADMIN", current_user)
    region = current_user.get("region", "R01")
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_user SET use_yn=%s, updated_at=NOW() WHERE region=%s AND user_id=%s",
                (body.use_yn, region, user_id),
            )
        conn.commit()
        logger.info(f"[auth] 활성상태 변경: {user_id} → {body.use_yn} by {current_user.get('sub')}")
        return {"message": "상태가 변경되었습니다."}
    except Exception as e:
        conn.rollback()
        logger.error(f"[auth] 활성 토글 오류: {e}")
        raise HTTPException(status_code=500, detail="상태 변경 오류")
    finally:
        conn.close()


@router.post("/users/{user_id}/session-end")
async def end_user_session(user_id: str, current_user: dict = Depends(_get_current_user)):
    """사용자 세션 강제 종료 — ADMIN 이상."""
    _require_auth_level("ADMIN", current_user)
    region = current_user.get("region", "R01")
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_user SET current_session_id=NULL, updated_at=NOW() WHERE region=%s AND user_id=%s",
                (region, user_id),
            )
        conn.commit()
        logger.info(f"[auth] 세션 종료: {user_id} by {current_user.get('sub')}")
        return {"message": f"'{user_id}' 세션이 종료되었습니다."}
    except Exception as e:
        conn.rollback()
        logger.error(f"[auth] 세션 종료 오류: {e}")
        raise HTTPException(status_code=500, detail="세션 종료 오류")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 접속 이력 조회
# ---------------------------------------------------------------------------

@router.get("/access-logs")
async def get_access_logs(
    region: str = "R01",
    user_id: Optional[str] = None,
    limit: int = 100,
    current_user: dict = Depends(_get_current_user),
):
    """접속 이력 조회 — ADMIN 이상."""
    _require_auth_level("ADMIN", current_user)
    conn = _db()
    try:
        where = ["region=%s"]
        params: list = [region]
        if user_id:
            where.append("user_id=%s")
            params.append(user_id)
        params.append(min(limit, 500))

        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT log_id, user_id, request_path, request_method,
                       client_ip, browser, os, access_time
                FROM tb_access_log
                WHERE {' AND '.join(where)}
                ORDER BY access_time DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()

        return [
            {
                "log_id": r[0],
                "user_id": r[1],
                "request_path": r[2],
                "request_method": r[3],
                "client_ip": r[4],
                "browser": r[5],
                "os": r[6],
                "access_time": r[7].isoformat() if r[7] else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[auth] 접속 이력 오류: {e}")
        raise HTTPException(status_code=500, detail="접속 이력 조회 오류")
    finally:
        conn.close()
