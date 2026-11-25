# Start the portable PostgreSQL 17 server (no admin rights needed).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start_db.ps1
$pg = "$env:LOCALAPPDATA\Programs\PostgreSQL17"
& "$pg\pgsql\bin\pg_isready.exe" -h localhost -p 5432 -q
if ($LASTEXITCODE -eq 0) { Write-Host "PostgreSQL is already running on port 5432."; exit 0 }
& "$pg\pgsql\bin\pg_ctl.exe" -D "$pg\data" -l "$pg\postgres.log" -o "-p 5432 -c listen_addresses=localhost" -w start
