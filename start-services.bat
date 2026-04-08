@echo off
chcp 949 >nul
title SLM Dashboard - Service Startup
echo ============================================
echo   SLM Dashboard Service Startup
echo ============================================
echo.

:: 1. WSL2 IP 자동 감지 (Docker/DB 접속용)
echo [1/4] WSL2 IP 감지 중...
for /f "delims=" %%i in ('wsl -d Ubuntu -- bash -c "ip route get 1.1.1.1 2>/dev/null | awk '/src/{print $7}'"') do set WSL_IP=%%i
if "%WSL_IP%"=="" set WSL_IP=localhost
echo   WSL2 IP: %WSL_IP%
echo.

:: 2. PostgreSQL 연결 정리
echo [2/4] PostgreSQL 연결 정리 중...
C:\Python313\python.exe -c "
import psycopg2, os, sys
host = '%WSL_IP%'
try:
    c=psycopg2.connect(host=host,port=5433,dbname='slm',user='slm_dev',password='slm_dev_1234')
    cur=c.cursor()
    cur.execute(\"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='slm' AND state IN ('idle in transaction','active') AND pid<>pg_backend_pid()\")
    n=len(cur.fetchall())
    c.commit();c.close()
    print(f'  [OK] 유휴 연결 {n}개 정리 완료 (host={host})')
except Exception as e:
    print(f'  [WARN] DB 연결 정리 (무시): {e}')
"
echo.

:: 3. 기존 프로세스 종료 (IPv4 + IPv6 모두)
echo [3/4] 기존 프로세스 종료 중...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000"') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000"') do taskkill /PID %%a /F >nul 2>&1
timeout /t 2 /nobreak >nul
echo   [OK] 포트 정리 완료
echo.

:: 4. 서비스 시작
echo [4/4] 서비스 시작 중...
echo   AI Server (FastAPI, port 8000) 시작...
start "AI Server" cmd /k "set DB_HOST=%WSL_IP%&& set DB_PORT=5433&& cd /d d:\slm && C:\Python313\python.exe ai_server.py"

echo   AI Server 초기화 대기 (15초)...
timeout /t 15 /nobreak >nul

echo   Next.js HTTPS (port 3000) 시작...
start "Next.js" cmd /k "cd /d d:\web\slm-dashboard\slm-dashboard && npm run dev:https:fast"

echo.
echo ============================================
echo   모든 서비스가 시작되었습니다!
echo.
echo   - TimescaleDB:  %WSL_IP%:5433 (WSL Docker)
echo   - AI Server:    http://localhost:8000
echo   - Dashboard:    https://localhost:3000
echo.
echo   이 창은 닫아도 됩니다.
echo ============================================
pause
