#!/bin/bash
set -euo pipefail
CONTAINER="slm-timescaledb"
DB_USER="slm_dev"
DB_NAME="slm"

echo "=== Applying seed data ==="
for f in /mnt/d/web/db/seed/*.sql; do
    filename=$(basename "$f")
    echo "  → $filename"
    sudo docker cp "$f" "$CONTAINER:/tmp/$filename"
    sudo docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -f "/tmp/$filename" 2>&1 | grep -E "^(ERROR|INSERT|psql:)" || true
done
echo "=== Seed complete ==="

echo ""
echo "=== Verify tables ==="
sudo docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "
SELECT schemaname, count(*) as tables
FROM pg_tables
WHERE schemaname = 'public'
GROUP BY schemaname;
"

echo ""
echo "=== Verify key data ==="
sudo docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT user_id, user_nm, user_auth FROM tb_user;"
sudo docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT menu_idn, menu_nm, app_path FROM tb_menu ORDER BY menu_idx;"
