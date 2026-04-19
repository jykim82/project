#!/bin/bash
CONTAINER="slm-timescaledb"
echo "Waiting for DB..."
for i in $(seq 1 30); do
    if sudo docker exec "$CONTAINER" pg_isready -U slm_dev -d slm >/dev/null 2>&1; then
        echo "DB ready after ${i}s"
        exit 0
    fi
    sleep 1
done
echo "Timeout waiting for DB"
exit 1
