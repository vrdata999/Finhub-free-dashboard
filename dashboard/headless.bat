@echo off
REM Launch dashboard headlessly (no browser popup). Useful for remote machines,
REM CI smoke-tests, or when you just want the API up.
REM Usage: headless.bat
REM        headless.bat 8765  (custom port)

setlocal

cd /d "%~dp0"

set PORT=8501
if not "%~1"=="" set PORT=%~1

python -c "import streamlit" 2>nul
if errorlevel 1 (
    echo streamlit is not installed. Run install.bat first.
    exit /b 1
)

echo === Dashboard (headless) on http://localhost:%PORT% ===
echo    Press Ctrl+C to stop.

python -m streamlit run app.py --server.port %PORT% --server.headless true --browser.gatherUsageStats false %2 %3 %4 %5 %6 %7 %8 %9

endlocal