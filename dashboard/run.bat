@echo off
REM Launch the Streamlit dashboard. Opens at http://localhost:8501
REM Usage: run.bat
REM        run.bat 8765      (custom port)

setlocal

cd /d "%~dp0"

set PORT=8501
if not "%~1"=="" set PORT=%~1

REM Sanity checks - friendly error messages instead of stack traces
python -c "import streamlit" 2>nul
if errorlevel 1 (
    echo streamlit is not installed. Run install.bat first.
    exit /b 1
)

if not exist "..\.env" (
    echo WARNING: ..\.env not found.
    echo The dashboard reads ..\.env for FINNHUB_API_KEY.
    echo Create it with one line:  FINNHUB_API_KEY=your_key_here
    echo.
    set /p CONTINUE=Continue anyway? [y/N]
    if /i not "%CONTINUE%"=="y" exit /b 1
)

echo === Launching dashboard on http://localhost:%PORT% ===
echo    Press Ctrl+C to stop.
echo.

python -m streamlit run app.py --server.port %PORT% --server.headless false %2 %3 %4 %5 %6 %7 %8 %9

endlocal