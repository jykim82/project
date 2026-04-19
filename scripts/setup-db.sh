#!/bin/bash
# ============================================================
# SLM TimescaleDB 초기화 스크립트 (WSL/Linux)
#
# 사용법:
#   bash scripts/setup-db.sh          # 전체 초기화
#   bash scripts/setup-db.sh seed     # seed 데이터만 재적용
#   bash scripts/setup-db.sh reset    # 볼륨 삭제 후 재생성
# ============================================================

set -euo pipefail

COMPOSE_FILE="docker-compose.dev.yml"
CONTAINER="slm-timescaledb"
DB_USER="slm_dev"
DB_NAME="slm"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

wait_for_db() {
    log_info "TimescaleDB 시작 대기 중..."
    local max_wait=30
    local count=0
    while ! docker exec "$CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; do
        count=$((count + 1))
        if [ "$count" -ge "$max_wait" ]; then
            log_error "TimescaleDB 시작 타임아웃 (${max_wait}초)"
            exit 1
        fi
        sleep 1
    done
    log_info "TimescaleDB 준비 완료"
}

run_sql_file() {
    local file="$1"
    local filename
    filename=$(basename "$file")
    log_info "  → $filename"
    docker cp "$file" "$CONTAINER:/tmp/$filename"
    docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -f "/tmp/$filename" 2>&1 | \
        grep -v "^SET$\|^CREATE\|^ALTER\|^INSERT\|^COMMENT\|^SELECT\|^NOTICE" || true
}

apply_seed() {
    log_info "=== Seed 데이터 적용 ==="
    for f in db/seed/*.sql; do
        [ -f "$f" ] && run_sql_file "$f"
    done
    log_info "=== Seed 완료 ==="
}

# ============================================================
# 메인 로직
# ============================================================

MODE="${1:-full}"

case "$MODE" in
    seed)
        wait_for_db
        apply_seed
        ;;
    reset)
        log_warn "기존 볼륨을 삭제하고 재생성합니다."
        read -rp "계속하시겠습니까? (y/N) " confirm
        if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
            log_info "취소되었습니다."
            exit 0
        fi
        docker compose -f "$COMPOSE_FILE" down -v
        docker compose -f "$COMPOSE_FILE" up -d
        wait_for_db
        # docker-entrypoint-initdb.d가 자동 실행됨 (init/*.sql)
        sleep 3
        apply_seed
        ;;
    full|*)
        log_info "=== 1. TimescaleDB 컨테이너 시작 ==="
        docker compose -f "$COMPOSE_FILE" up -d
        wait_for_db

        log_info "=== 2. 스키마 초기화 (init/*.sql) ==="
        for f in db/init/*.sql; do
            [ -f "$f" ] && [[ "$f" != *.bak ]] && run_sql_file "$f"
        done

        log_info "=== 3. 마이그레이션 적용 ==="
        for f in db/migrations/*.sql; do
            [ -f "$f" ] && run_sql_file "$f"
        done

        log_info "=== 4. Seed 데이터 적용 ==="
        apply_seed

        log_info ""
        log_info "=== 초기화 완료 ==="
        log_info "  접속: psql -h localhost -p 5432 -U $DB_USER -d $DB_NAME"
        log_info "  또는: docker exec -it $CONTAINER psql -U $DB_USER -d $DB_NAME"
        ;;
esac
