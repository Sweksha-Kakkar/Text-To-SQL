# Stop the portable PostgreSQL 17 server.
$pg = "$env:LOCALAPPDATA\Programs\PostgreSQL17"
& "$pg\pgsql\bin\pg_ctl.exe" -D "$pg\data" -m fast stop
