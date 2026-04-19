$ErrorActionPreference = "Stop"

Write-Host "=== 1. TimescaleDB 시작 ===" -ForegroundColor Green
docker compose -f docker-compose.dev.yml up -d
Start-Sleep -Seconds 5

$usePsql = Get-Command psql -ErrorAction SilentlyContinue

Write-Host "=== 2. 스키마 초기화 ===" -ForegroundColor Green
if ($usePsql) {
    psql -h localhost -p 5432 -U slm_dev -d slm -f db\init\01_schema.sql
} else {
    docker cp db\init\01_schema.sql slm-timescaledb:/tmp/
    docker exec slm-timescaledb pg_restore -U slm_dev -d slm --no-owner --no-privileges /tmp/01_schema.sql
}

Write-Host "=== 3. 마이그레이션 적용 ===" -ForegroundColor Green
Get-ChildItem db\migrations\*.sql | Sort-Object Name | ForEach-Object {
    Write-Host "  -> $($_.Name)"
    if ($usePsql) {
        psql -h localhost -p 5432 -U slm_dev -d slm -f $_.FullName
    } else {
        docker cp $_.FullName slm-timescaledb:/tmp/
        docker exec slm-timescaledb psql -U slm_dev -d slm -f "/tmp/$($_.Name)"
    }
}

Write-Host "=== 4. 시드 데이터 ===" -ForegroundColor Green
Get-ChildItem db\seed\*.sql -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object {
    Write-Host "  -> $($_.Name)"
    if ($usePsql) {
        psql -h localhost -p 5432 -U slm_dev -d slm -f $_.FullName
    } else {
        docker cp $_.FullName slm-timescaledb:/tmp/
        docker exec slm-timescaledb psql -U slm_dev -d slm -f "/tmp/$($_.Name)"
    }
}

Write-Host "=== 5. Next.js 의존성 ===" -ForegroundColor Green
Set-Location D:\web\slm-dashboard
npm install
Set-Location D:\web

Write-Host ""
Write-Host "=== 완료! ===" -ForegroundColor Cyan
Write-Host "  프로젝트 루트: D:\web"
Write-Host "  TimescaleDB:   localhost:5432"
Write-Host "  Next.js:       cd D:\web\slm-dashboard; npm run dev"
