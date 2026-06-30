@echo off
REM Delete the SQLite cache so the next run re-fetches every endpoint.
REM Usage: clean-cache.bat

setlocal

cd /d "%~dp0"

if exist ".cache\finnhub.db" (
    del /f /q ".cache\finnhub.db"
    echo Cache deleted: .cache\finnhub.db
) else (
    echo No cache file found at .cache\finnhub.db
)

endlocal
