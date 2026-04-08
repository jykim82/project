#!/bin/bash
# Windows PATH 제거 — Linux Python 우선
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/jykim/.local/bin

cd /mnt/d/slm
echo "Python: $(which python3) $(python3 --version)"

nohup python3 -m uvicorn ai_server:app --host 0.0.0.0 --port 8000 \
  >> /mnt/d/web/aiserver_out.txt 2>> /mnt/d/web/aiserver_err.txt &
echo "AI Server PID: $!"
